#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CLI 运行模块, 负责命令行参数解析, 配置读取, 串口发送和命令模块执行。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import argparse
import sys
import time

import serial
import serial.tools.list_ports

from core.command_executor import ConfigCommandProvider, collect_module_commands
from core import output_rules
from core.output_rules import ANSI_RESET, rules_from_state
from app_identity import get_config_file
from managers.config_manager import ConfigManager
from utils.ui_utils import UIUtils, SpecialCommandType, OutputSource


class CliOutput:
    """CLI 输出管理器, 输出动作和规则分离."""

    def __init__(self, state):
        self.rules = rules_from_state(state)

    def append(self, text, source_type, end=None):
        if not self.rules.source_enabled(source_type):
            return
        color = self.rules.ansi_color_for(source_type)
        suffix = ANSI_RESET if color else ""
        output = f"{self.rules.timestamp_for(source_type)}{text}"
        self.rules.before_append(source_type)
        if end is None:
            end = "" if source_type == OutputSource.RECEIVE else "\n"
        stream = sys.stderr if source_type == OutputSource.ERROR else sys.stdout
        print(f"{color}{output}{suffix}", end=end, file=stream)

    def send_visible(self):
        return self.rules.show_send_enabled()


def normalize_port(port_text):
    return (port_text or "").strip().split(" ")[0]


def get_state(config_manager):
    state = config_manager.get("state")
    if state is None:
        state = config_manager.get("last_state")
    if state is None:
        state = {
            "basic_settings": {
                "port": "",
                "port_node": "",
                "baudrate": "115200",
                "databits": "8",
                "parity": "None",
                "stopbits": "1",
            },
            "send_settings": {
                "ending": r"\r\n"
            },
        }
        config_manager.config["state"] = state
    return state


def get_basic_settings(config_manager):
    state = get_state(config_manager)
    basic = state.setdefault("basic_settings", {})
    basic.setdefault("port", "")
    basic.setdefault("port_node", basic.get("port", ""))
    basic.setdefault("baudrate", "115200")
    basic.setdefault("databits", "8")
    basic.setdefault("parity", "None")
    basic.setdefault("stopbits", "1")
    return state, basic


def save_state(config_manager, state):
    config_manager.set("state", state)


def update_basic_setting(config_manager, key, value):
    state, basic = get_basic_settings(config_manager)
    basic[key] = str(value)
    return state


def set_com_port(config_manager, port_value):
    port = normalize_port(port_value)
    if not port:
        raise ValueError("COM 口不能为空")
    port_node = (port_value or "").strip()
    suffix = f" ({port})"
    if port_node.endswith(suffix):
        port_node = port_node[:-len(suffix)].strip()

    state, basic = get_basic_settings(config_manager)
    basic["port"] = port
    basic["port_node"] = port_node or port
    return output_rules.comport_updated(port)


def get_ending_bytes(ending_text):
    if ending_text == "None":
        return b""
    return ending_text.encode("utf-8").decode("unicode_escape").encode("utf-8")


def get_continuous_interval_ms(config_manager):
    state = get_state(config_manager)
    send_settings = state.get("send_settings", {})
    try:
        return int(send_settings.get("continuous_interval", 100))
    except (TypeError, ValueError):
        return 100


def sleep_ms(milliseconds):
    if milliseconds > 0:
        time.sleep(milliseconds / 1000.0)


def open_serial_from_config(basic):
    port = normalize_port(basic.get("port"))
    if not port:
        raise ValueError("配置文件中未设置 COM 口")

    available_ports = [p.device for p in serial.tools.list_ports.comports()]
    if port not in available_ports:
        raise ValueError(f"串口 {port} 不存在")

    parity_map = {
        "None": serial.PARITY_NONE,
        "Even": serial.PARITY_EVEN,
        "Odd": serial.PARITY_ODD,
        "Mark": serial.PARITY_MARK,
    }
    stopbits_map = {
        "1": serial.STOPBITS_ONE,
        "1.5": serial.STOPBITS_ONE_POINT_FIVE,
        "2": serial.STOPBITS_TWO,
    }

    return serial.Serial(
        port=port,
        baudrate=int(basic.get("baudrate", "115200")),
        bytesize=int(basic.get("databits", "8")),
        parity=parity_map.get(basic.get("parity", "None"), serial.PARITY_NONE),
        stopbits=stopbits_map.get(str(basic.get("stopbits", "1")), serial.STOPBITS_ONE),
        timeout=0.2,
    )


def build_payload(command, config_manager):
    cmd_type, param = UIUtils.parse_special_command(command)

    if cmd_type == "sendhex":
        hex_str = param.strip().replace(" ", "")
        if len(hex_str) % 2 != 0:
            hex_str = "0" + hex_str
        return bytes.fromhex(hex_str), None, False

    if cmd_type == "baudrate":
        baudrate = int(param.strip())
        update_basic_setting(config_manager, "baudrate", baudrate)
        return None, output_rules.baudrate_updated(baudrate), True

    if cmd_type == "setendlog":
        mapping = {
            "none": "None",
            "rn": r"\r\n",
            "r": r"\r",
            "n": r"\n",
        }
        target = mapping.get(param.strip().lower(), param.strip())
        state = get_state(config_manager)
        send_settings = state.setdefault("send_settings", {})
        send_settings["ending"] = target
        return None, output_rules.ending_set(target), True

    if cmd_type in ("comport", "com"):
        return None, set_com_port(config_manager, param), True

    if cmd_type in ("mode", "sendmode"):
        return None, None, False

    if cmd_type:
        raise ValueError(output_rules.unsupported_cli_command(cmd_type))

    state = get_state(config_manager)
    ending = state.get("send_settings", {}).get("ending", r"\r\n")
    return UIUtils.unescape_text(command).encode("utf-8") + get_ending_bytes(ending), None, False


def read_available_transport(transport, output, read_timeout):
    """在读取窗口内循环读取可用数据."""
    deadline = time.monotonic() + max(read_timeout, 0) / 1000.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        waiting = getattr(transport, "in_waiting", 0)
        if waiting:
            data = transport.read(waiting)
            if data:
                try:
                    output.append(data.decode("utf-8"), OutputSource.RECEIVE)
                except UnicodeDecodeError:
                    output.append(f"[非UTF-8数据: {data.hex()}]", OutputSource.ERROR)
                continue
        time.sleep(min(0.005, remaining))


def wait_with_transport_read(transport, output, wait_ms):
    """等待指定毫秒, 如果串口已打开则同时读取返回数据."""
    if transport is None:
        sleep_ms(wait_ms)
    else:
        read_available_transport(transport, output, wait_ms)


def execute_payload(ser, payload, read_timeout, output, send_display=None):
    ser.write(payload)
    ser.flush()
    if output.send_visible() and send_display is not None:
        output.append(send_display, OutputSource.SEND)
    read_available_transport(ser, output, read_timeout)
    return len(payload)


def execute_cli_command(command, config_manager, output, ser=None, read_timeout=0):
    cmd_type, param = UIUtils.parse_special_command(command)
    if cmd_type in ("mode", "sendmode"):
        return execute_module(param, config_manager, output, read_timeout, ser=ser)

    payload, message, state_changed = build_payload(command, config_manager)
    if message:
        output.append(message, OutputSource.SYSTEM)
    if payload is not None:
        if ser is None:
            _, basic = get_basic_settings(config_manager)
            with open_serial_from_config(basic) as local_ser:
                execute_payload(local_ser, payload, read_timeout, output, command)
        else:
            execute_payload(ser, payload, read_timeout, output, command)
    return state_changed


def execute_module(module_name, config_manager, output, read_timeout=0, ser=None, stack=None):
    stack = stack or []
    normalized_module = (module_name or "").strip()
    if normalized_module in stack:
        raise ValueError(f"检测到 SendMode 循环调用: {' -> '.join(stack + [normalized_module])}")

    state = get_state(config_manager)
    commands = collect_module_commands(ConfigCommandProvider(state), module_name)
    if not commands:
        output.append(output_rules.module_no_enabled_commands(module_name), OutputSource.SYSTEM)
        return False

    output.append(output_rules.sendmode_start(module_name), OutputSource.SYSTEM)
    state_changed = False
    interval_ms = get_continuous_interval_ms(config_manager)
    _, basic = get_basic_settings(config_manager)
    owns_serial = ser is None
    try:
        for _row, command, is_special, *special_args in commands:
            command_type = special_args[0] if is_special and special_args else None
            param = special_args[1] if is_special and len(special_args) > 1 else ""
            if command_type == SpecialCommandType.DELAY:
                try:
                    delay_ms = float(param.strip())
                except ValueError:
                    raise ValueError(f"无效的延迟参数: {param}")
                output.append(output_rules.sendmode_delay(delay_ms), OutputSource.SYSTEM)
                wait_with_transport_read(ser, output, delay_ms)
                continue
            if command_type in (SpecialCommandType.MODE, SpecialCommandType.MODEEND, SpecialCommandType.STOPCONTINUOUS):
                continue
            if command_type == SpecialCommandType.SENDMODE:
                state_changed = execute_module(
                    param,
                    config_manager,
                    output,
                    read_timeout=read_timeout,
                    ser=ser,
                    stack=stack + [normalized_module],
                ) or state_changed
                wait_with_transport_read(ser, output, interval_ms)
                continue
            payload, message, changed = build_payload(command, config_manager)
            state_changed = state_changed or changed
            if message:
                output.append(message, OutputSource.SYSTEM)
                if command_type == SpecialCommandType.BAUDRATE:
                    wait_with_transport_read(ser, output, 500)
                else:
                    wait_with_transport_read(ser, output, interval_ms)
            if payload is None:
                continue
            if ser is None:
                ser = open_serial_from_config(basic)
            send_display = f"[HEX]: {payload.hex(' ').upper()}" if command_type == SpecialCommandType.SENDHEX else command
            execute_payload(ser, payload, 0, output, send_display)
            wait_with_transport_read(ser, output, interval_ms)
    finally:
        if owns_serial and ser is not None:
            read_available_transport(ser, output, read_timeout)
            ser.close()
    return state_changed


def run_cli(args, exe_name, tool_version, tool_version_date):
    config_manager = ConfigManager(
        tool_version=tool_version,
        tool_version_date=tool_version_date,
        config_file=args.config or get_config_file(exe_name),
    )
    output = CliOutput(get_state(config_manager))

    state_changed = False

    if args.port:
        output.append(set_com_port(config_manager, args.port), OutputSource.SYSTEM)
        state_changed = True
    if args.baudrate:
        update_basic_setting(config_manager, "baudrate", args.baudrate)
        output.append(output_rules.baudrate_updated(args.baudrate), OutputSource.SYSTEM)
        state_changed = True

    if args.command is None:
        if state_changed:
            save_state(config_manager, get_state(config_manager))
        return 0

    try:
        command_changed_state = execute_cli_command(
            args.command,
            config_manager,
            output,
            ser=None,
            read_timeout=args.read_timeout,
        )
    except Exception as exc:
        if state_changed:
            save_state(config_manager, get_state(config_manager))
        output.append(f"错误: {exc}", OutputSource.ERROR)
        return 1
    state_changed = state_changed or command_changed_state
    if state_changed:
        save_state(config_manager, get_state(config_manager))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="GHowe 串口调试助手 CLI")
    parser.add_argument("--cli", action="store_true", help="启用 CLI 模式")
    parser.add_argument("--config", help="指定配置文件")
    parser.add_argument("--port", help="更新配置中的 COM 口，例如 COM5")
    parser.add_argument("--baudrate", type=int, help="更新配置中的波特率")
    parser.add_argument("--send", dest="command", help="发送字符串或特殊命令")
    parser.add_argument("--read-timeout", type=int, default=300, help="发送后读取等待时间，单位 ms")
    return parser


def should_run_cli(argv):
    return "--cli" in argv or "--send" in argv or "--port" in argv or "--baudrate" in argv
