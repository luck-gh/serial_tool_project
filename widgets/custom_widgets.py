#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
自定义控件模块, 负责接收区文本控件, 可折叠分组框和可点击下拉框等通用控件。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import subprocess
import sys
from PyQt5.QtWidgets import (QTextEdit, QTextBrowser, QAction, QMessageBox, 
                             QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QSizePolicy,
                             QComboBox)
from PyQt5.QtCore import Qt, pyqtSignal
from utils.ui_utils import UIUtils, resource_path, OutputSource, SpecialCommandType
from widgets.base_widgets import BaseWidgetMixin

class ClickableComboBox(QComboBox):
    """点击下拉菜单时触发信号的组合框"""
    popupAboutToBeShown = pyqtSignal()

    def showPopup(self):
        self.popupAboutToBeShown.emit()
        super().showPopup()

class ExpandingTextEdit(QTextEdit):
    """一个可以根据内容自动扩展高度的文本编辑框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QTextEdit.NoWrap)  # 初始不换行
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 关闭水平滚动条
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 关闭垂直滚动条
        self.textChanged.connect(self._on_text_changed)
        self._update_height()

    def _on_text_changed(self):
        """文本改变时触发, 检查是否需要换行"""
        text = self.toPlainText()
        font_metrics = self.fontMetrics()
        text_width = font_metrics.width(text)
        
        # 减去一个小的边距以获得更准确的宽度
        widget_width = self.width() - 10

        # 如果文本宽度大于控件宽度, 则启用自动换行
        if text_width > widget_width:
            if self.lineWrapMode() == QTextEdit.NoWrap:
                self.setLineWrapMode(QTextEdit.WidgetWidth)
                self._update_height()
        # 如果文本宽度小于控件宽度, 则恢复不换行
        else:
            if self.lineWrapMode() == QTextEdit.WidgetWidth:
                self.setLineWrapMode(QTextEdit.NoWrap)
                self._update_height()

    def _update_height(self):
        """根据内容更新控件的高度"""
        # 计算文档所需的总高度
        doc_height = self.document().size().height()
        # 获取单行文本的高度
        single_line_height = self.fontMetrics().height()
        
        # 添加边距
        margins = self.contentsMargins()
        new_height = doc_height + margins.top() + margins.bottom()

        # 确保最小高度至少为一行
        min_height = single_line_height + margins.top() + margins.bottom() + 5
        
        if new_height < min_height:
            new_height = min_height

        # 设置一个合理的最大高度（例如, 4行的高度）
        max_height = (single_line_height * 4) + margins.top() + margins.bottom() + 5
        if new_height > max_height:
            new_height = max_height

        # 仅在高度有显著变化时才设置, 以防止无限循环
        if abs(self.height() - int(new_height)) > 2:
            self.setFixedHeight(int(new_height))

    def resizeEvent(self, event):
        """当控件大小调整时, 重新检查换行设置"""
        super().resizeEvent(event)
        self._on_text_changed()

    # --- 提供与 QLineEdit 兼容的方法, 方便替换 ---
    def setText(self, text):
        """设置文本, 兼容 QLineEdit 的方法"""
        self.setPlainText(text)

    def text(self):
        """获取文本, 兼容 QLineEdit 的方法"""
        return self.toPlainText()

from managers.config_manager import ConfigManager

class CustomTextBrowser(QTextBrowser, BaseWidgetMixin):
    """自定义文本浏览器, 支持右键菜单和外部工具调用"""
    def __init__(self, config_manager: ConfigManager, parent=None, save_callback=None, clear_callback=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.save_callback = save_callback
        self.clear_callback = clear_callback
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, position):
        """显示自定义右键菜单"""
        menu = UIUtils.create_styled_menu(self)

        # --- 标准编辑操作 ---
        copy_action = QAction("复制\tCtrl+C", self)
        copy_action.triggered.connect(self.copy)
        copy_action.setEnabled(self.textCursor().hasSelection())
        menu.addAction(copy_action)

        select_all_action = QAction("全选\tCtrl+A", self)
        select_all_action.triggered.connect(self.selectAll)
        menu.addAction(select_all_action)

        if self.save_callback or self.clear_callback:
            menu.addSeparator()

        if self.save_callback:
            save_action = QAction("保存数据", self)
            save_action.triggered.connect(self.save_callback)
            menu.addAction(save_action)

        if self.clear_callback:
            clear_action = QAction("清空数据", self)
            clear_action.triggered.connect(self.clear_callback)
            menu.addAction(clear_action)

        # --- 外部工具：数字转换器 ---
        selected_text = self.textCursor().selectedText().strip() if self.textCursor().hasSelection() else None
        self.add_number_converter_actions(menu, self.config_manager, selected_text)

        menu.exec_(self.mapToGlobal(position))

    def keyPressEvent(self, event):
        """处理快捷键"""
        if self.handle_common_shortcuts(event):
            return
        super().keyPressEvent(event)

class CollapsibleGroupBox(QWidget):
    """一个可以折叠的容器控件，支持水平或垂直折叠"""
    toggled = pyqtSignal(bool)

    def __init__(self, title="", parent=None, horizontal=False):
        super().__init__(parent)
        self.horizontal = horizontal
        self.title_text = title
        
        if horizontal:
            self.main_layout = QHBoxLayout(self)
        else:
            self.main_layout = QVBoxLayout(self)
            
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)
        
        if horizontal:
            self.toggle_button.setFixedWidth(25)
            self.toggle_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            self.toggle_button.setFixedHeight(35)
            self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._update_button_text()
        
        self.toggle_button.setStyleSheet("""
            QPushButton {
                text-align: center;
                font-weight: bold;
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                background-color: #ffffff;
                color: #333333;
                padding: 4px;
            }
            QPushButton:hover {
                background-color: #f5f5f5;
            }
        """)
        
        self.content_widget = QWidget()
        self.content_widget.setObjectName("content_widget")
        self.content_widget.setStyleSheet("""
            #content_widget {
                border: 1px solid #d0d0d0;
                border-radius: 6px;
                background-color: white;
            }
        """)
        
        if horizontal:
            self.content_layout = QHBoxLayout(self.content_widget)
            # 水平折叠时，内容在右侧，按钮在左侧
            self.main_layout.addWidget(self.toggle_button)
            self.main_layout.addWidget(self.content_widget)
            self.content_widget.setVisible(False)
        else:
            self.content_layout = QVBoxLayout(self.content_widget)
            # 垂直折叠时，内容在下方，按钮在上方
            self.main_layout.addWidget(self.toggle_button)
            self.main_layout.addWidget(self.content_widget)
            self.content_widget.setVisible(False)
            self.toggle_button.setStyleSheet(self.toggle_button.styleSheet() + """
                QPushButton { text-align: left; padding-left: 8px; }
                QPushButton:checked { border-bottom-left-radius: 0px; border-bottom-right-radius: 0px; }
            """)
            self.content_widget.setStyleSheet(self.content_widget.styleSheet() + """
                #content_widget { border-top: none; border-top-left-radius: 0px; border-top-right-radius: 0px; }
            """)

        self.content_layout.setContentsMargins(12, 8, 12, 12)
        self.content_layout.setSpacing(8)

        self.toggle_button.toggled.connect(self._on_toggled)

    def _update_button_text(self):
        checked = self.toggle_button.isChecked()
        if self.horizontal:
            arrow = "◀" if checked else "▶"
            # 水平按钮文字竖排
            display_text = arrow + "\n" + "\n".join(list(self.title_text)) if self.title_text else arrow
            self.toggle_button.setText(display_text)
        else:
            arrow = "▼" if checked else "▶"
            self.toggle_button.setText(f"{arrow} {self.title_text}")

    def _on_toggled(self, checked):
        self.content_widget.setVisible(checked)
        self._update_button_text()
        self.toggled.emit(checked)

    def isExpanded(self):
        return self.toggle_button.isChecked()

    def setExpanded(self, expanded):
        self.toggle_button.setChecked(expanded)

    def setTitle(self, title):
        """更新标题文本"""
        self.title_text = title
        self._update_button_text()

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)
        
    def addLayout(self, layout):
        self.content_layout.addLayout(layout)
