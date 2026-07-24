#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""固件下载特殊指令的共享辅助逻辑。"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal


FIRMWARE_TOOL_NAME = "firmware_downloader"


def normalize_firmware_path(value: str) -> str:
    """规范化特殊指令或配置中的固件文件路径。"""
    path = str(value or "").strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in ("'", '"'):
        path = path[1:-1].strip()
    if not path:
        return ""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def resolve_firmware_path(parameter: str, params: dict) -> tuple[str, str | None]:
    """按“指令参数 -> 工具配置”的优先级解析固件路径。"""
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
    """将串口助手保存的平铺配置转换为下载器所需的嵌套配置。"""
    params = params or {}

    def ack_config(prefix: str, include_crc: bool) -> dict:
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
    """判断内置或开发环境中的固件下载核心是否可导入。"""
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
    """动态获取现有固件下载核心，避免复制下载和 ACK 逻辑。"""
    if not firmware_project_importable():
        raise ImportError("固件下载模块未找到或未打包")
    module = importlib.import_module("firmware_downloader_project.core.downloader")
    return module.FirmwareDownloader


class FirmwareDownloadWorker(QThread):
    """在后台线程中执行现有 FirmwareDownloader。"""

    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, firmware_path: str, port_config: dict, download_config: dict, parent=None):
        super().__init__(parent)
        self.firmware_path = firmware_path
        self.port_config = dict(port_config)
        self.download_config = dict(download_config)
        self.downloader = None

    def cancel(self):
        if self.downloader is not None:
            self.downloader.stop()

    def run(self):
        try:
            downloader_class = get_firmware_downloader_class()
            self.downloader = downloader_class(self.port_config, self.download_config)
            success, message = self.downloader.open_port()
            if not success:
                self.finished_signal.emit(False, message)
                return
            self.log_signal.emit("INFO", message)
            success, message = self.downloader.download(
                self.firmware_path,
                progress_callback=lambda current, total, text: self.progress_signal.emit(current, total, text),
                log_callback=lambda level, text: self.log_signal.emit(level, text),
            )
            self.finished_signal.emit(success, message)
        except Exception as exc:
            self.finished_signal.emit(False, f"下载器启动失败: {exc}")
        finally:
            if self.downloader is not None:
                self.downloader.close_port()
