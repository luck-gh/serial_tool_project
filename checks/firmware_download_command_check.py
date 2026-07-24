#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""FirmwareDownload 特殊指令的轻量级回归检查。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.command_executor import command_type_from_string
from core.firmware_download import build_download_config, resolve_firmware_path
from managers.config_manager import ConfigManager
from managers.output_manager import OutputManager, OutputRecord
from utils.ui_utils import SpecialCommandType, UIUtils
from utils.ui_utils import OutputSource


class FirmwareDownloadCommandTests(unittest.TestCase):
    def test_parser_keeps_windows_drive_colon_in_parameter(self):
        command_type, parameter = UIUtils.parse_special_command(
            r'FirmwareDownload:"D:\firmware files\app.bin"'
        )
        self.assertEqual(command_type, "firmwaredownload")
        self.assertEqual(parameter, r'"D:\firmware files\app.bin"')
        self.assertEqual(
            command_type_from_string(command_type),
            SpecialCommandType.FIRMWAREDOWNLOAD,
        )

    def test_parameter_path_overrides_default_file(self):
        with tempfile.TemporaryDirectory() as directory:
            default_path = os.path.join(directory, "default.bin")
            parameter_path = os.path.join(directory, "parameter.bin")
            Path(default_path).touch()
            Path(parameter_path).touch()
            result, error = resolve_firmware_path(
                f'"{parameter_path}"', {"initial_file": default_path}
            )
            self.assertIsNone(error)
            self.assertEqual(result, os.path.abspath(parameter_path))

    def test_missing_path_reports_configuration_error(self):
        result, error = resolve_firmware_path("", {"initial_file": ""})
        self.assertEqual(result, "")
        self.assertIn("未指定固件文件", error)

    def test_download_config_keeps_ack_configuration(self):
        config = build_download_config(
            {
                "packet_size": 512,
                "wait_start_ack": True,
                "start_ack_check_data": True,
                "start_ack_expected_data": "READY",
                "wait_packet_ack": True,
                "packet_ack_check_crc": True,
                "packet_ack_crc_type": "CRC32",
                "wait_last_packet_ack": True,
                "last_packet_ack_timeout": 9000,
            }
        )
        self.assertEqual(config["packet_size"], 512)
        self.assertTrue(config["start_ack_config"]["check_data"])
        self.assertEqual(config["start_ack_config"]["expected_data"], "READY")
        self.assertTrue(config["packet_ack_config"]["check_crc"])
        self.assertEqual(config["packet_ack_config"]["crc_type"], "CRC32")
        self.assertEqual(config["last_packet_ack_timeout"], 9000)

    def test_development_tree_exposes_builtin_downloader(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = os.path.join(directory, "config.json")
            config_manager = ConfigManager(config_file=config_file)
            self.assertTrue(config_manager.is_tool_available("firmware_downloader"))

    def test_system_log_level_filter_keeps_non_system_records_visible(self):
        manager = object.__new__(OutputManager)
        manager.rules = type(
            "Rules", (), {"source_enabled": staticmethod(lambda _source: True)}
        )()
        manager.system_level_filter_getter = lambda level: level != "debug"

        self.assertTrue(
            manager._record_enabled(
                OutputRecord("download warning", OutputSource.SYSTEM, "gray", "warning")
            )
        )
        self.assertFalse(
            manager._record_enabled(
                OutputRecord("download debug", OutputSource.SYSTEM, "gray", "debug")
            )
        )
        self.assertTrue(
            manager._record_enabled(
                OutputRecord("download error", OutputSource.ERROR, "red", "debug")
            )
        )

    def test_export_can_filter_system_log_levels(self):
        manager = object.__new__(OutputManager)
        manager.records = [
            OutputRecord("system normal\n", OutputSource.SYSTEM, "gray", "normal"),
            OutputRecord("system debug\n", OutputSource.SYSTEM, "gray", "debug"),
            OutputRecord("serial data\n", OutputSource.RECEIVE, "black"),
        ]
        exported = manager.text_for_sources(
            {OutputSource.SYSTEM, OutputSource.RECEIVE},
            {"normal"},
        )
        self.assertIn("system normal", exported)
        self.assertNotIn("system debug", exported)
        self.assertIn("serial data", exported)


if __name__ == "__main__":
    unittest.main()
