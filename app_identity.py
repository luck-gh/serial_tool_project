#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
应用标识辅助模块, 负责可执行文件名和配置文件名转换。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import re


def normalize_exe_name(exe_name):
    """去除 GUI/CLI 专用打包后缀, 让不同入口共用同一份配置。"""
    normalized = (exe_name or "main").strip()
    return re.sub(r"_(cli|gui)$", "", normalized, flags=re.IGNORECASE)


def get_config_file(exe_name):
    """根据可执行文件名生成配置文件名。"""
    return f"{normalize_exe_name(exe_name)}_config.json"
