#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""配置自动保存基础设施的轻量级回归检查。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from managers.config_manager import ConfigManager


class ConfigPersistenceTests(unittest.TestCase):
    def test_save_config_writes_valid_json_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            manager = ConfigManager(config_file=str(config_path))

            manager.set("state", {"basic_settings": {"baudrate": "115200"}})

            with config_path.open("r", encoding="utf-8") as config_file:
                saved = json.load(config_file)
            self.assertEqual(
                saved["state"]["basic_settings"]["baudrate"],
                "115200",
            )
            self.assertEqual(list(Path(directory).glob(".config.json.*.tmp")), [])

    def test_replace_failure_preserves_previous_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            manager = ConfigManager(config_file=str(config_path))
            previous_content = config_path.read_bytes()

            with mock.patch(
                "managers.config_manager.os.replace",
                side_effect=OSError("simulated replace failure"),
            ):
                with self.assertRaises(OSError):
                    manager.set("state", {"commands": [[True, "new", ""]]})

            self.assertEqual(config_path.read_bytes(), previous_content)
            self.assertEqual(list(Path(directory).glob(".config.json.*.tmp")), [])


class GuiAutoSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_control_change_is_saved_after_debounce(self):
        from PyQt5.QtTest import QTest
        from main_window import SerialTool

        previous_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                window = SerialTool(exe_name="autosave_check")
                self.assertFalse(window._state_dirty)

                window.baud_combo.setCurrentText("9600")
                self.assertTrue(window._state_dirty)
                QTest.qWait(window.STATE_SAVE_DEBOUNCE_MS + 100)
                self.assertFalse(window._state_dirty)

                config_path = Path(directory) / "autosave_check_config.json"
                with config_path.open("r", encoding="utf-8") as config_file:
                    saved = json.load(config_file)
                self.assertEqual(
                    saved["state"]["basic_settings"]["baudrate"],
                    "9600",
                )
                window.close()
                window.deleteLater()
                self.app.processEvents()
            finally:
                os.chdir(previous_directory)


if __name__ == "__main__":
    unittest.main()
