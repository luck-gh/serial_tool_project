"""全局默认规则的编辑草稿和 JSON 恢复检查,不访问真实串口."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QDialog
from core import gui_config
from dialogs.replacement_rule_dialog import ReplacementRuleDialog
from main_window import SerialTool

APP = QApplication.instance() or QApplication([])


class ReplacementDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "settings.json"
        self.serial = patch("serial.Serial", side_effect=AssertionError("禁止访问真实串口"))
        self.ports = patch("serial.tools.list_ports.comports", return_value=[])
        self.serial.start()
        self.ports.start()
        self.window = SerialTool(config_file=str(self.path))

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.ports.stop()
        self.serial.stop()
        self.folder.cleanup()

    def test_empty_saved_global_rule_prefills_draft_without_saving_on_cancel(self):
        w = self.window
        gui_config.apply(w, {"state": {"send_settings": {
            "global_replacement_rule": None, "continuous_interval": 250}}})
        w.close()
        w.deleteLater()
        APP.processEvents()
        self.window = w = SerialTool(config_file=str(self.path))
        self.assertIsNone(w.global_replacement_rule)
        before = self.path.read_bytes()

        def inspect_draft(dialog):
            self.assertEqual(dialog.find_input.text(), w.DEFAULT_GLOBAL_REPLACEMENT_RULE["find"])
            self.assertEqual(dialog.response_input.text(), w.DEFAULT_GLOBAL_REPLACEMENT_RULE["response_validation"]["expression"])
            self.assertEqual(dialog.response_settings["timeout_ms"], 250)
            self.assertFalse(dialog.response_enable_check.isChecked())
            self.assertIsNone(w.global_replacement_rule)
            return QDialog.Rejected

        with patch.object(ReplacementRuleDialog, "exec_", inspect_draft):
            w.edit_global_replacement_rule()
        self.assertIsNone(w.global_replacement_rule)
        self.assertEqual(self.path.read_bytes(), before)
        with patch.object(ReplacementRuleDialog, "exec_", return_value=QDialog.Accepted):
            w.edit_global_replacement_rule()
        w.save_state()
        saved = json.loads(self.path.read_text(encoding="utf-8"))["state"]["send_settings"]["global_replacement_rule"]
        self.assertEqual(saved["find"], w.DEFAULT_GLOBAL_REPLACEMENT_RULE["find"])
        self.assertEqual(saved["response_validation"]["timeout_ms"], 250)

    def test_custom_rule_and_explicit_clear_are_preserved(self):
        custom = {"find": "PING", "replace": "PONG", "options": {}}
        dialog = ReplacementRuleDialog(custom, default_rule=self.window.DEFAULT_GLOBAL_REPLACEMENT_RULE)
        self.assertEqual(dialog.find_input.text(), "PING")
        dialog.clear_btn.click()
        self.assertEqual(dialog.find_input.text(), "")
        dialog._validate_and_accept()
        self.assertTrue(dialog.clear_requested)
        self.assertIsNone(dialog.get_rule())
        dialog.deleteLater()
        # 行独立规则没有默认模板,仍从空草稿开始.
        dialog = ReplacementRuleDialog()
        self.assertEqual(dialog.find_input.text(), "")
        self.assertFalse(dialog.default_btn.isEnabled())
        dialog.deleteLater()

    def test_fresh_config_has_default_global_rule(self):
        self.assertEqual(self.window.global_replacement_rule["find"], self.window.DEFAULT_GLOBAL_REPLACEMENT_RULE["find"])
        self.assertFalse(self.window.global_replacement_rule["response_validation"]["enabled"])

    def test_default_response_requires_complete_value_and_accepts_hex_formatting(self):
        from core.text_search import compile_pattern, compile_expanded_response_pattern
        rule = self.window.DEFAULT_GLOBAL_REPLACEMENT_RULE
        source_pattern = compile_pattern(rule["find"], rule["options"])
        for value, returned in (("00000000", "00000000"), ("00000000", "0"),
                                ("0803C0C8", "803c0c8"), ("0803C0C8", "0x0803c0c8")):
            with self.subTest(value=value, returned=returned):
                source = source_pattern.search(f"write 2 0x40108048 0x{value}")
                _, pattern = compile_expanded_response_pattern(
                    rule["response_validation"]["expression"], source, rule["options"])
                response = f"read 2 0x40108048 1\r\n40108048 = {returned}"
                self.assertIsNone(pattern.search(response))
                self.assertIsNotNone(pattern.search(response + "\r\n"))
                self.assertIsNone(pattern.search(response + "F\r\n"))


if __name__ == "__main__":
    unittest.main()
