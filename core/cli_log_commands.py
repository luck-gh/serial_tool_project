"""日志管理命令;配置对象与GUI事务共享,不依赖控件."""
import json
from pathlib import Path
import sys

from core.log_settings import prepare_settings, apply_local


def read_patch(path):
    """读取至多128 KiB的外部JSON补丁,不修改文件."""
    if path == "-":
        raw = sys.stdin.buffer.read(131073)
    else:
        with open(path, "rb") as stream:
            raw = stream.read(131073)
    if len(raw) > 131072:
        raise ValueError("日志补丁超过128 KiB")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("日志补丁必须为JSON对象")
    return value


def run_log_action(args, config_file, version, date):
    """一次性日志管理,独立恢复明确限定当前存活进程."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if (args.command is not None or args.console or args.machine or args.gui_action or args.port or args.baudrate
                or args.import_template is not None or args.export_template is not None or args.save_received is not None
                or args.expect is not None or any(getattr(args, key) is not None for key in
                    ("remote_host", "remote_port", "remote_password", "config_patch", "config_switch"))):
            raise ValueError("--log-action须单独执行,可与--config/--via-gui组合")
        if (args.log_patch is not None) != (args.log_action in ("check", "apply")):
            raise ValueError("check/apply必须提供--log-patch,其他日志操作不接受补丁")
        if (args.log_recover is not None) != (args.log_action == "recover"):
            raise ValueError("recover必须提供--log-recover,其他日志操作不接受恢复路径")
        patch = read_patch(args.log_patch) if args.log_patch is not None else None
        if args.via_gui:
            from core.cli_bridge_client import run_gui_cli
            return run_gui_cli(args, config_file, log_request=dict(action="log-" + args.log_action,
                **({"log_patch": patch} if patch is not None else {}),
                **({"path": str(Path(args.log_recover).resolve())} if args.log_recover is not None else {})))
        from managers.config_manager import ConfigManager
        # 管理入口严格只读解析,不借启动加载器创建/修复/迁移用户文件.
        with open(config_file, "r", encoding="utf-8-sig") as stream:
            config = json.load(stream)
        if not isinstance(config, dict) or not isinstance(config.get("state", {}), dict):
            raise ValueError("配置文件必须为包含state对象的JSON对象")
        if ConfigManager._compare_versions(config.get("config_version", "0.0.0"), version) > 0:
            raise ValueError("配置版本高于当前工具版本")
        manager = ConfigManager(version, date, config_file, config_data=config)
        state = manager.get("state", {})
        settings = prepare_settings(state, {})
        fields = {}
        if args.log_action == "get":
            fields["log_settings"] = settings
        elif args.log_action == "check":
            fields["log_settings"] = prepare_settings(state, patch)
        elif args.log_action == "apply":
            fields["log_settings"] = apply_local(manager, patch)
        elif args.log_action == "status":
            fields["log_status"] = dict(scope="current-process", enabled=False, explicit=False, archive=None)
        else:
            raise ValueError("独立一次性进程没有上一进程的内存批次;请在仍存活的控制台使用LogRecover或--via-gui")
        print(json.dumps(dict(event="result", success=True, status="success", message="日志操作完成", **fields), ensure_ascii=False))
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps(dict(event="result", success=False, status="failed", message=str(exc)), ensure_ascii=False))
        return 1
