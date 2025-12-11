import json
import os
import datetime

class ConfigManager:
    """管理应用程序的配置"""
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

        # --- MIGRATION LOGIC ---
        migrated = False
        if "tools" in config:
            for tool_name, tool_config in config["tools"].items():
                if isinstance(tool_config, str):
                    config["tools"][tool_name] = {
                        "path": tool_config,
                        "enabled": True
                    }
                    migrated = True
        
        if migrated:
            self.save_config(config)

        return config

    def create_default_config(self):
        """创建并保存默认配置文件"""
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        default_config = {
            "tool_version": self.tool_version,
            "tool_update_time": self.tool_version_date,
            "config_version": self.tool_version,
            "config_last_updated": now,
            "tools": {
                "number_conversion_dialog": {
                    "path": "",
                    "enabled": True
                }
            }
        }
        self.save_config(default_config)
        return default_config

    def save_config(self, config_data=None):
        """保存当前配置到文件"""
        if config_data is None:
            config_data = self.config
        
        # 更新版本和时间戳
        config_data["tool_version"] = self.tool_version
        config_data["tool_update_time"] = self.tool_version_date
        config_data["config_last_updated"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)

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

    def set_tool_config(self, tool_name, enabled, path):
        """设置工具的完整配置"""
        if "tools" not in self.config:
            self.config["tools"] = {}
        
        self.config["tools"][tool_name] = {
            "enabled": enabled,
            "path": path,
        }
        self.save_config()

    def set_last_used_directory(self, path):
        """设置最后使用的目录"""
        self.config["last_used_directory"] = path
        self.save_config()

    def get_last_used_directory(self):
        """获取最后使用的目录, 如果没有则返回当前工作目录"""
        return self.config.get("last_used_directory", os.getcwd())
