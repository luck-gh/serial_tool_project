#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""固件下载特殊指令的共享辅助逻辑."""

from __future__ import annotations

import importlib
import os
import sys
import serial
from pathlib import Path



FIRMWARE_TOOL_NAME = "firmware_downloader"


def normalize_firmware_path(value: str) -> str:
    """规范化特殊指令或配置中的固件文件路径."""
    path = str(value or "").strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '"'):
        path = path[1:-1].strip()
    if not path:
        return ""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def resolve_firmware_path(parameter: str, params: dict) -> tuple[str, str | None]:
    """按'指令参数 -> 工具配置'的优先级解析固件路径."""
    provided = bool(str(parameter or "").strip())
    candidate = parameter if provided else (params or {}).get("initial_file", "")
    path = normalize_firmware_path(candidate)
    if not path:
        return "", "未指定固件文件，且固件下载工具未配置默认文件"
    if not os.path.isfile(path):
        source = "指令参数" if provided else "固件下载工具默认文件"
        return "", f"{source}不存在或不是普通文件: {path}"
    return path, None


def build_download_config(params: dict) -> dict:
    """将串口助手保存的平铺配置转换为下载器所需的嵌套配置."""
    params = params or {}

    def ack_config(prefix: str, include_crc: bool) -> dict:
        """将指定 start_ack/packet_ack/last_packet_ack 前缀的平铺字段组装成 ACK 字典."""
        config = {
            "check_length": bool(params.get(f"{prefix}_check_length", False)),
            "expected_length": int(params.get(f"{prefix}_expected_length", 1)),
            "check_data": bool(params.get(f"{prefix}_check_data", False)),
            "expected_data": str(params.get(f"{prefix}_expected_data", "")),
            "data_format": str(params.get(f"{prefix}_data_format", "ASCII")),
            "check_mode": str(params.get(f"{prefix}_check_mode", "AND")),
        }
        if include_crc:
            config.update(
                {
                    "check_crc": bool(params.get(f"{prefix}_check_crc", False)),
                    "crc_type": str(params.get(f"{prefix}_crc_type", "CRC16-MODBUS")),
                }
            )
        return config

    return {
        "start_command": str(params.get("start_command", "download 0\\n")),
        "packet_size": int(params.get("packet_size", 256)),
        "wait_start_ack": bool(params.get("wait_start_ack", False)),
        "start_ack_timeout": int(params.get("start_ack_timeout", 1000)),
        "start_ack_config": ack_config("start_ack", False),
        "wait_packet_ack": bool(params.get("wait_packet_ack", False)),
        "packet_ack_timeout": int(params.get("packet_ack_timeout", 1000)),
        "packet_ack_config": ack_config("packet_ack", True),
        "wait_last_packet_ack": bool(params.get("wait_last_packet_ack", False)),
        "last_packet_ack_timeout": int(params.get("last_packet_ack_timeout", 5000)),
        "last_packet_ack_config": ack_config("last_packet_ack", True),
        "add_packet_crc": bool(params.get("add_packet_crc", False)),
        "packet_crc_type": str(params.get("packet_crc_type", "CRC16-MODBUS")),
        "send_end_string": bool(params.get("send_end_string", False)),
        "end_string": str(params.get("end_string", "?\\r\\n")),
    }


def firmware_project_importable() -> bool:
    """判断内置或开发环境中的固件下载核心是否可导入."""
    try:
        importlib.import_module("firmware_downloader_project.core.downloader")
        return True
    except ImportError:
        workspace_root = Path(__file__).resolve().parents[2]
        root_text = str(workspace_root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        try:
            importlib.import_module("firmware_downloader_project.core.downloader")
            return True
        except ImportError:
            return False


def get_firmware_downloader_class():
    """动态获取现有固件下载核心,避免复制下载和 ACK 逻辑."""
    if not firmware_project_importable():
        raise ImportError("固件下载模块未找到或未打包")
    module = importlib.import_module("firmware_downloader_project.core.downloader")
    return module.FirmwareDownloader


def build_external_firmware_arguments(firmware_path, port_config, params):
    """构造外部下载工具的自动下载参数,字段与内置下载器保持一致."""
    parity_map = {
        serial.PARITY_NONE: "N", serial.PARITY_EVEN: "E", serial.PARITY_ODD: "O",
        serial.PARITY_MARK: "M", serial.PARITY_SPACE: "S",
    }
    stopbits_map = {
        serial.STOPBITS_ONE: "1", serial.STOPBITS_ONE_POINT_FIVE: "1.5",
        serial.STOPBITS_TWO: "2",
    }
    args = [
        "--auto-start", "--exit-on-complete", "--file", firmware_path,
        "--port", str(port_config["port"]),
        "--baudrate", str(port_config["baudrate"]),
        "--bytesize", str(port_config["bytesize"]),
        "--parity", parity_map.get(port_config["parity"], "N"),
        "--stopbits", stopbits_map.get(port_config["stopbits"], "1"),
        "--packet-size", str(params["packet_size"]),
        "--start-command", str(params["start_command"]),
    ]
    if params.get("add_packet_crc"):
        args.extend(["--add-packet-crc", "--packet-crc-type", str(params["packet_crc_type"])])
    for prefix, cli_prefix, supports_crc in (
        ("start_ack", "start-ack", False),
        ("packet_ack", "packet-ack", True),
        ("last_packet_ack", "last-packet-ack", True),
    ):
        if not params.get(f"wait_{prefix}"):
            continue
        args.extend([
            f"--wait-{cli_prefix}",
            f"--{cli_prefix}-timeout", str(params[f"{prefix}_timeout"]),
            f"--{cli_prefix}-check-mode", str(params[f"{prefix}_check_mode"]),
        ])
        if params.get(f"{prefix}_check_length"):
            args.extend([
                f"--{cli_prefix}-check-length",
                f"--{cli_prefix}-expected-length", str(params[f"{prefix}_expected_length"]),
            ])
        if params.get(f"{prefix}_check_data"):
            args.extend([
                f"--{cli_prefix}-check-data",
                f"--{cli_prefix}-expected-data", str(params[f"{prefix}_expected_data"]),
                f"--{cli_prefix}-data-format", str(params[f"{prefix}_data_format"]),
            ])
        if supports_crc and params.get(f"{prefix}_check_crc"):
            args.extend([
                f"--{cli_prefix}-check-crc",
                f"--{cli_prefix}-crc-type", str(params[f"{prefix}_crc_type"]),
            ])
    if params.get("send_end_string"):
        args.extend(["--send-end-string", "--end-string", str(params["end_string"])])
    return args
