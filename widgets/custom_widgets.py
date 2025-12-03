import os
import subprocess
import sys
from enum import Enum
from PyQt5.QtWidgets import QTextEdit, QTextBrowser, QAction, QMessageBox
from PyQt5.QtCore import Qt
from utils.ui_utils import UIUtils

class OutputSource(Enum):
    """输出来源类型"""
    SEND = "send"      # 发送数据
    RECEIVE = "receive" # 接收数据
    SYSTEM = "system"  # 系统信息
    ERROR = "error"    # 错误信息


class SpecialCommandType(Enum):
    """特殊指令类型"""
    MODE = "mode"
    DELAY = "delay"

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

class CustomTextBrowser(QTextBrowser):
    """自定义文本浏览器, 支持右键菜单和外部工具调用"""
    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    @property
    def converter_path(self):
        """从配置中获取转换器路径"""
        return self.config_manager.get_tool_path("number_conversion_dialog")

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

        # --- 外部工具：数字转换器 ---
        tool_name = "number_conversion_dialog"
        is_enabled = self.config_manager.is_tool_enabled(tool_name)
        tool_path = self.config_manager.get_tool_path(tool_name)
        is_available = is_enabled and tool_path and os.path.exists(tool_path)

        if is_available:
            menu.addSeparator()
            if self.textCursor().hasSelection():
                selected_text = self.textCursor().selectedText().strip()
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
        if not self.converter_path:
            QMessageBox.warning(self, "错误", "未找到数字转换器工具。")
            return

        command = []
        # 如果是Python脚本, 使用python解释器启动
        if self.converter_path.endswith('.py'):
            command.append(sys.executable) # 使用当前Python解释器
        
        command.append(self.converter_path)

        # 添加参数
        if text:
            command.append(text)
            command.append(conversion_type)

        try:
            # 使用 Popen 启动一个完全独立的进程, 不会阻塞UI
            subprocess.Popen(command, cwd=os.path.dirname(self.converter_path))
        except Exception as e:
            QMessageBox.critical(self, "启动失败", f"无法启动数字转换器:\n{str(e)}")
