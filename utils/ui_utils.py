import os
import sys
from PyQt5.QtWidgets import QMenu
from PyQt5.QtGui import QColor

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
        # 在开发环境中
        base_path = os.path.abspath(".")
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
