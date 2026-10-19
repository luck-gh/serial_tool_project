"""统一控制台回归:纯解析,真实 pySerial 回环,固件独占和 GUI 主线程适配."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import serial
from core.command_parser import Command, RawData, parse_input
from core.receive_text import ExpectMatcher
from core.console_execution import ConsoleExecutor
from core.console_serial import LocalConsoleTransport
from core.cli_console import LocalConsoleBackend, GuiConsoleBackend, ConsoleOutput
from core.cli_console import run_console, GuiNotFound
from core.cli_runner import build_parser, build_payload, run_cli, should_run_cli, read_available_transport
from managers.config_manager import ConfigManager
import gui_cli_bridge_check
from remote_download_check import FakeSerial, pump_until
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtCore import QTimer


def state(**send):
    return dict(basic_settings=dict(port="LOOP", port_node="LOOP", baudrate="115200", databits="8", parity="None", stopbits="1"),
                send_settings=dict(ending=r"\r\n", continuous_interval=1, loop_interval=1, **send),
                commands=[[True, "Mode:A", ""], [True, "SendHex:41", ""], [True, "mode/:literal", ""], [True, "ModeEnd:0", ""]])


class Peer:
    """只替代socket收发;GUI,线程与设备适配均使用真实实现."""
    def __init__(self):
        self.events = []
        self.connected = True
    def state(self):
        return QLocalSocket.ConnectedState if self.connected else QLocalSocket.UnconnectedState
    def write(self, data):
        self.events.append(json.loads(bytes(data).decode("utf-8")))
        return len(data)
    def flush(self):
        return True
    def bytesToWrite(self):
        return 0
    def disconnectFromServer(self):
        self.connected = False
    def abort(self):
        self.connected = False
    def deleteLater(self):
        pass


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = ConfigManager(config_file=str(Path(self.temp.name) / "config.json"))
        self.manager.config["state"] = state()
        self.events = []

    def tearDown(self):
        self.temp.cleanup()

    def transport(self):
        return LocalConsoleTransport(self.manager, self.events.append)

    def test_parser_and_incremental_regex(self):
        for text in ("quit", "QUIT", "Quit:cli"):
            self.assertEqual(parse_input(text).name, "Quit")
        for text, expected in [("Quit/:0", "Quit:0"), ("mode/:init", "mode:init"), ("a/b", "a/b"), ("a//b", "a/b"), (r"a\:b", "a:b"), ("mode//:x", "mode/:x")]:
            self.assertEqual(parse_input(text), RawData(expected))
        self.assertEqual(parse_input('FirmwareDownload:"D:\\firmware files\\app.bin"'), Command("FirmwareDownload", '"D:\\firmware files\\app.bin"'))
        self.assertEqual(parse_input("AT:0"), RawData("AT:0", "AT"))
        matcher = ExpectMatcher("中文.*OK")
        data = "中文OK".encode()
        self.assertFalse(matcher.feed(data[:1]))
        self.assertFalse(matcher.feed(data[1:5]))
        self.assertTrue(matcher.feed(data[5:]))
        self.assertTrue(matcher.finish())
        start = time.monotonic()
        read_available_transport(SimpleNamespace(in_waiting=0), SimpleNamespace(matcher=ExpectMatcher(".*")), 1000)
        self.assertLess(time.monotonic() - start, 0.05)

    def test_real_pyserial_loop_and_settings(self):
        loop = serial.serial_for_url("loop://", timeout=0.05)
        with mock.patch("core.cli_runner.open_serial_from_config", return_value=loop):
            transport = self.transport()
            executor = ConsoleExecutor(transport, self.events.append)
            transport.open()
            try:
                for text in ("SendHex:41", "BaudRate:9600", "ComPort:OTHER", "SetEndlog: n ", "Mode:A", "Delay:0"):
                    result = executor.execute(text)
                    self.assertTrue(result["success"], (text, result))
                deadline = time.monotonic() + 2
                while not any(e.get("source") == "receive" for e in self.events) and time.monotonic() < deadline:
                    time.sleep(0.005)
                transmitted = b"".join(base64.b64decode(e["data"]) for e in self.events if e.get("source") == "send")
                self.assertEqual(transmitted, b"A\r\nA\nmode:literal\n")
                self.assertEqual(loop.baudrate, 9600)
                self.assertEqual(loop.port, "loop://")
                saved = json.loads(Path(self.manager.config_file).read_text(encoding="utf-8"))["state"]
                self.assertEqual(saved["basic_settings"]["port"], "OTHER")
                self.assertEqual(saved["send_settings"]["ending"], r"\n")
                self.assertTrue(any(e.get("event") == "receive" for e in self.events))
                self.assertFalse(executor.execute("Delay:60001")["success"])
                self.assertFalse(executor.execute("SetEndlog:other")["success"])
                self.assertFalse(executor.execute("BaudRate:+9600")["success"])
            finally:
                transport.close()

            self.assertFalse(loop.is_open)
            self.assertIsNone(transport.reader)

    def test_hex_contract_keeps_standalone_and_console_endings_distinct(self):
        """共享解码后,独立发送仍不追加结束符,控制台仍拒绝空数据."""
        transport = self.transport()
        executor = ConsoleExecutor(transport, self.events.append)
        with mock.patch.object(transport, "write") as write:
            self.assertEqual(build_payload("SendHex: A BC ", self.manager)[0], b"\x0a\xbc")
            self.assertTrue(executor.execute("SendHex: A BC ")["success"])
            write.assert_called_once_with(b"\x0a\xbc\r\n", "SendHex: 0A BC")
            self.assertEqual(build_payload("SendHex: ", self.manager)[0], b"")
            self.assertFalse(executor.execute("SendHex: ")["success"])
            self.assertFalse(executor.execute("SendHex:GG")["success"])
            self.assertEqual(write.call_count, 1)
            for ending, expected in (("NONE", b""), ("RN", b"\r\n"), ("r", b"\r"), ("n", b"\n")):
                build_payload("SetEndlog:" + ending, self.manager)
                self.assertEqual(build_payload("text", self.manager)[0], b"text" + expected)

    def test_cancel_long_delay_and_loop(self):
        transport = self.transport()
        backend = LocalConsoleBackend(self.manager, self.events.append)
        backend.submit("Delay:5000")
        self.assertTrue(backend.executor.started.wait(1))
        start = time.monotonic()
        backend.submit("Cancel")
        backend.close()
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(any(e.get("status") == "cancelled" for e in self.events))
        executor = ConsoleExecutor(transport, self.events.append)
        self.manager.config["state"]["send_settings"]["loop_send"] = True
        writes = []
        def write(data, display):
            writes.append(data)
            if len(writes) == 1:
                executor.stop_continuous(0)
            elif len(writes) == 2:
                executor.stop_continuous(1)
        with mock.patch.object(transport, "write", side_effect=write):
            result = executor.execute("SendMode:A")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(writes, [b"A\r\n", b"A\r\n"])

    def test_firmware_exclusive_reader_and_restore(self):
        self.manager.config["state"]["basic_settings"].update(
            baudrate="9600", databits="7", parity="Even", stopbits="1.5")
        path = Path(self.temp.name) / "app.bin"
        path.write_bytes(b"abcdefgh")
        tools = self.manager.config["tools"]["firmware_downloader"]
        tools.update(packet_size=4, wait_start_ack=False, wait_packet_ack=False,
                     wait_last_packet_ack=True, last_packet_ack_expected_data="OK", last_packet_ack_timeout=100)
        opened = []
        reply_ack = b"OK"
        def create(*args, **kwargs):
            self.assertFalse(any(port.is_open for port in opened), "两个串口拥有者同时打开")
            port = FakeSerial(*args, ack=reply_ack, **kwargs)
            port.port = kwargs.get("port", "LOOP")
            port.bytesize = kwargs.get("bytesize", 8)
            port.parity = kwargs.get("parity", "N")
            port.stopbits = kwargs.get("stopbits", 1)
            opened.append(port)
            return port
        with mock.patch("serial.Serial", side_effect=create), mock.patch("serial.tools.list_ports.comports", return_value=[SimpleNamespace(device="LOOP")]):
            transport = self.transport()
            transport.open()
            try:
                self.assertTrue(ConsoleExecutor(transport, self.events.append).execute("ComPort:OTHER")["success"])
                result = ConsoleExecutor(transport, self.events.append).execute(f"FirmwareDownload:{path}")
                self.assertTrue(result["success"], result)
                self.assertEqual(len(opened), 3)
                self.assertTrue(opened[0].closed)
                self.assertTrue(opened[1].closed)
                self.assertTrue(opened[2].is_open)
                for port in opened:
                    self.assertEqual((port.port, port.baudrate, port.bytesize, port.parity, port.stopbits),
                                     ("LOOP", 9600, 7, "E", 1.5))
                self.assertEqual(opened[1].writes[1:], [b"abcd", b"efgh"])
                self.assertIsNotNone(transport.reader)
                tools.update(wait_last_packet_ack=False)
                unknown = ConsoleExecutor(transport, self.events.append).execute(f"FirmwareDownload:{path}")
                self.assertEqual(unknown["status"], "unknown")
                self.assertTrue(transport.serial.is_open)
                tools.update(wait_last_packet_ack=True, last_packet_ack_expected_data="NEVER", last_packet_ack_timeout=2000)
                reply_ack = b""
                executor = ConsoleExecutor(transport, self.events.append)
                outcome = []
                worker = threading.Thread(target=lambda: outcome.append(executor.execute(f"FirmwareDownload:{path}")))
                worker.start()
                deadline = time.monotonic() + 2
                # 写出后取消才应为 unknown;仅打开句柄时取消仍可安全判 cancelled.
                while (len(opened) < 6 or not opened[5].writes) and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertGreaterEqual(len(opened), 6)
                self.assertTrue(opened[5].writes)
                self.assertIsNone(transport.reader)
                executor.cancel()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(outcome[0]["status"], "unknown")
                self.assertTrue(transport.serial.is_open)
                self.assertIsNotNone(transport.reader)
            finally:
                transport.close()

            external = Path(self.temp.name) / "external_stub.py"
            external.write_text("print('INFO: external-check')\n", encoding="utf-8")
            tools["path"] = str(external)
            result = ConsoleExecutor(transport, self.events.append).execute(f"FirmwareDownload:{path}")
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(any("external-check" in event.get("message", "") for event in self.events))

    def test_expect_exit_codes_and_old_send(self):
        for response, pattern, expected in [("中文OK".encode(), "中文OK", 0),
                (b"000 = 20240205", r"[0-9a-fA-F]{8} = ", 4), (b"", "never", 4),
                (b"legacy", None, 0)]:
            fake = FakeSerial(ack=response, read_size=1)
            fake.flush = lambda: None
            args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "AT", "--read-timeout", "40"] + (["--expect", pattern] if pattern else []))
            with mock.patch("core.cli_runner.open_serial_from_config") as opened, contextlib.redirect_stdout(io.StringIO()) as captured:
                opened.return_value.__enter__.return_value = fake
                self.assertEqual(run_cli(args, "test", "0", "today"), expected)
            if pattern is not None:
                result = json.loads(captured.getvalue().splitlines()[-1])
                self.assertEqual(result["received_bytes"], len(response))
                self.assertEqual(result["expect_matched"], expected == 0)
                if expected == 4:
                    self.assertIn("响应不匹配" if response else "设备无响应", result["message"])
        args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "AT", "--expect", "OK"])
        with mock.patch("core.cli_runner.open_serial_from_config", side_effect=RuntimeError("port blocked")), \
                contextlib.redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(run_cli(args, "test", "0", "today"), 1)
        self.assertEqual(json.loads(captured.getvalue().splitlines()[-1])["received_bytes"], 0)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                build_parser().parse_args(["--console", "--send", "AT"])
        self.assertEqual(raised.exception.code, 2)
        self.assertTrue(should_run_cli(["-i"]))

    def test_expect_summary_is_bounded_and_counts_raw_device_bytes(self):
        matcher = ExpectMatcher("never")
        matcher.feed(b"a" * 10000)
        matcher.feed(b"\xff")
        summary = matcher.result_fields()
        self.assertEqual(summary["received_bytes"], 10001)
        self.assertTrue(summary["received_preview_truncated"])
        self.assertEqual(len(summary["received_preview"]), 128)
        self.assertLess(len(json.dumps(summary)), 1024)

    def test_one_shot_short_or_failed_write_stops_module_and_returns_unknown_once(self):
        self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, "FIRST", ""], [True, "SECOND", ""]]
        self.manager.save_config()
        for failure in (0, 1, serial.SerialTimeoutException("write timeout"), OSError("hardware failed")):
            for use_expect in (False, True):
                with self.subTest(failure=repr(failure), expect=use_expect):
                    fake = FakeSerial(ack=b"")
                    fake.flush = mock.Mock()
                    def write(data):
                        fake.writes.append(data)
                        if isinstance(failure, OSError):
                            raise failure
                        return failure
                    fake.write = write
                    args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "SendMode:A", "--read-timeout", "0"] +
                                                     (["--expect", "OK"] if use_expect else []))
                    with mock.patch("core.cli_runner.open_serial_from_config", return_value=fake), \
                            contextlib.redirect_stdout(io.StringIO()) as captured, contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(run_cli(args, "test", "0.0.0", "N/A"), 3)
                    results = [json.loads(line) for line in captured.getvalue().splitlines() if line.startswith('{')]
                    self.assertEqual(len(results), 1)
                    self.assertEqual(results[0]["status"], "unknown")
                    self.assertFalse(results[0]["success"])
                    self.assertEqual(fake.writes, [b"FIRST\r\n"])
                    fake.flush.assert_not_called()
                    self.assertTrue(fake.closed)

    def test_one_shot_complete_write_requires_flush_and_returns_normal_result(self):
        for flush_error in (None, OSError("flush failed")):
            with self.subTest(flush_error=flush_error):
                fake = FakeSerial(ack=b"OK")
                fake.flush = mock.Mock(side_effect=flush_error)
                args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "AT", "--expect", "OK", "--read-timeout", "30"])
                with mock.patch("core.cli_runner.open_serial_from_config") as opened, \
                        contextlib.redirect_stdout(io.StringIO()) as captured, contextlib.redirect_stderr(io.StringIO()):
                    opened.return_value.__enter__.return_value = fake
                    code = run_cli(args, "test", "0.0.0", "N/A")
                result = json.loads(captured.getvalue().splitlines()[-1])
                self.assertEqual(code, 3 if flush_error else 0)
                self.assertEqual(result["status"], "unknown" if flush_error else "success")
                self.assertEqual(fake.writes, [b"AT\r\n"])
                fake.flush.assert_called_once()

    def test_unknown_result_preserves_short_write_with_cleanup_and_log_failure(self):
        logs = Path(self.temp.name) / "logs"
        self.manager.config["state"]["log_settings"] = dict(save_full_log=True, log_directory=str(logs))
        self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, "FIRST", ""], [True, "SECOND", ""]]
        self.manager.save_config()
        for command in ("AT", "SendMode:A"):
            with self.subTest(command=command):
                fake = FakeSerial(ack=b"", short=True)
                fake.flush = lambda: None
                fake.close = mock.Mock(side_effect=OSError("close failed"))
                args = build_parser().parse_args(["--config", self.manager.config_file, "--send", command, "--expect", "OK", "--read-timeout", "0"])
                with mock.patch("core.cli_runner.open_serial_from_config") as opened, \
                        mock.patch("core.cli_log.write_batch", side_effect=OSError("log tail failed")), \
                        contextlib.redirect_stdout(io.StringIO()) as captured, contextlib.redirect_stderr(io.StringIO()):
                    if command == "AT":
                        opened.return_value.__enter__.return_value = fake
                        opened.return_value.__exit__.side_effect = OSError("close failed")
                    else:
                        opened.return_value = fake
                    self.assertEqual(run_cli(args, "test", "0.0.0", "N/A"), 3)
                results = [json.loads(line) for line in captured.getvalue().splitlines() if line.startswith('{')]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["status"], "unknown")
                for message in ("短写", "close failed", "log tail failed"):
                    self.assertIn(message, results[0]["message"])
                self.assertEqual(len(fake.writes), 1)

    def test_unknown_module_write_survives_tail_read_and_close_errors(self):
        self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, "FIRST", ""], [True, "SECOND", ""]]
        self.manager.save_config()
        fake = FakeSerial(ack=b"", short=True)
        fake.close = mock.Mock(side_effect=OSError("close failed"))
        args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "SendMode:A", "--expect", "OK"])
        with mock.patch("core.cli_runner.open_serial_from_config", return_value=fake), \
                mock.patch("core.cli_runner.read_available_transport", side_effect=OSError("tail read failed")), \
                contextlib.redirect_stdout(io.StringIO()) as captured, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_cli(args, "test", "0.0.0", "N/A"), 3)
        result = json.loads(captured.getvalue().splitlines()[-1])
        self.assertEqual(result["status"], "unknown")
        for message in ("短写", "tail read failed", "close failed"):
            self.assertIn(message, result["message"])
        fake.close.assert_called_once()
        self.assertEqual(fake.writes, [b"FIRST\r\n"])

    def test_console_bridge_failure_identifies_account_and_config(self):
        backend = GuiConsoleBackend(str(Path(self.temp.name) / "missing-gui.json"), self.events.append)
        try:
            with self.assertRaises(GuiNotFound) as raised:
                backend.start()
            self.assertIn(str(Path(self.temp.name) / "missing-gui.json"), str(raised.exception))
            self.assertIn("实际账户", str(raised.exception))
            self.assertIn("同一用户", str(raised.exception))
        finally:
            backend.close()

    def test_legacy_module_unknown_refuses_and_literal_escape_is_once(self):
        for text, expected in (("Unknown:0", None), ("Unknown/:0", b"Unknown:0\r\n"),
                               ("SendHex/:41", b"SendHex:41\r\n"), ("a//b", b"a/b\r\n"), ("Help", b"Help\r\n")):
            self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, text, ""], [True, "ModeEnd:0", ""]]
            self.manager.set("state", self.manager.config["state"])
            fake = FakeSerial(ack=b"")
            fake.flush = lambda: None
            args = build_parser().parse_args(["--config", self.manager.config_file, "--send", "SendMode:A", "--read-timeout", "0"])
            with mock.patch("core.cli_runner.open_serial_from_config", return_value=fake) as opened, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = run_cli(args, "test", "0", "today")
            if expected is None:
                self.assertEqual(code, 1)
                opened.assert_not_called()
                self.assertEqual(fake.writes, [])
            else:
                self.assertEqual(code, 0, text)
                self.assertEqual(fake.writes, [expected], text)

    def test_quit_and_log_session_only(self):
        output = ConsoleOutput()
        output.configure(state())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(output.local_command(Command("Quit", "cli")))
            for parameter in ("gui", "all"):
                with self.assertRaises(ValueError):
                    output.local_command(Command("Quit", parameter))
            output.local_command(Command("Timestamp", "on"))
            path = Path(self.temp.name) / "serial.log"
            output.local_command(Command("Log", str(path)))
            output.emit(dict(event="receive", source="receive", data=base64.b64encode(b"RX").decode()))
            output.emit(dict(event="progress", source="send", data=base64.b64encode(b"TX").decode()))
            output.close()
        self.assertIn("[RECEIVE]", path.read_text(encoding="utf-8"))
        self.assertIn("[SEND]", path.read_text(encoding="utf-8"))
        self.assertNotIn("other_settings", self.manager.config["state"])

    def test_module_session_commands_never_reach_device_and_timestamp_minimum_state(self):
        transport = self.transport()
        writes = []
        executor = ConsoleExecutor(transport, self.events.append)
        with mock.patch.object(transport, "write", side_effect=lambda data, display: writes.append(data)):
            for text in ("Help:0", "Quit:0", "ShowHex:on", "Timestamp:on", "Clear:0", "Log:off", "Status:0", "Cancel:0"):
                self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, text, ""], [True, "ModeEnd:0", ""]]
                result = executor.execute("Mode:A")
                self.assertFalse(result["success"], (text, result))
                self.assertEqual(writes, [])
            self.manager.config["state"]["commands"] = [[True, "Mode:A", ""], [True, "Help/:0", ""], [True, "a//b", ""], [True, "Device:0", ""]]
            self.assertTrue(executor.execute("Mode:A")["success"])
            self.assertEqual(writes, [b"Help:0\r\n", b"a/b\r\n", b"Device:0\r\n"])
        output = ConsoleOutput()
        output.configure(dict(send_settings={}))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            output.local_command(Command("Timestamp", "on"))
            output.emit(dict(event="receive", source="receive", data=base64.b64encode(b"RX").decode()))
        self.assertRegex(captured.getvalue(), r"\[\d{4}-\d{2}-\d{2} ")

    def test_prompt_session_quit_interrupt_and_eof(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.application import create_app_session
        args = build_parser().parse_args(["--console", "--config", self.manager.config_file])
        for keys in ("Help\nQuit\n", "\x03", "\x04", "Quit:gui\nQuit\n", "Quit:all\nQuit\n"):
            with self.subTest(keys=repr(keys)), create_pipe_input() as pipe:
                session = PromptSession(input=pipe, output=DummyOutput())
                finished = threading.Event()
                def feed_keys():
                    for piece in keys.splitlines(keepends=True):
                        if finished.wait(0.1):
                            return
                        pipe.send_text(piece)
                    if not finished.wait(3) and session.app.is_running:
                        session.app.loop.call_soon_threadsafe(lambda: session.app.exit(exception=TimeoutError("可控输入未结束会话")))
                feeder = threading.Thread(target=feed_keys)
                feeder.start()
                with mock.patch("prompt_toolkit.PromptSession", return_value=session), \
                     mock.patch.object(GuiConsoleBackend, "start", side_effect=GuiNotFound()), \
                     create_app_session(input=pipe, output=DummyOutput()), \
                     contextlib.redirect_stdout(io.StringIO()) as captured:
                    code = run_console(args, self.manager.config_file, "0", "today")
                finished.set()
                feeder.join(1)
                self.assertEqual(code, 0, captured.getvalue())


class GuiConsoleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gui_cli_bridge_check.GuiCliBridgeTests()
        self.fixture.setUp()
        self.window = self.fixture.window
        self.bridge = self.window.gui_cli_bridge
        self.writes = []
        self.window.serial_thread = SimpleNamespace(write_data=lambda data: self.writes.append(bytes(data)) or len(data), set_baudrate=lambda value: True)
        self.window.is_connected = True
        self.window.ending_combo.setCurrentText(r"\r\n")

    def tearDown(self):
        if self.bridge.active and self.bridge.active.get("executor"):
            self.bridge.active["executor"].cancel()
            pump_until(lambda: self.bridge.active is None)
        self.fixture.tearDown()

    def request(self, action="console-send", command=None):
        peer = Peer()
        self.bridge.connections[peer] = bytearray()
        request = dict(id=uuid.uuid4().hex, action=action, detach_on_disconnect=True, read_timeout=0)
        if command is not None:
            request["command"] = command
        self.bridge.dispatch(peer, request)
        return peer

    def finished(self, text):
        peer = self.request(command=text)
        pump_until(lambda: any(e.get("event") == "result" for e in peer.events))
        return peer.events[-1]

    def test_gui_and_local_payload_and_settings_contract(self):
        subscription = self.request("subscribe")
        self.assertEqual(subscription.events[0]["console_protocol"], 1)
        for text in ("SendHex:41", "BaudRate:9600", "ComPort:OTHER", "SetEndlog: n ", "Delay:0"):
            result = self.finished(text)
            self.assertTrue(result["success"], (text, result))
        self.window.command_table.setRowCount(0)
        for enabled, text, comment in state()["commands"]:
            self.window.command_table.add_command_row(enabled, text, comment)
        self.window.refresh_modules(silent=True)
        self.assertTrue(self.finished("Mode:A")["success"])
        self.assertEqual(self.writes, [b"A\r\n", b"A\n", b"mode:literal\n"])
        tx = [base64.b64decode(e["data"]) for e in subscription.events if e.get("source") == "send"]
        self.assertEqual(tx, self.writes)
        self.assertEqual(self.window.baud_combo.currentText(), "9600")
        self.assertEqual(self.window.get_selected_port_id(), "OTHER")
        self.assertFalse(self.finished("BaudRate:+9600")["success"])
        self.assertFalse(self.finished("SetEndlog:other")["success"])

    def test_gui_special_sendhex_and_literal_ending_keep_existing_contract(self):
        """窗口特殊指令保留奇数位,空输入及实际换行结束符的行为."""
        from core.command_types import SpecialCommandType
        self.assertTrue(self.window.set_ending("\r\n"))
        self.assertEqual(self.window.get_ending_chars(), b"\r\n")
        manager = self.window.special_command_manager
        self.assertTrue(manager.execute(SpecialCommandType.SENDHEX, "A BC", self.window))
        self.assertEqual(self.writes, [b"\x0a\xbc\r\n"])
        self.assertFalse(manager.execute(SpecialCommandType.SENDHEX, " ", self.window))
        self.assertFalse(self.finished("SendHex: ")["success"])
        self.assertEqual(len(self.writes), 1)

    def test_detach_keeps_task_and_serial_then_explicit_cancel(self):
        peer = self.request(command="Delay:5000")
        pump_until(lambda: self.bridge.active is not None and self.bridge.active["executor"].busy)
        task_id = self.bridge.active["id"]
        self.bridge.disconnected(peer)
        self.assertIsNotNone(self.bridge.active)
        self.assertIsNone(self.bridge.active["socket"])
        self.assertFalse(self.bridge.active["executor"]._cancel.is_set())
        self.assertTrue(self.window.is_connected)
        cancel = self.request(command="Cancel")
        self.assertTrue(cancel.events[-1]["success"])
        pump_until(lambda: self.bridge.active is None)
        self.assertEqual(self.bridge.results[task_id]["status"], "cancelled")
        self.assertTrue(self.window.is_connected)

    def test_real_gui_client_subscription_and_detach(self):
        events = []
        backend = GuiConsoleBackend(self.window.config_manager.config_file, events.append)
        started = []
        def start_client():
            try:
                started.append(backend.start())
            except Exception as exc:
                started.append(exc)
        starter = threading.Thread(target=start_client)
        starter.start()
        pump_until(lambda: bool(started), 6)
        starter.join()
        self.assertIsInstance(started[0], dict, started)
        try:
            request_id = backend.submit("SendHex:41")
            pump_until(lambda: any(e.get("id") == request_id and e.get("event") == "result" for e in events))
            self.window.on_data_received("中文".encode())
            pump_until(lambda: any(e.get("source") == "receive" for e in events))
            tx = [base64.b64decode(e["data"]) for e in events if e.get("source") == "send"]
            self.assertEqual(tx, [b"A\r\n"])
            backend.submit("Delay:5000")
            pump_until(lambda: self.bridge.active is not None and self.bridge.active.get("executor") and self.bridge.active["executor"].busy)
            task_id = self.bridge.active["id"]
        finally:
            backend.close()
        pump_until(lambda: self.bridge.active["socket"] is None)
        self.assertFalse(self.bridge.active["executor"]._cancel.is_set())
        cancel = self.request(command="Cancel")
        self.assertTrue(cancel.events[-1]["success"])
        pump_until(lambda: self.bridge.active is None)
        self.assertEqual(self.bridge.results[task_id]["status"], "cancelled")
        self.assertTrue(self.window.is_connected)

    def test_bridge_expect_fragmented_utf8_timeout_and_full_module(self):
        def reply(data):
            self.writes.append(bytes(data))
            response = "中文OK".encode()
            QTimer.singleShot(0, lambda: self.window.on_data_received(response[:1]))
            QTimer.singleShot(5, lambda: self.window.on_data_received(response[1:]))
            return len(data)
        self.window.serial_thread.write_data = reply
        code, lines = self.fixture.collect(self.fixture.launch("--send", "AT", "--expect", "中文OK", "--read-timeout", "150"))
        self.assertEqual(code, 0, lines)
        self.assertTrue(lines[-1]["expect_matched"])
        self.assertEqual(lines[-1]["received_bytes"], len("中文OK".encode()))
        self.window.serial_thread.write_data = lambda data: len(data)
        code, lines = self.fixture.collect(self.fixture.launch("--send", "AT", "--expect", "never", "--read-timeout", "20"))
        self.assertEqual(code, 4, lines)
        self.assertEqual(lines[-1]["received_bytes"], 0)
        self.assertIn("设备无响应", lines[-1]["message"])
        def mismatched_reply(data):
            task = self.bridge.active
            self.bridge.send(task["socket"], dict(event="receive", id="unrelated",
                data=base64.b64encode(b"unrelated IPC data").decode("ascii")))
            self.window.on_data_received(b"000 = 20240205")
            return len(data)
        self.window.serial_thread.write_data = mismatched_reply
        code, lines = self.fixture.collect(self.fixture.launch("--send", "AT", "--expect", r"[0-9a-fA-F]{8} = ", "--read-timeout", "20"))
        self.assertEqual(code, 4, lines)
        self.assertEqual(lines[-1]["received_bytes"], 14)
        self.assertIn("响应不匹配", lines[-1]["message"])
        self.window.command_table.setRowCount(0)
        for text in ("Mode:FULL", "FIRST", "SECOND", "ModeEnd:0"):
            self.window.command_table.add_command_row(True, text, "")
        self.window.refresh_modules(silent=True)
        self.window.interval_spin.setValue(1)
        def early_reply(data):
            self.writes.append(bytes(data))
            if data.startswith(b"FIRST"):
                self.window.on_data_received(b"OK")
            return len(data)
        self.window.serial_thread.write_data = early_reply
        before = len(self.writes)
        code, lines = self.fixture.collect(self.fixture.launch("--send", "SendMode:FULL", "--expect", "OK", "--read-timeout", "30"))
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.writes[before:], [b"FIRST\r\n", b"SECOND\r\n"])
        def late_failure(data):
            if data.startswith(b"FIRST"):
                self.window.on_data_received(b"OK")
                return len(data)
            return 0
        self.window.serial_thread.write_data = late_failure
        code, lines = self.fixture.collect(self.fixture.launch("--send", "SendMode:FULL", "--expect", "OK", "--read-timeout", "30"))
        self.assertIn(code, (1, 3), lines)
        self.assertFalse(lines[-1]["success"])
        self.assertEqual(lines[-1]["received_bytes"], 2)

    def test_firmware_progress_and_logs_once_per_subscription(self):
        subscription = self.request("subscribe")
        self.window._firmware_download_log("INFO", "firmware-info-once")
        self.window._firmware_download_log("ERROR", "firmware-error-once")
        self.window._forward_external_firmware_output("INFO: external-once\n", "INFO")
        self.bridge.progress(1, 2, "firmware-progress-once")
        for marker in ("firmware-info-once", "firmware-error-once", "external-once", "firmware-progress-once"):
            matches = [event for event in subscription.events if marker in event.get("message", "")]
            self.assertEqual(len(matches), 1, (marker, subscription.events))


if __name__ == "__main__":
    unittest.main()
