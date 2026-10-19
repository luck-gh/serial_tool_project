"""断连回归:复用隔离 GUI 夹具,真实子进程保留 stdin 开放,禁止物理串口."""
import contextlib
import io
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import console_check
from console_check import GuiConsoleBackend, run_console, state
from core.cli_runner import build_parser
from core.command_types import OutputSource
from core.remote_control import RemoteControlServer
from remote_download_check import pump_until


class TerminalDisconnectTests(unittest.TestCase):
    def test_gui_close_or_crash_exits_idle_and_active_console_with_stdin_open(self):
        program = r'''
import os,sys
from pathlib import Path
from unittest import mock
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from main_window import SerialTool
app=QApplication([])
with mock.patch('serial.Serial',side_effect=AssertionError('physical serial forbidden')), mock.patch('serial.tools.list_ports.comports',return_value=[]):
    window=SerialTool(config_file=sys.argv[1]);window.open_serial=lambda **kw:True
    window.show();progress=Path(sys.argv[2]);control=progress.with_suffix('.control')
    def tick():
        command=control.read_text() if control.exists() else None
        if command=='crash': os._exit(7)
        if command=='close': window.close();return
        bridge=window.gui_cli_bridge
        value='active' if bridge.active and bridge.active.get('command')=='Delay:5000' else 'subscribed' if bridge.subscribers else 'ready'
        if not progress.exists() or progress.read_text()!=value: progress.write_text(value)
    timer=QTimer();timer.timeout.connect(tick);timer.start(10)
    raise SystemExit(app.exec_())
'''
        terminal = r'''
import json,sys
sys.__stderr__.reconfigure(encoding='utf-8')
from pathlib import Path
from unittest import mock
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.application import create_app_session
from core.cli_console import ConsoleOutput,GuiConsoleBackend
original=ConsoleOutput.emit
def emit(self,event):
    print('EVENT:'+json.dumps(event,ensure_ascii=False),file=sys.__stderr__,flush=True)
    return original(self,event)
active=sys.argv.pop(1)=='active'
original_open=GuiConsoleBackend.open_initial
def open_initial(self):
    original_open(self)
    Path(sys.argv[-1]).with_suffix('.opened').touch()
    if active: self.submit('Delay:5000')
with create_pipe_input() as pipe, create_app_session(input=pipe,output=DummyOutput()):
    session=PromptSession(input=pipe,output=DummyOutput())
    with mock.patch('prompt_toolkit.PromptSession',return_value=session),mock.patch.object(ConsoleOutput,'emit',emit),mock.patch.object(GuiConsoleBackend,'open_initial',open_initial):
        from cli_main import main
        main()
'''
        for ending in ("close", "crash"):
            for active in (False, True):
                with self.subTest(ending=ending, active=active), tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
                    config, progress = Path(folder) / "config.json", Path(folder) / "state.txt"
                    logs = {name: stack.enter_context((Path(folder) / name).open("wb"))
                            for name in ("gui.out", "gui.err", "cli.out", "cli.err")}
                    processes = []
                    try:
                        gui = subprocess.Popen([sys.executable, "-c", program, str(config), str(progress)], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=logs["gui.out"], stderr=logs["gui.err"],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        processes.append(gui)
                        pump_until(lambda: progress.exists(), 5)
                        cli = subprocess.Popen([sys.executable, "-c", terminal, "active" if active else "idle", "--via-gui", "--console", "--config", str(config)],
                            cwd=ROOT, stdin=subprocess.PIPE, stdout=logs["cli.out"], stderr=logs["cli.err"],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        processes.append(cli)
                        pump_until(lambda: config.with_suffix('.opened').exists() and progress.read_text() == ("active" if active else "subscribed"), 5)
                        progress.with_suffix('.control').write_text(ending)
                        pump_until(lambda: cli.poll() is not None, 5)
                        self.assertEqual(cli.returncode, 2)
                        output, errors = (Path(folder) / "cli.out").read_bytes(), (Path(folder) / "cli.err").read_bytes()
                        events = [json.loads(line[6:]) for line in errors.decode("utf-8").splitlines() if line.startswith("EVENT:")]
                        fatal = [e for e in events if e.get("fatal")]
                        self.assertEqual(len(fatal), 1, (output, errors))
                        self.assertIn("GUI", fatal[0]["message"])
                        self.assertEqual(gui.wait(timeout=2), 0 if ending == "close" else 7)
                    except AssertionError as exc:
                        raise AssertionError(f"{exc}; CLI stderr={(Path(folder) / 'cli.err').read_bytes()!r}; GUI stderr={(Path(folder) / 'gui.err').read_bytes()!r}") from exc
                    finally:
                        for process in reversed(processes):
                            if process.poll() is None:
                                process.kill(); process.wait()
                            for stream in (process.stdin, process.stdout, process.stderr):
                                if stream: stream.close()

    def test_fatal_between_loop_check_and_prompt_start_exits_without_input(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.application import create_app_session
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            session = PromptSession(input=pipe, output=DummyOutput())
            real_prompt, backend, events = session.prompt, {}, []
            original_emit = console_check.ConsoleOutput.emit
            def emit(instance, event):
                events.append(event)
                return original_emit(instance, event)
            def start(instance):
                backend["emit"] = instance.emit
                return dict(state=state())
            def prompt(*args, **kwargs):
                backend["emit"](dict(source="error", fatal=True, message="lost-before-prompt"))
                return real_prompt(*args, **kwargs)
            with mock.patch("prompt_toolkit.PromptSession", return_value=session), \
                 mock.patch.object(GuiConsoleBackend, "start", autospec=True, side_effect=start), \
                 mock.patch.object(GuiConsoleBackend, "open_initial"), \
                 mock.patch.object(console_check.ConsoleOutput, "emit", autospec=True, side_effect=emit), \
                 mock.patch.object(session, "prompt", side_effect=prompt), \
                 contextlib.redirect_stdout(io.StringIO()) as captured:
                code = run_console(build_parser().parse_args(["--console", "--via-gui"]), "unused.json", "0", "today")
            self.assertEqual(code, 2)
            self.assertEqual(sum(e.get("message") == "lost-before-prompt" for e in events), 1, captured.getvalue())


class RemoteDisconnectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = console_check.GuiConsoleTests()
        self.fixture.setUp()
        self.window, self.bridge = self.fixture.window, self.fixture.bridge

    def tearDown(self):
        self.fixture.tearDown()

    def test_remote_error_shared_once_and_pending_response_is_unknown(self):
        subscription = self.fixture.request("subscribe")
        self.window.remote_mode = "client"
        self.window.remote_client_connected = True
        self.window.remote_thread = mock.Mock(session="session")
        request = console_check.Peer()
        self.bridge.connections[request] = bytearray()
        self.bridge.active = dict(id="a" * 32, action="send", socket=request,
                                 matcher=None, read_timeout=5000)
        self.window.on_remote_error("remote-link-lost", fatal=True)
        events = subscription.events
        shared = [event for event in events if event.get("message") == "错误: remote-link-lost"]
        self.assertEqual(len(shared), 1)
        record = next(r for r in self.window.output_manager.records if r.text.rstrip() == shared[0]["message"])
        self.assertEqual(record.source_type, OutputSource.ERROR)
        self.assertEqual(record.timestamp, shared[0]["timestamp"])
        forwarded = [e for e in request.events if e.get("message") == shared[0]["message"]]
        self.assertEqual([(e["message"], e["timestamp"]) for e in forwarded],
                         [(e["message"], e["timestamp"]) for e in shared])
        self.assertEqual(request.events[-1]["status"], "unknown")
        self.assertFalse(request.events[-1]["success"])
        self.assertIsNone(self.bridge.active)

    def test_remote_loss_reaches_real_one_shot_cli_then_exits_unknown(self):
        self.window.remote_mode = "client"
        self.window.remote_thread = mock.Mock(session="session")
        with mock.patch.object(self.window, "send_command", return_value=True):
            process = self.fixture.fixture.launch("--send", "AT", "--expect", "COMPLETE", "--read-timeout", "5000")
            pump_until(lambda: self.bridge.active is not None)
            self.window.on_data_received(b"PARTIAL")
            self.window.on_remote_error("one-shot-remote-loss", fatal=True)
            code, events = self.fixture.fixture.collect(process)
        self.assertEqual(code, 3)
        self.assertEqual(sum(e.get("message") == "错误: one-shot-remote-loss" for e in events), 1)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertFalse(events[-1]["expect_matched"])

    def test_one_shot_gui_bridge_close_returns_unknown(self):
        process = self.fixture.fixture.launch("--send", "Delay:5000")
        pump_until(lambda: self.bridge.active is not None)
        self.bridge.close()
        code, events = self.fixture.fixture.collect(process)
        self.assertEqual(code, 3)
        self.assertEqual(events[-1]["status"], "unknown")

    def test_server_peer_loss_is_shared_error_and_local_stop_is_not(self):
        subscription = self.fixture.request("subscribe")
        server = RemoteControlServer("127.0.0.1", 0)
        self.window.remote_thread, self.window.remote_mode = server, "server"
        server.error_occurred.connect(self.window.on_remote_error)
        server.connected_changed.connect(self.window.on_remote_connected_changed)
        server.start()
        try:
            pump_until(lambda: server.listening)
            with socket.create_connection(("127.0.0.1", server.port), timeout=1):
                pump_until(lambda: self.window.remote_client_connected)
            pump_until(lambda: any(e.get("source") == "error" for e in subscription.events))
            self.assertEqual(sum(e.get("source") == "error" for e in subscription.events), 1)
            self.assertTrue(server.listening)
            self.window.stop_remote_control()
            pump_until(lambda: not server.isRunning())
            self.assertEqual(sum(e.get("source") == "error" for e in subscription.events), 1)
        finally:
            server.stop()
            server.deleteLater()

    def test_remote_loss_cancels_console_module_before_next_write(self):
        self.window.command_table.setRowCount(0)
        for text in ("Mode:STOP", "Delay:5000", "MUST_NOT_SEND", "ModeEnd:0"):
            self.window.command_table.add_command_row(True, text, "")
        self.window.refresh_modules(silent=True)
        peer = self.fixture.request(command="SendMode:STOP")
        self.window.remote_mode = "client"
        self.window.remote_thread = mock.Mock(session="session")
        self.window.stop_remote_control()
        pump_until(lambda: self.bridge.active is None)
        self.assertFalse(peer.events[-1]["success"])
        self.assertEqual(self.fixture.writes, [])

    def test_intentional_remote_stop_ends_pending_request_without_transport_error(self):
        subscription = self.fixture.request("subscribe")
        self.window.remote_mode = "client"
        self.window.remote_thread = mock.Mock(session="session")
        self.bridge.active = dict(id="b" * 32, action="send", socket=subscription, matcher=None)
        self.window.stop_remote_control()
        self.assertEqual(subscription.events[-1]["status"], "unknown")
        self.assertFalse(any(e.get("remote") and e.get("source") == "error" for e in subscription.events))


if __name__ == "__main__":
    unittest.main()
