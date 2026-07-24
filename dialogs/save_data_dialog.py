#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""接收区数据保存来源选择对话框。"""

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from utils.ui_utils import OutputSource


class SaveDataSelectionDialog(QDialog):
    SOURCE_OPTIONS = (
        (OutputSource.SEND, "发送"),
        (OutputSource.RECEIVE, "接收"),
        (OutputSource.SYSTEM, "系统"),
        (OutputSource.ERROR, "错误"),
    )
    SYSTEM_LEVEL_OPTIONS = (
        ("normal", "常规"),
        ("warning", "告警"),
        ("info", "信息"),
        ("debug", "调试"),
    )

    def __init__(self, parent=None, selected_sources=None, selected_system_levels=None):
        super().__init__(parent)
        self.setWindowTitle("选择保存内容")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("请选择需要从当前接收区保存的信息来源："))

        source_layout = QHBoxLayout()
        self.source_checks = {}
        selected_sources = (
            set(OutputSource) if selected_sources is None else set(selected_sources)
        )
        for source, label in self.SOURCE_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(source in selected_sources)
            self.source_checks[source] = checkbox
            source_layout.addWidget(checkbox)
        source_layout.addStretch()
        layout.addLayout(source_layout)

        selected_system_levels = (
            {level for level, _label in self.SYSTEM_LEVEL_OPTIONS}
            if selected_system_levels is None
            else set(selected_system_levels)
        )
        system_group = QGroupBox("保存系统日志级别（可多选）")
        system_layout = QHBoxLayout(system_group)
        self.system_level_checks = {}
        for level, label in self.SYSTEM_LEVEL_OPTIONS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(level in selected_system_levels)
            self.system_level_checks[level] = checkbox
            system_layout.addWidget(checkbox)
        system_layout.addStretch()
        layout.addWidget(system_group)
        self.source_checks[OutputSource.SYSTEM].toggled.connect(
            system_group.setEnabled
        )
        system_group.setEnabled(self.source_checks[OutputSource.SYSTEM].isChecked())

        hint = QLabel("保存筛选独立于当前显示来源，未显示的信息也可以保存。")
        hint.setStyleSheet("color: #666666;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_sources(self):
        return {
            source
            for source, checkbox in self.source_checks.items()
            if checkbox.isChecked()
        }

    def selected_system_levels(self):
        return {
            level
            for level, checkbox in self.system_level_checks.items()
            if checkbox.isChecked()
        }

    def _validate_and_accept(self):
        if not self.selected_sources():
            QMessageBox.warning(self, "未选择保存内容", "请至少选择一种信息来源。")
            return
        if (
            OutputSource.SYSTEM in self.selected_sources()
            and not self.selected_system_levels()
        ):
            QMessageBox.warning(
                self, "未选择系统级别", "保存系统信息时请至少选择一个日志级别。"
            )
            return
        self.accept()
