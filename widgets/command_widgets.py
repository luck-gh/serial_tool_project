#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
命令控件模块, 负责命令输入框, 注释显示和命令表格的编辑交互。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import csv
import copy
import io
import re
import os
import sys
import subprocess
from PyQt5.QtWidgets import (QApplication, QWidget, QLineEdit, QTableWidget, QCheckBox, QPushButton,
                             QHeaderView, QTableWidgetItem, QAction, QInputDialog, QMessageBox,
                             QDialog, QHBoxLayout, QVBoxLayout, QLabel, QDialogButtonBox, QTextEdit,
                             QComboBox, QStyleOptionFrame, QStyle, QFileDialog)
from PyQt5.QtCore import Qt, QRegExp, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics, QRegExpValidator, QPalette

from utils.ui_utils import UIUtils, Colors, resource_path
from widgets.base_widgets import BaseWidgetMixin
from dialogs.comment_edit_dialog import CommentEditDialog
from dialogs.replacement_rule_dialog import ReplacementRuleDialog
from managers.config_manager import ConfigManager
from core.text_search import normalize_rule

class HexInputDialog(QDialog):
    """SendHex 专用输入对话框，支持实时格式化"""
    def __init__(self, initial_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("SendHex 设置")
        self.setMinimumWidth(300)
        
        layout = QVBoxLayout(self)
        
        self.label = QLabel("请输入HEX数据 (自动格式化):")
        layout.addWidget(self.label)
        
        self.edit = QLineEdit()
        # 限制只能输入十六进制字符和空格
        regex = QRegExp(r"[0-9a-fA-F\s]+")
        self.edit.setValidator(QRegExpValidator(regex, self.edit))
        self.edit.setText(initial_text)
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit)
        
        # 添加按钮
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _on_text_changed(self, text):
        """实时格式化十六进制输入"""
        # 提取所有有效的十六进制字符
        clean_hex = "".join(c for c in text if c.lower() in "0123456789abcdef")
        
        # 每两个字符加一个空格进行格式化
        formatted_hex = ""
        for i in range(0, len(clean_hex), 2):
            formatted_hex += clean_hex[i:i+2] + " "
        formatted_hex = formatted_hex.strip().upper()
        
        if formatted_hex != text:
            cursor_pos = self.edit.cursorPosition()
            # 简单的光标位置修正逻辑
            # 统计光标前有多少个有效的十六进制字符
            hex_chars_before = 0
            for i in range(min(cursor_pos, len(text))):
                if text[i].lower() in "0123456789abcdef":
                    hex_chars_before += 1
            
            self.edit.blockSignals(True)
            self.edit.setText(formatted_hex)
            
            # 重新定位光标
            new_pos = 0
            count = 0
            for i in range(len(formatted_hex)):
                if formatted_hex[i].lower() in "0123456789abcdef":
                    count += 1
                if count == hex_chars_before:
                    new_pos = i + 1
                    break
            self.edit.setCursorPosition(new_pos)
            self.edit.blockSignals(False)

    def get_text(self):
        return self.edit.text().strip()


class FirmwareDownloadPathDialog(QDialog):
    """FirmwareDownload 指令的可选固件文件输入框。"""

    def __init__(self, initial_path="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("固件下载设置")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("固件文件（留空时使用固件下载工具的默认文件）:"))
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(initial_path)
        self.path_edit.setPlaceholderText("例如: D:\\firmware\\app.bin")
        browse_button = QPushButton("浏览...")
        browse_button.clicked.connect(self._browse_file)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(browse_button)
        layout.addLayout(path_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择固件文件",
            self.path_edit.text().strip(),
            "固件文件 (*.bin *.hex *.fw);;所有文件 (*)",
        )
        if path:
            self.path_edit.setText(path)

    def path_text(self):
        return self.path_edit.text().strip()


class CommandRowsTextParser:
    """把模板文本或纯文本解析成命令表格行。"""
    FORMAT_TEMPLATE = "模板格式 (CSV)"
    FORMAT_PLAIN_TEXT = "纯文本"

    TEMPLATE_HELP = (
        "模板格式每行应为: True,串口需要发送的数据,注释\n"
        "或: False,串口需要发送的数据,注释\n"
        "注释列可以为空；以 // 开头的行会被当作说明跳过。"
    )

    @classmethod
    def parse_rows(cls, text, input_format):
        if input_format not in (cls.FORMAT_TEMPLATE, cls.FORMAT_PLAIN_TEXT):
            raise ValueError("请先选择输入格式。")

        if input_format == cls.FORMAT_PLAIN_TEXT:
            return cls.parse_plain_text_rows(text)

        return cls.parse_template_rows(text)

    @classmethod
    def parse_plain_text_rows(cls, text):
        rows = []
        for line in text.splitlines():
            command = line.strip()
            if command:
                rows.append((False, command, ""))
        if not rows:
            raise ValueError("请输入至少一行非空文本。")
        return rows

    @classmethod
    def parse_template_rows(cls, text):
        rows = []
        reader = csv.reader(io.StringIO(text))
        for line_number, row in enumerate(reader, start=1):
            if not row or not any(cell.strip() for cell in row):
                continue

            first_cell = row[0].strip()
            if first_cell.startswith("//") or first_cell.startswith("#"):
                continue

            enable_str = first_cell.lower()
            if enable_str not in ("true", "false"):
                raise ValueError(
                    f"第 {line_number} 行格式不正确，第一列必须是 True 或 False。"
                )
            if len(row) < 2:
                raise ValueError(f"第 {line_number} 行格式不正确，缺少发送字符串列。")

            enable = enable_str == "true"
            command = UIUtils.unescape_csv_text(row[1].strip())
            if len(row) > 2:
                comment = ",".join(cell.strip() for cell in row[2:])
                comment = UIUtils.unescape_csv_text(comment)
            else:
                comment = ""
            rows.append((enable, command, comment))

        if not rows:
            raise ValueError("没有解析到可添加的命令行。")
        return rows


class TextRowsInputDialog(QDialog):
    """根据多行文本生成命令行的输入对话框。"""
    FORMAT_TEMPLATE = CommandRowsTextParser.FORMAT_TEMPLATE
    FORMAT_PLAIN_TEXT = CommandRowsTextParser.FORMAT_PLAIN_TEXT
    TEMPLATE_HELP = CommandRowsTextParser.TEMPLATE_HELP

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("根据文本添加多行")
        self.setMinimumSize(520, 360)
        self.parsed_rows = []

        layout = QVBoxLayout(self)

        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("输入格式:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems([self.FORMAT_TEMPLATE, self.FORMAT_PLAIN_TEXT])
        format_layout.addWidget(self.format_combo, 1)
        layout.addLayout(format_layout)

        self.help_label = QLabel(self.TEMPLATE_HELP)
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("请在这里输入多行文本...")
        layout.addWidget(self.text_edit, 1)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.format_combo.currentTextChanged.connect(self._update_help_text)
        self.format_combo.setCurrentText(self.FORMAT_PLAIN_TEXT)
        self._update_help_text(self.FORMAT_PLAIN_TEXT)

    @staticmethod
    def parse_rows(text, input_format):
        return CommandRowsTextParser.parse_rows(text, input_format)

    @staticmethod
    def parse_template_rows(text):
        return CommandRowsTextParser.parse_template_rows(text)

    def get_rows(self):
        return self.parsed_rows

    def _update_help_text(self, input_format):
        if input_format == self.FORMAT_TEMPLATE:
            self.help_label.setText(self.TEMPLATE_HELP)
            self.text_edit.setPlaceholderText(
                "例如:\n"
                "// 选择框,串口需要发送的数据,注释\n"
                "True,AT+RESET,复位命令\n"
                "False,AT+VERSION,读取版本"
            )
        elif input_format == self.FORMAT_PLAIN_TEXT:
            self.help_label.setText("纯文本格式: 每一行文本会作为一条发送字符串添加。")
            self.text_edit.setPlaceholderText("例如:\nAT+RESET\nAT+VERSION")
        else:
            self.help_label.setText("请先选择输入格式。")
            self.text_edit.setPlaceholderText("请选择上方的输入格式后，再输入多行文本。")

    def _validate_and_accept(self):
        try:
            self.parsed_rows = self.parse_rows(
                self.text_edit.toPlainText(),
                self.format_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "格式不正确", f"{exc}\n\n{self.TEMPLATE_HELP}")
            return

        self.accept()


class CommandLineEdit(QLineEdit, BaseWidgetMixin):
    """自定义命令行编辑框, 支持注释显示和文本编辑右键菜单"""
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.command_search_matches = []
        self.command_search_highlight = None
        self.comment_text = ""
        self.comment_search_matches = []
        self.comment_search_highlight = None
        self.command_table = None
        self.row_index = -1
        self.validation_state = None

        # 设置上下文菜单策略为自定义
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_text_context_menu)

    def set_comment(self, comment):
        """设置注释文本"""
        self.comment_text = comment
        self.comment_search_matches = [
            (start, end) for start, end in self.comment_search_matches
            if 0 <= start < end <= len(comment)
        ]
        if self.comment_search_highlight not in self.comment_search_matches:
            self.comment_search_highlight = None
        # 预留右边距给注释文本, 避免与输入文本重叠
        # 根据注释长度动态调整边距
        if self.comment_text:
            # 使用与paintEvent中相同的字体来计算所需空间
            comment_font = QFont("Arial", 8)
            font_metrics = QFontMetrics(comment_font)
            comment_width = font_metrics.horizontalAdvance(self.comment_text)

            # 如果注释太长(超过控件宽度的40%),不设置边距,让paintEvent根据文本长度自动隐藏
            max_comment_width = self.width() * 0.4
            if comment_width > max_comment_width:
                margin = 0
            else:
                # 额外增加一点边距, 比如 10px
                margin = comment_width + 10
        else:
            margin = 0
        self.setTextMargins(0, 0, margin, 0)
        self.update() # 触发重绘

    def set_comment_search_highlight(self, start, end):
        """将备注中的一个搜索匹配项标记为当前项。"""
        match = (start, end)
        matches = self.comment_search_matches or [match]
        self.set_comment_search_matches(matches, match)

    def set_comment_search_matches(self, matches, active_match=None):
        """设置备注的全部搜索匹配项及当前匹配项。"""
        self.comment_search_matches = [
            (start, end) for start, end in matches
            if 0 <= start < end <= len(self.comment_text)
        ]
        self.comment_search_highlight = (
            active_match if active_match in self.comment_search_matches else None
        )
        self.update()

    def clear_comment_search_highlight(self):
        if self.comment_search_matches or self.comment_search_highlight is not None:
            self.comment_search_matches = []
            self.comment_search_highlight = None
            self.update()

    def set_command_search_match(self, matched, active=False):
        """标记字符串输入行是否包含搜索结果，以及是否为当前结果。"""
        matched = bool(matched)
        active = bool(active and matched)
        if (
            bool(self.property("searchMatchStyle")) == matched
            and bool(self.property("activeSearchMatchStyle")) == active
        ):
            return
        self.setProperty("searchMatchStyle", matched)
        self.setProperty("activeSearchMatchStyle", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_command_search_matches(self, matches, active_match=None):
        """设置字符串中的全部搜索匹配项及当前匹配项。"""
        text_length = len(self.text())
        self.command_search_matches = [
            (start, end) for start, end in matches
            if 0 <= start < end <= text_length
        ]
        self.command_search_highlight = (
            active_match if active_match in self.command_search_matches else None
        )
        self.set_command_search_match(
            bool(self.command_search_matches),
            self.command_search_highlight is not None,
        )
        self.update()

    def clear_command_search_highlight(self):
        self.command_search_matches = []
        self.command_search_highlight = None
        self.set_command_search_match(False)

    def set_validation_state(self, state, color_policy="latest"):
        """设置响应校验背景；sticky_failure 下通过结果不覆盖失败。"""
        if state not in (None, "passed", "failed"):
            state = None
        if (
            color_policy == "sticky_failure"
            and self.validation_state == "failed"
            and state == "passed"
        ):
            return
        if self.validation_state == state:
            return
        self.validation_state = state
        self.setProperty("validationState", state or "none")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _paint_command_search_matches(self, painter):
        if self.hasSelectedText():
            return

        text = self.text()
        font_metrics = self.fontMetrics()
        cursor_rect = self.cursorRect()
        cursor_position = self.cursorPosition()
        text_x = (
            cursor_rect.x() + cursor_rect.width() // 2
            - font_metrics.horizontalAdvance(text[:cursor_position])
        )
        content_option = QStyleOptionFrame()
        self.initStyleOption(content_option)
        content_rect = self.style().subElementRect(
            QStyle.SE_LineEditContents,
            content_option,
            self,
        )
        text_top = content_rect.y() + (
            content_rect.height() - font_metrics.height()
        ) // 2
        baseline = text_top + font_metrics.ascent()
        margins = self.textMargins()
        clip_rect = content_rect.adjusted(
            margins.left(),
            0,
            -margins.right(),
            0,
        )

        painter.save()
        painter.setClipRect(clip_rect)
        paint_matches = [
            (start, end) for start, end in self.command_search_matches
            if not (
                self.hasFocus()
                and start <= self.cursorPosition() <= end
            )
        ]
        for start, end in paint_matches:
            match_x = text_x + font_metrics.horizontalAdvance(text[:start])
            match_width = font_metrics.horizontalAdvance(text[start:end])
            painter.fillRect(
                match_x,
                text_top,
                max(1, match_width),
                font_metrics.height(),
                QColor("#FFF59D"),
            )

        if self.command_search_highlight in paint_matches:
            start, end = self.command_search_highlight
            match_x = text_x + font_metrics.horizontalAdvance(text[:start])
            match_width = font_metrics.horizontalAdvance(text[start:end])
            painter.fillRect(
                match_x,
                text_top,
                max(1, match_width),
                font_metrics.height(),
                QColor("#FFD54F"),
            )
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QPalette.Text))
        for start, end in paint_matches:
            match_x = text_x + font_metrics.horizontalAdvance(text[:start])
            painter.drawText(match_x, baseline, text[start:end])
        painter.restore()

    def paintEvent(self, event):
        """重写paintEvent, 在绘制完基础控件后, 绘制注释文本"""
        super().paintEvent(event)
        if self.command_search_matches:
            painter = QPainter(self)
            self._paint_command_search_matches(painter)
        else:
            painter = None

        if not self.comment_text:
            return

        # 只在输入框为空或输入文本较短时才显示注释
        current_text = self.text()
        if current_text:
            # 计算当前文本的显示宽度
            font_metrics = self.fontMetrics()
            text_width = font_metrics.horizontalAdvance(current_text)
            # 获取可视区域宽度（考虑边距和滚动）
            visible_width = self.width() - self.textMargins().right() - 20  # 留出一些缓冲

            # 如果输入文本已经占用了大部分空间，不显示注释
            if text_width > visible_width * 0.6 and not self.comment_search_matches:
                return

        if painter is None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)

        font = QFont("Arial", 8)
        painter.setFont(font)

        # 在右侧绘制注释文本
        text_rect = self.rect().adjusted(5, 0, -5, 0)
        if self.comment_search_matches:
            font_metrics = QFontMetrics(font)
            comment_width = font_metrics.horizontalAdvance(self.comment_text)
            highlight_y = (self.height() - font_metrics.height()) // 2
            for start, end in self.comment_search_matches:
                prefix_width = font_metrics.horizontalAdvance(self.comment_text[:start])
                match_width = font_metrics.horizontalAdvance(self.comment_text[start:end])
                highlight_x = text_rect.right() - comment_width + 1 + prefix_width
                painter.fillRect(
                    highlight_x,
                    highlight_y,
                    max(1, match_width),
                    font_metrics.height(),
                    QColor("#FFF59D"),
                )

        if self.comment_search_highlight:
            start, end = self.comment_search_highlight
            prefix_width = font_metrics.horizontalAdvance(self.comment_text[:start])
            match_width = font_metrics.horizontalAdvance(self.comment_text[start:end])
            highlight_x = text_rect.right() - comment_width + 1 + prefix_width
            painter.fillRect(
                highlight_x,
                highlight_y,
                max(1, match_width),
                font_metrics.height(),
                QColor("#FFD54F"),
            )

        if self.comment_search_matches:
            painter.setPen(QColor("#000000"))
        else:
            painter.setPen(QColor(150, 150, 150, 180))
        painter.drawText(text_rect, Qt.AlignRight | Qt.AlignVCenter, self.comment_text)

    def show_text_context_menu(self, position):
        """显示文本编辑右键菜单"""
        menu = UIUtils.create_styled_menu(self)

        # --- 标准编辑操作 ---
        undo_action = QAction("回退\tCtrl+Z", self)
        undo_action.triggered.connect(self.undo)
        undo_action.setEnabled(self.isUndoAvailable())
        menu.addAction(undo_action)

        redo_action = QAction("恢复\tCtrl+Y", self)
        redo_action.triggered.connect(self.redo)
        redo_action.setEnabled(self.isRedoAvailable())
        menu.addAction(redo_action)

        menu.addSeparator()

        cut_action = QAction("剪贴\tCtrl+X", self)
        cut_action.triggered.connect(self.cut)
        cut_action.setEnabled(self.hasSelectedText())
        menu.addAction(cut_action)

        copy_action = QAction("复制\tCtrl+C", self)
        copy_action.triggered.connect(self.copy)
        copy_action.setEnabled(self.hasSelectedText())
        menu.addAction(copy_action)

        paste_action = QAction("粘贴\tCtrl+V", self)
        paste_action.triggered.connect(self.paste)
        menu.addAction(paste_action)

        delete_action = QAction("删除", self)
        delete_action.triggered.connect(self.del_)
        delete_action.setEnabled(self.hasSelectedText())
        menu.addAction(delete_action)

        menu.addSeparator()

        select_all_action = QAction("全选\tCtrl+A", self)
        select_all_action.triggered.connect(self.selectAll)
        menu.addAction(select_all_action)

        if self.command_table is not None and self.row_index >= 0:
            menu.addSeparator()
            target_rows = self.command_table.get_replacement_target_rows(self.row_index)
            multiple_rows = len(target_rows) > 1
            replacement_action = QAction(
                "设置选中行替换规则..." if multiple_rows else "设置本行替换规则...",
                self,
            )
            replacement_action.triggered.connect(
                lambda: self.command_table.edit_replacement_rules(target_rows)
            )
            menu.addAction(replacement_action)

            clear_replacement_action = QAction(
                "清除选中行替换规则" if multiple_rows else "清除本行替换规则",
                self,
            )
            clear_replacement_action.setEnabled(
                any(self.command_table.get_replacement_rule(row) for row in target_rows)
            )
            clear_replacement_action.triggered.connect(
                lambda: self.command_table.set_replacement_rules(target_rows, None)
            )
            menu.addAction(clear_replacement_action)

        # --- 外部工具：数字转换器 ---
        selected_text = self.selectedText().strip() if self.hasSelectedText() else None
        self.add_number_converter_actions(menu, self.config_manager, selected_text)

        menu.exec_(self.mapToGlobal(position))

    def keyPressEvent(self, event):
        """处理快捷键"""
        if self.handle_common_shortcuts(event):
            return
        super().keyPressEvent(event)


class CommandTableWidget(QTableWidget, BaseWidgetMixin):
    """命令表格控件"""
    commandsChanged = pyqtSignal(int)
    replacementRulesChanged = pyqtSignal()

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        # 设置右键菜单
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["连续发送", "字符串", "发送"])

        # 设置列宽
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.verticalHeader().setVisible(False)

        # 设置交替行颜色
        self.setAlternatingRowColors(True)

        # 设置表格样式, 包括输入框背景色
        self.setStyleSheet(f"""
            QTableWidget {{
                alternate-background-color: {Colors.TABLE_ODD_ROW};
                background-color: {Colors.TABLE_EVEN_ROW};
                gridline-color: #e0e0e0;
            }}
            QTableWidget::item {{
                border: none;
                padding: 1px;
            }}
            QTableWidget::item:selected {{
                background-color: #2196F3;
                color: white;
            }}
            QLineEdit[commandEditor="true"][rowParity="even"] {{
                background-color: {Colors.TABLE_EVEN_ROW};
            }}
            QLineEdit[commandEditor="true"][rowParity="odd"] {{
                background-color: {Colors.TABLE_ODD_ROW};
            }}
            QLineEdit[commandEditor="true"][validationState="passed"] {{
                background-color: #E8F5E9;
            }}
            QLineEdit[commandEditor="true"][validationState="failed"] {{
                background-color: #FFEBEE;
            }}
            QLineEdit[commandEditor="true"][hasReplacementRuleStyle="true"] {{
                border: 2px solid {Colors.PURPLE_BUTTON};
            }}
            QPushButton[commandSendButton="true"] {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px;
            }}
            QPushButton[commandSendButton="true"]:hover {{
                background-color: #1976D2;
            }}
            QPushButton[commandSendButton="true"]:pressed {{
                background-color: #0D47A1;
            }}
            QPushButton[commandSendButton="true"][replaceableStyle="true"] {{
                background-color: {Colors.PURPLE_BUTTON};
            }}
            QPushButton[commandSendButton="true"][replaceableStyle="true"]:hover {{
                background-color: {Colors.PURPLE_BUTTON_DARK};
            }}
            QPushButton[commandSendButton="true"][replaceableStyle="true"]:pressed {{
                background-color: #4A148C;
            }}
        """)

        # 存储注释数据
        self.comments = {}
        self.replacement_rules = {}

    def add_command_row(self, enable=False, command="", comment="", row_index=None):
        """添加命令行"""
        if row_index is None:
            row_index = self.rowCount()
        self.insertRow(row_index)

        # 连续发送勾选框 (居中)
        checkbox_widget = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_widget)
        enable_checkbox = QCheckBox()
        enable_checkbox.setChecked(enable)
        # 连接勾选框状态改变信号，支持批量操作
        enable_checkbox.stateChanged.connect(lambda state, r=row_index: self.on_checkbox_toggled(state, r))
        checkbox_layout.addWidget(enable_checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.setCellWidget(row_index, 0, checkbox_widget)

        # 命令编辑框 (使用自定义控件)
        # 命令编辑框-右键菜单
        command_edit = CommandLineEdit(self.config_manager)
        command_edit.command_table = self
        command_edit.row_index = row_index
        command_edit.setText(command)
        command_edit.textChanged.connect(lambda _: self.on_command_changed(row_index))
        # 回车发送功能
        command_edit.returnPressed.connect(lambda: self.on_return_pressed(row_index))

        command_edit.setProperty("commandEditor", True)
        command_edit.setProperty("rowParity", "even" if row_index % 2 == 0 else "odd")
        command_edit.setProperty("hasReplacementRuleStyle", False)
        command_edit.setProperty("validationState", "none")

        self.setCellWidget(row_index, 1, command_edit)

        # 发送按钮
        send_btn = QPushButton(f"{row_index + 1}")
        send_btn.setFixedWidth(60)
        send_btn.setProperty("commandSendButton", True)
        self._set_send_button_style(send_btn, False)
        self.setCellWidget(row_index, 2, send_btn)

        # --- DPI-Aware 动态行高 ---
        # 结合字体度量和屏幕DPI缩放因子, 确保在任何分辨率下都能正确显示
        font_metrics = command_edit.fontMetrics()
        
        # 获取设备像素比, 兼容不同版本的PyQt
        try:
            # PyQt5, a more reliable way
            screen = self.window().windowHandle().screen() if self.window() and self.window().windowHandle() else QApplication.primaryScreen()
            device_pixel_ratio = screen.devicePixelRatio()
        except AttributeError:
            # Fallback for older versions or different environments
            device_pixel_ratio = 1.0

        # 计算行高: 字体高度 * 2 (为注释和内边距留出空间) * DPI缩放比例
        # 额外增加一个小的固定值, 作为内边距, 避免文本紧贴边框
        padding = 4 * device_pixel_ratio
        # 增加行高倍数, 从1.8到2.0, 为文本提供更充足的垂直空间
        row_height = int(font_metrics.height() * 2.0 * device_pixel_ratio + padding)
        self.setRowHeight(row_index, row_height)


        # 存储注释
        if comment:
            self.comments[row_index] = comment

        # 连接发送按钮
        self.connect_send_button(row_index, send_btn)
        self.on_command_changed(row_index)

        return send_btn

    def on_command_changed(self, row):
        """命令内容改变时更新注释显示并处理特殊格式"""
        command_edit = self.cellWidget(row, 1)
        if not command_edit:
            return
        command_edit.set_validation_state(None)
            
        text = command_edit.text()
        
        # 处理 SendHex 自动加空格和格式化
        match = re.match(r'^(sendhex):(.*)$', text, re.IGNORECASE)
        if match:
            prefix_name = "SendHex"
            hex_part = match.group(2)
            
            # 记录原始光标位置
            cursor_pos = command_edit.cursorPosition()
            colon_idx = text.find(":")
            
            # 统计光标前有多少个有效的十六进制字符
            hex_chars_before_cursor = 0
            for i in range(colon_idx + 1, min(cursor_pos, len(text))):
                if text[i].lower() in "0123456789abcdef":
                    hex_chars_before_cursor += 1
            
            # 提取所有有效的十六进制字符
            clean_hex = "".join(c for c in hex_part if c.lower() in "0123456789abcdef")
            
            # 每两个字符加一个空格进行格式化
            formatted_hex = ""
            for i in range(0, len(clean_hex), 2):
                formatted_hex += clean_hex[i:i+2] + " "
            formatted_hex = formatted_hex.strip().upper()
            
            # 规范化格式： "SendHex:" + 格式化后的十六进制 (不强制加空格，保持紧凑)
            new_text = f"{prefix_name}:{formatted_hex}"
            
            if new_text != text:
                command_edit.blockSignals(True)
                command_edit.setText(new_text)
                
                # 重新计算光标位置：找到第 hex_chars_before_cursor 个十六进制字符的位置
                if cursor_pos <= colon_idx + 1:
                    # 光标在冒号前或刚过冒号，保持相对位置
                    command_edit.setCursorPosition(min(cursor_pos, len(prefix_name) + 1))
                else:
                    # 光标在十六进制部分
                    new_pos = len(prefix_name) + 1
                    if hex_chars_before_cursor > 0:
                        count = 0
                        for i in range(len(prefix_name) + 1, len(new_text)):
                            if new_text[i].lower() in "0123456789abcdef":
                                count += 1
                            if count == hex_chars_before_cursor:
                                new_pos = i + 1
                                break
                    command_edit.setCursorPosition(new_pos)
                    
                command_edit.blockSignals(False)

        comment = self.comments.get(row, "")
        command_edit.set_comment(comment)
        self.commandsChanged.emit(row)

    def on_checkbox_toggled(self, state, row):
        """当勾选框状态改变时，如果该行在选中范围内，同步更新其他选中行"""
        # 获取所有选中的行号
        selected_rows = set(item.row() for item in self.selectionModel().selectedRows())
        
        # 如果当前行在选中行中，且选中了多行
        if row in selected_rows and len(selected_rows) > 1:
            is_checked = (state == Qt.Checked)
            for r in selected_rows:
                if r == row:
                    continue
                widget = self.cellWidget(r, 0)
                if widget:
                    cb = widget.findChild(QCheckBox)
                    if cb:
                        cb.blockSignals(True)
                        cb.setChecked(is_checked)
                        cb.blockSignals(False)
        self.commandsChanged.emit(row)

    def get_row_data(self, row):
        """获取行数据"""
        checkbox_widget = self.cellWidget(row, 0)
        enable = checkbox_widget.findChild(QCheckBox).isChecked()
        command_edit = self.cellWidget(row, 1)
        command = command_edit.text() if command_edit else ""
        comment = self.comments.get(row, "")
        return enable, command, comment

    def set_row_comment(self, row, comment):
        """设置行注释"""
        self.comments[row] = comment
        command_edit = self.cellWidget(row, 1)
        if command_edit:
            command_edit.set_comment(comment)
        self.commandsChanged.emit(row)

    def get_all_commands(self):
        """获取所有命令数据"""
        commands = []
        for row in range(self.rowCount()):
            enable, command, comment = self.get_row_data(row)
            commands.append((enable, command, comment))
        return commands

    def get_replacement_rule(self, row):
        return self.replacement_rules.get(row)

    def set_replacement_rule(self, row, rule):
        self.set_replacement_rules([row], rule)

    def set_replacement_rules(self, rows, rule):
        rule = normalize_rule(rule)
        valid_rows = sorted({row for row in rows if 0 <= row < self.rowCount()})
        for row in valid_rows:
            if rule:
                self.replacement_rules[row] = copy.deepcopy(rule)
            else:
                self.replacement_rules.pop(row, None)
            edit = self.cellWidget(row, 1)
            if edit:
                edit.set_validation_state(None)
            self.update_row_replacement_indicator(row)
        self.replacementRulesChanged.emit()

    def edit_replacement_rule(self, row):
        self.edit_replacement_rules([row])

    def edit_replacement_rules(self, rows):
        rows = sorted({row for row in rows if 0 <= row < self.rowCount()})
        if not rows:
            return
        existing_rules = [self.get_replacement_rule(row) for row in rows]
        initial_rule = existing_rules[0] if all(rule == existing_rules[0] for rule in existing_rules) else None
        title = (
            f"选中 {len(rows)} 行替换规则"
            if len(rows) > 1
            else f"第 {rows[0] + 1} 行替换规则"
        )
        dialog = ReplacementRuleDialog(
            initial_rule,
            title=title,
            parent=self,
            default_rule=getattr(
                self.get_main_window(),
                "DEFAULT_GLOBAL_REPLACEMENT_RULE",
                None,
            ),
            default_timeout_ms=self._default_response_timeout(),
        )
        if dialog.exec_() == QDialog.Accepted:
            self.set_replacement_rules(
                rows,
                None if dialog.clear_requested else dialog.get_rule(),
            )

    def get_replacement_target_rows(self, row):
        selected_rows = sorted({index.row() for index in self.selectionModel().selectedRows()})
        if len(selected_rows) > 1 and row in selected_rows:
            return selected_rows
        return [row]

    def get_replacement_rules(self):
        return {str(row): rule for row, rule in self.replacement_rules.items()}

    def load_replacement_rules(self, rules, notify=True):
        self.replacement_rules = {}
        for row, rule in (rules or {}).items():
            try:
                row_index = int(row)
            except (TypeError, ValueError):
                continue
            normalized = normalize_rule(rule)
            if normalized and 0 <= row_index < self.rowCount():
                self.replacement_rules[row_index] = normalized
        self.update_all_replacement_indicators()
        if notify:
            self.replacementRulesChanged.emit()

    def update_row_replacement_indicator(self, row):
        edit = self.cellWidget(row, 1)
        if not edit:
            return
        parity = "even" if row % 2 == 0 else "odd"
        has_rule = row in self.replacement_rules
        style_changed = (
            edit.property("rowParity") != parity
            or edit.property("hasReplacementRuleStyle") != has_rule
        )
        if not style_changed:
            return
        edit.setProperty("rowParity", parity)
        edit.setProperty("hasReplacementRuleStyle", has_rule)
        if has_rule:
            edit.setToolTip("本行已设置独立替换规则")
        else:
            edit.setToolTip("")
        edit.style().unpolish(edit)
        edit.style().polish(edit)
        edit.update()

    def update_all_replacement_indicators(self):
        for row in range(self.rowCount()):
            self.update_row_replacement_indicator(row)

    def clear_validation_states(self):
        for row in range(self.rowCount()):
            edit = self.cellWidget(row, 1)
            if edit:
                edit.set_validation_state(None)

    def _default_response_timeout(self):
        main_window = self.get_main_window()
        interval_spin = getattr(main_window, "interval_spin", None)
        return interval_spin.value() if interval_spin is not None else 100

    def _set_send_button_style(self, button, replaceable):
        if button.property("replaceableStyle") == replaceable:
            return
        had_style = button.property("replaceableStyle") is not None
        button.setProperty("replaceableStyle", replaceable)
        if had_style:
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def set_send_button_replaceable(self, row, replaceable):
        button = self.cellWidget(row, 2)
        if button:
            self._set_send_button_style(button, replaceable)

    def clear_all(self):
        """清空所有行"""
        self.setRowCount(0)
        self.comments.clear()
        self.replacement_rules.clear()
        self.commandsChanged.emit(-1)
        self.replacementRulesChanged.emit()

    def show_context_menu(self, position):
        """显示表格行右键菜单"""
        row = self.rowAt(position.y())
        column = self.columnAt(position.x())

        # 如果点击的是输入框区域, 不显示表格菜单 (输入框有自己的菜单)
        if column == 1:
            item = self.cellWidget(row, column)
            if item and item.underMouse():
                return

        if row < 0:
            return

        menu = UIUtils.create_styled_menu(self)

        # 修改注释
        edit_comment_action = QAction("修改注释", self)
        edit_comment_action.triggered.connect(lambda: self.edit_comment(row))
        menu.addAction(edit_comment_action)

        replacement_target_rows = self.get_replacement_target_rows(row)
        multiple_replacement_rows = len(replacement_target_rows) > 1
        replacement_action = QAction(
            "设置选中行替换规则..." if multiple_replacement_rows else "设置本行替换规则...",
            self,
        )
        replacement_action.triggered.connect(
            lambda: self.edit_replacement_rules(replacement_target_rows)
        )
        menu.addAction(replacement_action)

        clear_replacement_action = QAction(
            "清除选中行替换规则" if multiple_replacement_rows else "清除本行替换规则",
            self,
        )
        clear_replacement_action.setEnabled(
            any(self.get_replacement_rule(target_row) for target_row in replacement_target_rows)
        )
        clear_replacement_action.triggered.connect(
            lambda: self.set_replacement_rules(replacement_target_rows, None)
        )
        menu.addAction(clear_replacement_action)

        # 特殊指令
        special_command_action = QAction("特殊指令", self)
        special_command_menu = UIUtils.create_styled_menu(self)
        special_command_menu.setTitle("添加特殊指令")

        mode_action = QAction("mode (模块命名)", self)
        mode_action.triggered.connect(lambda: self.add_special_command(row, "mode"))
        special_command_menu.addAction(mode_action)

        modeend_action = QAction("modeend (结束模块定义)", self)
        modeend_action.triggered.connect(lambda: self.add_special_command(row, "modeend"))
        special_command_menu.addAction(modeend_action)

        delay_action = QAction("delay (延迟设置)", self)
        delay_action.triggered.connect(lambda: self.add_special_command(row, "delay"))
        special_command_menu.addAction(delay_action)

        sendhex_action = QAction("SendHex (十六进制发送)", self)
        sendhex_action.triggered.connect(lambda: self.add_special_command(row, "sendhex"))
        special_command_menu.addAction(sendhex_action)

        baudrate_action = QAction("BaudRate (波特率设置)", self)
        baudrate_action.triggered.connect(lambda: self.add_special_command(row, "baudrate"))
        special_command_menu.addAction(baudrate_action)

        setendlog_action = QAction("SetEndlog (结尾符设置)", self)
        setendlog_action.triggered.connect(lambda: self.add_special_command(row, "setendlog"))
        special_command_menu.addAction(setendlog_action)

        sendmode_action = QAction("SendMode (发送指定模块)", self)
        sendmode_action.triggered.connect(lambda: self.add_special_command(row, "sendmode"))
        special_command_menu.addAction(sendmode_action)

        stopcontinuous_action = QAction("StopContinuous (停止发送)", self)
        stopcontinuous_action.triggered.connect(lambda: self.add_special_command(row, "stopcontinuous"))
        special_command_menu.addAction(stopcontinuous_action)

        main_window = self.get_main_window()
        config_manager = getattr(main_window, "config_manager", None) if main_window else None
        if (
            config_manager
            and config_manager.is_tool_enabled("firmware_downloader")
            and config_manager.is_tool_available("firmware_downloader")
        ):
            firmware_action = QAction("FirmwareDownload (固件下载)", self)
            firmware_action.triggered.connect(
                lambda: self.add_special_command(row, "firmwaredownload")
            )
            special_command_menu.addAction(firmware_action)

        special_command_action.setMenu(special_command_menu)
        menu.addAction(special_command_action)

        menu.addSeparator()

        # 获取选中的行
        selected_rows = self.selectionModel().selectedRows()
        num_selected = len(selected_rows)

        # 在上方插入行
        if num_selected > 1:
            # 获取选中的最小行号，确保在最上方插入
            min_row = min(index.row() for index in selected_rows)
            insert_action = QAction(f"在上方插入 {num_selected} 行", self)
            insert_action.triggered.connect(lambda: self.insert_row_above(min_row, num_selected))
        else:
            insert_action = QAction("在上方插入行", self)
            insert_action.triggered.connect(lambda: self.insert_row_above(row))
        menu.addAction(insert_action)

        # 插入多行
        insert_multi_action = QAction("插入多行...", self)
        # 如果选中多行，则在选中的最上方插入
        target_row = min(index.row() for index in selected_rows) if num_selected > 1 else row
        insert_multi_action.triggered.connect(lambda: self.insert_multi_rows(target_row))
        menu.addAction(insert_multi_action)

        insert_from_text_action = QAction("根据文本添加多行...", self)
        insert_from_text_action.triggered.connect(lambda: self.insert_rows_from_text(target_row))
        menu.addAction(insert_from_text_action)

        # 删除行
        if num_selected > 1:
            delete_action = QAction(f"删除选中的 {num_selected} 行", self)
            delete_action.triggered.connect(self.delete_selected_rows)
        else:
            delete_action = QAction("删除行", self)
            delete_action.triggered.connect(lambda: self.delete_row(row))
        menu.addAction(delete_action)

        menu.exec_(self.viewport().mapToGlobal(position))

    def edit_comment(self, row):
        """编辑注释"""
        current_comment = self.comments.get(row, "")
        dialog = CommentEditDialog(current_comment, self)
        if dialog.exec_() == QDialog.Accepted:
            new_comment = dialog.get_comment()
            self.set_row_comment(row, new_comment)

    def add_special_command(self, row, command_type):
        """添加特殊指令"""
        command_edit = self.cellWidget(row, 1)
        if not command_edit:
            return

        current_text = command_edit.text()
        cmd_type_str, param = UIUtils.parse_special_command(current_text)

        if command_type == "mode":
            initial_val = ""
            if cmd_type_str == "mode":
                initial_val = param.strip()
            module_name, ok = QInputDialog.getText(self, "模块命名", "请输入模块名称:", QLineEdit.Normal, initial_val)
            if ok and module_name:
                command_edit.setText(f"mode:{module_name}")
                self._uncheck_row(row)

                # 通过主窗口刷新模块列表并显示系统消息
                # 查找主窗口实例
                main_window = None
                parent = self.parent()
                while parent is not None:
                    if hasattr(parent, 'refresh_modules') and hasattr(parent, 'output_manager'):
                        main_window = parent
                        break
                    parent = parent.parent()

                if main_window:
                    # 刷新模块列表
                    main_window.refresh_modules(silent=True)
                    # 显示系统消息
                    from utils.ui_utils import OutputSource
                    main_window.output_manager.append_text(f"已创建模块: '{module_name}'", OutputSource.SYSTEM)
        elif command_type == "modeend":
            # modeend 指令不需要参数，直接设置为 modeend:0
            command_edit.setText("modeend:0")
            self._uncheck_row(row)

            # 通过主窗口刷新模块列表并显示系统消息
            main_window = None
            parent = self.parent()
            while parent is not None:
                if hasattr(parent, 'refresh_modules') and hasattr(parent, 'output_manager'):
                    main_window = parent
                    break
                parent = parent.parent()

            if main_window:
                # 刷新模块列表
                main_window.refresh_modules(silent=True)
                # 显示系统消息
                from utils.ui_utils import OutputSource
                main_window.output_manager.append_text("已添加 modeend 指令: 结束当前模块定义", OutputSource.SYSTEM)
        elif command_type == "delay":
            initial_val = 100.0
            if cmd_type_str == "delay":
                try:
                    initial_val = float(param.strip())
                except:
                    pass
            # 使用 getDouble 确保只能输入数字，范围 0-1000000ms
            delay_time, ok = QInputDialog.getDouble(self, "延迟设置", "请输入延迟时间(ms):", initial_val, 0, 1000000, 1)
            if ok:
                # 如果是整数，则显示为整数形式
                display_val = int(delay_time) if delay_time.is_integer() else delay_time
                command_edit.setText(f"delay:{display_val}")
                self._uncheck_row(row)
        elif command_type == "sendhex":
            initial_hex = ""
            if cmd_type_str == "sendhex":
                initial_hex = param.strip()
            # 使用自定义的十六进制输入对话框
            dialog = HexInputDialog(initial_text=initial_hex, parent=self)
            if dialog.exec_() == QDialog.Accepted:
                hex_data = dialog.get_text()
                command_edit.setText(f"SendHex:{hex_data}")
                self._uncheck_row(row)
        elif command_type == "baudrate":
            # 常用波特率列表
            baudrate_options = ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
            initial_val = "115200"
            if cmd_type_str == "baudrate":
                initial_val = param.strip()
            
            # 计算初始选中索引
            initial_idx = 0
            if initial_val in baudrate_options:
                initial_idx = baudrate_options.index(initial_val)
            
            # 使用 getItem 并设置 editable=True 允许手动输入
            baudrate, ok = QInputDialog.getItem(self, "BaudRate设置", "请选择或输入波特率:", baudrate_options, initial_idx, True)
            if ok and baudrate:
                # 验证输入是否为有效数字
                try:
                    baudrate_int = int(baudrate)
                    if baudrate_int > 0:
                        command_edit.setText(f"BaudRate:{baudrate_int}")
                        self._uncheck_row(row)
                except ValueError:
                    pass  # 无效输入，不做任何操作
        elif command_type == "setendlog":
            # 使用正确的转义字符表示
            options = ["None", "\\r\\n", "\\r", "\\n"]
            initial_idx = 0
            if cmd_type_str == "setendlog":
                val = param.strip()
                # 匹配转义字符
                if val in options:
                    initial_idx = options.index(val)
                elif val.lower() == "none":
                    initial_idx = 0
            # 使用 getItem 提供预选项
            ending, ok = QInputDialog.getItem(self, "SetEndlog设置", "请选择结尾标识符:", options, initial_idx, False)
            if ok:
                command_edit.setText(f"SetEndlog:{ending}")
                self._uncheck_row(row)
        elif command_type == "sendmode":
            main_window = self.get_main_window()
            options = ["全部"]
            if main_window and hasattr(main_window, 'modules'):
                # 刷新一下模块列表以确保最新
                main_window.refresh_modules()
                # 排除"默认"模块，除非它真的被重命名了
                options.extend([m for m in main_window.modules.keys() if m != "默认"])

            initial_val = "全部"
            if cmd_type_str == "sendmode":
                val = param.strip()
                if val in options:
                    initial_val = val
                else:
                    initial_val = val  # 允许不在列表中的值

            initial_idx = options.index(initial_val) if initial_val in options else 0

            # 使用 getItem 并允许编辑，方便用户输入新模块名或选择现有模块
            module_name, ok = QInputDialog.getItem(self, "SendMode 设置", "请选择要跳转发送的模块:", options, initial_idx, True)
            if ok and module_name:
                command_edit.setText(f"SendMode:{module_name}")
                self._uncheck_row(row)
        elif command_type == "stopcontinuous":
            # StopContinuous 指令 - 可选参数 0 或 1
            # 0 = 仅停止连续发送（默认）
            # 1 = 同时停止连续发送和循环发送
            options = ["0 - 仅停止连续发送", "1 - 同时停止连续和循环发送"]
            initial_idx = 0
            if cmd_type_str == "stopcontinuous":
                val = param.strip()
                if val == "1":
                    initial_idx = 1

            choice, ok = QInputDialog.getItem(self, "StopContinuous 设置", "请选择停止模式:", options, initial_idx, False)
            if ok:
                param_val = "0" if "0 -" in choice else "1"
                command_edit.setText(f"StopContinuous:{param_val}")
                self._uncheck_row(row)
        elif command_type == "firmwaredownload":
            initial_path = param.strip() if cmd_type_str == "firmwaredownload" else ""
            if len(initial_path) >= 2 and initial_path[0] == initial_path[-1] and initial_path[0] in ("'", '"'):
                initial_path = initial_path[1:-1]
            dialog = FirmwareDownloadPathDialog(initial_path, self)
            if dialog.exec_() == QDialog.Accepted:
                firmware_path = dialog.path_text()
                if firmware_path and any(char.isspace() for char in firmware_path):
                    firmware_path = f'"{firmware_path}"'
                command_edit.setText(f"FirmwareDownload:{firmware_path}")
                self._uncheck_row(row)

    def _uncheck_row(self, row):
        """取消勾选指定行的连续发送"""
        checkbox_widget = self.cellWidget(row, 0)
        if checkbox_widget:
            cb = checkbox_widget.findChild(QCheckBox)
            if cb:
                cb.setChecked(False)


    def insert_row_above(self, row, count=1):
        """在指定行上方插入新行"""
        if count <= 0:
            return

        # 更新注释存储
        new_comments = {}
        for old_row, comment in self.comments.items():
            if old_row < row:
                new_comments[old_row] = comment
            else:  # old_row >= row
                new_comments[old_row + count] = comment
        self.comments = new_comments
        self.replacement_rules = {
            (old_row if old_row < row else old_row + count): rule
            for old_row, rule in self.replacement_rules.items()
        }

        for i in range(count):
            self.add_command_row(False, "", "", row + i)

        # 更新下方行的发送按钮编号和连接
        self.update_send_buttons_after_row(row)

        # 刷新模块列表
        main_window = self.get_main_window()
        if main_window:
            main_window.refresh_modules(silent=True)

    def insert_multi_rows(self, row):
        """弹出对话框询问插入行数并执行插入"""
        count, ok = QInputDialog.getInt(self, "插入多行", "请输入要插入的行数:", 1, 1, 100, 1)
        if ok:
            self.insert_row_above(row, count)

    def insert_rows_from_text(self, row):
        """根据用户输入的多行文本插入命令行。"""
        dialog = TextRowsInputDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            self.insert_command_rows_above(row, dialog.get_rows())

    def insert_command_rows_above(self, row, commands):
        """在指定行上方插入带内容的多行命令。"""
        if not commands:
            return

        count = len(commands)
        new_comments = {}
        for old_row, comment in self.comments.items():
            if old_row < row:
                new_comments[old_row] = comment
            else:
                new_comments[old_row + count] = comment
        self.comments = new_comments
        self.replacement_rules = {
            (old_row if old_row < row else old_row + count): rule
            for old_row, rule in self.replacement_rules.items()
        }

        self.setUpdatesEnabled(False)
        try:
            for offset, (enable, command, comment) in enumerate(commands):
                self.add_command_row(enable, command, comment, row + offset)
            self.update_send_buttons_after_row(row)
        finally:
            self.setUpdatesEnabled(True)
            self.viewport().update()

        main_window = self.get_main_window()
        if main_window:
            main_window.refresh_modules(silent=True)

    def connect_send_button(self, row, send_btn):
        """连接发送按钮到正确的行号"""
        # 先断开所有现有连接
        try:
            send_btn.clicked.disconnect()
        except:
            pass
            
        # 获取主窗口
        main_window = self.get_main_window()
        if main_window:
            send_btn.clicked.connect(lambda checked, r=row: main_window.on_send_clicked(r))

    def delete_row(self, row):
        """删除指定行"""
        self.removeRow(row)

        # 更新注释存储
        new_comments = {}
        for old_row in self.comments:
            if old_row < row:
                new_comments[old_row] = self.comments[old_row]
            elif old_row > row:
                new_comments[old_row - 1] = self.comments[old_row]
        self.comments = new_comments
        self.replacement_rules = {
            (old_row if old_row < row else old_row - 1): rule
            for old_row, rule in self.replacement_rules.items()
            if old_row != row
        }

        # 更新发送按钮编号和连接
        self.update_send_buttons_after_row(row)

        # 刷新模块列表
        main_window = self.get_main_window()
        if main_window:
            main_window.refresh_modules(silent=True)

    def delete_selected_rows(self):
        """删除所有选中的行"""
        # 获取所有选中的行索引并去重，从大到小排序以安全删除
        selected_indices = self.selectionModel().selectedRows()
        if not selected_indices:
            return
            
        rows_to_delete = sorted([index.row() for index in selected_indices], reverse=True)
        min_affected_row = rows_to_delete[-1]

        # 执行删除
        for row in rows_to_delete:
            self.removeRow(row)

        # 重新构建注释字典
        new_comments = {}
        old_comments = sorted(self.comments.items())
        for old_row, comment in old_comments:
            if old_row in rows_to_delete:
                continue
            # 计算新行号：旧行号 - 小于该行号的被删除行数
            offset = len([r for r in rows_to_delete if r < old_row])
            new_comments[old_row - offset] = comment
        self.comments = new_comments
        self.replacement_rules = {
            old_row - len([deleted_row for deleted_row in rows_to_delete if deleted_row < old_row]): rule
            for old_row, rule in self.replacement_rules.items()
            if old_row not in rows_to_delete
        }

        # 更新发送按钮编号和连接
        self.update_send_buttons_after_row(min_affected_row)

        # 刷新模块列表
        main_window = self.get_main_window()
        if main_window:
            main_window.refresh_modules(silent=True)

    def on_return_pressed(self, row):
        """编辑框回车事件"""
        main_window = self.get_main_window()
        if main_window:
            main_window.on_send_clicked(row)

    def update_send_buttons_after_row(self, start_row):
        """从指定行开始更新所有发送按钮的编号、连接以及背景颜色"""
        for row in range(start_row, self.rowCount()):
            # 更新勾选框连接
            checkbox_widget = self.cellWidget(row, 0)
            if checkbox_widget:
                cb = checkbox_widget.findChild(QCheckBox)
                if cb:
                    try:
                        cb.stateChanged.disconnect()
                    except:
                        pass
                    cb.stateChanged.connect(lambda state, r=row: self.on_checkbox_toggled(state, r))

            # 更新发送按钮
            btn = self.cellWidget(row, 2)
            if btn:
                btn.setText(f"{row + 1}")
                # 重新连接按钮以确保使用正确的行号
                self.connect_send_button(row, btn)
            
            # 更新编辑框
            edit = self.cellWidget(row, 1)
            if edit:
                edit.row_index = row
                # 更新回车连接
                try:
                    edit.returnPressed.disconnect()
                except:
                    pass
                edit.returnPressed.connect(lambda checked=False, r=row: self.on_return_pressed(r))
                
                # 更新文本改变连接 (重要：确保 SendHex 格式化在行号变动后依然有效)
                try:
                    edit.textChanged.disconnect()
                except:
                    pass
                # 注意：textChanged 信号会传递一个字符串参数，需要用 lambda _, r=row 接收
                edit.textChanged.connect(lambda _, r=row: self.on_command_changed(r))
                
                self.update_row_replacement_indicator(row)

        self.commandsChanged.emit(-1)
        self.replacementRulesChanged.emit()
