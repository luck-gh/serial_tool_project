"""日志设置的唯一默认值及规则;普通CLI收录路径不加载schema依赖."""
import copy


DEFAULT_LOG_SETTINGS = dict(limit_mb=4, retain_all=True, keep_timestamp=True,
    save_full_log=False, log_directory="", sources={key: True for key in ("send", "receive", "system", "error")},
    system_levels={key: True for key in ("normal", "warning", "info", "debug")})


def settings_from_state(state):
    """当前契约字段缺省使用产品默认值,不读取已废弃的保存规则."""
    result = copy.deepcopy(DEFAULT_LOG_SETTINGS)
    for key, value in state.get("log_settings", {}).items():
        if key in ("sources", "system_levels"):
            result[key].update(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def log_schema():
    """独立日志对象的JSON Schema,GUI和CLI管理入口共享."""
    def obj(properties):
        return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)
    return obj(dict(limit_mb=dict(type="integer", minimum=1, maximum=1024),
        retain_all=dict(type="boolean"), keep_timestamp=dict(type="boolean"), save_full_log=dict(type="boolean"),
        log_directory=dict(type="string", maxLength=32767, pattern=r"^[^\r\n\u0000]*$"),
        sources=obj({key: dict(type="boolean") for key in DEFAULT_LOG_SETTINGS["sources"]}),
        system_levels=obj({key: dict(type="boolean") for key in DEFAULT_LOG_SETTINGS["system_levels"]})))


def prepare_settings(state, patch):
    """校验边界输入的日志Merge Patch,不导入GUI控件schema."""
    from jsonschema import Draft202012Validator
    if not isinstance(patch, dict):
        raise ValueError("日志补丁必须为JSON对象")
    # 文件可省略当前字段以使用默认值,但已提供字段必须符合对象/类型契约.
    provided_schema = log_schema()
    provided_schema["required"] = []
    for key in ("sources", "system_levels"):
        provided_schema["properties"][key]["required"] = []
    errors = list(Draft202012Validator(provided_schema).iter_errors(state.get("log_settings", {})))
    if errors:
        raise ValueError("已有日志配置校验失败: " + errors[0].message)
    result = settings_from_state(state)
    for key, value in patch.items():
        if key in ("sources", "system_levels") and isinstance(value, dict):
            result[key].update(value)
        else:
            result[key] = copy.deepcopy(value)
    errors = list(Draft202012Validator(log_schema()).iter_errors(result))
    if errors:
        raise ValueError("日志配置校验失败: " + errors[0].message)
    return result


def capture_enabled(state, settings, source, level):
    """发送快捷开关与日志规则OR联动;其余来源和级别按产生时收录."""
    if source == "send" and state.get("send_settings", {}).get("show_send", False):
        return True
    return settings["retain_all"] or (settings["sources"][source] and
        (source != "system" or settings["system_levels"][level]))


def apply_local(manager, patch, validate_runtime=lambda settings: None):
    """校验后原子保存一次;保存失败恢复包含元数据的整个内存配置."""
    state = manager.get("state", {})
    settings = prepare_settings(state, patch)
    validate_runtime(settings)
    candidate = copy.deepcopy(manager.config)
    candidate.setdefault("state", {})["log_settings"] = settings
    manager.save_config(candidate)
    manager.config = candidate
    return settings
