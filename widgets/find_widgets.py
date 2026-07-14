#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""接收区和发送编辑区共用的查找栏。"""

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QCheckBox, QLabel, QComboBox
)

from core.text_search import MatchOptions


class FindBar(QWidget):
    searchChanged = pyqtSignal(str, object, str)
    navigateRequested = pyqtSignal(int)
    closed = pyqtSignal()

    def __init__(self, show_scope=False, parent=None):
        super().__init__(parent)
        self.show_scope = show_scope
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(5)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("查找")
        self.search_input.setClearButtonEnabled(True)
        layout.addWidget(self.search_input, 1)

        self.scope_combo = QComboBox(self)
        self.scope_combo.addItem("全部")
        if not show_scope:
            self.scope_combo.hide()
        if show_scope:
            self.scope_combo.setToolTip("查找范围")
            layout.addWidget(self.scope_combo)

        self.case_check = QCheckBox("Aa")
        self.case_check.setToolTip("区分大小写")
        self.regex_check = QCheckBox(".*")
        self.regex_check.setToolTip("使用正则表达式")
        self.whole_check = QCheckBox("全词")
        self.whole_check.setToolTip("全字符匹配（完整单词）")
        layout.addWidget(self.case_check)
        layout.addWidget(self.regex_check)
        layout.addWidget(self.whole_check)

        self.result_label = QLabel("0/0")
        self.result_label.setMinimumWidth(54)
        self.result_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.result_label)

        self.previous_btn = QPushButton("↑")
        self.previous_btn.setFixedWidth(30)
        self.previous_btn.setToolTip("上一个 (Shift+Enter)")
        self.next_btn = QPushButton("↓")
        self.next_btn.setFixedWidth(30)
        self.next_btn.setToolTip("下一个 (Enter)")
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedWidth(30)
        self.close_btn.setToolTip("关闭 (Esc)")
        layout.addWidget(self.previous_btn)
        layout.addWidget(self.next_btn)
        layout.addWidget(self.close_btn)

        self.search_input.textChanged.connect(self._emit_search)
        self.case_check.toggled.connect(self._emit_search)
        self.regex_check.toggled.connect(self._emit_search)
        self.whole_check.toggled.connect(self._emit_search)
        self.scope_combo.currentTextChanged.connect(self._emit_search)
        self.search_input.returnPressed.connect(lambda: self.navigateRequested.emit(1))
        self.previous_btn.clicked.connect(lambda: self.navigateRequested.emit(-1))
        self.next_btn.clicked.connect(lambda: self.navigateRequested.emit(1))
        self.close_btn.clicked.connect(self.close_bar)

    def options(self):
        return MatchOptions(
            case_sensitive=self.case_check.isChecked(),
            use_regex=self.regex_check.isChecked(),
            whole_word=self.whole_check.isChecked(),
        )

    def _emit_search(self, *_args):
        self.searchChanged.emit(
            self.search_input.text(),
            self.options(),
            self.scope_combo.currentText() if self.show_scope else "全部",
        )

    def set_scopes(self, scopes):
        if not self.show_scope:
            return
        current = self.scope_combo.currentText()
        self.scope_combo.blockSignals(True)
        self.scope_combo.clear()
        self.scope_combo.addItem("全部")
        self.scope_combo.addItems([scope for scope in scopes if scope != "全部"])
        index = self.scope_combo.findText(current)
        self.scope_combo.setCurrentIndex(index if index >= 0 else 0)
        self.scope_combo.blockSignals(False)
        self._emit_search()

    def show_and_focus(self, selected_text=""):
        self.show()
        if selected_text:
            self.search_input.setText(selected_text)
        self.search_input.setFocus()
        self.search_input.selectAll()
        self._emit_search()

    def set_result(self, current, total, error=""):
        if error:
            self.result_label.setText("错误")
            self.result_label.setToolTip(error)
            self.search_input.setStyleSheet("border: 1px solid #f44336;")
            return
        self.search_input.setStyleSheet("")
        self.result_label.setToolTip("")
        self.result_label.setText(f"{current}/{total}" if total else "0/0")

    def close_bar(self):
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_bar()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
            self.navigateRequested.emit(-1)
            return
        super().keyPressEvent(event)
