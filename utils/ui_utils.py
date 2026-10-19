#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
UI 工具模块, 负责菜单样式,资源路径及 CSV 文本转义.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import sys
import re
from PyQt5.QtWidgets import QMenu
from core.command_templates import escape_text, unescape_csv_text

# 统一使用公共资源定位实现,默认导入共享模块;若导入失败则回退为本地实现
try:
    from common_utils.resource import resource_path
except Exception:
    # 回退实现(兼容原有逻辑)
    def resource_path(relative_path):
        """
        获取资源的绝对路径, 兼容开发环境和PyInstaller打包环境.
        在打包环境中, 会尝试在相对路径和根路径下寻找资源.
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

# `resource_path` 已由顶部尝试导入共享实现并保留回退实现,
# 因此删除本地重复定义以避免覆盖或重复声明.

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
    escape_text = staticmethod(escape_text)
    unescape_csv_text = staticmethod(unescape_csv_text)

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
