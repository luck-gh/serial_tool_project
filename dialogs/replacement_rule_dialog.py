#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""发送字符串临时替换规则编辑对话框。"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QDialogButtonBox, QPushButton, QMessageBox
)

from core.text_search import MatchOptions, compile_pattern, normalize_rule


class ReplacementRuleDialog(QDialog):
    def __init__(self, rule=None, title="编辑替换规则", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.clear_requested = False
        rule = normalize_rule(rule) or {"find": "", "replace": "", "options": {}}
        options = MatchOptions.from_dict(rule.get("options"))

        layout = QVBoxLayout(self)
        find_layout = QHBoxLayout()
        find_layout.addWidget(QLabel("查找:"))
        self.find_input = QLineEdit(rule.get("find", ""))
        find_layout.addWidget(self.find_input, 1)
        layout.addLayout(find_layout)

        replace_layout = QHBoxLayout()
        replace_layout.addWidget(QLabel("替换为:"))
        self.replace_input = QLineEdit(rule.get("replace", ""))
        replace_layout.addWidget(self.replace_input, 1)
        layout.addLayout(replace_layout)

        option_layout = QHBoxLayout()
        self.case_check = QCheckBox("区分大小写")
        self.case_check.setChecked(options.case_sensitive)
        self.regex_check = QCheckBox("正则表达式")
        self.regex_check.setChecked(options.use_regex)
        self.whole_check = QCheckBox("全字符匹配（完整单词）")
        self.whole_check.setChecked(options.whole_word)
        option_layout.addWidget(self.case_check)
        option_layout.addWidget(self.regex_check)
        option_layout.addWidget(self.whole_check)
        option_layout.addStretch()
        layout.addLayout(option_layout)

        group_help = QLabel(
            "正则替换支持分组引用：$1、${1}；$& 表示完整匹配，$$ 表示字符 $。"
        )
        group_help.setStyleSheet("color: #666666;")
        group_help.setWordWrap(True)
        layout.addWidget(group_help)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.clear_btn = QPushButton("清除规则")
        buttons.addButton(self.clear_btn, QDialogButtonBox.ResetRole)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        self.clear_btn.clicked.connect(self._clear_and_accept)
        layout.addWidget(buttons)

        self.find_input.setFocus()

    def get_rule(self):
        return normalize_rule({
            "find": self.find_input.text(),
            "replace": self.replace_input.text(),
            "options": {
                "case_sensitive": self.case_check.isChecked(),
                "use_regex": self.regex_check.isChecked(),
                "whole_word": self.whole_check.isChecked(),
            },
        })

    def _validate_and_accept(self):
        rule = self.get_rule()
        if not rule:
            QMessageBox.warning(self, "规则无效", "查找字符串不能为空。")
            return
        try:
            compile_pattern(rule["find"], rule["options"])
        except ValueError as exc:
            QMessageBox.warning(self, "规则无效", str(exc))
            return
        self.accept()

    def _clear_and_accept(self):
        self.clear_requested = True
        self.accept()
