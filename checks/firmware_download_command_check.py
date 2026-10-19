#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""FirmwareDownload 特殊指令的轻量级回归检查."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.command_parser import command_parts
from core.command_executor import command_type_from_string
from core.firmware_download import build_download_config, resolve_firmware_path
from managers.config_manager import ConfigManager
from managers.output_manager import OutputManager, OutputRecord
from core.command_types import SpecialCommandType
from utils.ui_utils import UIUtils
from core.command_types import OutputSource
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from widgets.custom_widgets import CustomTextBrowser

APP = QApplication.instance() or QApplication([])


class FirmwareDownloadCommandTests(unittest.TestCase):
    def setUp(self):
        self.browser = CustomTextBrowser(None)
        self.manager = OutputManager(self.browser, lambda: False, lambda: "red")

    def tearDown(self):
        self.manager.clear()
        self.browser.deleteLater()
        APP.processEvents()

    def test_parser_keeps_windows_drive_colon_in_parameter(self):
        command_type, parameter = command_parts(
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
        manager = self.manager
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
        manager = self.manager
        manager.append_text("system normal", OutputSource.SYSTEM)
        manager.append_text("system debug", OutputSource.SYSTEM, system_level="debug")
        manager.append_text("serial data\n", OutputSource.RECEIVE)
        exported = manager.text_for_sources(
            {OutputSource.SYSTEM, OutputSource.RECEIVE},
            {"normal"},
        )
        self.assertIn("system normal", exported)
        self.assertNotIn("system debug", exported)
        self.assertIn("serial data", exported)


if __name__ == "__main__":
    unittest.main()
