"""真实 Qt 文档回归:接收分批显示,系统日志仅在有限时间内避让残缺行."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from core.command_types import OutputSource
from core.receive_text import ExpectMatcher, ReceivedFile
from managers.output_manager import OutputManager
from widgets.custom_widgets import CustomTextBrowser

APP = QApplication.instance() or QApplication([])


class OutputDisplayTests(unittest.TestCase):
    def setUp(self):
        self.browser = CustomTextBrowser(None)
        self.show_system = True
        self.manager = OutputManager(self.browser, lambda: False, lambda: "red",
                                     source_filter_getter=lambda source: self.show_system or source != OutputSource.SYSTEM)

    def tearDown(self):
        self.manager.clear()
        self.browser.deleteLater()
        APP.processEvents()

    def receive(self, text):
        self.manager.append_text(text, OutputSource.RECEIVE)

    def log(self, text="[CLI test] success: 响应已匹配"):
        self.manager.append_text(text, OutputSource.SYSTEM)

    def text(self):
        # 顺序断言只执行当前排版批次,不提前结束20ms日志宽限.
        while self.manager._render_queue:
            self.manager._render_batch()
        return self.browser.toPlainText()

    def test_split_response_is_contiguous_on_next_render(self):
        cases = (
            ("ok", "write 0 0x4010800C 0xFFFFFFFF\r\nO", "K\r\n", "OK"),
            ("registers", "read 0 0x40017000 5\r\n40017000 = 001B55AD\r\n"
             "40017004 = 0000465C\r\n40017008 = 0000465C\r\n4001700C = 0",
             "0000000\r\n40017010 = 12345678\r\n", "40017010 = 12345678"),
        )
        for name, first, second, pattern in cases:
            with self.subTest(response=name), tempfile.TemporaryDirectory() as folder:
                self.manager.clear()
                matcher = ExpectMatcher(pattern)
                path = Path(folder) / "raw.bin"
                recorder = ReceivedFile(path)
                try:
                    for index, text in enumerate((first, second)):
                        data = text.encode("utf-8")
                        recorder.feed(data)
                        matcher.feed(data)
                        self.receive(text)
                        if index == 0:
                            self.assertEqual(self.text(), first.replace("\r\n", "\n"))
                            self.log()
                            self.assertNotIn("[CLI", self.text())
                finally:
                    recorder.close()
                expected_rx = (first + second).replace("\r\n", "\n")
                expected_display = (first + second.partition("\r\n")[0]).replace("\r\n", "\n")
                expected_display += "\n[CLI test] success: 响应已匹配\n" + second.partition("\r\n")[2].replace("\r\n", "\n")
                self.assertEqual(self.text(), expected_display)
                self.manager.refresh_display()
                self.assertEqual(self.text(), expected_display)
                # 完整 RX 导出/原始文件不混入日志,数值和后续行不丢不重排.
                self.assertEqual(self.manager.text_for_sources({OutputSource.RECEIVE}),
                                 self.manager.rules.current_timestamp(self.manager.records[0].timestamp) + expected_rx)
                self.assertEqual(path.read_bytes(), (first + second).encode("utf-8"))
                self.assertTrue(matcher.finish())
                self.assertEqual([r.source_type for r in self.manager.records],
                                 [OutputSource.RECEIVE, OutputSource.SYSTEM, OutputSource.RECEIVE])

    def test_line_boundary_inside_chunk_precedes_next_prefix_and_crlf_coalesces(self):
        self.receive("O")
        self.log("notice")
        self.receive("K\r\nNEXT")
        self.assertEqual(self.text(), "OK\nnotice\nNEXT")
        self.receive("tail")
        self.assertTrue(self.text().endswith("NEXTtail"))
        self.log("second")
        self.receive("\r")
        self.receive("\nTAIL")
        self.assertEqual(self.text(), "OK\nnotice\nNEXTtail\nsecond\nTAIL")

    def test_complete_register_lines_do_not_define_a_packet_boundary(self):
        self.receive("4001700C = 00000000\r\n")
        self.log("between lines")
        self.receive("40017010 = 12345678\r\n")
        self.assertEqual(self.text(), "4001700C = 00000000\nbetween lines\n40017010 = 12345678\n")

    def test_grace_flushes_log_without_delaying_unterminated_rx(self):
        self.receive("partial")
        self.assertEqual(self.text(), "partial")
        self.log("notice")
        QTest.qWait(self.manager.LOG_GRACE_MS + 40)
        self.assertEqual(self.text(), "partial\nnotice\n")
        self.receive("tail")
        self.assertEqual(self.text(), "partial\nnotice\ntail")

    def test_continuous_stream_cannot_postpone_logs_indefinitely(self):
        self.receive("stream")
        self.log("bounded notice")
        timer = QTimer(self.browser)
        timer.timeout.connect(lambda: self.receive("x"))
        timer.start(5)
        deadline_signal = QSignalSpy(self.manager._log_timer.timeout)
        try:
            # 等待真实 deadline 信号,不用 QTest 固定睡眠后尚未泵送的快照判断.
            # 事件循环 watchdog 容忍宿主调度延迟,产品定时仍固定 20 ms.
            self.assertTrue(deadline_signal.wait(600))
            self.assertEqual(len(deadline_signal), 1)
            self.assertIn("\nbounded notice\n", self.text())
            self.receive("after deadline")
            self.assertTrue(self.text().endswith("\nafter deadline"))
        finally:
            timer.stop()

    def test_send_forces_separate_line_and_clear_cancels_pending_logs(self):
        self.receive("O")
        self.log("notice")
        self.manager.append_text("TX", OutputSource.SEND)
        self.receive("K")
        self.assertEqual(self.text(), "O\nnotice\nTX\nK")
        self.log("must not return")
        self.manager.clear()
        QTest.qWait(self.manager.LOG_GRACE_MS + 30)
        self.assertEqual(self.text(), "")
        self.assertEqual(self.manager.records, [])
        self.manager.refresh_display()
        self.assertEqual(self.text(), "")

    def test_refresh_and_filters_preserve_display_order_and_pending_state(self):
        self.receive("O")
        self.log("notice")
        self.manager.refresh_display()
        self.assertEqual(self.text(), "O")
        self.receive("K\n")
        expected = "OK\nnotice\n"
        self.assertEqual(self.text(), expected)
        self.manager.refresh_display()
        self.assertEqual(self.text(), expected)
        self.show_system = False
        self.manager.refresh_display()
        self.assertEqual(self.text(), "OK\n")
        self.show_system = True
        self.manager.refresh_display()
        self.assertEqual(self.text(), expected)

    def test_explicit_close_flush_preserves_pending_records(self):
        self.receive("partial")
        self.manager.append_text("error", OutputSource.ERROR)
        self.manager.flush_pending_output()
        self.assertEqual(self.text(), "partial\nerror\n")
        QTest.qWait(self.manager.LOG_GRACE_MS + 30)
        self.assertEqual(self.text(), "partial\nerror\n")

    def test_hidden_send_notification_is_still_a_display_boundary(self):
        self.receive("old partial")
        self.log("old notice")
        # 窗口隐藏 TX 时仍调用此共同发送通知,不创建 SEND 显示记录.
        self.manager.reset_receive_timestamp()
        self.receive("new response\n")
        self.assertEqual(self.text(), "old partial\nold notice\nnew response\n")


class CommandCommentDisplayTests(unittest.TestCase):
    def test_comment_never_paints_over_visible_command(self):
        """复用真实 Qt 控件离屏绘制,覆盖窄宽/滚动/选中和搜索高亮."""
        from widgets.command_widgets import CommandLineEdit
        edit = CommandLineEdit(None)
        note = "很长的中文注释 Mixed proportional text " * 12
        try:
            for font in (QFont("Arial", 12), QFont("Consolas", 14)):
                edit.setFont(font)
                for width in (180, 620):
                    edit.resize(width, edit.sizeHint().height())
                    edit.show()
                    APP.processEvents()
                    for command in ("mode:ZPX", "mode:长命令 Mixed " * 18):
                        for selected in (False, True):
                            with self.subTest(font=font.family(), width=width, command=command[:12], selected=selected):
                                edit.setText(command)
                                edit.setCursorPosition(len(command))
                                if selected:
                                    edit.selectAll()
                                edit.set_comment("")
                                before = edit.grab().toImage()
                                metrics = edit.fontMetrics()
                                x = edit.cursorRect().center().x() - metrics.horizontalAdvance(command[:edit.cursorPosition()])
                                boundary = min(width, max(0, x + metrics.horizontalAdvance(command) + 8))
                                edit.set_comment(note)
                                edit.set_comment_search_matches([(0, 2), (len(note) - 2, len(note))], (0, 2))
                                after = edit.grab().toImage()
                                # 注释与高亮都不得修改命令区域的任何像素.
                                pixels = round(boundary * before.devicePixelRatio())
                                self.assertEqual(before.copy(0, 0, pixels, before.height()),
                                                 after.copy(0, 0, pixels, after.height()))
                                self.assertEqual(edit.text(), command)
                                self.assertEqual(edit.comment_text, note)
                                if width == 620 and command == "mode:ZPX":
                                    self.assertNotEqual(before, after)
            edit.setText("short")
            edit.resize(620, edit.sizeHint().height())
            edit.set_comment("注释…")
            edit.clear_comment_search_highlight()
            plain = edit.grab().toImage()
            edit.set_comment_search_matches([(2, 3)], (2, 3))
            highlighted = edit.grab().toImage()
            self.assertTrue(any(highlighted.pixelColor(x, y).name() == "#ffd54f"
                                for y in range(highlighted.height()) for x in range(highlighted.width())))
            # Qt 光标位置以 UTF-16 计数;独立按光标后的后缀测量右边界.
            edit.setText("😀" * 10 + "W" * 20)
            edit.resize(420, edit.sizeHint().height())
            edit.setCursorPosition(20)
            edit.set_comment("")
            plain = edit.grab().toImage()
            boundary = min(edit.width(), edit.cursorRect().center().x()
                           + edit.fontMetrics().horizontalAdvance("W" * 20) + 8)
            edit.set_comment(note)
            edit.set_comment_search_matches([(0, 2)], (0, 2))
            pixels = round(boundary * plain.devicePixelRatio())
            self.assertEqual(plain.copy(0, 0, pixels, plain.height()),
                             edit.grab().toImage().copy(0, 0, pixels, plain.height()))
        finally:
            edit.close()
            edit.deleteLater()
            APP.processEvents()

    def test_comment_editor_receives_full_original(self):
        """原编辑入口传递完整注释,省略文本不回写命令或模型."""
        from widgets.command_widgets import CommandTableWidget
        from dialogs.comment_edit_dialog import CommentEditDialog
        note = "完整多行长注释\n" * 50
        editor = CommentEditDialog(note)
        self.assertEqual(editor.comment_edit.toPlainText(), note)
        editor.deleteLater()
        table = CommandTableWidget(None)
        try:
            table.add_command_row(True, "mode:ZPX", note)
            with patch("widgets.command_widgets.CommentEditDialog") as dialog:
                dialog.return_value.exec_.return_value = 0
                table.edit_comment(0)
                dialog.assert_called_once_with(note, table)
            self.assertEqual(table.get_row_data(0), (True, "mode:ZPX", note))
        finally:
            table.deleteLater()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
