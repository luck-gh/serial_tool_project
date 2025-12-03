import re
import os
import sys
import subprocess
from PyQt5.QtWidgets import (QApplication, QWidget, QLineEdit, QTableWidget, QCheckBox, QPushButton,
                             QHeaderView, QTableWidgetItem, QAction, QInputDialog, QMessageBox,
                             QDialog, QHBoxLayout)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics

from utils.ui_utils import UIUtils, Colors
from dialogs.comment_edit_dialog import CommentEditDialog
from managers.config_manager import ConfigManager

class CommandLineEdit(QLineEdit):
    """自定义命令行编辑框, 支持注释显示和文本编辑右键菜单"""
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.comment_text = ""

        # 设置上下文菜单策略为自定义
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_text_context_menu)

    def set_comment(self, comment):
        """设置注释文本"""
        self.comment_text = comment
        # 预留右边距给注释文本, 避免与输入文本重叠
        # 根据注释长度动态调整边距
        if self.comment_text:
            # 使用与paintEvent中相同的字体来计算所需空间
            comment_font = QFont("Arial", 8)
            font_metrics = QFontMetrics(comment_font)
            # 额外增加一点边距, 比如 10px
            margin = font_metrics.horizontalAdvance(self.comment_text) + 10
        else:
            margin = 0
        self.setTextMargins(0, 0, margin, 0)
        self.update() # 触发重绘

    def paintEvent(self, event):
        """重写paintEvent, 在绘制完基础控件后, 绘制注释文本"""
        super().paintEvent(event) # 先绘制QLineEdit本身

        if not self.comment_text:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 设置注释文字颜色和字体
        painter.setPen(QColor(100, 100, 100))
        font = QFont("Arial", 8)
        painter.setFont(font)

        # 在右侧绘制注释文本
        # 移除垂直方向的-2调整, 确保有足够的高度来绘制j/g/p等字符的下半部分
        text_rect = self.rect().adjusted(5, 0, -5, 0)
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

        # --- 外部工具：数字转换器 ---
        tool_name = "number_conversion_dialog"
        is_enabled = self.config_manager.is_tool_enabled(tool_name)
        tool_path = self.config_manager.get_tool_path(tool_name)
        is_available = is_enabled and tool_path and os.path.exists(tool_path)

        if is_available:
            menu.addSeparator()
            if self.hasSelectedText():
                selected_text = self.selectedText().strip()
                # 有选中文本时, 显示HEX和DEC计算
                hex_action = QAction("HEX 计算", self)
                hex_action.triggered.connect(lambda: self.open_number_converter(selected_text, "HEX"))
                menu.addAction(hex_action)

                dec_action = QAction("DEC 计算", self)
                dec_action.triggered.connect(lambda: self.open_number_converter(selected_text, "DEC"))
                menu.addAction(dec_action)
            else:
                # 没有选中文本时, 只显示一个通用的计算选项
                calc_action = QAction("进制转换器", self)
                calc_action.triggered.connect(lambda: self.open_number_converter())
                menu.addAction(calc_action)

        menu.exec_(self.mapToGlobal(position))

    def open_number_converter(self, text="", conversion_type="HEX"):
        """使用subprocess启动独立的数字转换器进程"""
        tool_path = self.config_manager.get_tool_path("number_conversion_dialog")
        if not tool_path:
            QMessageBox.warning(self, "错误", "未找到数字转换器工具。")
            return

        command = []
        if tool_path.endswith('.py'):
            command.append(sys.executable)
        
        command.append(tool_path)

        if text:
            command.append(text)
            command.append(conversion_type)

        try:
            subprocess.Popen(command, cwd=os.path.dirname(tool_path))
        except Exception as e:
            QMessageBox.critical(self, "启动失败", f"无法启动数字转换器:\n{str(e)}")


class CommandTableWidget(QTableWidget):
    """命令表格控件"""
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
        """)

        # 存储注释数据
        self.comments = {}

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
        checkbox_layout.addWidget(enable_checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.setCellWidget(row_index, 0, checkbox_widget)

        # 命令编辑框 (使用自定义控件)
        # 命令编辑框-右键菜单
        command_edit = CommandLineEdit(self.config_manager)
        command_edit.setText(command)
        command_edit.set_comment(comment)
        command_edit.textChanged.connect(lambda: self.on_command_changed(row_index))

        # 根据行号设置输入框背景色
        if row_index % 2 == 0:
            command_edit.setStyleSheet(f"background-color: {Colors.TABLE_EVEN_ROW};")
        else:
            command_edit.setStyleSheet(f"background-color: {Colors.TABLE_ODD_ROW};")

        self.setCellWidget(row_index, 1, command_edit)

        # 发送按钮
        send_btn = QPushButton(f"{row_index + 1}")
        send_btn.setFixedWidth(60)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Colors.BLUE_BUTTON};
                color: white;
                border: none;
                border-radius: 3px;
                padding: 5px;
            }}
            QPushButton:hover {{
                background-color: #1976D2;
            }}
            QPushButton:pressed {{
                background-color: #0D47A1;
            }}
        """)
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

        return send_btn

    def on_command_changed(self, row):
        """命令内容改变时更新注释显示"""
        command_edit = self.cellWidget(row, 1)
        comment = self.comments.get(row, "")
        command_edit.set_comment(comment)

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

    def get_all_commands(self):
        """获取所有命令数据"""
        commands = []
        for row in range(self.rowCount()):
            enable, command, comment = self.get_row_data(row)
            commands.append((enable, command, comment))
        return commands

    def clear_all(self):
        """清空所有行"""
        self.setRowCount(0)
        self.comments.clear()

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

        # 特殊指令
        special_command_action = QAction("特殊指令", self)
        special_command_menu = UIUtils.create_styled_menu(self)
        special_command_menu.setTitle("添加特殊指令")

        mode_action = QAction("mode", self)
        mode_action.triggered.connect(lambda: self.add_special_command(row, "mode"))
        special_command_menu.addAction(mode_action)

        delay_action = QAction("delay", self)
        delay_action.triggered.connect(lambda: self.add_special_command(row, "delay"))
        special_command_menu.addAction(delay_action)

        special_command_action.setMenu(special_command_menu)
        menu.addAction(special_command_action)

        menu.addSeparator()

        # 在上方插入行
        insert_action = QAction("在上方插入行", self)
        insert_action.triggered.connect(lambda: self.insert_row_above(row))
        menu.addAction(insert_action)

        # 删除行
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
        if command_type == "mode":
            module_name, ok = QInputDialog.getText(self, "模块命名", "请输入模块名称:")
            if ok and module_name:
                command_edit = self.cellWidget(row, 1)
                command_edit.setText(f"mode:{module_name}")
                # 取消勾选连续发送
                checkbox_widget = self.cellWidget(row, 0)
                checkbox_widget.findChild(QCheckBox).setChecked(False)
        elif command_type == "delay":
            delay_time, ok = QInputDialog.getText(self, "延迟设置", "请输入延迟时间(ms):")
            if ok and delay_time:
                try:
                    float(delay_time)  # 验证是否为数字
                    command_edit = self.cellWidget(row, 1)
                    command_edit.setText(f"delay:{delay_time}")
                    # 取消勾选连续发送
                    checkbox_widget = self.cellWidget(row, 0)
                    checkbox_widget.findChild(QCheckBox).setChecked(False)
                except ValueError:
                    QMessageBox.warning(self, "错误", "请输入有效的数字")

    def get_main_window(self):
        """递归查找主窗口"""
        parent = self.parent()
        while parent is not None:
            # 假设主窗口类名为 SerialTool
            if parent.__class__.__name__ == 'SerialTool':
                return parent
            parent = parent.parent()
        return None

    def insert_row_above(self, row):
        """在指定行上方插入新行"""
        # 更新注释存储
        new_comments = {}
        for old_row, comment in self.comments.items():
            if old_row < row:
                new_comments[old_row] = comment
            else:  # old_row >= row
                new_comments[old_row + 1] = comment
        self.comments = new_comments

        send_btn = self.add_command_row(False, "", "", row)

        # 更新下方行的发送按钮编号和连接
        self.update_send_buttons_after_row(row)

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

        # 更新发送按钮编号和连接
        self.update_send_buttons_after_row(row)

    def update_send_buttons_after_row(self, start_row):
        """从指定行开始更新所有发送按钮的编号和连接"""
        for row in range(start_row, self.rowCount()):
            btn = self.cellWidget(row, 2)
            if btn:
                btn.setText(f"{row + 1}")
                # 重新连接按钮以确保使用正确的行号
                self.connect_send_button(row, btn)
