"""GUI 入口配置传递及真实窗口配置/端点的离线回归."""
import contextlib
import importlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_identity import get_config_file
from core.cli_protocol import endpoint_name
from core.cli_runner import build_parser


class GuiEntryTests(unittest.TestCase):
    def test_both_entries_forward_config_and_preserve_qt_arguments(self):
        for name in ("main", "gui_main"):
            entry = importlib.import_module(name)
            for path in (None, "relative.json", "中文 目录/配置 文件.json", str(ROOT / "配置 文件.json")):
                forms = [[]] if path is None else [["--config", path], ["--config=" + path]]
                for form in forms:
                    for arguments in (form + ["-platform", "offscreen"], ["-platform", "offscreen"] + form):
                        with self.subTest(entry=name, arguments=arguments), contextlib.ExitStack() as stack:
                            stack.enter_context(mock.patch.object(sys, "argv", [name + ".py", *arguments]))
                            app = stack.enter_context(mock.patch.object(entry, "QApplication"))
                            window = stack.enter_context(mock.patch.object(entry, "SerialTool"))
                            stack.enter_context(mock.patch.object(entry, "hide_console_for_gui"))
                            if name == "main":
                                stack.enter_context(mock.patch.object(entry.signal, "signal"))
                                stack.enter_context(mock.patch.object(entry, "QTimer"))
                            app.return_value.exec_.return_value = 0
                            with self.assertRaises(SystemExit) as result:
                                entry.main()
                            self.assertEqual(result.exception.code, 0)
                            app.assert_called_once_with([name + ".py", "-platform", "offscreen"])
                            self.assertEqual(window.call_args.kwargs["config_file"], path)
                            if path is not None:
                                self.assertEqual(build_parser().parse_args(form).config, path)

    def test_missing_config_value_fails_before_application_creation(self):
        for name in ("main", "gui_main"):
            entry = importlib.import_module(name)
            for arguments in (["--config"], ["--config", "-platform", "offscreen"]):
                with self.subTest(entry=name, arguments=arguments), mock.patch.object(sys, "argv", [name, *arguments]), \
                        mock.patch.object(entry, "QApplication") as app, contextlib.redirect_stderr(io.StringIO()) as error:
                    with self.assertRaises(SystemExit) as result:
                        entry.main()
                    self.assertEqual(result.exception.code, 2)
                    self.assertIn("expected one argument", error.getvalue())
                    app.assert_not_called()


class GuiConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_window_uses_cli_config_path_and_endpoint(self):
        from main_window import SerialTool
        from core import gui_config
        with tempfile.TemporaryDirectory() as directory:
            original = os.getcwd()
            try:
                os.chdir(directory)
                for exe_name in ("tool", "tool_GUI", "tool_CLI"):
                    for path in (None, "relative.json", "中文 配置.json", str(Path(directory) / "absolute 配置.json")):
                        with self.subTest(exe_name=exe_name, path=path):
                            expected = path or get_config_file(exe_name)
                            window = SerialTool(exe_name=exe_name, config_file=path)
                            try:
                                self.assertEqual(window.config_manager.config_file, expected)
                                self.assertTrue(Path(expected).is_file())
                                self.assertEqual(window.gui_cli_bridge.name, endpoint_name(expected))
                                cli_path = build_parser().parse_args(["--config", expected]).config
                                self.assertEqual(endpoint_name(cli_path), window.gui_cli_bridge.name)
                                self.assertFalse(window.is_connected)
                                self.assertEqual(window.remote_mode, "off")
                                self.assertIn("basic_settings", gui_config.snapshot(window)["state"])
                            finally:
                                window.close()
                                window.deleteLater()
                                self.app.processEvents()
                self.assertEqual(get_config_file("tool_GUI"), get_config_file("tool_CLI"))
            finally:
                os.chdir(original)

    def test_remote_role_labels_keep_persisted_roles(self):
        from main_window import SerialTool
        from core import gui_config
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("serial.tools.list_ports.comports", return_value=[]), \
             mock.patch("serial.Serial", side_effect=AssertionError("禁止操作真实串口")), \
             mock.patch.object(SerialTool, "get_local_ipv4_addresses", return_value=["10.0.0.2", "127.0.0.1"]):
            path = str(Path(directory) / "settings.json")
            for stored, label, address_label in (
                ("主控端", "设备端（连接硬件）", "地址:"),
                ("远程端", "控制端（远程操作）", "地址:"),
            ):
                window = SerialTool(config_file=path)
                try:
                    gui_config.apply(window, {"state": {"remote_settings": {"mode": stored, "host": "10.0.0.2"}}})
                    self.assertEqual(window.remote_mode_combo.currentText(), label)
                    self.assertEqual(window.remote_host_label.text(), address_label)
                    self.assertEqual(window.snapshot_state()["remote_settings"]["mode"], stored)
                    enum = gui_config.schema(window)["properties"]["state"]["properties"]["remote_settings"]["properties"]["mode"]["enum"]
                    self.assertEqual(enum, ["主控端", "远程端"])
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()
                window = SerialTool(config_file=path)
                try:
                    self.assertEqual(window.remote_mode_combo.currentText(), label)
                    self.assertEqual(window.remote_host_label.text(), address_label)
                    self.assertEqual(window.remote_host_input.currentText(), "10.0.0.2")
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
