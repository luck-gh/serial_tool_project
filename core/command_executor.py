#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
命令执行辅助模块, 负责将 GUI 表格和配置文件中的命令统一解析为可执行序列。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from utils.ui_utils import UIUtils, SpecialCommandType


def normalize_command_row(row, index=0):
    """将 GUI/配置命令行统一为 (row_index, enabled, command, comment)."""
    if isinstance(row, (list, tuple)):
        enabled = bool(row[0]) if len(row) > 0 else False
        command = str(row[1]) if len(row) > 1 else ""
        comment = str(row[2]) if len(row) > 2 else ""
        return index, enabled, command, comment

    if isinstance(row, dict):
        enabled = row.get("enabled", row.get("checked", row.get("enable", False)))
        command = row.get("command", "")
        comment = row.get("comment", "")
        row_index = row.get("row", index)
        return int(row_index), bool(enabled), str(command), str(comment)

    return index, False, "", ""


class ConfigCommandProvider:
    """基于配置 state 的命令数据提供器."""

    def __init__(self, state):
        self.state = state or {}

    def get_commands(self):
        return self.state.get("commands", [])


class TableCommandProvider:
    """基于 GUI 命令表格的命令数据提供器."""

    def __init__(self, command_table):
        self.command_table = command_table

    def get_commands(self):
        return self.command_table.get_all_commands()


def command_type_from_string(cmd_type_str):
    if not cmd_type_str:
        return None
    for command_type in SpecialCommandType:
        if command_type.value == cmd_type_str:
            return command_type
    return None


def collect_module_commands(provider, module_name):
    """根据 mode/modeend 标记收集模块内启用的命令."""
    target = (module_name or "").strip()
    current_module = "默认"
    in_target = target in ("", "全部", current_module)
    commands = []

    for index, raw_row in enumerate(provider.get_commands()):
        row, enabled, command, _comment = normalize_command_row(raw_row, index)
        if command.strip().lower() == SpecialCommandType.MODEEND.value:
            if enabled and in_target:
                commands.append((row, command, True, SpecialCommandType.MODEEND, ""))
            current_module = "默认"
            in_target = target in ("全部", current_module)
            continue

        cmd_type_str, param = UIUtils.parse_special_command(command)

        if cmd_type_str == "mode":
            current_module = param.strip()
            in_target = target in ("全部", current_module)
            continue

        if cmd_type_str == "modeend":
            if enabled and in_target:
                commands.append((row, command, True, SpecialCommandType.MODEEND, param))
            current_module = "默认"
            in_target = target in ("全部", current_module)
            continue

        if not enabled or not in_target:
            continue

        if cmd_type_str:
            command_type = command_type_from_string(cmd_type_str)
            if command_type:
                commands.append((row, command, True, command_type, param))
                continue

        commands.append((row, UIUtils.unescape_text(command), False))

    return commands
