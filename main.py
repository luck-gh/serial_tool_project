#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
应用程序入口, 负责根据启动参数进入 GUI 模式或 CLI 模式。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import sys
import os
import signal
from PyQt5.QtWidgets import (QApplication, QStyleFactory)
from PyQt5.QtCore import QTimer
from main_window import SerialTool
from core.cli_runner import build_parser, run_cli, should_run_cli
from version_info import TOOL_VERSION, TOOL_VERSION_DATE


def hide_console_for_gui():
    """打包为控制台程序时, GUI 模式隐藏控制台窗口."""
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
    """应用程序主入口"""
    # 获取可执行文件名 (不含扩展名)
    if getattr(sys, 'frozen', False):
        # 在打包后运行
        exe_path = sys.executable
    else:
        # 在开发环境中运行
        exe_path = os.path.abspath(__file__)
    
    exe_name = os.path.splitext(os.path.basename(exe_path))[0]

    if should_run_cli(sys.argv[1:]):
        parser = build_parser()
        args = parser.parse_args()
        sys.exit(run_cli(args, exe_name, TOOL_VERSION, TOOL_VERSION_DATE))

    hide_console_for_gui()

    app = QApplication(sys.argv)

    # 让开发环境中的 Ctrl+C 通过 Qt 事件循环安全退出，避免强制中断原生控件。
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    # 设置应用程序样式
    app.setStyle(QStyleFactory.create('Fusion'))

    window = SerialTool(
        tool_version=TOOL_VERSION,
        tool_version_date=TOOL_VERSION_DATE,
        exe_name=exe_name
    )
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
