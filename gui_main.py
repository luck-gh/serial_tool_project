#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
GUI 独立入口, 只启动串口调试助手图形界面。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import sys

from PyQt5.QtWidgets import QApplication, QStyleFactory

from main_window import SerialTool
from version_info import TOOL_VERSION, TOOL_VERSION_DATE


def hide_console_for_gui():
    """打包为控制台程序时, GUI 模式隐藏控制台窗口。"""
    if not (getattr(sys, 'frozen', False) and os.name == 'nt'):
        return
    try:
        import ctypes
        console_window = ctypes.windll.kernel32.GetConsoleWindow()
        if console_window:
            ctypes.windll.user32.ShowWindow(console_window, 0)
    except Exception:
        pass


def main():
    """GUI 主入口。"""
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = os.path.abspath(__file__)
    exe_name = os.path.splitext(os.path.basename(exe_path))[0]

    hide_console_for_gui()

    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create('Fusion'))

    window = SerialTool(
        tool_version=TOOL_VERSION,
        tool_version_date=TOOL_VERSION_DATE,
        exe_name=exe_name,
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
