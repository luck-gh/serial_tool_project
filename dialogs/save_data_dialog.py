#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""接收区数据保存来源选择对话框。"""

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
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

    def __init__(self, parent=None, selected_sources=None):
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

    def _validate_and_accept(self):
        if not self.selected_sources():
            QMessageBox.warning(self, "未选择保存内容", "请至少选择一种信息来源。")
            return
        self.accept()
