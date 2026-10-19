#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
应用标识辅助模块, 负责可执行文件名和配置文件名转换。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import argparse
import re


def normalize_exe_name(exe_name):
    """去除 GUI/CLI 专用打包后缀, 让不同入口共用同一份配置。"""
    normalized = (exe_name or "main").strip()
    return re.sub(r"_(cli|gui)$", "", normalized, flags=re.IGNORECASE)


def get_config_file(exe_name):
    """根据可执行文件名生成配置文件名。"""
    return f"{normalize_exe_name(exe_name)}_config.json"


def parse_gui_arguments(argv):
    """解析 GUI 配置路径,返回配置参数及交给 Qt 的剩余启动参数.

    Args:
        argv (list[str]): 不含程序名的启动参数;相对路径沿用进程工作目录.
    """
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--config", help="指定配置文件")
    return parser.parse_known_args(argv)
