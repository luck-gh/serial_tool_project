#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
UI 工具模块, 负责通用枚举, 样式常量, 资源路径和特殊命令解析辅助函数。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import sys
import re
from enum import Enum
from PyQt5.QtWidgets import QMenu
from PyQt5.QtGui import QColor

# 统一使用公共资源定位实现，默认导入共享模块；若导入失败则回退为本地实现
try:
    from common_utils.resource import resource_path
except Exception:
    # 回退实现（兼容原有逻辑）
    def resource_path(relative_path):
        """
        获取资源的绝对路径, 兼容开发环境和PyInstaller打包环境。
        在打包环境中, 会尝试在相对路径和根路径下寻找资源。
        """
        try:
            base_path = sys._MEIPASS

            path1 = os.path.join(base_path, relative_path)
            if os.path.exists(path1):
                return path1

            path2 = os.path.join(base_path, os.path.basename(relative_path))
            if os.path.exists(path2):
                return path2

            return path1

        except Exception:
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(base_path, relative_path)

class OutputSource(Enum):
    """输出来源类型"""
    SEND = "send"      # 发送数据
    RECEIVE = "receive" # 接收数据
    SYSTEM = "system"  # 系统信息
    ERROR = "error"    # 错误信息


class SpecialCommandType(Enum):
    """特殊指令类型"""
    MODE = "mode"           # 模块命名
    MODEEND = "modeend"     # 结束模块定义
    DELAY = "delay"         # 延迟设置
    SENDHEX = "sendhex"     # 十六进制发送
    BAUDRATE = "baudrate"   # 波特率设置
    COMPORT = "comport"     # COM口设置
    SETENDLOG = "setendlog" # 结尾符设置
    SENDMODE = "sendmode"   # 发送指定模块
    STOPCONTINUOUS = "stopcontinuous"  # 停止连续发送（可选择是否停止循环）

# `resource_path` 已由顶部尝试导入共享实现并保留回退实现，
# 因此删除本地重复定义以避免覆盖或重复声明。

class Colors:
    """全局颜色配置"""
    BLUE_BUTTON = "#2196F3"
    GREEN_BUTTON = "#4CAF50"
    RED_BUTTON = "#f44336"
    PURPLE_BUTTON = "#9C27B0"
    PURPLE_BUTTON_DARK = "#7B1FA2"
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
    def escape_text(text):
        """
        转义不可见字符和反斜杠,用于CSV导出等场景
        将 \t, \n, \r 等不可见字符转义为可见的字符串表示
        同时将已存在的反斜杠转义为双反斜杠
        """
        if not text:
            return ""

        # 先转义反斜杠(必须首先处理,避免二次转义)
        result = text.replace('\\', '\\\\')

        # 然后转义不可见字符
        replacements = {
            '\t': '\\t',
            '\n': '\\n',
            '\r': '\\r',
            '\0': '\\0',
            '\b': '\\b',
            '\f': '\\f',
            '\v': '\\v'
        }
        for char, escaped in replacements.items():
            result = result.replace(char, escaped)
        return result

    @staticmethod
    def unescape_csv_text(text):
        """
        反转义CSV导入的文本
        将 \\t, \\n, \\r 等转义序列还原为不可见字符
        将 \\\\ 还原为单个反斜杠
        """
        if not text:
            return ""

        # 使用正则表达式处理转义序列
        import re

        def replace_escape(match):
            escape_char = match.group(1)
            escape_map = {
                't': '\t',
                'n': '\n',
                'r': '\r',
                '0': '\0',
                'b': '\b',
                'f': '\f',
                'v': '\v',
                '\\': '\\'
            }
            return escape_map.get(escape_char, '\\' + escape_char)

        # 匹配 \\ 后跟一个字符
        result = re.sub(r'\\(.)', replace_escape, text)
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
