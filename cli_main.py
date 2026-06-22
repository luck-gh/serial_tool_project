#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
CLI 独立入口, 只运行串口调试助手命令行模式。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import os
import sys

from core.cli_runner import build_parser, run_cli
from version_info import TOOL_VERSION, TOOL_VERSION_DATE


def main():
    """CLI 主入口。"""
    if getattr(sys, 'frozen', False):
        exe_path = sys.executable
    else:
        exe_path = os.path.abspath(__file__)
    exe_name = os.path.splitext(os.path.basename(exe_path))[0]

    parser = build_parser()
    args = parser.parse_args()
    sys.exit(run_cli(args, exe_name, TOOL_VERSION, TOOL_VERSION_DATE))


if __name__ == '__main__':
    main()
