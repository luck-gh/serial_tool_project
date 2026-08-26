#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
配置管理模块, 负责配置文件加载, 版本迁移, 工具注册表和配置持久化。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import json
import os
import datetime
import importlib.util
import tempfile

class ConfigManager:
    """管理应用程序的配置"""

    # 工具注册表：统一管理所有可用工具
    REGISTERED_TOOLS = {
        "number_conversion_dialog": {
            "display_name": "进制转换器",
            "button_text": "位计算器",
            "requires_serial_port": False,  # 是否需要独占串口
            "default_config": {
                "path": "",
                "enabled": True,
                "data_width": "DWORD",  # 默认数据宽度
                "always_on_top": False  # 是否始终置顶
            }
        },
        "bin_hex_converter": {
            "display_name": "Bin转Hex转换器",
            "button_text": "Bin转Hex",
            "requires_serial_port": False,  # 是否需要独占串口
            "default_config": {
                "path": "",
                "enabled": True,
                "data_width": 1,
                "bytes_per_row": 16,
                "byteorder": "little",
                "uppercase": True,
                "always_on_top": False  # 是否始终置顶
            }
        },
        "firmware_downloader": {
            "display_name": "固件下载工具",
            "button_text": "固件下载",
            "requires_serial_port": True,  # 需要独占串口
            "default_config": {
                "path": "",
                "enabled": True,
                "initial_file": "",  # 初始固件文件路径
                "always_on_top": False,  # 是否始终置顶
                "packet_size": 256,
                "start_command": "download 0\\n",
                "add_packet_crc": False,
                "packet_crc_type": "CRC16-MODBUS",
                # 开始命令 ACK 配置
                "wait_start_ack": True,
                "start_ack_check_mode": "OR",
                "start_ack_timeout": 1000,
                "start_ack_check_length": False,
                "start_ack_expected_length": 1,
                "start_ack_check_data": True,
                "start_ack_expected_data": "download 0\\nOK\\r\\n",
                "start_ack_data_format": "ASCII",
                # 数据包 ACK 配置
                "wait_packet_ack": True,
                "packet_ack_check_mode": "OR",
                "packet_ack_timeout": 1000,
                "packet_ack_check_length": False,
                "packet_ack_expected_length": 1,
                "packet_ack_check_data": True,
                "packet_ack_expected_data": "OK\\r\\n",
                "packet_ack_data_format": "ASCII",
                "packet_ack_check_crc": False,
                "packet_ack_crc_type": "CRC16-MODBUS",
                # 末尾数据包 ACK 配置
                "wait_last_packet_ack": True,
                "last_packet_ack_check_mode": "OR",
                "last_packet_ack_timeout": 5000,
                "last_packet_ack_check_length": False,
                "last_packet_ack_expected_length": 1,
                "last_packet_ack_check_data": True,
                "last_packet_ack_expected_data": "END\\r\\n",
                "last_packet_ack_data_format": "ASCII",
                "last_packet_ack_check_crc": False,
                "last_packet_ack_crc_type": "CRC16-MODBUS",
                # 结尾字符串配置
                "send_end_string": False,
                "end_string": "?\\r\\n"
            }
        }
    }

    def __init__(self, tool_version="0.0.0", tool_version_date="N/A", config_file="config.json"):
        self.tool_version = tool_version
        self.tool_version_date = tool_version_date
        self.config_file = config_file
        self.config = self.load_config()

    def load_config(self):
        """加载配置, 如果文件不存在则创建默认配置"""
        if not os.path.exists(self.config_file):
            return self.create_default_config()

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return self.create_default_config()

        # --- VERSION CHECK ---
        config_version = config.get("config_version", "0.0.0")
        if self._compare_versions(config_version, self.tool_version) > 0:
            # 配置文件版本高于当前工具版本，抛出错误
            raise ValueError(
                f"配置文件版本 ({config_version}) 高于当前工具版本 ({self.tool_version})。\n"
                f"请使用版本 {config_version} 或更高版本的工具打开此配置文件。"
            )

        # --- MIGRATION LOGIC ---
        migrated = False

        # 确保 tools 字段存在
        if "tools" not in config:
            config["tools"] = {}
            migrated = True

        if "last_state" in config:
            if "state" not in config:
                config["state"] = config["last_state"]
            del config["last_state"]
            migrated = True

        # 迁移旧格式的工具配置（字符串 -> 字典）
        for tool_name, tool_config in config["tools"].items():
            if isinstance(tool_config, str):
                config["tools"][tool_name] = {
                    "path": tool_config,
                    "enabled": True
                }
                migrated = True

        # 补充缺失的工具配置（保留现有配置，只添加缺失的）
        for tool_name, tool_info in self.REGISTERED_TOOLS.items():
            if tool_name not in config["tools"]:
                config["tools"][tool_name] = tool_info["default_config"].copy()
                migrated = True
            else:
                # 补充缺失的参数（保留已有的值，只添加新增的默认参数）
                existing_config = config["tools"][tool_name]
                default_config = tool_info["default_config"]
                for key, default_value in default_config.items():
                    if key not in existing_config:
                        existing_config[key] = default_value
                        migrated = True

        if migrated:
            self.save_config(config)

        return config

    def create_default_config(self):
        """创建并保存默认配置文件"""
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 从注册表生成工具配置
        tools_config = {}
        for tool_name, tool_info in self.REGISTERED_TOOLS.items():
            tools_config[tool_name] = tool_info["default_config"].copy()

        default_config = {
            "tool_version": self.tool_version,
            "tool_update_time": self.tool_version_date,
            "config_version": self.tool_version,
            "config_last_updated": now,
            "tools": tools_config
        }
        self.save_config(default_config)
        return default_config

    def save_config(self, config_data=None):
        """原子保存当前配置，避免进程中断时留下不完整的 JSON。"""
        if config_data is None:
            config_data = self.config

        # 更新版本和时间戳
        config_data["tool_version"] = self.tool_version
        config_data["tool_update_time"] = self.tool_version_date
        config_data["config_version"] = self.tool_version  # 配置版本与工具版本保持一致
        config_data["config_last_updated"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        config_path = os.path.abspath(self.config_file)
        config_directory = os.path.dirname(config_path)
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                newline='\n',
                prefix=f'.{os.path.basename(config_path)}.',
                suffix='.tmp',
                dir=config_directory,
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                json.dump(config_data, temp_file, indent=4, ensure_ascii=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            os.replace(temp_path, config_path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def get(self, key, default=None):
        """获取配置项"""
        return self.config.get(key, default)

    def set(self, key, value):
        """设置配置项"""
        self.config[key] = value
        self.save_config()

    def get_tool_config(self, tool_name):
        """获取工具的完整配置"""
        return self.config.get("tools", {}).get(tool_name)

    def get_tool_path(self, tool_name):
        """获取工具路径"""
        tool_config = self.get_tool_config(tool_name)
        return tool_config.get("path", "") if tool_config else ""

    def is_tool_enabled(self, tool_name):
        """检查工具是否启用"""
        tool_config = self.get_tool_config(tool_name)
        return tool_config.get("enabled", False) if tool_config else False

    def is_tool_available(self, tool_name):
        """检查工具是否可从自定义路径或内置模块启动。"""
        custom_path = self.get_tool_path(tool_name)
        if custom_path and os.path.isfile(custom_path):
            return True

        if tool_name != "firmware_downloader":
            return True

        try:
            module_available = importlib.util.find_spec(
                "firmware_downloader_project.core.downloader"
            ) is not None
            if module_available:
                return True
        except (ImportError, AttributeError, ValueError):
            pass

        workspace_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.isfile(
            os.path.join(
                workspace_root,
                "firmware_downloader_project",
                "core",
                "downloader.py",
            )
        )

    def set_tool_config(self, tool_name, enabled, path, **kwargs):
        """设置工具的完整配置"""
        if "tools" not in self.config:
            self.config["tools"] = {}

        tool_config = {
            "enabled": enabled,
            "path": path,
        }

        # 添加额外的配置参数
        tool_config.update(kwargs)

        self.config["tools"][tool_name] = tool_config
        self.save_config()

    def get_bin_hex_converter_params(self):
        """获取 bin2hex 转换器的参数"""
        tool_config = self.get_tool_config("bin_hex_converter")
        default_config = self.REGISTERED_TOOLS["bin_hex_converter"]["default_config"]

        if tool_config:
            return {
                key: tool_config.get(key, default_config[key])
                for key in default_config.keys()
                if key not in ["path", "enabled"]
            }
        return {k: v for k, v in default_config.items() if k not in ["path", "enabled"]}

    def get_number_conversion_params(self):
        """获取进制转换器的参数"""
        tool_config = self.get_tool_config("number_conversion_dialog")
        default_config = self.REGISTERED_TOOLS["number_conversion_dialog"]["default_config"]

        if tool_config:
            return {
                key: tool_config.get(key, default_config[key])
                for key in default_config.keys()
                if key not in ["path", "enabled"]
            }
        return {k: v for k, v in default_config.items() if k not in ["path", "enabled"]}

    def get_firmware_downloader_params(self):
        """获取固件下载工具的参数"""
        tool_config = self.get_tool_config("firmware_downloader")
        default_config = self.REGISTERED_TOOLS["firmware_downloader"]["default_config"]

        if tool_config:
            return {
                key: tool_config.get(key, default_config[key])
                for key in default_config.keys()
                if key not in ["path", "enabled"]
            }
        return {k: v for k, v in default_config.items() if k not in ["path", "enabled"]}

    @classmethod
    def get_all_tool_names(cls):
        """获取所有已注册的工具名称列表"""
        return list(cls.REGISTERED_TOOLS.keys())

    @classmethod
    def get_tool_info(cls, tool_name):
        """获取工具信息"""
        return cls.REGISTERED_TOOLS.get(tool_name)

    @classmethod
    def get_tool_display_name(cls, tool_name):
        """获取工具显示名称"""
        tool_info = cls.REGISTERED_TOOLS.get(tool_name)
        return tool_info["display_name"] if tool_info else tool_name

    @classmethod
    def get_tool_button_text(cls, tool_name):
        """获取工具按钮文本"""
        tool_info = cls.REGISTERED_TOOLS.get(tool_name)
        return tool_info["button_text"] if tool_info else tool_name

    @classmethod
    def tool_requires_serial_port(cls, tool_name):
        """检查工具是否需要独占串口"""
        tool_info = cls.REGISTERED_TOOLS.get(tool_name)
        return tool_info.get("requires_serial_port", False) if tool_info else False

    @staticmethod
    def _compare_versions(version1, version2):
        """
        比较两个版本号
        返回值:
            > 0: version1 > version2
            = 0: version1 == version2
            < 0: version1 < version2
        """
        try:
            # 将版本号分割为数字列表
            v1_parts = [int(x) for x in version1.split('.')]
            v2_parts = [int(x) for x in version2.split('.')]

            # 补齐长度
            max_len = max(len(v1_parts), len(v2_parts))
            v1_parts.extend([0] * (max_len - len(v1_parts)))
            v2_parts.extend([0] * (max_len - len(v2_parts)))

            # 逐位比较
            for v1, v2 in zip(v1_parts, v2_parts):
                if v1 > v2:
                    return 1
                elif v1 < v2:
                    return -1
            return 0
        except (ValueError, AttributeError):
            # 如果版本号格式不正确，返回0表示相等
            return 0


    def set_last_used_directory(self, path):
        """设置最后使用的目录"""
        self.config["last_used_directory"] = path
        self.save_config()

    def get_last_used_directory(self):
        """获取最后使用的目录, 如果没有则返回当前工作目录"""
        return self.config.get("last_used_directory", os.getcwd())
