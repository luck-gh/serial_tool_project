import re
import os
import sys
import subprocess
from PyQt5.QtWidgets import (QApplication, QWidget, QLineEdit, QTableWidget, QCheckBox, QPushButton,
                             QHeaderView, QTableWidgetItem, QAction, QInputDialog, QMessageBox,
                             QDialog, QHBoxLayout, QVBoxLayout, QLabel, QDialogButtonBox)
from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtGui import QPainter, QColor, QFont, QFontMetrics, QRegExpValidator

from utils.ui_utils import UIUtils, Colors, resource_path
from widgets.base_widgets import BaseWidgetMixin
from dialogs.comment_edit_dialog import CommentEditDialog
from managers.config_manager import ConfigManager

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


class CommandLineEdit(QLineEdit, BaseWidgetMixin):
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

    def paintEvent(self, event):
        """重写paintEvent, 在绘制完基础控件后, 绘制注释文本"""
        super().paintEvent(event) # 先绘制QLineEdit本身

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
            if text_width > visible_width * 0.6:
                return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 设置注释文字颜色和字体（半透明灰色）
        painter.setPen(QColor(150, 150, 150, 180))
        font = QFont("Arial", 8)
        painter.setFont(font)

        # 在右侧绘制注释文本
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
        # 连接勾选框状态改变信号，支持批量操作
        enable_checkbox.stateChanged.connect(lambda state, r=row_index: self.on_checkbox_toggled(state, r))
        checkbox_layout.addWidget(enable_checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)
        self.setCellWidget(row_index, 0, checkbox_widget)

        # 命令编辑框 (使用自定义控件)
        # 命令编辑框-右键菜单
        command_edit = CommandLineEdit(self.config_manager)
        # 先连接信号再设置文本，确保初始文本也能触发格式化
        # 注意：textChanged 信号会传递一个字符串参数，需要用 lambda _ 接收并忽略它
        command_edit.textChanged.connect(lambda _: self.on_command_changed(row_index))
        command_edit.setText(command)
        command_edit.set_comment(comment)
        # 回车发送功能
        command_edit.returnPressed.connect(lambda: self.on_return_pressed(row_index))

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
        """命令内容改变时更新注释显示并处理特殊格式"""
        command_edit = self.cellWidget(row, 1)
        if not command_edit:
            return
            
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

        mode_action = QAction("mode (模块命名)", self)
        mode_action.triggered.connect(lambda: self.add_special_command(row, "mode"))
        special_command_menu.addAction(mode_action)

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
            initial_val = 115200
            if cmd_type_str == "baudrate":
                try:
                    initial_val = int(param.strip())
                except:
                    pass
            # 使用 getInt 确保只能输入数字
            baudrate, ok = QInputDialog.getInt(self, "BaudRate设置", "请输入波特率:", initial_val, 0, 10000000, 1)
            if ok:
                command_edit.setText(f"BaudRate:{baudrate}")
                self._uncheck_row(row)
        elif command_type == "setendlog":
            options = ["None", "rn", "r", "n"]
            initial_idx = 0
            if cmd_type_str == "setendlog":
                val = param.strip().lower()
                if val in options:
                    initial_idx = options.index(val)
                elif val == "none":
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
                
                # 更新背景颜色以保持交替效果
                if row % 2 == 0:
                    edit.setStyleSheet(f"background-color: {Colors.TABLE_EVEN_ROW};")
                else:
                    edit.setStyleSheet(f"background-color: {Colors.TABLE_ODD_ROW};")
