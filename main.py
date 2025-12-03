import sys
import os
from PyQt5.QtWidgets import (QApplication, QStyleFactory)
from main_window import SerialTool
import datetime

TOOL_VERSION = "1.0.1"
TOOL_VERSION_DATE = datetime.datetime(2025, 12, 2, 0, 0, 0).strftime('%Y-%m-%d %H:%M:%S')

def main():
    """应用程序主入口"""
    app = QApplication(sys.argv)

    # 设置应用程序样式
    app.setStyle(QStyleFactory.create('Fusion'))

    # 获取可执行文件名 (不含扩展名)
    if getattr(sys, 'frozen', False):
        # 在打包后运行
        exe_path = sys.executable
    else:
        # 在开发环境中运行
        exe_path = os.path.abspath(__file__)
    
    exe_name = os.path.splitext(os.path.basename(exe_path))[0]

    window = SerialTool(
        tool_version=TOOL_VERSION,
        tool_version_date=TOOL_VERSION_DATE,
        exe_name=exe_name
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
