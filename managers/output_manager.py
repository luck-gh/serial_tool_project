#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
输出管理模块, 负责将分类后的输出文本写入 Qt 接收显示控件。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor
from utils.ui_utils import OutputSource
from widgets.custom_widgets import CustomTextBrowser
from core.output_rules import OutputRules

class OutputManager:
    """统一输出管理器"""
    def __init__(self, text_browser:CustomTextBrowser, timestamp_check, show_send_check, send_color_getter, source_filter_getter=None):
        self.text_browser = text_browser
        self.rules = OutputRules(
            timestamp_enabled=timestamp_check,
            show_send_enabled=show_send_check,
            send_color_getter=send_color_getter,
            source_filter_getter=source_filter_getter,
            timestamp_format="[%Y-%m-%d %H:%M:%S.%f]",
            trim_microseconds=True,
        )

    def append_text(self, text, source_type, color=None):
        """统一添加文本到显示区"""
        if not self.rules.source_enabled(source_type):
            return

        cursor = self.text_browser.textCursor()
        cursor.movePosition(QTextCursor.End)

        char_format = QTextCharFormat()
        color_name = color or self.rules.color_for(source_type)
        if color_name == "white":
            color_name = "black"
        char_format.setForeground(QColor(color_name))

        cursor.setCharFormat(char_format)
        timestamp = self.rules.timestamp_for(source_type)
        if timestamp:
            cursor.insertText(timestamp)

        cursor.insertText(text)
        self.rules.before_append(source_type)

        if source_type != OutputSource.RECEIVE:
            cursor.insertText("\n")

        self.text_browser.setTextCursor(cursor)
        self.text_browser.ensureCursorVisible()

    def reset_receive_timestamp(self):
        """重置接收时间戳标志 (在发送数据后调用) """
        self.rules.reset_receive_timestamp()

    def clear(self):
        """清空显示区"""
        self.text_browser.clear()
        self.rules.reset_receive_timestamp()
