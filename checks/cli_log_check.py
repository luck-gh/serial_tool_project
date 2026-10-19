"""新日志CLI契约:临时文件、假设备及真实GUI IPC,不接触真实配置/硬件."""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.cli_log import CliLog, ThreadArchive
from core.cli_console import ConsoleOutput, GuiConsoleBackend
from core.cli_runner import CliOutput, build_parser, run_cli, execute_payload
from core.command_parser import Command
from core.command_types import OutputSource as Source
from core.log_settings import settings_from_state, apply_local
from managers.config_manager import ConfigManager
import gui_cli_bridge_check
from remote_download_check import pump_until


STAMP = "2026-10-20T01:02:03.456+08:00"


class CliLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.manager = ConfigManager("0.0.0", "N/A", str(self.folder / "config.json"))
        self.state = dict(receive_settings=dict(show_timestamp=False,
            display_sources={s.value: False for s in Source}), send_settings=dict(show_send=False),
            log_settings=settings_from_state({}))
        self.state["log_settings"].update(save_full_log=True, log_directory=str(self.folder / "logs"))
        self.manager.config["state"] = self.state
        self.manager.save_config()

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = run_cli(build_parser().parse_args(["--config", self.manager.config_file, *arguments]), "test", "0.0.0", "N/A")
        return code, output.getvalue()

    def test_one_shot_hidden_tx_is_still_logged_and_tail_completes(self):
        class Device:
            in_waiting = 0
            def write(self, data): return len(data)
            def flush(self): pass
        output = CliOutput(self.state)
        with contextlib.redirect_stdout(io.StringIO()) as screen:
            execute_payload(Device(), b"TX", 0, output, "TX")
        self.assertEqual(screen.getvalue(), "")
        self.assertEqual(output.log.archive.committed_sequence, -1)
        output.close()
        self.assertIn("TX", output.log.archive.path.read_text(encoding="utf-8"))
        self.assertGreater(output.log.archive.committed_sequence, -1)

    def test_timestamp_display_and_capture_are_independent(self):
        for show, keep in ((False, True), (True, False)):
            state = copy.deepcopy(self.state)
            state["receive_settings"]["show_timestamp"] = show
            state["receive_settings"]["display_sources"]["system"] = True
            state["log_settings"]["keep_timestamp"] = keep
            output = ConsoleOutput()
            output.configure(state)
            with contextlib.redirect_stdout(io.StringIO()) as screen:
                output.emit(dict(source="system", message="fixed", timestamp=STAMP))
            output.close()
            self.assertEqual("01:02:03" in screen.getvalue(), show)
            self.assertEqual("01:02:03" in output.log_service.archive.path.read_text(encoding="utf-8"), keep)

    def test_send_or_rules_and_colors_for_all_base_combinations(self):
        for shortcut in (False, True):
            for display in (False, True):
                for capture in (False, True):
                    state = copy.deepcopy(self.state)
                    state["send_settings"].update(show_send=shortcut, send_color="蓝色")
                    state["receive_settings"]["display_sources"]["send"] = display
                    state["log_settings"].update(retain_all=False, keep_timestamp=False)
                    state["log_settings"]["sources"]["send"] = capture
                    output = ConsoleOutput()
                    output.configure(state)
                    with contextlib.redirect_stdout(io.StringIO()) as screen:
                        output.emit(dict(source="send", message="TX"))
                    output.close()
                    self.assertEqual("TX" in screen.getvalue(), shortcut or display)
                    if shortcut or display:
                        self.assertIn("\033[34m", screen.getvalue())
                    path = output.log_service.archive.path
                    stored = path.read_text(encoding="utf-8") if path.exists() else ""
                    self.assertEqual("TX" in stored, shortcut or capture)

    def test_selective_levels_ignore_obsolete_save_sources(self):
        self.state["log_settings"].update(retain_all=False, keep_timestamp=False)
        self.state["log_settings"]["system_levels"]["debug"] = False
        self.state["receive_settings"].update(save_sources={s.value: False for s in Source}, save_system_levels={"normal": False})
        output = ConsoleOutput()
        output.configure(self.state)
        with contextlib.redirect_stdout(io.StringIO()):
            output.emit(dict(source="system", system_level="debug", message="excluded"))
            output.emit(dict(source="system", system_level="normal", message="included"))
        output.close()
        stored = output.log_service.archive.path.read_text(encoding="utf-8")
        self.assertIn("included", stored)
        self.assertNotIn("excluded", stored)

    def test_atomic_apply_failure_leaves_memory_and_disk_unchanged(self):
        before, content = copy.deepcopy(self.manager.config), Path(self.manager.config_file).read_bytes()
        with patch("managers.config_manager.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                apply_local(self.manager, {"limit_mb": 8})
        self.assertEqual(self.manager.config, before)
        self.assertEqual(Path(self.manager.config_file).read_bytes(), content)
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_log_query_check_apply_and_invalid_values(self):
        before = Path(self.manager.config_file).read_bytes()
        patch_file = self.folder / "patch.json"
        patch_file.write_text('{"limit_mb":8,"keep_timestamp":false}', encoding="utf-8")
        self.assertEqual(self.call("--log-action", "get")[0], 0)
        self.assertEqual(self.call("--log-action", "check", "--log-patch", str(patch_file))[0], 0)
        self.assertEqual(Path(self.manager.config_file).read_bytes(), before)
        self.assertEqual(self.call("--log-action", "apply", "--log-patch", str(patch_file))[0], 0)
        self.assertEqual(json.loads(Path(self.manager.config_file).read_text(encoding="utf-8"))["state"]["log_settings"]["limit_mb"], 8)
        for value in ('{"limit_mb":0}', '{"keep_timestamp":null}', '{"sources":{"typo":true}}', '{"save_sources":{}}'):
            patch_file.write_text(value, encoding="utf-8")
            self.assertNotEqual(self.call("--log-action", "apply", "--log-patch", str(patch_file))[0], 0)
        self.assertNotEqual(self.call("--log-action", "recover", "--log-recover", str(self.folder / "new.log"))[0], 0)
        self.assertNotEqual(self.call("--log-patch", str(patch_file))[0], 0)

    def test_real_cli_subprocess_query_is_pure_json(self):
        result = subprocess.run([sys.executable, str(ROOT / "cli_main.py"), "--config", self.manager.config_file,
            "--log-action", "get"], capture_output=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertTrue(json.loads(result.stdout)["success"])

    def test_query_and_check_never_create_migrate_or_repair_config(self):
        target = Path(self.manager.config_file)
        target.write_text('{"state":{}}', encoding="utf-8")
        before = target.read_bytes()
        self.assertEqual(self.call("--log-action", "get")[0], 0)
        self.assertEqual(target.read_bytes(), before)
        target.write_text('{broken', encoding="utf-8")
        self.assertEqual(self.call("--log-action", "get")[0], 1)
        self.assertEqual(target.read_text(), '{broken')
        target.unlink()
        self.assertEqual(self.call("--log-action", "get")[0], 1)
        self.assertFalse(target.exists())

    def test_malformed_log_objects_return_json_failures_without_file_changes(self):
        target = Path(self.manager.config_file)
        for logs in (None, [], {"sources": []}, {"system_levels": None}, {"limit_mb": "4"}):
            target.write_text(json.dumps({"state": {"log_settings": logs}}), encoding="utf-8")
            before = target.read_bytes()
            code, text = self.call("--log-action", "get")
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(text)["success"])
            self.assertEqual(target.read_bytes(), before)

    def test_log_management_names_never_reach_device_entries(self):
        from core.cli_runner import build_payload
        for name in ("LogGet", "LogStatus", "LogCheck", "LogApply", "LogRecover"):
            with self.assertRaises(ValueError):
                build_payload(name, self.manager)

    def test_partial_write_failure_recovery_and_existing_target_rejected(self):
        archive = ThreadArchive(self.folder / "failed.log", 10000)
        archive.append(1, "unconfirmed")
        def partial(path, entries, create):
            path.write_bytes(b"part")
            raise OSError("partial injected")
        with patch("core.cli_log.write_batch", side_effect=partial):
            with self.assertRaisesRegex(OSError, "partial injected"):
                archive.drain()
        self.assertEqual(archive.committed_sequence, -1)
        self.assertEqual(archive.unsaved_entries(), ((1, "unconfirmed"),))
        archive.recover_to(self.folder / "failed.log")
        with self.assertRaises(OSError):
            archive.drain()
        self.assertEqual((self.folder / "failed.log").read_bytes(), b"part")
        archive.recover_to(self.folder / "recovered.log")
        archive.drain()
        self.assertEqual(archive.committed_sequence, 1)
        self.assertIsNone(archive._result)
        self.assertEqual((self.folder / "recovered.log").read_text(), "unconfirmed")

    def test_threshold_background_bounded_large_event_and_stop(self):
        archive = ThreadArchive(self.folder / "slow.log", 10000)
        entered, release = threading.Event(), threading.Event()
        from core.cli_log import write_batch
        def slow(*args):
            entered.set()
            release.wait(3)
            write_batch(*args)
        with patch("core.cli_log.write_batch", side_effect=slow):
            archive.append(1, "x" * 7000)
            self.assertTrue(entered.wait(1))
            self.assertEqual(archive.committed_sequence, -1)
            try:
                self.assertFalse(archive.append(2, "中" * 100000))
                self.assertLessEqual(archive.pending_bytes, archive.limit)
                self.assertIn("未收录", archive.error)
            finally:
                release.set()
                with self.assertRaises(OSError):
                    archive.drain()
        self.assertEqual(archive.committed_sequence, 1)
        archive.recover_to(self.folder / "slow-recovered.log")
        archive.drain()

    def test_tail_failure_changes_one_shot_exit_and_no_success_result(self):
        with patch("core.cli_log.write_batch", side_effect=OSError("tail failed")):
            code, text = self.call("--send", "SetEndlog:rn")
        self.assertEqual(code, 1)
        results = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
        self.assertFalse(results[-1]["success"])
        self.assertIn("tail failed", results[-1]["message"])

    def test_console_apply_check_state_status_recover_and_log_off(self):
        output = ConsoleOutput()
        output.configure(self.state)
        with contextlib.redirect_stdout(io.StringIO()):
            output.local_log(Command("LogCheck", '{"keep_timestamp":false}'), self.manager)
            self.assertTrue(output.state["log_settings"]["keep_timestamp"])
            output.local_log(Command("LogApply", '{"keep_timestamp":false}'), self.manager)
            self.assertFalse(output.log_service.settings["keep_timestamp"])
            output.emit(dict(source="system", message="tail"))
            with patch("core.cli_log.write_batch", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    output.local_command(Command("Log", "off"))
            output.local_log(Command("LogStatus", ""), self.manager)
            output.local_log(Command("LogRecover", str(self.folder / "console-recovered.log")), self.manager)
            output.local_command(Command("Log", "off"))
        output.close()
        self.assertIn("tail", (self.folder / "console-recovered.log").read_text(encoding="utf-8"))

    def test_bridge_console_never_automatically_creates_second_archive(self):
        output = ConsoleOutput()
        output.configure(self.state, automatic=False)
        with contextlib.redirect_stdout(io.StringIO()):
            output.emit(dict(source="system", message="GUI owns automatic archive"))
            output.emit(dict(event="result", success=True, log_applied=True, log_settings=self.state["log_settings"]))
        self.assertIsNone(output.log_service.archive)
        output.close()
        self.assertFalse((self.folder / "logs").exists())

    def test_remote_preference_sync_keeps_failed_explicit_log_recoverable(self):
        output = ConsoleOutput()
        output.configure(self.state, automatic=False)
        with contextlib.redirect_stdout(io.StringIO()):
            output.local_command(Command("Log", str(self.folder / "explicit.log")))
            output.emit(dict(source="system", message="pending_local"))
            with patch("core.cli_log.write_batch", side_effect=OSError("local disk failed")):
                with self.assertRaises(OSError):
                    output.log_service.close()
            incoming = copy.deepcopy(self.state["log_settings"])
            incoming.update(keep_timestamp=False, log_directory=str(self.folder / "gui-only"))
            output.emit(dict(event="log-settings", log_settings=incoming,
                             send_settings=dict(show_send=True, send_color="绿色")))
            self.assertFalse(output.fatal.is_set())
            self.assertIsNotNone(output.log_service.archive.error)
            output.local_log(Command("LogRecover", str(self.folder / "local-recovered.log")))
        output.close()
        self.assertIn("pending_local", (self.folder / "local-recovered.log").read_text(encoding="utf-8"))


class GuiLogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gui_cli_bridge_check.GuiCliBridgeTests()
        self.fixture.setUp()
        self.window = self.fixture.window
        self.folder = Path(self.fixture.temp.name)

    def tearDown(self):
        archive = self.window.output_manager.archive
        if archive:
            pump_until(lambda: not archive.busy)
            if archive.error:
                archive.recover_to(self.folder / "cleanup.log")
                pump_until(lambda: not archive.busy)
        self.fixture.tearDown()

    def action(self, action, *args):
        return self.fixture.collect(self.fixture.launch("--log-action", action, *args))

    def test_real_ipc_config_transactions_status_and_failed_save(self):
        patch_file = self.folder / "log-patch.json"
        patch_file.write_text('{"retain_all":false,"limit_mb":7}', encoding="utf-8")
        self.assertEqual(self.action("check", "--log-patch", str(patch_file))[0], 0)
        self.assertTrue(self.window.log_settings["retain_all"])
        self.assertEqual(self.action("apply", "--log-patch", str(patch_file))[0], 0)
        self.assertFalse(self.window.log_settings["retain_all"])
        patch_file.write_text('{"limit_mb":8}', encoding="utf-8")
        with patch.object(self.window.config_manager, "save_config", side_effect=OSError("save failed")):
            self.assertEqual(self.action("apply", "--log-patch", str(patch_file))[0], 1)
        self.assertEqual(self.window.log_settings["limit_mb"], 7)
        code, events = self.action("status")
        self.assertEqual(code, 0)
        self.assertIsNone(events[-1]["log_status"]["archive"])

    def test_subscribed_terminal_follows_successful_gui_log_transaction_only(self):
        from core import gui_config
        state = gui_config.snapshot(self.window)["state"]
        output = ConsoleOutput()
        output.configure(state, automatic=False)
        backend = GuiConsoleBackend(self.window.config_manager.config_file, output.emit)
        with contextlib.redirect_stdout(io.StringIO()):
            backend.thread.start()
            pump_until(backend.ready.is_set)
            self.assertIsNone(backend.error)
            output.local_command(Command("Timestamp", "on"))
            output.local_command(Command("Log", str(self.folder / "terminal.log")))
            try:
                gui_config.apply(self.window, {"state": {"log_settings": {"retain_all": False,
                    "keep_timestamp": False, "sources": {"system": False}}, "send_settings": {"show_send": True}}})
                pump_until(lambda: not output.state["log_settings"]["retain_all"])
                self.assertTrue(output.state["receive_settings"]["show_timestamp"])
                self.assertTrue(output.state["send_settings"]["show_send"])
                self.window.gui_cli_bridge.publish_state("excluded_after_gui_apply")
                with patch.object(self.window.config_manager, "save_config", side_effect=OSError("failed")):
                    with self.assertRaises(OSError):
                        gui_config.apply(self.window, {"state": {"log_settings": {"retain_all": True}}})
                self.assertFalse(output.state["log_settings"]["retain_all"])
            finally:
                backend.close()
                output.close()
        self.assertNotIn("excluded_after_gui_apply", (self.folder / "terminal.log").read_text(encoding="utf-8"))

    def test_real_ipc_recovery_waits_for_actual_write_and_reports_failures(self):
        from core import gui_config
        gui_config.apply(self.window, {"state": {"log_settings": {"save_full_log": True, "log_directory": str(self.folder / "logs")}}})
        self.window.output_manager.append_text("unconfirmed", Source.SYSTEM)
        archive = self.window.output_manager.archive
        with patch("core.archive_buffer.os.fsync", side_effect=OSError("disk failed")):
            archive.flush(force=True)
            pump_until(lambda: not archive.busy)
        code, events = self.action("status")
        self.assertEqual(code, 0)
        self.assertIn("disk failed", events[-1]["log_status"]["archive"]["error"])
        target = self.folder / "recovered.log"
        code, events = self.action("recover", "--log-recover", str(target))
        self.assertEqual(code, 0)
        self.assertFalse(events[-1]["log_status"]["archive"]["busy"])
        self.assertIn("unconfirmed", target.read_text(encoding="utf-8"))
        self.assertFalse(archive.unsaved_entries())
        self.assertEqual(self.action("recover", "--log-recover", str(self.folder / "unused.log"))[0], 1)

    def test_gui_recovery_failure_and_concurrent_attempt_keep_unconfirmed(self):
        from core import gui_config
        gui_config.apply(self.window, {"state": {"log_settings": {"save_full_log": True, "log_directory": str(self.folder / "logs")}}})
        self.window.output_manager.append_text("pending", Source.SYSTEM)
        archive = self.window.output_manager.archive
        with patch("core.archive_buffer.os.fsync", side_effect=OSError("disk failed")):
            archive.flush(force=True)
            pump_until(lambda: not archive.busy)
        target = self.folder / "occupied.log"
        target.write_text("existing", encoding="utf-8")
        code, events = self.action("recover", "--log-recover", str(target))
        self.assertEqual(code, 1)
        self.assertFalse(events[-1]["success"])
        self.assertEqual(target.read_text(), "existing")
        self.assertTrue(archive.unsaved_entries())
        entered, release = threading.Event(), threading.Event()
        from core.log_archive import ArchiveBatch
        original = ArchiveBatch.run
        def slow(worker):
            entered.set()
            release.wait(3)
            original(worker)
        replies = []
        with patch.object(ArchiveBatch, "run", slow):
            self.window.recover_log_archive(str(self.folder / "delayed.log"), lambda *result: replies.append(result))
            self.assertTrue(entered.wait(1))
            try:
                self.assertFalse(replies)
                with self.assertRaisesRegex(ValueError, "批次尚未结束"):
                    self.window.recover_log_archive(str(self.folder / "second.log"))
            finally:
                release.set()
                pump_until(lambda: not archive.busy)
        self.assertEqual(replies, [(True, "未确认日志已另存完成")])


if __name__ == "__main__":
    unittest.main()
