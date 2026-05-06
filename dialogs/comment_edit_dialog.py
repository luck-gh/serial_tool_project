#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
注释编辑对话框模块, 负责命令注释的多行编辑界面。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTextEdit, QDialogButtonBox)

class CommentEditDialog(QDialog):
    """注释编辑对话框"""
    def __init__(self, current_comment="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑注释")
        self.setModal(True)
        self.resize(400, 200)

        layout = QVBoxLayout(self)

        # 注释编辑框
        self.comment_edit = QTextEdit()
        self.comment_edit.setPlainText(current_comment)
        layout.addWidget(QLabel("注释:"))
        layout.addWidget(self.comment_edit)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_comment(self):
        return self.comment_edit.toPlainText()
