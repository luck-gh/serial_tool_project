from datetime import datetime
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor
from widgets.custom_widgets import OutputSource, CustomTextBrowser

class OutputManager:
    """统一输出管理器"""
    def __init__(self, text_browser:CustomTextBrowser, timestamp_check, show_send_check, send_color_getter):
        self.text_browser = text_browser
        self.timestamp_check = timestamp_check
        self.show_send_check = show_send_check
        self.send_color_getter = send_color_getter
        self.last_receive_timestamp = True  # 控制接收数据时间戳显示

    def append_text(self, text, source_type, color=None):
        """统一添加文本到显示区"""
        cursor = self.text_browser.textCursor()
        cursor.movePosition(QTextCursor.End)

        # 时间戳逻辑
        show_timestamp = False
        if self.timestamp_check():
            if source_type == OutputSource.SEND:
                # 发送数据: 每条都显示时间戳
                show_timestamp = True
            elif source_type == OutputSource.RECEIVE:
                # 接收数据: 只在每次输出后的第一包数据显示时间戳
                if self.last_receive_timestamp:
                    show_timestamp = True
                    self.last_receive_timestamp = False

        # 创建文本格式
        char_format = QTextCharFormat()

        # 根据来源类型设置颜色
        if source_type == OutputSource.SEND and self.show_send_check():
            color_name = self.send_color_getter()
            char_format.setForeground(QColor(color_name))
        elif source_type == OutputSource.ERROR:
            char_format.setForeground(QColor("red"))
        elif source_type == OutputSource.SYSTEM:
            char_format.setForeground(QColor("gray"))
        else:
            char_format.setForeground(QColor("black"))

        # 应用格式
        cursor.setCharFormat(char_format)

        # 处理时间戳
        if show_timestamp:
            timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-4] + "] "
            cursor.insertText(timestamp)

        # 处理文本内容
        cursor.insertText(text)

        # 发送数据后重置接收时间戳标志
        if source_type == OutputSource.SEND:
            self.last_receive_timestamp = True

        # 对于非接收数据, 适当添加换行
        if source_type != OutputSource.RECEIVE:
            cursor.insertText("\n")

        # 自动滚动到底部
        self.text_browser.setTextCursor(cursor)
        self.text_browser.ensureCursorVisible()

    def reset_receive_timestamp(self):
        """重置接收时间戳标志 (在发送数据后调用) """
        self.last_receive_timestamp = True

    def clear(self):
        """清空显示区"""
        self.text_browser.clear()
        self.last_receive_timestamp = True
