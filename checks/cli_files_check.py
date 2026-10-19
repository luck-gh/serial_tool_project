"""模板文件与 RX 保存的真实本机/Qt 桥接回归,不连接物理串口."""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import serial
from core.cli_runner import build_parser, run_cli, should_run_cli
from core.cli_console import ConsoleOutput, LocalConsoleBackend, GuiConsoleBackend
from core.command_parser import Command, parse_input
from core.command_templates import load_template, export_template, apply_local_template
from core.receive_text import ReceivedFile, ReceiveSaveError
from managers.config_manager import ConfigManager
import gui_cli_bridge_check
from remote_download_check import pump_until


ROWS = [[True, "Mode:测试", "模块"],
        [False, 'a,"中文"\n换行\t' + r'C:\new\app.bin', '注释,"引号"\n第二行'],
        [True, "ModeEnd:0", ""]]


class LocalFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.config = self.folder / "config.json"
        self.manager = ConfigManager(config_file=str(self.config))

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *arguments):
        args = build_parser().parse_args(["--config", str(self.config), *arguments])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_cli(args, "test", "0.0.0", "N/A")

    def test_csv_txt_bom_roundtrip_and_atomic_export_cancel(self):
        for suffix in ("csv", "txt"):
            target = self.folder / ("模板." + suffix)
            export_template(target, ROWS)
            target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())
            self.assertEqual(load_template(target), [tuple(row) for row in ROWS])
            before = target.read_bytes()
            with self.assertRaises(InterruptedError):
                export_template(target, ROWS, progress=lambda index: False)
            self.assertEqual(target.read_bytes(), before)
        self.assertFalse(list(self.folder.glob("*.tmp")))

    def test_offline_template_import_export_preserves_settings_and_invalid_input(self):
        self.manager.config["state"] = dict(basic_settings=dict(port="NO_DEVICE"),
            remote_settings=dict(host="192.0.2.1"), commands=[[False, "old", ""]],
            command_replacement_rules={"0": {"find": "old", "replace": "new"}})
        self.manager.save_config()
        original = copy.deepcopy(self.manager.config["state"])
        template = self.folder / "in.csv"
        export_template(template, ROWS)
        with mock.patch("serial.Serial", side_effect=AssertionError("no device access")):
            self.assertEqual(self.run_cli("--import-template", str(template)), 0)
            out = self.folder / "out.txt"
            self.assertEqual(self.run_cli("--export-template", str(out)), 0)
            self.assertEqual(load_template(out), [tuple(row) for row in ROWS])
            current = json.loads(self.config.read_text(encoding="utf-8"))["state"]
            self.assertEqual(current["basic_settings"], original["basic_settings"])
            self.assertEqual(current["remote_settings"], original["remote_settings"])
            self.assertEqual(current["command_replacement_rules"], {})
            before = self.config.read_bytes()
            for invalid in ('True,"unterminated', 'not-a-template', 'True', '// empty'):
                template.write_text(invalid, encoding="utf-8")
                self.assertEqual(self.run_cli("--import-template", str(template)), 1)
                self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(should_run_cli(["--import-template=x.csv"]))

    def test_local_import_save_failure_preserves_memory_and_disk(self):
        before = copy.deepcopy(self.manager.config)
        on_disk = self.config.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                apply_local_template(self.manager, ROWS)
        self.assertEqual(self.manager.config, before)
        self.assertEqual(self.config.read_bytes(), on_disk)

    def test_once_save_appends_exact_rx_even_hidden_and_expect_timeout(self):
        self.manager.config["state"] = dict(basic_settings=dict(port="LOOP"),
            receive_settings=dict(display_sources=dict(receive=False)))
        self.manager.save_config()
        target = self.folder / "rx.bin"
        target.write_bytes(b"previous")
        payload = "中文".encode() + b"\x00\xff"
        loop = serial.serial_for_url("loop://", timeout=0.05)
        with mock.patch("core.cli_runner.open_serial_from_config", return_value=loop):
            self.assertEqual(self.run_cli("--send", "SendHex:" + payload.hex(),
                "--expect", "never", "--save-received", str(target), "--read-timeout", "30"), 4)
        self.assertEqual(target.read_bytes(), b"previous" + payload)
        self.assertFalse(loop.is_open)
        target.rename(self.folder / "closed.bin")

    def test_invalid_combinations_and_unwritable_save_do_not_open_device(self):
        with mock.patch("core.cli_runner.open_serial_from_config", side_effect=AssertionError("no device")):
            self.assertEqual(self.run_cli("--save-received", str(self.folder / "unused")), 2)
            self.assertFalse((self.folder / "unused").exists())
            self.assertEqual(self.run_cli("--gui-action", "status", "--save-received", "unused"), 2)
            self.assertEqual(self.run_cli("--send", "AT", "--save-received", str(self.folder / "missing" / "rx")), 2)

    def test_writer_failure_is_explicit_and_failed_switch_keeps_previous_file(self):
        target = self.folder / "rx"
        writer = ReceivedFile(target)
        with self.assertRaises(OSError):
            writer.start(self.folder / "missing" / "rx")
        writer.feed(b"still here")
        writer.close()
        self.assertEqual(target.read_bytes(), b"still here")
        writer.stream = mock.Mock()
        writer.stream.write.side_effect = OSError("disk full")
        with self.assertRaisesRegex(ReceiveSaveError, "接收数据保存失败.*设备操作可能已执行"):
            writer.feed(b"data")
        self.assertIsNone(writer.stream)

    def test_once_disk_error_counts_rx_fails_and_never_retries_write(self):
        writer = ReceivedFile()
        stream = mock.Mock()
        stream.write.side_effect = OSError("disk full")
        writer.stream = stream
        loop = serial.serial_for_url("loop://", timeout=0.05)
        output = io.StringIO()
        args = build_parser().parse_args(["--config", str(self.config), "--send", "AT", "--expect", "never",
            "--save-received", str(self.folder / "rx"), "--read-timeout", "30"])
        with mock.patch("core.receive_text.ReceivedFile", return_value=writer), \
                mock.patch("core.cli_runner.open_serial_from_config", return_value=loop), \
                mock.patch.object(loop, "write", wraps=loop.write) as write, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_cli(args, "test", "0.0.0", "N/A"), 1)
        write.assert_called_once()
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertIn("接收数据保存失败", result["message"])
        self.assertEqual(result["received_bytes"], 4)
        self.assertIsNone(writer.stream)
        self.assertFalse(loop.is_open)

    def test_file_commands_are_rejected_before_device_module_writes(self):
        from core.console_execution import ConsoleExecutor
        from core.console_serial import LocalConsoleTransport
        for command in ("ImportTemplate", "ExportTemplate:out.csv", "SaveReceived:rx.bin"):
            self.manager.config["state"] = dict(commands=[[True, "Mode:A", ""], [True, command, ""]])
            self.manager.save_config()
            with mock.patch("core.cli_runner.open_serial_from_config", side_effect=AssertionError("no device writes")):
                self.assertEqual(self.run_cli("--send", command), 1)
                self.assertEqual(self.run_cli("--send", "SendMode:A"), 1)
            executor = ConsoleExecutor(LocalConsoleTransport(self.manager, lambda event: None), lambda event: None)
            self.assertFalse(executor.execute("SendMode:A")["success"])

    def test_console_startup_save_option_and_eof_close(self):
        from core.cli_console import GuiNotFound, run_console
        target = self.folder / "startup.bin"
        backend = mock.Mock()
        backend.start.return_value = dict(state={})
        session = mock.Mock()
        def prompt(*args, pre_run=None):
            if pre_run:
                pre_run()
            # 模拟启动后的真实 RX 事件;不经过显示规则或设备发送路径.
            backend.emit(dict(event="receive", source="receive", data=base64.b64encode(b"startup RX").decode()))
            raise EOFError()
        session.prompt.side_effect = prompt
        def local(manager, emit):
            backend.emit = emit
            return backend
        args = build_parser().parse_args(["--console", "--save-received", str(target)])
        captured = io.StringIO()
        with mock.patch("core.cli_console.GuiConsoleBackend.start", side_effect=GuiNotFound("absent")), \
                mock.patch("core.cli_console.LocalConsoleBackend", side_effect=local), \
                mock.patch("prompt_toolkit.PromptSession", return_value=session), \
                mock.patch("prompt_toolkit.patch_stdout.patch_stdout", return_value=contextlib.nullcontext()), \
                contextlib.redirect_stdout(captured):
            self.assertEqual(run_console(args, str(self.config), "0.0.0", "N/A"), 0, captured.getvalue())
        backend.close.assert_called_once()
        self.assertEqual(target.read_bytes(), b"startup RX")
        target.rename(self.folder / "closed-startup.bin")

    def test_console_local_templates_and_rx_dynamic_recording(self):
        target = self.folder / "rx"
        template = self.folder / "template.csv"
        export_template(template, ROWS)
        output = ConsoleOutput()
        output.configure(dict(receive_settings=dict(display_sources=dict(receive=False))))
        backend = LocalConsoleBackend(self.manager, output.emit)
        output.local_command(Command("SaveReceived", str(target)))
        backend.submit_template(Command("ImportTemplate", str(template)))
        backend.submit_template(Command("ExportTemplate", str(self.folder / "export.txt")))
        loop = serial.serial_for_url("loop://", timeout=0.05)
        with mock.patch("core.cli_runner.open_serial_from_config", return_value=loop):
            backend.open_initial()
            backend.submit("SendHex:e4b8ad00ff")
            deadline = time.monotonic() + 2
            while (not target.exists() or target.stat().st_size < 7) and time.monotonic() < deadline:
                time.sleep(0.01)
            backend.close()
        output.local_command(Command("SaveReceived", "off"))
        self.assertIsNone(output.received_file.stream)
        output.close()
        self.assertEqual(target.read_bytes(), bytes.fromhex("e4b8ad00ff0d0a"))
        self.assertEqual(load_template(self.folder / "export.txt"), [tuple(row) for row in ROWS])
        self.assertEqual(parse_input("ImportTemplate:C:\\folder\\a.csv"), Command("ImportTemplate", "C:\\folder\\a.csv"))

    def test_console_save_failure_is_fatal_without_raising_serial_error(self):
        output = ConsoleOutput()
        output.received_file.stream = mock.Mock()
        output.received_file.stream.write.side_effect = OSError("disk full")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            output.emit(dict(event="receive", source="receive", data=base64.b64encode(b"RX").decode()))
        self.assertTrue(output.fatal.is_set())
        self.assertIn("接收数据保存失败", captured.getvalue())
        self.assertNotIn("串口读取失败", captured.getvalue())
        output.close()

    def test_console_system_log_keeps_original_time_when_display_disabled(self):
        output = ConsoleOutput()
        target = self.folder / "session.log"
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            output.local_command(Command("Log", str(target)))
            output.emit(dict(source="system", message="fixed system event", timestamp="2026-10-15T01:02:03.456+08:00"))
        output.close()
        self.assertIn("fixed system event", captured.getvalue())
        self.assertNotIn("01:02:03", captured.getvalue())
        self.assertIn("[2026-10-15 01:02:03] fixed system event", target.read_text(encoding="utf-8"))


class GuiFileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gui_cli_bridge_check.GuiCliBridgeTests()
        self.fixture.setUp()
        self.window = self.fixture.window
        self.folder = Path(self.fixture.temp.name)

    def tearDown(self):
        pump_until(lambda: self.window.gui_cli_bridge.active is None)
        self.fixture.tearDown()

    def test_template_dialogs_share_selected_directory_and_persist(self):
        """中文空格目录共享并跨重建保留,取消和 CLI 路径不改 GUI 偏好."""
        from core.command_types import OutputSource
        folders = [self.folder / name for name in ("导入 目录", "导出 目录", "非法 模板目录")]
        for folder in folders:
            folder.mkdir()
        source = folders[0] / "输入.csv"
        exported = folders[1] / "输出.csv"
        invalid = folders[2] / "非法.csv"
        export_template(source, ROWS)
        invalid.write_text("not-a-template", encoding="utf-8")
        manager = self.window.config_manager
        original_save, saves = ConfigManager.save_config, []
        def save(config_manager, *args, **kwargs):
            saves.append(config_manager.config.get("last_used_directory"))
            return original_save(config_manager, *args, **kwargs)
        with mock.patch.object(ConfigManager, "save_config", new=save):
            for expected_saves in (2, 1):
                before = len(saves)
                with mock.patch("main_window.QFileDialog.getOpenFileName", return_value=(str(source), "")):
                    self.window.import_template()
                    pump_until(lambda: self.window._template_task is None)
                # 首次目录保存+正常提交;同目录再次导入仅正常提交.
                self.assertEqual(len(saves) - before, expected_saves)
            self.assertEqual(ConfigManager(config_file=manager.config_file).get_last_used_directory(), str(folders[0]))
            for method, dialog in ((self.window.import_template, "getOpenFileName"),
                                   (self.window.export_template, "getSaveFileName")):
                before = len(saves)
                with mock.patch("main_window.QFileDialog." + dialog, return_value=("", "")) as chooser:
                    method()
                    self.assertEqual(chooser.call_args.args[2], str(folders[0]))
                self.assertEqual(len(saves), before)
            for expected_extra in (1, 0):
                before = len(saves)
                with mock.patch("main_window.QFileDialog.getSaveFileName", return_value=(str(exported), "")) as chooser:
                    self.window.export_template()
                    self.assertEqual(chooser.call_args.args[2], str(folders[0] if expected_extra else folders[1]))
                    pump_until(lambda: self.window._template_task is None)
                self.assertEqual(len(saves) - before, expected_extra)
            with mock.patch("main_window.QFileDialog.getOpenFileName", return_value=(str(invalid), "")) as chooser, \
                    mock.patch.object(self.window, "_show_template_format_error_dialog", return_value="cancel"):
                before = len(saves)
                self.window.import_template()
                self.assertEqual(chooser.call_args.args[2], str(folders[1]))
                pump_until(lambda: self.window._template_task is None)
                self.assertEqual(len(saves) - before, 1)
            self.assertEqual(ConfigManager(config_file=manager.config_file).get_last_used_directory(), str(folders[2]))
            code, _events = self.fixture.collect(self.fixture.launch("--export-template", str(folders[0] / "CLI.csv")))
            self.assertEqual(code, 0)
            self.assertEqual(manager.get_last_used_directory(), str(folders[2]))
            self.assertEqual(ConfigManager(config_file=manager.config_file).get_last_used_directory(), str(folders[2]))
        before_file = Path(manager.config_file).read_bytes()
        with mock.patch.object(manager, "save_config", side_effect=OSError("directory save denied")):
            self.window._remember_template_directory(str(source))
        self.assertEqual(manager.get_last_used_directory(), str(folders[2]))
        self.assertEqual(Path(manager.config_file).read_bytes(), before_file)
        self.assertTrue(any(record.source_type == OutputSource.ERROR and "保存模板目录失败" in record.text
                            for record in self.window.output_manager.records))

    def test_gui_cli_template_roundtrip_live_state_and_invalid_file(self):
        self.window.command_table.clear_all()
        for row in ROWS:
            self.window.command_table.add_command_row(*row)
        self.window.refresh_modules(silent=True)
        gui_file = self.folder / "gui.csv"
        with mock.patch("main_window.QFileDialog.getSaveFileName", return_value=(str(gui_file), "")):
            self.window.export_template()
        pump_until(lambda: self.window._template_task is None)
        self.assertEqual(load_template(gui_file), [tuple(row) for row in ROWS])
        self.window.command_table.clear_all()
        code, events = self.fixture.collect(self.fixture.launch("--import-template", str(gui_file)))
        self.assertEqual(code, 0, events)
        self.assertEqual(self.window.command_table.get_all_commands(), [tuple(row) for row in ROWS])
        # 直接改内存且不保存;导出必须取当前窗口,不能重读旧配置.
        self.window.command_table.add_command_row(False, "MEMORY ONLY", "内存")
        exported = self.folder / "cli.txt"
        code, events = self.fixture.collect(self.fixture.launch("--export-template", str(exported)))
        self.assertEqual(code, 0, events)
        self.assertEqual(load_template(exported)[-1][1], "MEMORY ONLY")
        self.window.command_table.clear_all()
        with mock.patch("main_window.QFileDialog.getOpenFileName", return_value=(str(exported), "")):
            self.window.import_template()
        pump_until(lambda: self.window._template_task is None)
        self.assertEqual(self.window.command_table.get_all_commands()[-1][1], "MEMORY ONLY")
        before = self.window.command_table.get_all_commands()
        gui_file.write_text("invalid", encoding="utf-8")
        code, events = self.fixture.collect(self.fixture.launch("--import-template", str(gui_file)))
        self.assertEqual(code, 1, events)
        self.assertEqual(self.window.command_table.get_all_commands(), before)
        self.assertIsNone(self.window.serial_thread)

    def test_gui_once_save_only_current_rx_not_history_or_unrelated_events(self):
        from PyQt5.QtCore import QTimer
        self.window.on_data_received(b"HISTORY")
        self.window.show_recv_source_check.setChecked(False)
        fake = mock.Mock()
        self.window.serial_thread = fake
        self.window.is_connected = True
        payload = "中文".encode() + b"\x00\xff"
        def reply(data):
            QTimer.singleShot(0, lambda: self.window.on_data_received(payload[:1]))
            QTimer.singleShot(5, lambda: self.window.on_data_received(payload[1:]))
            return len(data)
        fake.write_data.side_effect = reply
        target = self.folder / "rx.bin"
        code, events = self.fixture.collect(self.fixture.launch("--send", "AT", "--expect", "never",
            "--read-timeout", "40", "--save-received", str(target)))
        self.assertEqual(code, 4, events)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(events[-1]["received_bytes"], len(payload))
        fake.write_data.assert_called_once_with(b"AT\r\n")
        self.assertTrue(self.window.is_connected)

    def test_gui_console_template_operations_and_subscription_save(self):
        output = ConsoleOutput()
        target = self.folder / "console.bin"
        output.local_command(Command("SaveReceived", str(target)))
        backend = GuiConsoleBackend(str(self.window.config_manager.config_file), output.emit)
        try:
            started = []
            thread = threading.Thread(target=lambda: started.append(backend.start()))
            thread.start()
            pump_until(lambda: bool(started))
            thread.join()
            template = self.folder / "console.csv"
            export_template(template, ROWS)
            backend.submit_template(Command("ImportTemplate", str(template)))
            pump_until(lambda: self.window._template_task is None and self.window.command_table.get_all_commands() == [tuple(row) for row in ROWS])
            exported = self.folder / "console.txt"
            backend.submit_template(Command("ExportTemplate", str(exported)))
            pump_until(lambda: exported.exists() and self.window._template_task is None)
            self.assertEqual(load_template(exported), [tuple(row) for row in ROWS])
            self.window.on_data_received(b"\xe4")
            self.window.on_data_received(b"\xb8\xad\x00\xff")
            pump_until(lambda: target.stat().st_size == 5)
        finally:
            backend.close()
            output.close()
        self.assertFalse(backend.thread.is_alive())
        self.assertEqual(target.read_bytes(), b"\xe4\xb8\xad\x00\xff")

    def test_large_template_uses_gui_file_and_file_commands_do_not_write_device(self):
        template = self.folder / "large.csv"
        export_template(template, [[False, "x" * 150, ""] for _ in range(1000)])
        before = self.window.command_table.get_all_commands()
        code, events = self.fixture.collect(self.fixture.launch("--import-template", str(template)))
        self.assertEqual(code, 0, events)
        self.assertEqual(len(self.window.command_table.get_all_commands()), 1000)
        self.assertEqual(events[-1]["command_count"], 1000)
        fake = mock.Mock()
        self.window.serial_thread = fake
        self.window.is_connected = True
        for text in ("ImportTemplate", "ExportTemplate:out.txt", "SaveReceived:rx"):
            self.window.command_table.clear_all()
            self.window.command_table.add_command_row(True, "Mode:A", "")
            self.window.command_table.add_command_row(True, text, "")
            self.window.refresh_modules(silent=True)
            for command in (text, "SendMode:A"):
                code, events = self.fixture.collect(self.fixture.launch("--send", command))
                self.assertEqual(code, 1, events)
        fake.write_data.assert_not_called()

    def test_gui_save_error_detaches_without_cancelling_accepted_task(self):
        from core.cli_bridge_client import run_gui_cli
        writer = ReceivedFile()
        writer.stream = mock.Mock()
        writer.stream.write.side_effect = OSError("disk full")
        fake = mock.Mock()
        fake.write_data.side_effect = lambda data: len(data)
        self.window.serial_thread = fake
        self.window.is_connected = True
        args = build_parser().parse_args(["--via-gui", "--send", "AT", "--read-timeout", "500", "--expect", "never"])
        events, codes = [], []
        thread = threading.Thread(target=lambda: codes.append(run_gui_cli(args,
            str(self.window.config_manager.config_file), received_file=writer, emit=events.append)))
        try:
            thread.start()
            pump_until(lambda: fake.write_data.called)
            self.window.on_data_received(b"RX")
            pump_until(lambda: bool(codes))
            self.assertEqual(codes, [3], events)
            self.assertIn("接收数据保存失败", events[-1]["message"])
            self.assertEqual(events[-1]["received_bytes"], 2)
            self.assertTrue(self.window.is_connected)
            self.assertIs(self.window.serial_thread, fake)
            bridge = self.window.gui_cli_bridge
            pump_until(lambda: bridge.active is None)
            self.assertEqual(list(bridge.results.values())[-1]["status"], "failed")
            self.assertNotEqual(list(bridge.results.values())[-1]["message"], "CLI 命令链已取消")
        finally:
            thread.join(3)
            writer.close()
        self.assertFalse(thread.is_alive())

    def test_template_results_match_gui_and_once_without_duplicates_or_serial(self):
        target = self.folder / "same.txt"
        code, events = self.fixture.collect(self.fixture.launch("--export-template", str(target)))
        self.assertEqual(code, 0, events)
        result = events[-1]
        records = [record for record in self.window.output_manager.records if record.text.rstrip() == result["message"]]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].timestamp, result["timestamp"])
        self.assertEqual(records[0].system_level, "normal")
        self.assertIsNone(self.window.serial_thread)
        before = len(self.window.output_manager.records)
        with mock.patch("main_window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            self.window.export_template()
        pump_until(lambda: self.window._template_task is None)
        self.assertEqual(sum(record.text.rstrip() == result["message"] for record in self.window.output_manager.records[before:]), 1)
        code, events = self.fixture.collect(self.fixture.launch("--import-template", str(target)))
        self.assertEqual(code, 0, events)
        self.assertEqual(sum(record.text.rstrip() == events[-1]["message"] for record in self.window.output_manager.records), 1)

    def test_gui_system_history_toggle_and_export_preserve_original_timestamp(self):
        from core.command_types import OutputSource
        manager = self.window.output_manager
        self.window.timestamp_check.setChecked(False)
        with mock.patch("managers.output_manager.event_timestamp", return_value="2026-10-15T01:02:03.456+08:00"):
            record = manager.append_text("stable system history", OutputSource.SYSTEM)
        self.assertEqual(record.timestamp, "2026-10-15T01:02:03.456+08:00")
        self.assertNotIn("01:02:03", self.window.receive_browser.toPlainText())
        self.assertIn("[2026-10-15 01:02:03.456] stable system history", manager.text_for_sources({OutputSource.SYSTEM}))
        self.window.timestamp_check.setChecked(True)
        pump_until(lambda: not manager._render_timer.isActive())
        self.assertIn("[2026-10-15 01:02:03.456] stable system history", self.window.receive_browser.toPlainText())
        self.window.timestamp_check.setChecked(False)
        pump_until(lambda: not manager._render_timer.isActive())
        self.assertIn("stable system history", self.window.receive_browser.toPlainText())
        self.assertNotIn("01:02:03", self.window.receive_browser.toPlainText())
        self.window.timestamp_check.setChecked(True)
        pump_until(lambda: not manager._render_timer.isActive())
        self.assertIn("[2026-10-15 01:02:03.456] stable system history", self.window.receive_browser.toPlainText())

    def test_status_and_config_share_gui_record_timestamp(self):
        self.window.timestamp_check.setChecked(True)
        for action in ("status", "config-get"):
            with mock.patch("core.output_rules.event_timestamp", return_value="2026-10-15T04:05:06.789+08:00"):
                code, events = self.fixture.collect(self.fixture.launch("--gui-action", action))
            self.assertEqual(code, 0, events)
            record = next(record for record in self.window.output_manager.records if "[CLI " + events[-1]["id"][:8] in record.text)
            self.assertEqual(record.timestamp, events[-1]["timestamp"])
            pump_until(lambda: not self.window.output_manager._render_timer.isActive())
            self.assertIn("[2026-10-15 04:05:06.789] [CLI " + events[-1]["id"][:8], self.window.receive_browser.toPlainText())

    def test_gui_button_cannot_bypass_active_cli_task(self):
        target = self.folder / "busy.csv"
        export_template(target, ROWS)
        before = self.window.command_table.get_all_commands()
        process = self.fixture.launch("--send", "Delay:1000")
        pump_until(lambda: self.window.gui_cli_bridge.active is not None)
        with mock.patch("main_window.QFileDialog.getOpenFileName", return_value=(str(target), "")):
            self.window.import_template()
        self.assertIsNone(self.window._template_task)
        self.assertEqual(self.window.command_table.get_all_commands(), before)
        self.assertEqual(self.fixture.collect(process)[0], 0)
        self.assertTrue(any("模板导入失败" in record.text for record in self.window.output_manager.records))

    def test_template_reuses_rows_and_preserves_editing_contract(self):
        """增减行复用前缀控件,重置旧显示状态,编辑和发送仍读取完整新内容."""
        table = self.window.command_table
        for old_count, new_count in ((0, 3), (3, 7), (7, 2), (2, 2)):
            with self.subTest(old=old_count, new=new_count):
                self.window._loading_state = True
                table.blockSignals(True)
                table.clear_all()
                for index in range(old_count):
                    table.add_command_row(True, f"old {index}", "旧注释")
                    edit = table.cellWidget(index, 1)
                    edit.set_validation_state("failed")
                    edit.set_command_search_matches([(0, 3)], (0, 3))
                    edit.set_comment_search_matches([(0, 2)], (0, 2))
                table.replacement_rules = {index: {"find": "old"} for index in range(old_count)}
                table.update_all_replacement_indicators()
                if old_count:
                    last = table.cellWidget(old_count - 1, 1)
                    font = last.font()
                    font.setPointSize(font.pointSize() + 2)
                    last.setFont(font)
                    table.setRowHeight(old_count - 1, table.rowHeight(old_count - 1) + 4)
                table.blockSignals(False)
                self.window._loading_state = False
                shared = min(old_count, new_count)
                editors = [table.cellWidget(index, 1) for index in range(shared)]
                fonts = [edit.font() for edit in editors]
                heights = [table.rowHeight(index) for index in range(shared)]
                rows = [(index % 2 == 0, f"read 0 {index} 1", "完整中文注释 " * 19 + "完整中文注释") for index in range(new_count)]
                target = self.folder / "reuse.csv"
                export_template(target, rows)
                result = []
                with mock.patch.object(table, "add_command_row", wraps=table.add_command_row) as added, \
                        mock.patch.object(table, "insertRow", wraps=table.insertRow) as inserted, \
                        mock.patch.object(table, "setRowCount", wraps=table.setRowCount) as allocated:
                    self.window.start_template_operation("template-import", str(target), result.append)
                    pump_until(lambda: bool(result))
                    self.assertEqual(added.call_count, max(0, new_count - old_count))
                    inserted.assert_not_called()
                    self.assertEqual(allocated.call_count, int(old_count != new_count))
                self.assertEqual(result[-1]["outcome"], "success")
                self.assertEqual(table.get_all_commands(), rows)
                self.assertFalse(table.replacement_rules)
                reference = max(0, old_count - 1)
                for index in range(old_count, new_count):
                    self.assertEqual(table.cellWidget(index, 1).font(), table.cellWidget(reference, 1).font())
                    self.assertEqual(table.rowHeight(index), table.rowHeight(reference))
                for index, edit in enumerate(editors):
                    self.assertIs(table.cellWidget(index, 1), edit)
                    self.assertEqual(edit.font(), fonts[index])
                    self.assertEqual(table.rowHeight(index), heights[index])
                    self.assertIsNone(edit.validation_state)
                    self.assertFalse(edit.command_search_matches or edit.comment_search_matches)
                    self.assertFalse(edit.property("hasReplacementRuleStyle"))
                    self.assertEqual(edit.toolTip(), "")
                with mock.patch.object(self.window, "send_command", return_value=True) as sent:
                    table.cellWidget(new_count - 1, 2).click()
                    sent.assert_called_once_with(rows[-1][1], new_count - 1)
                # 既有连接只建一次;真实编辑仍发出一次命令变化通知.
                changes = []
                table.commandsChanged.connect(changes.append)
                table.cellWidget(0, 1).setText("edited full command")
                table.commandsChanged.disconnect(changes.append)
                self.assertEqual(changes, [0])
                self.assertEqual(table.get_row_data(0)[1], "edited full command")
                table.add_command_row(False, "normal append", "完整注释")
                self.assertEqual(table.rowCount(), new_count + 1)
                with mock.patch.object(self.window, "send_command", return_value=True) as sent:
                    table.cellWidget(new_count, 2).click()
                    sent.assert_called_once_with("normal append", new_count)

    def test_partial_preallocated_import_query_guard_and_cancel(self):
        """结构已分配但控件未填满时拒绝读取半态,取消仍恢复原表和文件."""
        table = self.window.command_table
        before = table.get_all_commands()
        self.window.save_state()
        old_file = Path(self.window.config_manager.config_file).read_bytes()
        target = self.folder / "partial.csv"
        export_template(target, [[False, f"new {index}", "中文注释"] for index in range(1000)])
        original_progress = self.window._template_progress
        observed = []
        def progress(value, total):
            original_progress(value, total)
            if self.window._template_task["restoring"]:
                return
            self.assertGreater(value, 0)
            self.assertLess(value, total)
            self.assertEqual(table.rowCount(), 1000)
            self.assertIsNone(table.cellWidget(999, 1))
            replacement = self.window.replace_send_check.isChecked()
            self.assertFalse(self.window.replace_send_check.isEnabled())
            self.window.replace_mode_shortcut.activated.emit()
            self.assertEqual(self.window.replace_send_check.isChecked(), replacement)
            for action in ("config-get", "config-schema", "config-check"):
                request = dict(id="f" * 32, action=action)
                if action == "config-check":
                    request["config_patch"] = {}
                with self.assertRaisesRegex(ValueError, "模板事务"):
                    self.window.gui_cli_bridge.dispatch(mock.Mock(), request)
            observed.append(True)
            self.window.cancel_template_operation()
        result = []
        with mock.patch.object(self.window, "_template_progress", side_effect=progress):
            self.window.start_template_operation("template-import", str(target), result.append)
            pump_until(lambda: bool(result))
        self.assertEqual(observed, [True], result)
        self.assertEqual(result[-1]["outcome"], "cancelled")
        self.assertEqual(table.get_all_commands(), before)
        self.assertEqual(Path(self.window.config_manager.config_file).read_bytes(), old_file)
        self.assertTrue(self.window.replace_send_check.isEnabled())
        replacement = self.window.replace_send_check.isChecked()
        self.window.replace_mode_shortcut.activated.emit()
        self.assertEqual(self.window.replace_send_check.isChecked(), not replacement)

    def test_template_detaches_late_validation_display_even_after_rollback(self):
        """旧响应/超时保留统计及诊断,不会污染复用或回滚后的命令背景."""
        import re
        target = self.folder / "validation.csv"
        export_template(target, ROWS)
        for rollback in (False, True):
            for passed in (False, True):
                with self.subTest(rollback=rollback, passed=passed):
                    before = self.window.command_table.get_all_commands()
                    self.window.save_state()
                    before_file = Path(self.window.config_manager.config_file).read_bytes()
                    edit = self.window.command_table.cellWidget(0, 1)
                    self.window._register_response_validation(dict(pattern=re.compile("OLD_RESPONSE"),
                        expanded_expression="OLD_RESPONSE", timeout_ms=60000, on_failure="continue",
                        color_policy="sticky_failure", show_error=True), 0,
                        self.window.response_validation_manager.end_offset)
                    pending = self.window.response_validation_manager.pending[-1]
                    result = []
                    context = (mock.patch.object(self.window.config_manager, "save_config", side_effect=OSError("disk full"))
                               if rollback else contextlib.nullcontext())
                    with context:
                        self.window.start_template_operation("template-import", str(target), result.append)
                        pump_until(lambda: bool(result))
                    self.assertEqual(result[-1]["outcome"], "failed" if rollback else "success")
                    if rollback:
                        self.assertEqual(self.window.command_table.get_all_commands(), before)
                        self.assertEqual(Path(self.window.config_manager.config_file).read_bytes(), before_file)
                    self.assertFalse(self.window._state_dirty)
                    self.assertTrue(self.window.replace_send_check.isEnabled())
                    self.assertIs(self.window.command_table.cellWidget(0, 1), edit)
                    self.assertIsNone(pending.row_widget_ref)
                    complete_before = self.window.validation_complete_count
                    pass_before = self.window.validation_pass_count
                    if passed:
                        self.window.on_data_received(b"OLD_RESPONSE")
                    else:
                        pending.deadline = 0
                        self.window._expire_response_validations()
                    self.assertEqual(self.window.validation_complete_count, complete_before + 1)
                    self.assertEqual(self.window.validation_pass_count, pass_before + int(passed))
                    self.assertIsNone(edit.validation_state)
                    self.assertNotIn(pending, self.window.response_validation_manager.pending)

    def test_template_apply_heartbeat_busy_queries_and_atomic_save_failure(self):
        from PyQt5.QtCore import QTimer
        target = self.folder / "large.csv"
        rows = [[False, f"row {index}", ""] for index in range(600)]
        export_template(target, rows)
        before = self.window.command_table.get_all_commands()
        self.window.save_state()
        old_file = Path(self.window.config_manager.config_file).read_bytes()
        beats = []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: beats.append((time.monotonic(), bool(self.window._template_task and self.window._template_task.get("applied")))))
        original_add = self.window.command_table.add_command_row
        def slow_add(*args, **kwargs):
            result = original_add(*args, **kwargs)
            time.sleep(0.002)
            return result
        result = []
        try:
            timer.start()
            with mock.patch.object(self.window.command_table, "add_command_row", side_effect=slow_add), \
                    mock.patch.object(self.window.config_manager, "save_config", side_effect=OSError("disk full")):
                self.window.start_template_operation("template-import", str(target), result.append)
                pump_until(lambda: self.window._template_task and 0 < self.window._template_task.get("index", 0) < len(rows))
                for action in ("config-get", "config-schema"):
                    # Windows 创建子进程本身可能阻塞调用线程;测试驱动也须留出 Qt 心跳.
                    launched = []
                    launcher = threading.Thread(target=lambda action=action: launched.append(self.fixture.launch("--gui-action", action)))
                    launcher.start()
                    pump_until(lambda: bool(launched))
                    launcher.join()
                    code, events = self.fixture.collect(launched[0])
                    self.assertEqual(code, 1, events)
                    self.assertIn("模板事务", events[-1]["message"])
                    self.assertNotIn("config", events[-1])
                pump_until(lambda: bool(result), 10)
        finally:
            timer.stop()
        self.assertEqual(result[-1]["outcome"], "failed")
        self.assertGreater(sum(during for _time, during in beats), 5)
        self.assertLess(max(b - a for (a, _), (b, _) in zip(beats, beats[1:])), 0.25)
        self.assertEqual(self.window.command_table.get_all_commands(), before)
        self.assertEqual(Path(self.window.config_manager.config_file).read_bytes(), old_file)
        code, events = self.fixture.collect(self.fixture.launch("--gui-action", "config-get"))
        self.assertEqual(code, 0, events)
        self.assertEqual(events[-1]["config"]["state"]["commands"], [list(row) for row in before])

    def test_last_apply_chunk_cancel_rolls_back_before_persist(self):
        target = self.folder / "cancel.csv"
        export_template(target, ROWS)
        self.window.save_state()
        before = self.window.command_table.get_all_commands()
        old_file = Path(self.window.config_manager.config_file).read_bytes()
        result = []
        progress = self.window._template_progress
        def cancel_at_last(current, total):
            progress(current, total)
            task = self.window._template_task
            if current == total and task.get("applied") and not task["restoring"]:
                self.window.cancel_template_operation()
        with mock.patch.object(self.window, "_template_progress", side_effect=cancel_at_last), \
                mock.patch.object(self.window.config_manager, "save_config", wraps=self.window.config_manager.save_config) as save:
            self.window.start_template_operation("template-import", str(target), result.append)
            pump_until(lambda: bool(result))
            save.assert_not_called()
        self.assertEqual(result[-1]["outcome"], "cancelled")
        self.assertEqual(self.window.command_table.get_all_commands(), before)
        self.assertEqual(Path(self.window.config_manager.config_file).read_bytes(), old_file)

    def test_hidden_system_template_failure_has_no_success_and_rx_feedback_is_once(self):
        self.window.show_sys_source_check.setChecked(False)
        code, events = self.fixture.collect(self.fixture.launch("--export-template", str(self.folder / "missing" / "bad.csv")))
        self.assertEqual(code, 1, events)
        self.assertEqual(events[-1]["outcome"], "failed")
        self.assertFalse(any("模板已导出" in record.text for record in self.window.output_manager.records))
        output = ConsoleOutput()
        backend = GuiConsoleBackend(str(self.window.config_manager.config_file), output.emit)
        started = []
        start_thread = threading.Thread(target=lambda: started.append(backend.start()))
        captured = io.StringIO()
        target = self.folder / "feedback.bin"
        try:
            start_thread.start()
            pump_until(lambda: bool(started))
            start_thread.join()
            output.file_reporter = backend.report_file_event
            with contextlib.redirect_stdout(captured):
                output.local_command(Command("SaveReceived", str(target)))
                event = output.received_file.last_event
                pump_until(lambda: any(record.text.rstrip() == event["message"] for record in self.window.output_manager.records))
                self.window.on_data_received(b"RAW")
                pump_until(lambda: target.stat().st_size == 3)
                output.local_command(Command("SaveReceived", "off"))
                pump_until(lambda: any("接收数据保存已停止" in record.text for record in self.window.output_manager.records))
                backend.close()
            self.assertEqual(captured.getvalue().count(event["message"]), 1)
            self.assertEqual(sum(record.text.rstrip() == event["message"] for record in self.window.output_manager.records), 1)
            self.assertNotIn(event["message"], self.window.receive_browser.toPlainText())
            self.assertEqual(target.read_bytes(), b"RAW")
        finally:
            backend.close()
            output.close()


if __name__ == "__main__":
    unittest.main()
