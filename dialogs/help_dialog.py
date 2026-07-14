#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""从项目 Markdown 文档动态加载内容的帮助窗口。"""

import html
import os

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QTextBrowser, QDialogButtonBox, QTabWidget
)

from utils.ui_utils import resource_path


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowTitle("帮助")
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.browsers = {}
        for title, relative_path in self.document_specs():
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setStyleSheet("""
                QTextBrowser {
                    background-color: #ffffff;
                    color: #202020;
                    font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                    font-size: 10pt;
                    padding: 10px;
                }
            """)
            self.tabs.addTab(browser, title)
            self.browsers[relative_path] = browser
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    @staticmethod
    def document_specs():
        return [
            ("GUI 操作手册", "docs/OPERATION_MANUAL_FOR_GUI.md"),
            ("CLI 操作手册", "docs/OPERATION_MANUAL_FOR_CLI.md"),
        ]

    def reload_content(self):
        """每次打开时重新读取所有文档，确保内容随文件变化。"""
        for _title, relative_path in self.document_specs():
            self._reload_document(relative_path, self.browsers[relative_path])

    def _reload_document(self, relative_path, browser):
        document_path = resource_path(relative_path)
        try:
            with open(document_path, "r", encoding="utf-8") as document_file:
                markdown_text = document_file.read()
        except OSError as exc:
            browser.setPlainText(f"无法读取帮助文件：\n{document_path}\n\n{exc}")
            return

        browser.document().setBaseUrl(
            QUrl.fromLocalFile(os.path.dirname(document_path) + os.sep)
        )
        if hasattr(browser, "setMarkdown"):
            browser.setMarkdown(markdown_text)
        else:
            browser.setHtml(f"<pre>{html.escape(markdown_text)}</pre>")
        cursor = browser.textCursor()
        cursor.movePosition(cursor.Start)
        browser.setTextCursor(cursor)
