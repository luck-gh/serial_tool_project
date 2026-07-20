#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
输出规则模块, 负责 GUI 和 CLI 共用的输出过滤, 时间戳, 颜色和提示文案。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from datetime import datetime

from utils.ui_utils import OutputSource


ANSI_RESET = "\033[0m"
ANSI_COLORS = {
    "black": "\033[30m",
    "white": "\033[37m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "purple": "\033[35m",
    "gray": "\033[90m",
}

SEND_COLOR_MAP = {
    "红色": "red",
    "蓝色": "blue",
    "绿色": "green",
    "紫色": "purple",
    "黑色": "black",
}


def normalize_send_color(color_text):
    """将 UI 颜色名转换为内部颜色名."""
    return SEND_COLOR_MAP.get(color_text, color_text or "red")


class OutputRules:
    """统一管理输出过滤, 颜色和时间戳规则."""

    def __init__(
        self,
        timestamp_enabled,
        show_send_enabled,
        send_color_getter,
        source_filter_getter=None,
        timestamp_format="[%Y-%m-%d %H:%M:%S]",
        trim_microseconds=False,
    ):
        self.timestamp_enabled = timestamp_enabled
        self.show_send_enabled = show_send_enabled
        self.send_color_getter = send_color_getter
        self.source_filter_getter = source_filter_getter
        self.timestamp_format = timestamp_format
        self.trim_microseconds = trim_microseconds
        self.last_receive_timestamp = True

    def source_enabled(self, source_type):
        if self.source_filter_getter:
            return self.source_filter_getter(source_type)
        return True

    def color_for(self, source_type):
        if source_type == OutputSource.SEND:
            return normalize_send_color(self.send_color_getter())
        if source_type == OutputSource.ERROR:
            return "red"
        if source_type == OutputSource.SYSTEM:
            return "gray"
        return "white"

    def timestamp_for(self, source_type):
        if not self.timestamp_enabled():
            return ""
        if source_type == OutputSource.SEND:
            return self.current_timestamp()
        if source_type == OutputSource.RECEIVE and self.last_receive_timestamp:
            self.last_receive_timestamp = False
            return self.current_timestamp()
        return ""

    def current_timestamp(self):
        timestamp = datetime.now().strftime(self.timestamp_format)
        if self.trim_microseconds and "%f" in self.timestamp_format:
            timestamp = timestamp[:-4] + "]"
        return timestamp + " "

    def before_append(self, source_type):
        if source_type == OutputSource.SEND:
            self.last_receive_timestamp = True

    def reset_receive_timestamp(self):
        self.last_receive_timestamp = True

    def ansi_color_for(self, source_type):
        return ANSI_COLORS.get(self.color_for(source_type), "")


def rules_from_state(state):
    """从配置 state 创建输出规则."""
    state = state or {}
    receive_settings = state.get("receive_settings", {})
    display_sources = receive_settings.get("display_sources") or {}
    send_settings = state.get("send_settings", {})
    other_settings = state.get("other_settings", {})

    def source_filter(source_type):
        if source_type == OutputSource.SEND:
            return display_sources.get(
                "send", receive_settings.get("show_send_source", True)
            )
        if source_type == OutputSource.RECEIVE:
            return display_sources.get(
                "receive", receive_settings.get("show_recv_source", True)
            )
        if source_type == OutputSource.SYSTEM:
            return display_sources.get(
                "system", receive_settings.get("show_sys_source", True)
            )
        if source_type == OutputSource.ERROR:
            return display_sources.get(
                "error", receive_settings.get("show_err_source", True)
            )
        return True

    return OutputRules(
        timestamp_enabled=lambda: other_settings.get("show_timestamp", False),
        show_send_enabled=lambda: send_settings.get("show_send", False),
        send_color_getter=lambda: send_settings.get("send_color", "红色"),
        source_filter_getter=source_filter,
    )


def module_not_found(module_name):
    """模块不存在."""
    return f"错误: 找不到模块 '{module_name}'"


def module_no_enabled_commands(module_name):
    """模块没有启用命令."""
    return f"警告: 模块 '{module_name}' 中没有启用的命令"


def sendmode_start(module_name):
    """SendMode 开始发送模块."""
    return f"SendMode: 发送模块 '{module_name}' 的内容"


def sendmode_delay(delay_ms):
    """SendMode 延迟."""
    return f"SendMode 延迟: {delay_ms}ms"


def continuous_delay(delay_ms):
    """连续发送延迟."""
    return f"连续发送延迟: {delay_ms}ms"


def invalid_delay(param):
    """延迟参数无效."""
    return f"错误: 无效的延迟参数: {param}"


def baudrate_updated(baudrate):
    """波特率已更新."""
    return f"波特率已更新为: {baudrate}"


def ending_set(ending):
    """结尾标识符已设置."""
    return f"已设置结尾标识符为: {ending}"


def comport_updated(port):
    """COM 口已更新."""
    return f"COM口已更新为: {port}"


def unsupported_cli_command(command_type):
    """CLI 不支持的特殊命令."""
    return f"CLI 暂不支持特殊命令: {command_type}"


def sendhex_error(error):
    """SendHex 执行错误."""
    return f"SendHex 错误: {error}"


def special_command_failed(command_name, param):
    """特殊命令执行失败."""
    return f"错误: {command_name} 执行失败: {param}"
