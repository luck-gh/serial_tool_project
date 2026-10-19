#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CLI 运行模块, 负责命令行参数解析, 配置读取, 串口发送和命令模块执行.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import argparse
import json
import sys
import time

import serial
import serial.tools.list_ports

from core.command_parser import (
    RawData, command_parts, unescape_text, require_device_command, parse_hex_bytes, normalize_ending_text, get_ending_bytes,
)
from core.serial_config import serial_kwargs

from core.command_executor import ConfigCommandProvider, collect_module_commands
from core import output_rules
from core.output_rules import ANSI_RESET, rules_from_state
from app_identity import get_config_file
from managers.config_manager import ConfigManager
from core.command_types import SpecialCommandType, OutputSource
from core.console_execution import OperationUnknown


class CliOutput:
    """CLI 输出管理器, 输出动作和规则分离."""

    def __init__(self, state, config_file=None):
        """从配置快照创建独立 CLI 的输出规则.

        Args:
            state (dict): 包含输出来源,颜色及时间戳等设置的应用状态.
        """
        self.rules = rules_from_state(state)
        from core.cli_log import CliLog
        self.log = CliLog(state, config_file=config_file)

    def append(self, text, source_type, end=None):
        """先按独立规则收录日志,再按显示规则输出."""
        if end is None:
            end = "" if source_type == OutputSource.RECEIVE else "\n"
        self.log.capture(text, source_type.value, "normal", output_rules.event_timestamp(), end)
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
        """返回独立 CLI 当前是否启用发送来源的显示."""
        return self.rules.source_enabled(OutputSource.SEND)

    def close(self):
        self.log.close()


def normalize_port(port_text):
    """从含描述的端口文本中提取首个空格前的设备端口名."""
    return (port_text or "").strip().split(" ")[0]


def get_state(config_manager):
    """读取内存配置中的状态;缺失时在内存补默认值,不在此处保存文件."""
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
    """返回 (state, basic_settings),并在内存补全缺失的基础串口字段."""
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
    """调用配置管理器持久化 state;这是保存设置,不是 GUI 通信机制."""
    config_manager.set("state", state)


def update_basic_setting(config_manager, key, value):
    """将单个串口字段以字符串写入内存状态,返回状态对象,不打开设备."""
    state, basic = get_basic_settings(config_manager)
    basic[key] = str(value)
    return state


def set_com_port(config_manager, port_value):
    """校验并更新内存中的端口名和显示描述;返回提示文本,不切换已打开句柄."""
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


def get_continuous_interval_ms(config_manager):
    """读取配置的命令间隔(毫秒);无法转换为整数时使用 100 ms."""
    state = get_state(config_manager)
    send_settings = state.get("send_settings", {})
    try:
        return int(send_settings.get("continuous_interval", 100))
    except (TypeError, ValueError):
        return 100


def sleep_ms(milliseconds):
    """在当前 CLI 线程阻塞指定毫秒数;非正值直接返回."""
    if milliseconds > 0:
        time.sleep(milliseconds / 1000.0)


def open_serial_from_config(basic):
    """确认设备端口存在并按配置打开本地串口,返回 pySerial 句柄.

    Args:
        basic (dict): port 为端口文本,baudrate/databits/parity/stopbits 为
            基础串口设置;缺省为 115200,8,None,1,读取超时固定为 0.2 s.

    Returns:
        serial.Serial: 已打开的本地句柄,由调用方关闭.

    Raises:
        ValueError: 端口未设置,不存在或数值字段无法转换.
        serial.SerialException: 串口打开失败.
    """
    port = normalize_port(basic.get("port"))
    if not port:
        raise ValueError("配置文件中未设置 COM 口")

    available_ports = [p.device for p in serial.tools.list_ports.comports()]
    if port not in available_ports:
        raise ValueError(f"串口 {port} 不存在")

    return serial.Serial(**serial_kwargs(basic), timeout=0.2)


def build_payload(command, config_manager):
    """解释独立 CLI 命令,生成设备字节或更新内存设置.

    Args:
        command (str): 原始文本或特殊命令.
        config_manager (ConfigManager): 当前独立 CLI 的配置,不是 GUI 内存对象.

    Returns:
        tuple[bytes | None, str | None, bool]: 待发送字节,可选提示及设置是否改变.
            BaudRate/ComPort 只修改配置;不在这里更新已打开的串口句柄.

    Raises:
        ValueError: 不支持的特殊命令或无法解析的参数.
    """
    require_device_command(command)
    cmd_type, param = command_parts(command)

    if cmd_type == "sendhex":
        return parse_hex_bytes(param), None, False

    if cmd_type == "baudrate":
        baudrate = int(param.strip())
        update_basic_setting(config_manager, "baudrate", baudrate)
        return None, output_rules.baudrate_updated(baudrate), True

    if cmd_type == "setendlog":
        target = normalize_ending_text(param.strip())
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
    return unescape_text(command).encode("utf-8") + get_ending_bytes(ending), None, False


def read_available_transport(transport, output, read_timeout, stop_on_match=True):
    """在读取窗口内循环读取可用数据."""
    if stop_on_match and getattr(output, "matcher", None) is not None and output.matcher.matched:
        return
    deadline = time.monotonic() + max(read_timeout, 0) / 1000.0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        waiting = getattr(transport, "in_waiting", 0)
        if waiting:
            data = transport.read(waiting)
            if data:
                matcher = getattr(output, "matcher", None)
                if matcher is not None:
                    matcher.feed(data)
                recorder = getattr(output, "received_file", None)
                if recorder is not None:
                    recorder.feed(data)
                if matcher is not None:
                    output.append(output.decoder.feed(data), OutputSource.RECEIVE)
                    if stop_on_match and matcher.matched:
                        break
                    continue
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
        read_available_transport(transport, output, wait_ms, stop_on_match=False)


def execute_payload(ser, payload, read_timeout, output, send_display=None):
    """通过独立 CLI 句柄写入,flush 并观察返回数据.

    Args:
        ser (serial.Serial): 已打开的串口句柄.
        payload (bytes): 待写出的字节.
        read_timeout (int): 发送后接收观察时间,单位 ms.
        output (CliOutput): 输出规则和终端输出对象.
        send_display (str | None): 发送日志文本,默认 None 表示不显示.

    Returns:
        int: 实际完整写出的长度,不代表DUT已执行命令.

    Raises:
        OperationUnknown: 短写、写入或flush异常,设备可能已收到部分字节,不重发.
    """
    from core.console_serial import write_serial_once
    count = write_serial_once(ser, payload)
    try:
        ser.flush()
    except OSError as exc:
        raise OperationUnknown(f"发送排空失败，设备结果未知: {exc}；未重发") from exc
    if send_display is not None:
        output.append(send_display, OutputSource.SEND)
    read_available_transport(ser, output, read_timeout)
    return count


def execute_cli_command(command, config_manager, output, ser=None, read_timeout=0):
    """执行独立 CLI 的单条命令,返回配置是否改变,不表示设备确认成功.

    Args:
        command (str): 单条文本或特殊命令.
        config_manager (ConfigManager): 独立 CLI 配置管理器.
        output (CliOutput): 终端输出对象.
        ser (serial.Serial | None): 可复用句柄;默认 None 时按需打开并自行关闭.
        read_timeout (int): 接收观察时间,单位 ms,默认 0.

    Raises:
        ValueError: 独立 CLI 不支持 FirmwareDownload,或参数无效.
    """
    cmd_type, param = command_parts(command)
    if cmd_type == SpecialCommandType.FIRMWAREDOWNLOAD.value:
        raise ValueError("FirmwareDownload 暂不支持 CLI 模式")
    if cmd_type in ("mode", "sendmode"):
        return execute_module(param, config_manager, output, read_timeout, ser=ser)

    payload, message, state_changed = build_payload(command, config_manager)
    if message:
        output.append(message, OutputSource.SYSTEM)
    if payload is not None:
        if ser is None:
            _, basic = get_basic_settings(config_manager)
            operation_error = None
            try:
                with open_serial_from_config(basic) as local_ser:
                    try:
                        execute_payload(local_ser, payload, read_timeout, output, command)
                    except OperationUnknown as exc:
                        operation_error = exc
                        raise
            except OSError as exc:
                if operation_error is not None:
                    raise OperationUnknown(f"{operation_error}；关闭串口失败: {exc}") from operation_error
                raise
        else:
            execute_payload(ser, payload, read_timeout, output, command)
    return state_changed


def execute_module(module_name, config_manager, output, read_timeout=0, ser=None, stack=None):
    """按配置顺序解释模块,检测 SendMode 递归并管理自建串口句柄.

    Args:
        module_name (str): 要执行的模块名称.
        config_manager (ConfigManager): 模块和串口设置的来源.
        output (CliOutput): 终端输出对象.
        read_timeout (int): 本层自建串口关闭前的接收等待,单位 ms,默认 0.
        ser (serial.Serial | None): 复用句柄,默认 None 时延迟到首次发送才打开.
        stack (list[str] | None): 递归调用路径,默认 None;仅用于检测模块循环.

    Returns:
        bool: 模块执行是否改变配置,不表示命令的设备执行结果.

    Raises:
        ValueError: 模块递归,延时参数无效或遇到独立 CLI 不支持的固件下载.
    """
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
            if command_type == SpecialCommandType.FIRMWAREDOWNLOAD:
                raise ValueError("FirmwareDownload 暂不支持 CLI 模式")
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
            if not is_special:
                parsed = special_args[0]
                if isinstance(parsed, RawData):
                    if parsed.prefix:
                        raise ValueError(output_rules.unsupported_cli_command(parsed.prefix))
                    ending = get_state(config_manager).get("send_settings", {}).get("ending", r"\r\n")
                    payload, message, changed = parsed.text.encode("utf-8") + get_ending_bytes(ending), None, False
                else:
                    payload, message, changed = build_payload(command, config_manager)
            else:
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
            operation_error = sys.exc_info()[1]
            cleanup_errors = []
            try:
                read_available_transport(ser, output, read_timeout)
            except OSError as exc:
                cleanup_errors.append(f"尾部接收失败: {exc}")
            try:
                ser.close()
            except OSError as exc:
                cleanup_errors.append(f"关闭串口失败: {exc}")
            if cleanup_errors:
                message = "；".join(cleanup_errors)
                if isinstance(operation_error, OperationUnknown):
                    raise OperationUnknown(f"{operation_error}；{message}") from operation_error
                raise OSError(message)
    return state_changed


def run_cli(args, exe_name, tool_version, tool_version_date):
    """按参数选择窗口桥接或独立 CLI 执行路径.

    Args:
        args (argparse.Namespace): build_parser 解析出的参数;via_gui 或 gui_action
            启用桥接,config 为可选配置路径,command 为要执行的命令.
        exe_name (str): 用于推导默认配置文件名的入口名称.
        tool_version (str): 独立 CLI 配置管理器所使用的应用版本.
        tool_version_date (str): 独立 CLI 配置管理器所使用的版本日期.

    Returns:
        int: 0=完成, 1=操作失败, 2=参数无效, 3=操作结果未知, 4=期望响应超时或未匹配.
            桥接在创建 ConfigManager 前返回,以配置路径定位 GUI,不读取配置
            内容或另开串口;GUI 内部设置变化仍可触发自身的自动保存.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if getattr(args, "log_action", None):
        from core.cli_log_commands import run_log_action
        return run_log_action(args, args.config or get_config_file(exe_name), tool_version, tool_version_date)
    if getattr(args, "log_patch", None) is not None or getattr(args, "log_recover", None) is not None:
        print(json.dumps(dict(event="result", success=False, status="failed", message="日志参数需要--log-action"), ensure_ascii=False))
        return 2
    try:
        return _run_cli(args, exe_name, tool_version, tool_version_date)
    except OSError as exc:
        print(json.dumps(dict(event="result", success=False, status="failed", message=str(exc)), ensure_ascii=False))
        return 1


def _run_cli(args, exe_name, tool_version, tool_version_date):
    """运行一次性路径;归档尾部完成后才允许输出成功结果."""
    if getattr(args, 'config_switch', None) is not None or args.gui_action == 'config-switch':
        if (not getattr(args, 'config_switch', None) or args.gui_action != 'config-switch'
                or getattr(args, 'console', False) or getattr(args, 'machine', False)):
            print(json.dumps(dict(event='result', success=False, status='failed',
                message='--config-switch 须与 --gui-action config-switch 单独使用,不支持 console/machine'), ensure_ascii=False))
            return 2
    if getattr(args, "machine", False):
        from core.cli_machine import run_machine
        return run_machine(args, args.config or get_config_file(exe_name))
    template_import = getattr(args, "import_template", None)
    template_export = getattr(args, "export_template", None)
    save_received = getattr(args, "save_received", None)
    template_operation = template_import is not None or template_export is not None
    if template_operation and (args.command is not None or getattr(args, "console", False) or args.gui_action
            or args.port or args.baudrate or getattr(args, "expect", None) is not None or save_received
            or any(getattr(args, key, None) is not None for key in ("remote_host", "remote_port", "remote_password", "config_patch", "config_switch"))):
        print("模板导入/导出须单独执行，可与 --config/--via-gui 组合", file=sys.stderr)
        return 2
    if save_received is not None and (args.gui_action or (args.command is None and not getattr(args, "console", False))):
        print("--save-received 仅与 --send 或 --console 组合", file=sys.stderr)
        return 2
    if template_operation:
        config_file = args.config or get_config_file(exe_name)
        if args.via_gui:
            from core.cli_bridge_client import run_gui_cli
            return run_gui_cli(args, config_file)
        from core.command_templates import run_local_template
        manager = ConfigManager(tool_version, tool_version_date, config_file)
        event = run_local_template(manager, "template-import" if template_import is not None else "template-export",
                                   template_import or template_export)
        success = event["outcome"] == "success"
        print(json.dumps(dict(event, event="result", success=success, status="success" if success else "failed"), ensure_ascii=False))
        return 0 if success else 1
    expect = getattr(args, "expect", None)
    if expect is not None:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        from core.receive_text import ExpectMatcher
        try:
            matcher = ExpectMatcher(expect)
        except Exception as exc:
            print(f"--expect 正则无效: {exc}", file=sys.stderr)
            return 2
        if args.command is None or getattr(args, "console", False):
            print("--expect 必须与 --send 使用", file=sys.stderr)
            return 2
    if getattr(args, "console", False):
        from core.cli_console import run_console
        return run_console(args, args.config or get_config_file(exe_name), tool_version, tool_version_date)
    from core.receive_text import ReceivedFile
    def file_event(event):
        if event["outcome"] != "failed":
            print("\n" + json.dumps(event, ensure_ascii=False), flush=True)
        if args.via_gui:
            from core.cli_bridge_client import report_receive_save
            report_receive_save(args.config or get_config_file(exe_name), event)

    try:
        received_file = ReceivedFile(save_received, emit=file_event)
    except (OSError, ValueError) as exc:
        event = getattr(exc, "file_event", None)
        if event is not None:
            result = dict(event, event="result", success=False, status="failed")
            if expect is not None:
                result.update(matcher.result_fields())
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"接收数据保存文件无法打开: {exc}；未执行设备操作", file=sys.stderr)
        return 2
    output = None
    output_closed = False
    try:
        if getattr(args, "via_gui", False) or getattr(args, "gui_action", None):
            from core.cli_bridge_client import run_gui_cli
            return run_gui_cli(args, args.config or get_config_file(exe_name), received_file=received_file)
        if any(getattr(args, name, None) is not None for name in ("remote_host", "remote_port", "remote_password", "config_patch", "config_switch")):
            print("远程或配置修改参数需要对应的 --gui-action；独立 CLI 不建立远程连接或在线修改窗口配置", file=sys.stderr)
            return 2
        config_manager = ConfigManager(
            tool_version=tool_version,
            tool_version_date=tool_version_date,
            config_file=args.config or get_config_file(exe_name),
        )
        output = CliOutput(get_state(config_manager), config_manager.config_file)
        output.received_file = received_file
        if expect is not None:
            from core.receive_text import ReceiveDecoder
            output.matcher = matcher
            output.decoder = ReceiveDecoder()

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
            output_closed = True
            output.close()
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
            received_file.close()
            if state_changed:
                save_state(config_manager, get_state(config_manager))
            from core.receive_text import ReceiveSaveError
            log_error = ""
            try:
                output.append(f"错误: {exc}", OutputSource.ERROR)
            except OSError as log_exc:
                log_error = str(log_exc)
            output_closed = True
            try:
                output.close()
            except OSError as log_exc:
                log_error = str(log_exc)
            unknown = isinstance(exc, OperationUnknown)
            message = str(exc) + (f"；{log_error}" if log_error else "")
            if isinstance(exc, ReceiveSaveError):
                result = dict(received_file.last_event or {}, event="result", success=False, status="failed", message=message)
                if expect is not None:
                    matcher.finish()
                    result.update(matcher.result_fields())
                print("\n" + json.dumps(result, ensure_ascii=False))
            else:
                if expect is not None or unknown or log_error:
                    fields = {}
                    if expect is not None:
                        matcher.finish()
                        fields = matcher.result_fields()
                    print("\n" + json.dumps(dict(event="result", success=False, status="unknown" if unknown else "failed", message=message,
                                          **fields), ensure_ascii=False))
            return 3 if unknown else 1
        received_file.close()
        output_closed = True
        output.close()
        state_changed = state_changed or command_changed_state
        if state_changed:
            save_state(config_manager, get_state(config_manager))
        if expect is not None:
            matched = matcher.finish()
            # 设备文本可能没有换行,最终 JSON 必须从独立一行开始.
            print("\n" + json.dumps(dict(event="result", success=matched, status="success" if matched else "failed",
                                  message=matcher.result_message(), expect_timeout=not matched,
                                  **matcher.result_fields()), ensure_ascii=False))
            return 0 if matched else 4
        return 0
    finally:
        received_file.close()
        if output is not None and not output_closed:
            output.close()


def read_timeout_ms(value):
    """解析统一的 0-60000 ms 接收观察窗口."""
    value = int(value)
    if not 0 <= value <= 60000:
        raise argparse.ArgumentTypeError("--read-timeout 必须为 0–60000 ms")
    return value


def build_parser():
    """构建独立 CLI 和窗口桥接共用的命令行参数解析器."""
    parser = argparse.ArgumentParser(description="GHowe 串口调试助手 CLI")
    parser.add_argument("--cli", action="store_true", help="启用 CLI 模式")
    parser.add_argument("--via-gui", action="store_true", help="通过已运行的 GUI 执行，窗口同步显示操作")
    parser.add_argument("--gui-action", choices=("status", "open", "close", "cancel", "remote-connect", "remote-listen", "remote-stop",
                        "config-get", "config-schema", "config-check", "config-apply", "config-switch"), help="查询或控制已运行的 GUI；remote-* 启停远程连接；config-* 查询、校验或应用配置")
    parser.add_argument("--config-switch", help="config-switch 的目标工作 JSON;不改变 --config 桥接定位")
    parser.add_argument("--config-patch", help="config-check/config-apply 的 JSON Merge Patch 文件；- 表示从标准输入读取")
    parser.add_argument("--log-action", choices=("get", "check", "apply", "status", "recover"), help="日志配置或当前进程归档管理;--via-gui操作窗口")
    parser.add_argument("--log-patch", help="log check/apply的日志对象Merge Patch JSON文件,-读取stdin")
    parser.add_argument("--log-recover", help="log recover显式另存到全新文件的路径")
    parser.add_argument("--remote-host", help="remote-connect 的目标设备端地址；省略沿用窗口配置")
    parser.add_argument("--remote-port", type=int, help="远程 TCP 端口，1–65535；省略沿用窗口配置")
    parser.add_argument("--remote-password", help="远程共享口令；省略沿用窗口配置")
    parser.add_argument("--gui-timeout", type=float, default=600, help="等待 GUI 最终结果的秒数，超时返回结果未知")
    parser.add_argument("--config", help="指定配置文件")
    parser.add_argument("--port", help="更新配置中的 COM 口，例如 COM5")
    parser.add_argument("--baudrate", type=int, help="更新配置中的波特率")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--send", dest="command", help="发送字符串或特殊命令")
    operation.add_argument("--console", "-i", action="store_true", help="进入常驻控制台，自动连接同配置 GUI 或独立串口")
    operation.add_argument("--import-template", help="用 UTF-8 CSV/TXT 模板替换命令表，不打开串口")
    operation.add_argument("--export-template", help="将命令表原子导出为 UTF-8 CSV/TXT，不打开串口")
    parser.add_argument("--machine", action="store_true", help="GUI 常驻机器 JSONL,须与 --console --via-gui --config 组合")
    parser.add_argument("--save-received", help="追加本次运行实际读取的原始 RX 字节，不含 TX/历史；与 --send/--console 组合")
    parser.add_argument("--expect", help="--send 接收文本正则，匹配退出 0，观察超时未匹配退出 4")
    parser.add_argument("--read-timeout", type=read_timeout_ms, default=300, help="发送后读取等待时间，0–60000 ms，默认300")
    return parser


def should_run_cli(argv):
    """判断 argv 中是否包含 CLI 操作标志,同时识别 --参数=值 的形式."""
    flags = ("--cli", "--machine", "--send", "--console", "-i", "--expect", "--port", "--baudrate", "--via-gui", "--gui-action",
             "--remote-host", "--remote-port", "--remote-password", "--config-patch", "--config-switch", "--log-action", "--log-patch", "--log-recover",
             "--import-template", "--export-template", "--save-received")
    return any(arg.split("=", 1)[0] in flags for arg in argv)
