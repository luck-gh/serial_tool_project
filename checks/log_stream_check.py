"""日志产生时取舍、真实 Qt 流显示和后台文件批次回归;不访问串口或真实配置."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from core.command_types import OutputSource as Source
from core.log_archive import ArchiveBatch
from managers.output_manager import OutputManager
from widgets.custom_widgets import CustomTextBrowser

APP = QApplication.instance() or QApplication([])
STAMP = "2026-10-21T08:00:02.863+08:00"


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    if not predicate():
        raise AssertionError("Qt 操作未在期限内结束")


class LogStreamTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.browser = CustomTextBrowser(None)
        self.all = True
        self.timestamp = False
        self.keep_time = False
        self.sources = set(Source)
        self.levels = {"normal", "info", "debug", "warning"}
        self.log_sources = set(Source)
        self.log_levels = set(self.levels)
        self.manager = OutputManager(self.browser, lambda: self.timestamp,
            lambda: "red", lambda source: source in self.sources,
            lambda level: level in self.levels,
            lambda source, level: self.all or (source in self.log_sources and
                (source != Source.SYSTEM or level in self.log_levels)),
            lambda: self.keep_time, limit=20_000)

    def tearDown(self):
        archive = self.manager.archive
        if archive:
            archive.flush(force=True)
            wait_for(lambda: not archive.busy)
        self.manager.clear()
        self.browser.deleteLater()
        APP.processEvents()
        self.folder.cleanup()

    def emit(self, text, source=Source.SYSTEM, level="normal"):
        return self.manager.append_text(text, source, system_level=level, timestamp=STAMP)

    def display(self):
        wait_for(lambda: not self.manager._render_timer.isActive() and not self.manager._log_timer.isActive())
        return self.browser.toPlainText()

    def finish_file(self):
        archive = self.manager.archive
        archive.flush(force=True)
        wait_for(lambda: not archive.busy and not archive.pending)
        self.assertEqual(archive.error, "")
        return archive.path.read_text(encoding="utf-8")

    def test_selective_capture_and_all_mode_switch_do_not_rewrite_history(self):
        self.keep_time = True
        self.sources.remove(Source.SYSTEM)
        self.log_sources.remove(Source.SYSTEM)
        self.emit("kept-hidden")
        self.assertNotIn("kept-hidden", self.display())
        self.all = False
        self.emit("never-captured")
        self.sources.add(Source.SYSTEM)
        self.log_sources.add(Source.SYSTEM)
        self.keep_time = False
        self.emit("selected-no-time")
        self.all = True
        self.manager.refresh_display()
        self.assertIn("kept-hidden", self.display())
        self.assertIn("selected-no-time", self.display())
        self.assertNotIn("never-captured", self.display())
        self.assertEqual([r.timestamp for r in self.manager.records], [STAMP, ""])
        self.timestamp = True
        self.manager.refresh_display()
        self.assertIn("[2026-10-21 08:00:02.863] kept-hidden", self.display())
        self.assertNotIn("08:00:02.863] selected-no-time", self.display())

    def test_display_filters_never_change_capture(self):
        self.all = False
        self.sources.clear()
        self.levels.clear()
        for source in Source:
            self.emit(source.value, source, "debug")
        self.assertEqual(self.display(), "")
        self.assertEqual(len(self.manager.records), len(Source))
        self.sources = set(Source)
        self.levels = set(self.log_levels)
        self.manager.refresh_display()
        for source in Source:
            self.assertIn(source.value, self.display())
        self.log_sources.clear()
        self.emit("not-captured", Source.SEND)
        self.assertNotIn("not-captured", self.display())

    def test_system_levels_and_timestamp_are_captured_at_arrival(self):
        self.all = False
        self.log_levels = {"normal"}
        self.emit("drop-debug", level="debug")
        self.emit("no-stamp")
        self.timestamp = True
        self.keep_time = True
        self.emit("with-stamp")
        self.log_levels.add("debug")
        self.manager.refresh_display()
        text = self.display()
        self.assertNotIn("drop-debug", text)
        self.assertEqual(text, "no-stamp\n[2026-10-21 08:00:02.863] with-stamp\n")

    def test_all_sources_still_obey_timestamp_switch_and_byte_budget(self):
        self.timestamp = True
        self.emit("no-time-even-in-all-mode")
        self.assertEqual(self.manager.records[-1].timestamp, "")
        self.assertNotIn("2026-10-21", self.display())
        self.keep_time = True
        self.emit("with-time")
        self.assertEqual(self.manager.records[-1].timestamp, STAMP)
        self.manager.clear()
        self.keep_time = False
        self.emit("a" * 100)
        ascii_bytes = self.manager.records[-1].memory_bytes
        self.emit("😀" * 100)
        self.assertGreater(self.manager.records[-1].memory_bytes, ascii_bytes)
        self.assertEqual(self.manager._history_bytes, sum(r.memory_bytes for r in self.manager.records))
        for _ in range(100):
            self.emit("中😀" * 100)
        self.assertLessEqual(self.manager._history_bytes, self.manager.limit)
        self.assertEqual(self.manager._history_bytes, sum(r.memory_bytes for r in self.manager.records))

    def test_no_disk_work_until_threshold_and_disabled_interval_not_backfilled(self):
        self.all = False
        self.manager.configure_archive(True, self.folder.name)
        self.emit("first")
        archive = self.manager.archive
        self.assertFalse(archive.path.exists())
        self.manager.configure_archive(False, self.folder.name)
        wait_for(lambda: not archive.busy)
        self.emit("not-in-file")
        self.manager.configure_archive(True, self.folder.name)
        self.emit("second")
        self.assertEqual(self.finish_file(), "first\nsecond\n")

    def test_archive_respects_capture_and_independent_timestamp(self):
        self.manager.configure_archive(True, self.folder.name)
        self.sources.clear()
        self.levels.clear()
        self.keep_time = True
        self.emit("all-hidden-send", Source.SEND)
        self.emit("all-hidden-system", level="debug")
        self.all = False
        self.log_sources.clear()
        self.emit("absent-system")
        self.log_sources.add(Source.ERROR)
        self.keep_time = False
        self.emit("selected-error", Source.ERROR)
        self.assertEqual(self.finish_file(), "[2026-10-21 08:00:02.863] all-hidden-send\n[2026-10-21 08:00:02.863] all-hidden-system\nselected-error\n")

    def test_multiple_batches_eviction_and_clear_have_no_gaps_or_duplicates(self):
        self.all = False
        self.manager.configure_archive(True, self.folder.name)
        expected = []
        for index in range(160):
            line = f"{index:04d}:" + "中" * 80
            expected.append(line + "\n")
            self.emit(line)
            QTest.qWait(2)
            if index == 77:
                self.manager.clear()
            self.assertLessEqual(self.manager._history_bytes, self.manager.limit)
        self.assertEqual(self.finish_file(), "".join(expected))
        self.assertGreater(self.manager.archive.committed_sequence, 100)
        self.manager.refresh_display()
        text = self.display()
        self.assertIn("0159:", text)
        self.assertNotIn("0000:", text)

    def test_write_failure_is_visible_preserves_pending_and_requires_explicit_recovery(self):
        self.all = False
        errors = []
        self.manager.archive_failed.connect(errors.append)
        self.manager.configure_archive(True, self.folder.name)
        self.emit("must-survive")
        with patch("core.log_archive.os.fsync", side_effect=OSError("disk failed")):
            self.manager.archive.flush(force=True)
            wait_for(lambda: not self.manager.archive.busy)
        archive = self.manager.archive
        self.assertIn("disk failed", errors[0])
        self.assertFalse(self.manager.archive_enabled)
        self.assertEqual(archive.committed_sequence, -1)
        self.assertEqual(archive.unsaved_entries(), ((0, "must-survive\n"),))
        self.emit("later-not-archived")
        archive.recover_to(Path(self.folder.name) / "recovered.log")
        self.assertEqual(self.finish_file(), "must-survive\n")

    def test_slow_disk_does_not_block_gui_or_grow_an_unbounded_queue(self):
        entered, release = threading.Event(), threading.Event()
        original = ArchiveBatch.run

        def slow(worker):
            entered.set()
            release.wait(5)
            original(worker)

        self.all = False
        self.manager.set_limit(2000)
        self.manager.configure_archive(True, self.folder.name)
        with patch.object(ArchiveBatch, "run", slow):
            self.emit("x" * 1700, Source.RECEIVE)
            wait_for(entered.is_set)
            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(5)
            try:
                self.emit("y" * 2200, Source.RECEIVE)
                # 在慢写者仍被屏障阻塞时确认心跳,不把主机调度误判为GUI阻塞.
                wait_for(lambda: len(ticks) > 2, timeout=1)
                self.assertGreater(len(ticks), 2)
                self.assertFalse(release.is_set())
                self.assertTrue(self.manager.archive.busy)
                self.assertIn("速度不足", self.manager.archive.error)
                self.assertLessEqual(self.manager.archive.pending_bytes, 2000 + self.manager.CHUNK_CHARS)
            finally:
                timer.stop()
                release.set()
                wait_for(lambda: not self.manager.archive.busy)

    def test_long_unterminated_stream_is_bounded_and_raw_history_unmodified(self):
        self.all = False
        self.manager.set_limit(100_000)
        self.emit("x" * 80_000, Source.RECEIVE)
        text = self.display()
        self.assertLessEqual(max(map(len, text.splitlines())), self.manager.CHUNK_CHARS)
        self.assertEqual(self.manager.text_for_sources({Source.RECEIVE}), "x" * 80_000)
        self.emit("y" * 80_000, Source.RECEIVE)
        self.manager.refresh_display()
        self.assertLessEqual(self.manager._history_bytes, 100_000)
        self.assertTrue(self.display().endswith("y" * 100))

    def test_eviction_preserves_deferred_order_and_unicode_positions(self):
        self.all = False
        self.manager.set_limit(20_000)
        self.emit("😀" * 500, Source.RECEIVE)
        self.emit("notice" * 6)
        self.emit("B\nC", Source.RECEIVE)
        self.emit("T" * 300, Source.RECEIVE)
        self.assertIn("notice", self.display())
        # 预算只足以容纳最后两条记录,宽限日志必须按自己的区间删除.
        keep_bytes = sum(r.memory_bytes for r in self.manager.records[-2:])
        self.manager.set_limit((keep_bytes * 4 + 2) // 3)
        # 日志比B更早产生却更晚展示,只删除旧日志,不能连B一起裁掉.
        expected = "B\nC" + "T" * 300
        self.assertEqual(self.display(), expected)
        self.manager.refresh_display()
        self.assertEqual(self.display(), expected)

    def test_stream_refresh_is_batched_and_gui_heartbeat_continues(self):
        self.all = False
        self.manager.set_limit(100_000)
        ticks, changes = [], []
        self.browser.textChanged.connect(lambda: changes.append(1))
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(5)
        try:
            for index in range(30):
                for row in range(100):
                    self.emit(f"{index}:{row} " + "0123456789" * 20, Source.RECEIVE)
                QTest.qWait(10)
            self.display()
            self.assertGreater(len(ticks), 10)
            self.assertLess(max(b - a for a, b in zip(ticks, ticks[1:])), 0.25)
            self.assertLess(len(changes), 300)
            self.assertLessEqual(self.manager._history_bytes, 100_000)
        finally:
            timer.stop()


class LogSettingsTests(unittest.TestCase):
    def test_cancel_close_after_write_failure_keeps_bridge_available(self):
        from main_window import SerialTool
        from PyQt5.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as folder, patch("serial.tools.list_ports.comports", return_value=[]):
            window = SerialTool(config_file=str(Path(folder) / "settings.json"))
            window.show()
            window.log_settings["log_directory"] = str(Path(folder) / "logs")
            window.save_full_log_check.setChecked(True)
            window.output_manager.append_text("unsaved-tail", Source.SYSTEM)
            archive = window.output_manager.archive
            try:
                with patch("core.log_archive.os.fsync", side_effect=OSError("disk failed")), \
                     patch("main_window.QMessageBox.warning", return_value=QMessageBox.Cancel):
                    window.close()
                    self.assertTrue(window._closing_logs)
                    with self.assertRaisesRegex(ValueError, "退出日志"):
                        window.gui_cli_bridge.dispatch(None, {"id": "a" * 32, "action": "status"})
                    wait_for(lambda: not archive.busy and not window._closing_logs)
                self.assertTrue(window.isVisible())
                self.assertTrue(window.isEnabled())
                self.assertTrue(window.gui_cli_bridge.server.isListening())
                self.assertTrue(window.recover_log_button.isVisible())
                archive.recover_to(Path(folder) / "recovered.log")
                wait_for(lambda: not archive.busy)
                self.assertEqual(archive.error, "")
                self.assertIn("unsaved-tail", archive.path.read_text(encoding="utf-8"))
            finally:
                window.close()
                window.deleteLater()
                APP.processEvents()

    def test_config_persistence_rollback_and_normal_close_flush(self):
        from main_window import SerialTool
        from core import gui_config
        with tempfile.TemporaryDirectory() as folder, patch("serial.tools.list_ports.comports", return_value=[]):
            path = str(Path(folder) / "settings.json")
            window = SerialTool(config_file=path)
            window.show()
            APP.processEvents()
            try:
                patch_data = {"state": {"log_settings": {"limit_mb": 2, "retain_all": False,
                    "keep_timestamp": False, "save_full_log": True, "log_directory": str(Path(folder) / "logs")}}}
                gui_config.apply(window, patch_data)
                window.output_manager.append_text("tail", Source.SYSTEM)
                archive = window.output_manager.archive
                with patch.object(window.config_manager, "save_config", side_effect=OSError("config failed")):
                    with self.assertRaises(OSError):
                        gui_config.apply(window, {"state": {"log_settings": {"limit_mb": 1, "retain_all": True}}})
                self.assertEqual(window.output_manager.limit, 2_000_000)
                self.assertFalse(window.log_settings["retain_all"])
                self.assertEqual(window.output_manager.records[-1].text, "tail\n")
                self.assertFalse(window.close())
                wait_for(lambda: not archive.busy and not window.isVisible())
                self.assertEqual(archive.path.read_text(encoding="utf-8"), "tail\n串口已关闭\n")
                settings = json.loads(Path(path).read_text(encoding="utf-8"))["state"]["log_settings"]
                self.assertEqual(settings["limit_mb"], 2)
                self.assertFalse(settings["retain_all"])
                self.assertTrue(settings["save_full_log"])
            finally:
                window.close()
                window.deleteLater()
                APP.processEvents()





class LogDialogTests(unittest.TestCase):
    def setUp(self):
        from main_window import SerialTool
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / "settings.json")
        self.ports = patch("serial.tools.list_ports.comports", return_value=[])
        self.ports.start()
        self.window = SerialTool(config_file=self.path)
        self.window.log_settings["retain_all"] = False
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.window.close()
        archive = self.window.output_manager.archive
        if archive:
            wait_for(lambda: not archive.busy)
        self.window.deleteLater()
        APP.processEvents()
        self.ports.stop()
        self.folder.cleanup()

    def dialog(self):
        from dialogs.config_dialog import ConfigDialog
        dialog = ConfigDialog(self.window.config_manager, self.window)
        self.dialogs.append(dialog)
        return dialog

    def test_shortcut_restores_all_four_base_combinations(self):
        w = self.window
        for display in (False, True):
            for capture in (False, True):
                with self.subTest(display=display, capture=capture):
                    w.show_send_source_check.setChecked(display)
                    w.log_settings["sources"]["send"] = capture
                    d = self.dialog()
                    w.show_send_check.setChecked(True)
                    for checkbox in (w.show_send_source_check, d.log_source_checks[Source.SEND]):
                        self.assertTrue(checkbox.isChecked())
                        self.assertFalse(checkbox.isEnabled())
                        self.assertIn("发送设置", checkbox.toolTip())
                    self.assertEqual(d._log_patch()["sources"]["send"], capture)
                    self.assertTrue(w.get_log_capture_filter(Source.SEND, "normal"))
                    w.show_send_check.setChecked(False)
                    self.assertEqual(w.show_send_source_check.isChecked(), display)
                    self.assertEqual(d.log_source_checks[Source.SEND].isChecked(), capture)
                    self.assertTrue(d.log_source_checks[Source.SEND].isEnabled())

    def test_cancel_draft_does_not_change_capture_history_or_json(self):
        from core import gui_config
        w = self.window
        w.save_state()
        before = gui_config.snapshot(w)
        file_before = Path(self.path).read_bytes()
        d = self.dialog()
        d.log_limit_spin.setValue(1)
        d.log_timestamp_check.setChecked(False)
        d.log_source_checks[Source.SYSTEM].setChecked(False)
        w.output_manager.append_text("during-edit", Source.SYSTEM)
        self.assertTrue(w.output_manager.records[-1].timestamp)
        self.assertEqual(w.output_manager.limit, 4_000_000)
        d.reject()
        self.assertEqual(gui_config.snapshot(w), before)
        self.assertEqual(Path(self.path).read_bytes(), file_before)
        self.assertIn("during-edit", w.output_manager.text_for_sources())

    def test_confirm_all_log_fields_writes_json_once_and_restarts(self):
        from PyQt5.QtWidgets import QDialog
        from main_window import SerialTool
        w = self.window
        w.show_send_source_check.setChecked(False)
        w.log_settings["sources"]["send"] = False
        w.show_send_check.setChecked(True)
        d = self.dialog()
        d.log_limit_spin.setValue(7)
        d.log_source_checks[Source.ERROR].setChecked(False)
        with patch("dialogs.config_dialog.SystemLogLevelDialog") as levels:
            levels.return_value.exec_.return_value = QDialog.Accepted
            levels.return_value.selected_levels.return_value = {"warning", "debug"}
            d.log_system_level_button.click()
        d.log_timestamp_check.setChecked(False)
        directory = str(Path(self.folder.name) / "logs")
        with patch("dialogs.config_dialog.QFileDialog.getExistingDirectory", return_value=directory):
            d.log_directory_button.click()
        d.number_conversion_dialog_always_on_top_check.setChecked(True)
        with patch.object(w.config_manager, "save_config", wraps=w.config_manager.save_config) as save:
            d.accept()
        self.assertEqual(d.result(), QDialog.Accepted)
        self.assertEqual(save.call_count, 1)
        state = json.loads(Path(self.path).read_text(encoding="utf-8"))["state"]
        self.assertEqual(state["log_settings"], {
            "limit_mb": 7, "retain_all": False, "keep_timestamp": False,
            "save_full_log": False, "log_directory": directory,
            "sources": {"send": False, "receive": True, "system": True, "error": False},
            "system_levels": {"normal": False, "warning": True, "info": False, "debug": True}})
        self.assertFalse(state["receive_settings"]["display_sources"]["send"])
        self.assertTrue(state["send_settings"]["show_send"])
        self.assertEqual(w.output_manager.limit, 7_000_000)
        d.deleteLater()
        self.dialogs.remove(d)
        w.close()
        w.deleteLater()
        APP.processEvents()
        self.window = w = SerialTool(config_file=self.path)
        self.assertFalse(w.log_settings["keep_timestamp"])
        self.assertTrue(w.show_send_check.isChecked())
        w.show_send_check.setChecked(False)
        self.assertFalse(w.show_send_source_check.isChecked())
        self.assertFalse(w.log_settings["sources"]["send"])
        self.assertEqual(w.log_settings["log_directory"], directory)

    def test_failed_save_rolls_back_tools_and_logs_without_trimming_history(self):
        from core import gui_config
        w = self.window
        w.output_manager.append_text("x" * 1_200_000, Source.RECEIVE)
        w.save_state()
        history_bytes = w.output_manager._history_bytes
        before, file_before = gui_config.snapshot(w), Path(self.path).read_bytes()
        d = self.dialog()
        d.log_limit_spin.setValue(1)
        d.log_timestamp_check.setChecked(False)
        d.number_conversion_dialog_always_on_top_check.setChecked(True)
        with patch.object(w.config_manager, "save_config", side_effect=OSError("disk failed")), \
             patch("dialogs.config_dialog.QMessageBox.warning") as warning:
            d.accept()
        self.assertIn("disk failed", warning.call_args[0][2])
        self.assertEqual(d.result(), 0)
        self.assertEqual(gui_config.snapshot(w), before)
        self.assertEqual(Path(self.path).read_bytes(), file_before)
        self.assertEqual(w.output_manager._history_bytes, history_bytes)
        self.assertEqual(w.output_manager.limit, 4_000_000)

    def test_running_archive_blocks_directory_change_and_confirm_does_not_restart_stopped_save(self):
        w = self.window
        w.log_settings["log_directory"] = str(Path(self.folder.name) / "logs")
        w.save_full_log_check.setChecked(True)
        d = self.dialog()
        self.assertFalse(d.log_directory_button.isEnabled())
        self.assertFalse(w.log_directory_button.isEnabled())
        d.log_directory_input.setText(str(Path(self.folder.name) / "other"))
        with patch("dialogs.config_dialog.QMessageBox.warning") as warning:
            d.accept()
        self.assertTrue(warning.called)
        self.assertEqual(d.result(), 0)
        w.save_full_log_check.setChecked(False)
        self.assertTrue(d.log_directory_button.isEnabled())
        self.assertTrue(w.log_directory_button.isEnabled())
        d.accept()
        self.assertEqual(d.result(), 1)
        self.assertFalse(w.save_full_log_check.isChecked())

    def test_directory_shortcut_and_dialog_share_persisted_setting(self):
        w = self.window
        directory = str(Path(self.folder.name) / "shortcut-logs")
        with patch("main_window.QFileDialog.getExistingDirectory", return_value=directory), \
             patch.object(w.config_manager, "save_config", wraps=w.config_manager.save_config) as save:
            w.log_directory_button.click()
        self.assertEqual(save.call_count, 1)
        self.assertEqual(w.log_settings["log_directory"], directory)
        self.assertEqual(json.loads(Path(self.path).read_text(encoding="utf-8"))["state"]["log_settings"]["log_directory"], directory)
        d = self.dialog()
        self.assertEqual(d.log_directory_input.text(), directory)
        directory = str(Path(self.folder.name) / "dialog-logs")
        d.log_directory_input.setText(directory)
        d.accept()
        self.assertIn(directory, w.log_directory_button.toolTip())
        with patch("main_window.QFileDialog.getExistingDirectory", return_value="") as chooser, \
             patch.object(w.config_manager, "save_config") as save:
            w.log_directory_button.click()
        self.assertEqual(chooser.call_args[0][2], directory)
        save.assert_not_called()
        self.assertEqual(w.log_settings["log_directory"], directory)
        w.save_full_log_check.setChecked(True)
        self.assertEqual(w.output_manager.archive.path.parent, Path(directory).resolve())

    def test_directory_shortcut_save_failure_preserves_runtime_and_json(self):
        from core import gui_config
        w = self.window
        w.save_state()
        before, file_before = gui_config.snapshot(w), Path(self.path).read_bytes()
        with patch("main_window.QFileDialog.getExistingDirectory", return_value=str(Path(self.folder.name) / "new")), \
             patch.object(w.config_manager, "save_config", side_effect=OSError("disk failed")), \
             patch("main_window.QMessageBox.warning") as warning:
            w.log_directory_button.click()
        self.assertIn("disk failed", warning.call_args[0][2])
        self.assertEqual(gui_config.snapshot(w), before)
        self.assertEqual(Path(self.path).read_bytes(), file_before)

    def test_save_switch_autosaves_and_status_follows_batch_lifecycle(self):
        w = self.window
        w.log_settings["log_directory"] = str(Path(self.folder.name) / "logs")
        w.save_full_log_check.setChecked(True)
        self.assertEqual(w.log_status_label.text(), "")
        self.assertTrue(w.log_status_label.isHidden())
        self.assertFalse(w.log_directory_button.isEnabled())
        wait_for(lambda: not w._state_dirty and not w._state_save_timer.isActive())
        self.assertTrue(json.loads(Path(self.path).read_text(encoding="utf-8"))["state"]["log_settings"]["save_full_log"])
        w.output_manager.append_text("tail", Source.SYSTEM)
        entered, release = threading.Event(), threading.Event()
        original = ArchiveBatch.run
        def slow(worker):
            entered.set()
            release.wait(5)
            original(worker)
        with patch.object(ArchiveBatch, "run", slow):
            w.save_full_log_check.setChecked(False)
            wait_for(entered.is_set)
            try:
                self.assertEqual(w.log_status_label.text(), "正在保存尾部")
                self.assertFalse(w.log_status_label.isHidden())
                self.assertFalse(w.log_directory_button.isEnabled())
                self.assertTrue(w.log_status_label.toolTip().endswith(".log"))
            finally:
                release.set()
                wait_for(lambda: not w.output_manager.archive.busy)
        self.assertEqual(w.log_status_label.text(), "")
        self.assertTrue(w.log_status_label.isHidden())
        self.assertTrue(w.log_directory_button.isEnabled())
        self.assertIn("tail", w.output_manager.archive.path.read_text(encoding="utf-8"))
        wait_for(lambda: not w._state_dirty and not w._state_save_timer.isActive())
        self.assertFalse(json.loads(Path(self.path).read_text(encoding="utf-8"))["state"]["log_settings"]["save_full_log"])

    def test_all_mode_and_shortcut_have_independent_restore_and_timestamp(self):
        w = self.window
        w.log_settings["sources"].update(send=False, system=False)
        d = self.dialog()
        d.log_timestamp_check.setChecked(False)
        d.retain_all_check.setChecked(True)
        w.show_send_check.setChecked(True)
        d.retain_all_check.setChecked(False)
        self.assertTrue(d.log_source_checks[Source.SEND].isChecked())
        self.assertFalse(d.log_source_checks[Source.SYSTEM].isChecked())
        w.show_send_check.setChecked(False)
        self.assertFalse(d.log_source_checks[Source.SEND].isChecked())
        self.assertFalse(d.log_timestamp_check.isChecked())

    def test_actual_send_paths_capture_without_shortcut_and_keep_color(self):
        w = self.window
        w.send_color_combo.setCurrentText("蓝色")
        w.show_send_source_check.setChecked(False)
        with patch.object(w, "can_send_serial_data", return_value=True), \
             patch.object(w, "_write_serial_data", side_effect=lambda data, **kwargs: len(data)):
            self.assertTrue(w.send_command("hello"))
            self.assertTrue(w.send_raw_data(b"hi"))
            from core.gui_cli_bridge import GuiConsoleTransport
            transport = GuiConsoleTransport(w.gui_cli_bridge)
            call = dict(operation="write", arguments=(b"bridge", "bridge"), done=threading.Event())
            transport.invoke(call)
            self.assertIsNone(call["error"])
        sent = [r for r in w.output_manager.records if r.source_type == Source.SEND]
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(r.color_name == "blue" for r in sent))
        wait_for(lambda: not w.output_manager._render_timer.isActive())
        self.assertNotIn("hello", w.receive_browser.toPlainText())
        w.show_send_source_check.setChecked(True)
        wait_for(lambda: not w.output_manager._render_timer.isActive())
        self.assertIn("hello", w.receive_browser.toPlainText())

    def test_layout_and_schema_have_no_removed_log_group(self):
        from core import gui_config
        w, d = self.window, self.dialog()
        layout = w.receive_group.content_layout
        self.assertLess(layout.indexOf(w.timestamp_check), layout.indexOf(w.save_btn))
        self.assertEqual(layout.itemAt(layout.indexOf(w.timestamp_check) + 1).layout().itemAt(0).widget(), w.save_full_log_check)
        self.assertEqual(d.log_limit_spin.suffix(), " MB")
        self.assertEqual(d.tabs.tabText(0), "日志配置")
        self.assertFalse(hasattr(w, "log_group"))
        schema = gui_config.schema(w)["properties"]["state"]["properties"]
        self.assertNotIn("log", schema["tool_settings"]["properties"]["left_group_expanded"]["properties"])
        for patch_data in ({"receive_settings": {"log_limit_kchars": 1000}},
                           {"log_settings": {"limit_mb": 0}},
                           {"log_settings": {"limit_mb": 1.5}}):
            with self.assertRaises(ValueError):
                gui_config.prepare(w, {"state": patch_data})



if __name__ == "__main__":
    unittest.main()
