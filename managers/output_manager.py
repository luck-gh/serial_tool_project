#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
输出管理模块, 负责将分类后的输出文本写入 Qt 接收显示控件。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from dataclasses import dataclass

from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat
from utils.ui_utils import OutputSource
from widgets.custom_widgets import CustomTextBrowser
from core.output_rules import OutputRules

@dataclass(frozen=True)
class OutputRecord:
    text: str
    source_type: OutputSource
    color_name: str


class OutputManager:
    """统一输出管理器"""
    SOURCE_PROPERTY = QTextFormat.UserProperty + 1

    def __init__(self, text_browser:CustomTextBrowser, timestamp_check, show_send_check, send_color_getter, source_filter_getter=None):
        self.text_browser = text_browser
        self._receive_pending_cr = False
        self.records = []
        self.rules = OutputRules(
            timestamp_enabled=timestamp_check,
            show_send_enabled=show_send_check,
            send_color_getter=send_color_getter,
            source_filter_getter=source_filter_getter,
            timestamp_format="[%Y-%m-%d %H:%M:%S.%f]",
            trim_microseconds=True,
        )

    def append_text(self, text, source_type, color=None):
        """记录全部分类输出，并按当前来源设置决定是否显示。"""
        if source_type == OutputSource.RECEIVE:
            text = self._coalesce_receive_crlf(text)
        text = self._normalize_document_text(text)
        color_name = color or self.rules.color_for(source_type)
        if color_name == "white":
            color_name = "black"
        timestamp = self.rules.timestamp_for(source_type)
        rendered_text = f"{timestamp}{text}"
        self.rules.before_append(source_type)
        if source_type != OutputSource.RECEIVE:
            rendered_text += "\n"

        record = OutputRecord(rendered_text, source_type, color_name)
        self.records.append(record)
        if self.rules.source_enabled(source_type):
            cursor = self.text_browser.textCursor()
            cursor.movePosition(QTextCursor.End)
            self._insert_record(cursor, record)
            self.text_browser.setTextCursor(cursor)
            self.text_browser.ensureCursorVisible()

    def refresh_display(self):
        """根据当前显示来源重新构建接收区，完整日志记录保持不变。"""
        scroll_bar = self.text_browser.verticalScrollBar()
        was_at_bottom = scroll_bar.value() >= scroll_bar.maximum()
        previous_value = scroll_bar.value()
        previous_block_state = self.text_browser.blockSignals(True)
        try:
            self.text_browser.clear()
            cursor = self.text_browser.textCursor()
            cursor.movePosition(QTextCursor.End)
            for record in self.records:
                if self.rules.source_enabled(record.source_type):
                    self._insert_record(cursor, record)
            self.text_browser.setTextCursor(cursor)
        finally:
            self.text_browser.blockSignals(previous_block_state)

        if was_at_bottom:
            self.text_browser.ensureCursorVisible()
        else:
            scroll_bar.setValue(min(previous_value, scroll_bar.maximum()))

    def _insert_record(self, cursor, record):
        char_format = QTextCharFormat()
        char_format.setProperty(self.SOURCE_PROPERTY, record.source_type.value)
        char_format.setForeground(QColor(record.color_name))
        cursor.setCharFormat(char_format)
        cursor.insertText(record.text)

    def text_for_sources(self, source_types=None):
        """从完整日志记录中按来源导出，包含当前未显示的信息。"""
        selected_values = None
        if source_types is not None:
            selected_values = {
                source.value if isinstance(source, OutputSource) else str(source)
                for source in source_types
            }
        if not self.records:
            return self.text_browser.toPlainText()
        return "".join(
            record.text
            for record in self.records
            if selected_values is None or record.source_type.value in selected_values
        )

    def reset_receive_timestamp(self):
        """重置接收时间戳标志 (在发送数据后调用) """
        self.rules.reset_receive_timestamp()

    def _coalesce_receive_crlf(self, text):
        """合并跨串口包拆开的 CRLF, 避免显示区偶发空行。"""
        if not text:
            return text

        if self._receive_pending_cr and text.startswith("\n"):
            text = text[1:]

        self._receive_pending_cr = text.endswith("\r")
        return text

    @staticmethod
    def _normalize_document_text(text):
        return str(text).replace("\r\n", "\n").replace("\r", "\n")

    def clear(self):
        """清空显示区"""
        self.records = []
        self.text_browser.clear()
        self._receive_pending_cr = False
        self.rules.reset_receive_timestamp()
