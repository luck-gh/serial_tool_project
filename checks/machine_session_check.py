"""机器会话全路径回归:真实 CLI 子进程与隔离 GUI IPC,禁止物理串口."""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest
import uuid

from unittest import mock
from PyQt5.QtCore import QTimer
import console_check
from console_check import Peer
from remote_download_check import pump_until

ROOT = Path(__file__).resolve().parents[1]


class MachineProcessTests(unittest.TestCase):
    def setUp(self):
        self.port_guard = mock.patch("serial.tools.list_ports.comports", return_value=[])
        self.port_guard.start()
        self.fixture = console_check.GuiConsoleTests()
        self.fixture.setUp()
        self.window, self.bridge = self.fixture.window, self.fixture.bridge
        self.processes = []

    def tearDown(self):
        for process, events, errors in self.processes:
            if process.poll() is None:
                process.stdin.close()
                pump_until(lambda: process.poll() is not None, 5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        self.fixture.tearDown()
        self.port_guard.stop()

    def start(self):
        candidate = os.environ.get("GHOWE_MACHINE_CANDIDATE")
        entry = [candidate, "--cli"] if candidate else [sys.executable, "cli_main.py"]
        process = subprocess.Popen(entry + ["--via-gui", "--console", "--machine", "--config",
                                             str(Path(self.fixture.fixture.temp.name) / "bridge_test_config.json")],
                                   cwd=ROOT, env=dict(os.environ, PYTHONIOENCODING="cp936"),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        events, errors = [], []
        def read_events():
            for raw in process.stdout:
                try:
                    events.append(json.loads(raw.decode("utf-8")))
                except Exception as exc:
                    errors.append((str(exc), raw))
        def read_errors():
            for raw in process.stderr:
                errors.append(raw)
        threading.Thread(target=read_events, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()
        self.processes.append((process, events, errors))
        try:
            self.wait(events, "ready")
        except AssertionError as exc:
            raise AssertionError(f"startup failed: events={events!r}, errors={errors!r}, exit={process.poll()}") from exc
        self.assertEqual(events[0]["event"], "ready", (events, errors))
        return process, events, errors

    def wait(self, events, kind, request_id=None):
        pump_until(lambda: any(e.get("event") == kind and (request_id is None or e.get("id") == request_id)
                               for e in events), 6)
        return next(e for e in reversed(events) if e.get("event") == kind
                    and (request_id is None or e.get("id") == request_id))

    def send(self, process, command=None, request_id=None, close=False):
        request_id = request_id or uuid.uuid4().hex
        frame = dict(action="close-window", id=request_id) if close else dict(id=request_id, command=command)
        process.stdin.write(json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n")
        process.stdin.flush()
        return request_id

    def finish(self, process, events):
        process.stdin.close()
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None, 5)
        return process.returncode

    def test_fragmented_rx_caller_id_close_boundary_and_same_process(self):
        original_write = self.window.serial_thread.write_data
        def write(data):
            original_write(data)
            response = "中文OK".encode("utf-8")
            QTimer.singleShot(0, lambda: self.window.on_data_received(response[:1]))
            QTimer.singleShot(8, lambda: self.window.on_data_received(response[1:4]))
            QTimer.singleShot(16, lambda: self.window.on_data_received(response[4:]))
            return len(data)
        self.window.serial_thread.write_data = write
        process, events, errors = self.start()
        pid = process.pid
        self.window.on_data_received(b"ambient-before")
        self.wait(events, "receive")
        self.assertEqual(events[-1]["scope"], "ambient")
        ids = []
        for command in ("first", "second"):
            rid = self.send(process, command)
            ids.append(rid)
            result = self.wait(events, "result", rid)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["completion"], "transport")
            pump_until(lambda: b"".join(base64.b64decode(e["data"]) for e in events
                                       if e.get("event") == "receive" and e.get("scope_id") == rid) == "中文OK".encode(), 3)
            rx = [e for e in events if e.get("event") == "receive" and e.get("scope_id") == rid]
            self.assertTrue(all(e["task_id"] in (rid, None) for e in rx), rx)
            self.send(process, request_id=rid, close=True)
            ack = self.wait(events, "window_closed", rid)
            self.assertEqual(ack["rx_sequence"], max(e["rx_sequence"] for e in rx))
            self.assertNotIn(rid, self.bridge.results)
            self.assertEqual(process.pid, pid)
        self.window.on_data_received(b"ambient-after")
        pump_until(lambda: any(e.get("data") == base64.b64encode(b"ambient-after").decode() for e in events), 3)
        self.assertEqual(events[-1]["scope"], "ambient")
        self.assertEqual(self.finish(process, events), 0)
        self.assertFalse(errors, errors)
        self.assertEqual([e["id"] for e in events if e.get("event") == "result"], ids)
        self.assertEqual(self.fixture.writes, [b"first\r\n", b"second\r\n"])

    def test_failure_stops_pipelined_next_and_no_response_is_not_device_success(self):
        process, events, errors = self.start()
        rid = self.send(process, "SendHex:GG")
        self.send(process, "must-not-send")
        result = self.wait(events, "result", rid)
        self.assertFalse(result["success"])
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None, 5)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.fixture.writes, [])
        self.assertEqual(sum(e.get("event") == "result" and e.get("id") == rid for e in events), 1)

    def test_eof_active_does_not_cancel_or_forge_result(self):
        self.window.command_table.setRowCount(0)
        for row in ([True, "Mode:A", ""], [True, "Delay:400", ""],
                    [True, "after-eof", ""], [True, "ModeEnd:0", ""]):
            self.window.command_table.add_command_row(*row)
        self.window.refresh_modules(silent=True)
        process, events, errors = self.start()
        rid = self.send(process, "SendMode:A")
        self.wait(events, "accepted", rid)
        process.stdin.close()
        end = self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
        self.assertEqual(end["status"], "unknown")
        self.assertEqual(end["pending_id"], rid)
        self.assertFalse(any(e.get("event") == "result" and e.get("id") == rid for e in events), events)
        self.assertEqual(self.fixture.writes, [b"after-eof\r\n"])

    def test_closed_stdout_exits_with_stdin_open_without_cancelling_gui_task(self):
        self.window.command_table.setRowCount(0)
        for row in ([True, "Mode:A", ""], [True, "Delay:300", ""],
                    [True, "after-stdout-loss", ""], [True, "ModeEnd:0", ""]):
            self.window.command_table.add_command_row(*row)
        self.window.refresh_modules(silent=True)
        for close_stderr in (False, True):
            with self.subTest(close_stderr=close_stderr):
                process = subprocess.Popen([sys.executable, str(ROOT / "cli_main.py"), "--via-gui", "--console", "--machine", "--config",
                    str(Path(self.fixture.fixture.temp.name) / "bridge_test_config.json")], cwd=ROOT,
                    env=dict(os.environ, PYTHONIOENCODING="cp936"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.processes.append((process, [], []))
                ready = []
                reader = threading.Thread(target=lambda: ready.append(process.stdout.readline()))
                reader.start()
                pump_until(lambda: bool(ready), 5)
                reader.join()
                self.assertEqual(json.loads(ready[0].decode("utf-8"))["event"], "ready")
                process.stdout.close()
                if close_stderr:
                    process.stderr.close()
                self.send(process, "SendMode:A")
                pump_until(lambda: process.poll() is not None and self.bridge.active is None, 4)
                self.assertEqual(process.returncode, 2)
                self.assertFalse(process.stdin.closed)
                self.assertEqual(self.fixture.writes, [b"after-stdout-loss\r\n"] * (2 if close_stderr else 1))
                if not close_stderr:
                    errors = process.stderr.read()
                    self.assertEqual(errors.count(b"machine stdout failed:"), 1, errors)
                    self.assertNotIn(b"Traceback", errors)
                pump_until(lambda: not self.bridge.machine_subscribers, 3)

    def test_idle_disconnect_and_target_change_exit_without_input(self):
        for mutation in ("disconnect", "target"):
            with self.subTest(mutation=mutation):
                process, events, errors = self.start()
                if mutation == "disconnect":
                    self.window.is_connected = False
                else:
                    self.window.baud_combo.setCurrentText("9600")
                self.wait(events, "session_end")
                pump_until(lambda: process.poll() is not None, 5)
                self.assertNotEqual(process.returncode, 0)
                self.assertTrue(any(e.get("fatal") for e in events), events)
                self.window.is_connected = True
                self.window.baud_combo.setCurrentText("115200")
                pump_until(lambda: not self.bridge.machine_subscribers, 3)

    def test_module_target_change_blocked_and_foreign_task_rx_preserved(self):
        self.window.command_table.setRowCount(0)
        for row in ([True, "Mode:A", ""], [True, "BaudRate:9600", ""],
                    [True, "must-not-send", ""], [True, "ModeEnd:0", ""]):
            self.window.command_table.add_command_row(*row)
        self.window.refresh_modules(silent=True)
        process, events, errors = self.start()
        rid = self.send(process, "SendMode:A")
        result = self.wait(events, "result", rid)
        self.assertFalse(result["success"])
        self.wait(events, "session_end")
        self.assertEqual(self.window.baud_combo.currentText(), "115200")
        self.assertEqual(self.fixture.writes, [])

    def test_no_rx_result_leaves_window_unknown_and_other_bridge_task_rejected(self):
        process, events, errors = self.start()
        rid = self.send(process, "no-response")
        self.assertTrue(self.wait(events, "result", rid)["success"])
        self.assertFalse(any(e.get("event") == "receive" for e in events))
        peer = Peer()
        self.bridge.connections[peer] = bytearray()
        with self.assertRaisesRegex(ValueError, "窗口"):
            self.bridge.dispatch(peer, dict(id=uuid.uuid4().hex, action="console-send", command="foreign"))
        self.assertNotEqual(self.finish(process, events), 0)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertEqual(self.fixture.writes, [b"no-response\r\n"])

    def test_close_before_transport_result_stops_without_cancel(self):
        process, events, errors = self.start()
        rid = self.send(process, "Delay:400")
        self.wait(events, "accepted", rid)
        self.send(process, request_id=rid, close=True)
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
        self.assertEqual(self.bridge.results[rid]["status"], "success")
        self.assertNotEqual(process.returncode, 0)

    def test_download_exclusive_phase_and_restoration_gate(self):
        def download(parameter, completed):
            self.window.is_connected = False
            self.bridge.publish_state("download owns serial", connected=False)
            def finish():
                self.window.is_connected = True
                self.window._last_firmware_result = (True, "ACK and restored")
                completed(True)
            QTimer.singleShot(50, finish)
        self.window.start_firmware_download_command = download
        process, events, errors = self.start()
        rid = self.send(process, "FirmwareDownload:fixture.bin")
        self.assertTrue(self.wait(events, "result", rid)["success"], events)
        self.assertTrue(any(e.get("event") == "connection" and
                            e.get("connection", {}).get("phase") == "download_exclusive" for e in events))
        self.send(process, request_id=rid, close=True)
        self.wait(events, "window_closed", rid)
        self.assertEqual(self.finish(process, events), 0)



    def test_foreign_rx_keeps_actual_task_id_and_own_scope_separate(self):
        process, events, errors = self.start()
        rid = self.send(process, "no-response")
        self.wait(events, "result", rid)
        foreign = uuid.uuid4().hex
        previous = self.bridge.active
        self.bridge.active = dict(id=foreign, socket=Peer())
        try:
            self.bridge.receive(b"foreign")
        finally:
            self.bridge.active = previous
        pump_until(lambda: any(e.get("event") == "receive" and e.get("task_id") == foreign for e in events), 3)
        rx = next(e for e in events if e.get("event") == "receive" and e.get("task_id") == foreign)
        self.assertEqual(rx["scope_id"], rid)
        self.assertNotEqual(rx["task_id"], rid)
        self.assertEqual(base64.b64decode(rx["data"]), b"foreign")
        self.send(process, request_id=rid, close=True)
        ack = self.wait(events, "window_closed", rid)
        self.assertEqual(ack["rx_sequence"], rx["rx_sequence"])
        self.assertEqual(self.finish(process, events), 0)

    def test_download_missing_restoration_fails_and_stops_next(self):
        def download(parameter, completed):
            self.window.is_connected = False
            self.bridge.publish_state("exclusive", connected=False)
            self.window._last_firmware_result = (True, "download ACK only")
            QTimer.singleShot(20, lambda: completed(True))
        self.window.start_firmware_download_command = download
        process, events, errors = self.start()
        rid = self.send(process, "FirmwareDownload:fixture.bin")
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
        self.assertTrue(any(e.get("event") == "result" and e.get("id") == rid
                            and e.get("success") is False for e in events), events)
        self.assertEqual(self.fixture.writes, [])
        self.window.is_connected = True

    def test_gui_bridge_loss_active_exits_with_unknown_once(self):
        process, events, errors = self.start()
        rid = self.send(process, "Delay:400")
        self.wait(events, "accepted", rid)
        self.bridge.close()
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None, 5)
        results = [e for e in events if e.get("event") == "result" and e.get("id") == rid]
        self.assertEqual(len(results), 1, events)
        self.assertEqual(results[0]["status"], "unknown")
        self.assertIn("target", results[0])
        self.assertIn("connection", results[0])
        self.assertNotEqual(process.returncode, 0)
        # 窗口已关闭,生产层不再投递finished;仅清理已退出夹具线程的残留引用.
        worker = self.bridge.active.get("thread") if self.bridge.active else None
        if worker:
            pump_until(lambda: not worker.is_alive(), 3)
        self.bridge.active = None



    def test_download_exclusive_does_not_hide_remote_link_loss(self):
        self.window.remote_mode = "client"
        self.window.remote_client_connected = True
        self.window.remote_serial_connected = True
        def download(parameter, completed):
            self.window.remote_serial_connected = False
            def lose():
                self.window.remote_client_connected = False
                self.bridge.publish_state("remote lost", error=True, remote=True, connected=False)
                self.window._last_firmware_result = (False, "结果未知: remote lost")
                completed(False)
            QTimer.singleShot(20, lose)
        self.window.start_firmware_download_command = download
        process, events, errors = self.start()
        rid = self.send(process, "FirmwareDownload:fixture.bin")
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
        self.assertTrue(any(e.get("fatal") and e.get("connection", {}).get("network_connected") is False
                            for e in events), events)
        self.assertEqual(sum(e.get("event") == "result" and e.get("id") == rid for e in events), 1)
        self.window.remote_mode = "off"
        self.window.remote_client_connected = False
        self.window.remote_serial_connected = False



    def test_active_module_target_failure_prevents_later_write_after_cli_detach(self):
        self.window.command_table.setRowCount(0)
        for row in ([True, "Mode:A", ""], [True, "Delay:300", ""],
                    [True, "must-not-send", ""], [True, "ModeEnd:0", ""]):
            self.window.command_table.add_command_row(*row)
        self.window.refresh_modules(silent=True)
        process, events, errors = self.start()
        rid = self.send(process, "SendMode:A")
        self.wait(events, "accepted", rid)
        self.window.baud_combo.setCurrentText("9600")
        self.wait(events, "session_end")
        pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
        self.assertEqual(self.fixture.writes, [])
        self.assertEqual(sum(e.get("event") == "result" and e.get("id") == rid for e in events), 1)
        self.assertEqual(self.bridge.results[rid]["status"], "unknown")



    def test_eof_then_target_or_link_change_still_guards_accepted_module(self):
        for mutation in ("target", "link"):
            with self.subTest(mutation=mutation):
                if mutation == "link":
                    self.window.remote_mode = "client"
                    self.window.remote_client_connected = True
                    self.window.remote_serial_connected = True
                self.window.command_table.setRowCount(0)
                for row in ([True, "Mode:A", ""], [True, "Delay:1900", ""],
                            [True, "must-not-send", ""], [True, "ModeEnd:0", ""]):
                    self.window.command_table.add_command_row(*row)
                self.window.refresh_modules(silent=True)
                process, events, errors = self.start()
                rid = self.send(process, "SendMode:A")
                self.wait(events, "accepted", rid)
                process.stdin.close()
                pump_until(lambda: not self.bridge.machine_subscribers, 4)
                self.assertIsNotNone(self.bridge.active)
                if mutation == "target":
                    self.window.baud_combo.setCurrentText("9600")
                else:
                    self.window.remote_client_connected = False
                self.wait(events, "session_end")
                pump_until(lambda: process.poll() is not None and self.bridge.active is None, 5)
                self.assertEqual(self.fixture.writes, [])
                self.assertEqual(self.bridge.results[rid]["status"], "unknown")
                self.window.remote_mode = "off"
                self.window.remote_client_connected = False
                self.window.remote_serial_connected = False
                self.window.baud_combo.setCurrentText("115200")

    def test_only_own_subscription_may_observe_download_exclusive(self):
        process, events, errors = self.start()
        peer = Peer()
        second = uuid.uuid4().hex
        self.bridge.connections[peer] = bytearray()
        self.bridge.dispatch(peer, dict(id=second, action="subscribe", machine_protocol=1))
        def download(parameter, completed):
            self.window.is_connected = False
            self.bridge.publish_state("owned download", connected=False)
            def finish():
                self.window.is_connected = True
                self.window._last_firmware_result = (True, "restored")
                completed(True)
            QTimer.singleShot(30, finish)
        self.window.start_firmware_download_command = download
        rid = self.send(process, "FirmwareDownload:fixture.bin")
        self.assertTrue(self.wait(events, "result", rid)["success"])
        self.assertTrue(any(e.get("fatal") and e.get("connection", {}).get("phase") == "connected"
                            for e in peer.events), peer.events)
        self.send(process, request_id=rid, close=True)
        self.wait(events, "window_closed", rid)
        self.assertEqual(self.finish(process, events), 0)
        self.bridge.disconnected(peer)


if __name__ == "__main__":
    unittest.main()
