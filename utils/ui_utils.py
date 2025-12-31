import os
import sys
import re
from enum import Enum
from PyQt5.QtWidgets import QMenu
from PyQt5.QtGui import QColor

class OutputSource(Enum):
    """输出来源类型"""
    SEND = "send"      # 发送数据
    RECEIVE = "receive" # 接收数据
    SYSTEM = "system"  # 系统信息
    ERROR = "error"    # 错误信息


class SpecialCommandType(Enum):
    """特殊指令类型"""
    MODE = "mode"           # 模块命名
    DELAY = "delay"         # 延迟设置
    SENDHEX = "sendhex"     # 十六进制发送
    BAUDRATE = "baudrate"   # 波特率设置
    SETENDLOG = "setendlog" # 结尾符设置
    SENDMODE = "sendmode"   # 发送指定模块

def resource_path(relative_path):
    """
    获取资源的绝对路径, 兼容开发环境和PyInstaller打包环境。
    在打包环境中, 会尝试在相对路径和根路径下寻找资源。
    """
    try:
        # PyInstaller创建的临时文件夹
        base_path = sys._MEIPASS
        
        # 尝试完整的相对路径
        path1 = os.path.join(base_path, relative_path)
        if os.path.exists(path1):
            return path1
            
        # 尝试只在根目录下寻找文件名 (处理--add-data data.txt:.的情况)
        path2 = os.path.join(base_path, os.path.basename(relative_path))
        if os.path.exists(path2):
            return path2
            
        # 如果都找不到, 返回原始的相对路径 (可能会失败, 但作为后备)
        return path1

    except Exception:
        # 在开发环境中, 使用当前文件所在目录的父目录作为根目录
        # ui_utils.py 在 utils/ 目录下, 所以其父目录是项目根目录
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_path, relative_path)

class Colors:
    """全局颜色配置"""
    BLUE_BUTTON = "#2196F3"
    GREEN_BUTTON = "#4CAF50"
    RED_BUTTON = "#f44336"
    TABLE_ODD_ROW = "#f0f0f0"
    TABLE_EVEN_ROW = "#ffffff"
    MENU_BACKGROUND = "#ffffff"
    MENU_SELECTION = "#2196F3"


class UIUtils:
    """UI辅助工具类, 用于创建通用UI组件"""
    @staticmethod
    def parse_special_command(text):
        """
        解析特殊指令，支持冒号转义。
        返回 (cmd_type_str, param) 如果是特殊指令格式，否则返回 (None, None)
        """
        if not text:
            return None, None

        unescaped_colon_pos = -1
        for i in range(len(text)):
            if text[i] == ':':
                # 统计冒号前的反斜杠数量
                backslash_count = 0
                for j in range(i - 1, -1, -1):
                    if text[j] == '\\':
                        backslash_count += 1
                    else:
                        break
                if backslash_count % 2 == 0:
                    unescaped_colon_pos = i
                    break
        
        if unescaped_colon_pos != -1:
            prefix = text[:unescaped_colon_pos]
            # 检查 prefix 是否只包含字母数字（符合 \w+）
            if re.match(r'^\w+$', prefix):
                param = text[unescaped_colon_pos + 1:]
                return prefix.lower(), param
        
        return None, None

    @staticmethod
    def unescape_text(text):
        """
        处理转义字符: '/:' -> ':'
        """
        if not text:
            return ""
        result = ""
        i = 0
        while i < len(text):
            if ((text[i] == '\\') or (text[i] == '/')) and ((i + 1) < len(text)):
                if text[i+1] in [':']:
                    result += text[i+1]
                    i += 2
                    continue
            result += text[i]
            i += 1
        return result

    @staticmethod
    def create_styled_menu(parent):
        """创建一个带统一样式的QMenu"""
        menu = QMenu(parent)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {Colors.MENU_BACKGROUND};
                border: 1px solid #d0d0d0;
                color: black;
            }}
            QMenu::item {{
                padding: 5px 20px 5px 20px;
                background-color: transparent;
            }}
            QMenu::item:selected {{
                background-color: {Colors.MENU_SELECTION};
                color: white;
            }}
            QMenu::item:disabled {{
                color: #808080;  /* 灰色字体 */
            }}
        """)
        return menu
