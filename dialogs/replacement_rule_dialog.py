#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""发送字符串临时替换规则编辑对话框。"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QCheckBox, QDialogButtonBox, QPushButton, QMessageBox, QSpinBox,
    QComboBox, QWidget
)

from core.text_search import (
    MatchOptions,
    compile_pattern,
    normalize_rule,
    validate_response_expression,
)


class ResponseValidationSettingsDialog(QDialog):
    def __init__(self, settings, failure_options, color_policy_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("响应匹配设置")
        self.setMinimumWidth(360)
        self.failure_options = failure_options
        self.color_policy_options = color_policy_options

        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 60000)
        self.timeout_spin.setValue(settings.get("timeout_ms", 100))
        form_layout.addRow("超时(ms):", self.timeout_spin)

        self.failure_combo = QComboBox()
        self.failure_combo.addItems(self.failure_options.keys())
        ReplacementRuleDialog._select_combo_value(
            self.failure_combo,
            self.failure_options,
            settings.get("on_failure", "continue"),
        )
        form_layout.addRow("失败后:", self.failure_combo)

        self.color_policy_combo = QComboBox()
        self.color_policy_combo.addItems(self.color_policy_options.keys())
        ReplacementRuleDialog._select_combo_value(
            self.color_policy_combo,
            self.color_policy_options,
            settings.get("color_policy", "sticky_failure"),
        )
        form_layout.addRow("循环策略:", self.color_policy_combo)

        self.show_error_check = QCheckBox("显示匹配系统错误")
        self.show_error_check.setChecked(settings.get("show_error", True))
        form_layout.addRow("错误信息:", self.show_error_check)
        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self):
        return {
            "timeout_ms": self.timeout_spin.value(),
            "on_failure": self.failure_options[self.failure_combo.currentText()],
            "color_policy": self.color_policy_options[
                self.color_policy_combo.currentText()
            ],
            "show_error": self.show_error_check.isChecked(),
        }


class ReplacementRuleDialog(QDialog):
    FAILURE_OPTIONS = {
        "继续发送": "continue",
        "停止连续发送": "stop",
    }
    COLOR_POLICY_OPTIONS = {
        "显示最近一次结果": "latest",
        "失败后保持红色": "sticky_failure",
    }

    def __init__(
        self,
        rule=None,
        title="编辑替换规则",
        parent=None,
        default_rule=None,
        default_timeout_ms=100,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.clear_requested = False
        self.default_rule = normalize_rule(default_rule)
        self.default_timeout_ms = max(10, min(60000, int(default_timeout_ms)))
        rule = normalize_rule(rule) or {"find": "", "replace": "", "options": {}}
        options = MatchOptions.from_dict(rule.get("options"))
        response_validation = rule.get("response_validation") or {}
        self.response_settings = {
            "timeout_ms": response_validation.get(
                "timeout_ms", self.default_timeout_ms
            ),
            "on_failure": response_validation.get("on_failure", "continue"),
            "color_policy": response_validation.get(
                "color_policy", "sticky_failure"
            ),
            "show_error": response_validation.get("show_error", False),
        }

        layout = QVBoxLayout(self)
        rule_layout = QFormLayout()
        rule_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.find_input = QLineEdit(rule.get("find", ""))
        self.replace_input = QLineEdit(rule.get("replace", ""))
        self.response_input = QLineEdit(response_validation.get("expression", ""))
        self.response_controls_widget = QWidget()
        response_layout = QHBoxLayout(self.response_controls_widget)
        response_layout.setContentsMargins(0, 0, 0, 0)
        response_layout.setSpacing(6)
        response_layout.addWidget(self.response_input, 1)
        self.response_input.setStyleSheet(
            "QLineEdit:disabled {"
            "background-color: #F0F0F0; color: #888888; border-color: #D6D6D6;"
            "}"
        )
        self.response_settings_btn = QPushButton("设置...")
        self.response_settings_btn.setFixedWidth(72)
        response_layout.addWidget(self.response_settings_btn)
        rule_layout.addRow("查找:", self.find_input)
        rule_layout.addRow("替换为:", self.replace_input)
        rule_layout.addRow("响应表达式:", self.response_controls_widget)
        layout.addLayout(rule_layout)

        option_layout = QHBoxLayout()
        self.case_check = QCheckBox("区分大小写")
        self.case_check.setChecked(options.case_sensitive)
        self.regex_check = QCheckBox("正则表达式")
        self.regex_check.setChecked(options.use_regex)
        self.whole_check = QCheckBox("全字符匹配（完整单词）")
        self.whole_check.setChecked(options.whole_word)
        self.response_enable_check = QCheckBox("开启响应匹配")
        self.response_enable_check.setChecked(
            response_validation.get("enabled", False)
        )
        option_layout.addWidget(self.case_check)
        option_layout.addWidget(self.regex_check)
        option_layout.addWidget(self.whole_check)
        option_layout.addWidget(self.response_enable_check)
        option_layout.addStretch()
        layout.addLayout(option_layout)

        group_help = QLabel(
            "替换和响应表达式支持 $1、${1}、${name}；响应中的普通正则只负责查找，"
            "引用的 $ 分组值同时用于校验。替换中的 $& 表示完整匹配，$$ 表示字符 $。"
        )
        group_help.setStyleSheet("color: #666666;")
        group_help.setWordWrap(True)
        layout.addWidget(group_help)

        footer_layout = QHBoxLayout()
        self.clear_btn = QPushButton("清除规则")
        self.default_btn = QPushButton("默认规则")
        footer_layout.addWidget(self.clear_btn)
        footer_layout.addWidget(self.default_btn)
        footer_layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        self.clear_btn.clicked.connect(self._clear_rule_fields)
        self.default_btn.clicked.connect(self._apply_default_rule)
        self.default_btn.setEnabled(self.default_rule is not None)
        self.response_settings_btn.clicked.connect(self._edit_response_settings)
        self.response_enable_check.toggled.connect(
            self._update_response_controls
        )
        footer_layout.addWidget(buttons)
        layout.addLayout(footer_layout)

        self._update_response_controls()
        self.find_input.setFocus()

    def get_rule(self):
        rule = {
            "find": self.find_input.text(),
            "replace": self.replace_input.text(),
            "options": {
                "case_sensitive": self.case_check.isChecked(),
                "use_regex": self.regex_check.isChecked(),
                "whole_word": self.whole_check.isChecked(),
            },
        }
        if self.response_input.text():
            rule["response_validation"] = {
                "enabled": self.response_enable_check.isChecked(),
                "expression": self.response_input.text(),
                **self.response_settings,
            }
        return normalize_rule(rule)

    def _validate_and_accept(self):
        if self.clear_requested and not any((
            self.find_input.text(),
            self.replace_input.text(),
            self.response_input.text(),
        )):
            self.accept()
            return
        self.clear_requested = False
        if self.response_enable_check.isChecked() and not self.response_input.text():
            QMessageBox.warning(self, "规则无效", "开启响应匹配时，响应表达式不能为空。")
            return
        rule = self.get_rule()
        if not rule:
            QMessageBox.warning(self, "规则无效", "查找字符串不能为空。")
            return
        try:
            source_pattern = compile_pattern(rule["find"], rule["options"])
            if self.response_enable_check.isChecked():
                response_validation = rule.get("response_validation") or {}
                validate_response_expression(
                    response_validation.get("expression", ""),
                    source_pattern,
                    rule["options"],
                )
        except ValueError as exc:
            QMessageBox.warning(self, "规则无效", str(exc))
            return
        self.accept()

    def _clear_rule_fields(self):
        self.clear_requested = True
        self.find_input.clear()
        self.replace_input.clear()
        self.response_input.clear()
        self.case_check.setChecked(False)
        self.regex_check.setChecked(False)
        self.whole_check.setChecked(False)
        self.response_enable_check.setChecked(False)
        self.response_settings = {
            "timeout_ms": self.default_timeout_ms,
            "on_failure": "continue",
            "color_policy": "sticky_failure",
            "show_error": False,
        }
        self._update_response_controls()
        self.find_input.setFocus()

    def _apply_default_rule(self):
        if not self.default_rule:
            return
        self.clear_requested = False
        rule = self.default_rule
        options = MatchOptions.from_dict(rule.get("options"))
        response_validation = rule.get("response_validation") or {}
        self.find_input.setText(rule.get("find", ""))
        self.replace_input.setText(rule.get("replace", ""))
        self.response_input.setText(response_validation.get("expression", ""))
        self.case_check.setChecked(options.case_sensitive)
        self.regex_check.setChecked(options.use_regex)
        self.whole_check.setChecked(options.whole_word)
        self.response_enable_check.setChecked(
            response_validation.get("enabled", False)
        )
        self.response_settings = {
            "timeout_ms": self.default_timeout_ms,
            "on_failure": response_validation.get("on_failure", "continue"),
            "color_policy": response_validation.get(
                "color_policy", "sticky_failure"
            ),
            "show_error": response_validation.get("show_error", False),
        }
        self._update_response_controls()

    def _edit_response_settings(self):
        dialog = ResponseValidationSettingsDialog(
            self.response_settings,
            self.FAILURE_OPTIONS,
            self.COLOR_POLICY_OPTIONS,
            self,
        )
        if dialog.exec_() == QDialog.Accepted:
            self.response_settings = dialog.get_settings()

    def _update_response_controls(self):
        enabled = self.response_enable_check.isChecked()
        self.response_controls_widget.setEnabled(enabled)
        self.response_input.setReadOnly(not enabled)

    @staticmethod
    def _select_combo_value(combo, mapping, value):
        for label, stored_value in mapping.items():
            if stored_value == value:
                combo.setCurrentText(label)
                return
