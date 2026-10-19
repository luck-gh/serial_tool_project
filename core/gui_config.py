"""本机 GUI 配置查询,JSON Merge Patch 校验与事务应用,不执行设备命令.

字段来自窗口持久化状态及工具注册表;JSON Schema 由 jsonschema 校验.
在线修改先更新控件,再原子保存一次;失败恢复内存与控件.版本和运行状态
不在可写配置中,远程设备端配置须在设备端电脑调用同一入口.
"""
import copy
import json
import os
import stat

from core.command_parser import command_parts, unescape_text

from jsonschema import Draft202012Validator
from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QSpinBox

from core.firmware_download import build_download_config, get_firmware_downloader_class
from core.serial_config import validate_baudrate
from core.text_search import compile_pattern, normalize_rule, validate_response_expression
from core.command_types import OutputSource
from utils.ui_utils import UIUtils


def bindings(window):
    """返回持久化标量字段到现有 GUI 控件的映射,不包含动作按钮."""
    sections = {
        "basic_settings": dict(baudrate="baud_combo", databits="data_bits_combo", parity="parity_combo", stopbits="stop_bits_combo"),
        "remote_settings": dict(mode="remote_mode_combo", host="remote_host_input", port="remote_port_spin", token="remote_token_input"),
        "send_settings": dict(loop_send="loop_send_check", loop_interval="loop_interval_spin", continuous_interval="interval_spin",
                              show_send="show_send_check", send_color="send_color_combo", ending="ending_combo", replace_send="replace_send_check"),
        "receive_settings": dict(show_timestamp="timestamp_check"),
        "log_settings": dict(save_full_log="save_full_log_check"),
    }
    return {(section, key): getattr(window, attribute)
            for section, fields in sections.items() for key, attribute in fields.items()}


def snapshot(window):
    """读取可配置数据的独立 JSON 副本,查询不读写配置文件."""
    tools = {name: dict(info["default_config"], **(window.config_manager.get_tool_config(name) or {}))
             for name, info in window.config_manager.REGISTERED_TOOLS.items()}
    return json.loads(json.dumps(dict(state=window.snapshot_state(), tools=tools)))


def redacted(config):
    """从导出副本移除口令,避免掩码被误当真实口令重新应用."""
    result = copy.deepcopy(config)
    result["state"]["remote_settings"].pop("token", None)
    return result


def object_schema(properties, required=True):
    """构造拒绝未知键的对象规则;required=False 用于含可选字段的替换规则."""
    return dict(type="object", properties=properties, additionalProperties=False,
                required=list(properties) if required else [])


def infer_schema(value):
    """从已知状态结构生成基础类型约束,业务枚举和边界由 schema 补充."""
    if isinstance(value, dict):
        return object_schema({key: infer_schema(item) for key, item in value.items()})
    if type(value) is bool:
        return dict(type="boolean")
    if type(value) is int:
        return dict(type="integer")
    if isinstance(value, str):
        return dict(type="string")
    if isinstance(value, list):
        return dict(type="array")
    return dict(type="null")


def schema(window):
    """返回完整可写配置的 JSON Schema,枚举与数值范围复用现有控件和下载核心.

    所有 state 分组及注册工具均列出;版本,时间戳及连接状态未包含.命令表
    行格式为 [是否启用, 命令, 注释],替换规则按从零开始的行号字符串索引.
    """
    example = snapshot(window)
    example["tools"] = {name: info["default_config"] for name, info in window.config_manager.REGISTERED_TOOLS.items()}
    result = infer_schema(example)
    result["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    state = result["properties"]["state"]["properties"]
    for (section, key), widget in bindings(window).items():
        rule = state[section]["properties"][key]
        if isinstance(widget, QSpinBox):
            rule.update(minimum=widget.minimum(), maximum=widget.maximum())
        elif isinstance(widget, QComboBox) and not widget.isEditable():
            rule["enum"] = [(widget.itemData(index) if widget is window.remote_mode_combo else widget.itemText(index))
                            for index in range(widget.count())]
    state["basic_settings"]["properties"]["baudrate"].update(pattern=r"^[0-9]+$")
    for field in ("port", "port_node"):
        state["basic_settings"]["properties"][field].update(maxLength=256, pattern=r"^[^\r\n\u0000]*$")
    state["remote_settings"]["properties"]["host"].update(minLength=1, maxLength=253, pattern=r"^[^\s\u0000]+$")
    state["remote_settings"]["properties"]["token"].update(maxLength=4096, writeOnly=True)
    from core.log_settings import log_schema
    state["log_settings"] = log_schema()
    state["commands"] = dict(type="array", maxItems=10000, items=dict(type="array", minItems=3, maxItems=3,
        prefixItems=[dict(type="boolean"), dict(type="string"), dict(type="string")], items=False))
    state["ui_settings"]["properties"]["h_splitter_sizes"] = dict(type="array", minItems=2, maxItems=2,
        items=dict(type="integer", minimum=0, maximum=100000))
    rule = object_schema(dict(find=dict(type="string", minLength=1), replace=dict(type="string"),
        options=object_schema({key: dict(type="boolean") for key in ("case_sensitive", "use_regex", "whole_word")}, False),
        response_validation=object_schema(dict(enabled=dict(type="boolean"), expression=dict(type="string"),
            timeout_ms=dict(type="integer", minimum=10, maximum=60000), on_failure=dict(enum=["continue", "stop"]),
            color_policy=dict(enum=["latest", "sticky_failure"]), show_error=dict(type="boolean")), False)), False)
    rule["required"] = ["find", "replace"]
    state["send_settings"]["properties"]["global_replacement_rule"] = dict(anyOf=[dict(type="null"), rule])
    state["command_replacement_rules"] = dict(type="object", patternProperties={r"^(0|[1-9][0-9]*)$": rule}, additionalProperties=False)
    tool_rules = result["properties"]["tools"]["properties"]
    number = tool_rules["number_conversion_dialog"]["properties"]
    number["data_width"]["enum"] = ["BYTE", "WORD", "DWORD", "QWORD"]
    binary = tool_rules["bin_hex_converter"]["properties"]
    binary["data_width"]["enum"] = [1, 2, 4, 8]
    binary["bytes_per_row"].update(minimum=1, maximum=128)
    binary["byteorder"]["enum"] = ["little", "big"]
    firmware = tool_rules["firmware_downloader"]["properties"]
    firmware["packet_size"].update(minimum=1, maximum=4096)
    for key, value in firmware.items():
        if key.endswith("crc_type"):
            value["enum"] = ["CRC16-MODBUS", "CRC16-CCITT", "CRC16-XMODEM", "CRC32"]
        elif key.endswith("data_format"):
            value["enum"] = ["HEX", "ASCII", "DEC"]
        elif key.endswith("check_mode"):
            value["enum"] = ["AND", "OR"]
        elif key.endswith("timeout"):
            value.update(minimum=1, maximum=600000)
        elif key.endswith("expected_length"):
            value.update(minimum=1, maximum=65536)
    return result


def merge_patch(target, patch):
    """按 RFC 7396 合并副本:对象逐键合并,数组整体替换,null 删除字段.

    删除必填字段会在后续校验中拒绝;清空替换规则可删除对应行号键.
    """
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = copy.deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def validate_document(window, candidate):
    """校验完整配置,只报告字段路径和规则,不把口令等原始值写入错误."""
    errors = list(Draft202012Validator(schema(window)).iter_errors(candidate))
    if errors:
        error = errors[0]
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        raise ValueError(f"配置校验失败: {path}（{error.validator}；请查询 config-schema）")


def prepare(window, patch):
    """生成并验证候选配置,不修改控件,文件或设备;非法字段只报告路径.

    Args:
        window (SerialTool): 本机已运行窗口,提供当前值和控件约束.
        patch (dict): 以 state/tools 为根的 JSON Merge Patch;不接受元数据键.

    Returns:
        tuple[dict, dict]: 修改前快照与完整候选配置.

    Raises:
        ValueError: 字段,类型,边界,规则,ACK 或行索引无效.
    """
    if not isinstance(patch, dict) or set(patch) - {"state", "tools"}:
        raise ValueError("配置修改只接受 state/tools 对象，不接受版本、时间戳或运行状态")
    before = snapshot(window)
    candidate = merge_patch(before, patch)
    # null 删除全局规则后,统一为现有 UI 使用的无规则状态.
    send = candidate.get("state", {}).get("send_settings", {}) if isinstance(candidate.get("state"), dict) else {}
    if isinstance(send, dict) and "global_replacement_rule" not in send:
        send["global_replacement_rule"] = None
    validate_document(window, candidate)
    state = candidate["state"]
    logs = state["log_settings"]
    archive = window.output_manager.archive
    if archive:
        if logs["save_full_log"] and archive.error:
            raise ValueError("请先另存未确认日志，再启用保存完整日志")
        if logs["log_directory"] != before["state"]["log_settings"]["log_directory"] and (
                window.output_manager.archive_enabled or archive.busy or archive.pending or archive.error):
            raise ValueError("修改日志目录前请关闭文件保存，并等待批次完成或另存未确认日志")
    validate_baudrate(state["basic_settings"]["baudrate"])
    basic_patch = patch.get("state", {}).get("basic_settings", {})
    if "port" in basic_patch and "port_node" not in basic_patch:
        state["basic_settings"]["port_node"] = state["basic_settings"]["port"]
    basic = state["basic_settings"]
    if basic["port_node"].split(" ")[0] != basic["port"]:
        raise ValueError("port_node 的端口标识必须与 port 一致")
    rules = state["command_replacement_rules"]
    for row in rules:
        if int(row) >= len(state["commands"]):
            raise ValueError(f"替换规则行号超出命令表: {row}")
    modules = {"全部"}
    for _, command, _ in state["commands"]:
        kind, parameter = command_parts(command)
        if kind == "mode":
            modules.add(parameter.strip())
    for key in ("jump_module", "send_module"):
        if state["send_settings"][key] not in modules:
            raise ValueError(f"配置中的模块不存在: {key}")
    for rule in [state["send_settings"]["global_replacement_rule"], *rules.values()]:
        if rule:
            pattern = compile_pattern(rule["find"], rule.get("options"))
            response = rule.get("response_validation", {})
            if response.get("expression"):
                validate_response_expression(response["expression"], pattern, rule.get("options"))
    # 只有修改下载参数时才校验它们,现存无效配置不妨碍修改显示设置.
    if candidate["tools"]["firmware_downloader"] != before["tools"]["firmware_downloader"]:
        get_firmware_downloader_class().validate_config(build_download_config(candidate["tools"]["firmware_downloader"]))
    return before, candidate


def apply_widgets(window, config):
    """将已校验配置映射到控件,阻断修改信号;不打开串口,不发起连接或发送.

    命令表仅在内容或规则变化时重建,避免无关设置修改破坏当前行选择.
    调用方负责事务回滚和保存.
    """
    state = config["state"]
    fields = bindings(window)
    sources = dict(send=window.show_send_source_check, receive=window.show_recv_source_check,
                   system=window.show_sys_source_check, error=window.show_err_source_check)
    widgets = list(fields.values()) + list(sources.values()) + [window.port_combo, window.command_table,
              window.jump_module_combo, window.send_module_combo, window.h_splitter]
    blockers = [QSignalBlocker(widget) for widget in widgets]
    try:
        for (section, key), widget in fields.items():
            value = state[section][key]
            if widget is window.remote_mode_combo:
                widget.setCurrentIndex(widget.findData(value))
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value)
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
        basic = state["basic_settings"]
        window.port_combo.setCurrentText(basic["port_node"] or basic["port"])
        receive = state["receive_settings"]
        for name, widget in sources.items():
            widget.setChecked(receive["display_sources"][name])
        window._send_display_selected = receive["display_sources"]["send"]
        window.log_settings = {key: copy.deepcopy(value) for key, value in state["log_settings"].items()
                               if key != "save_full_log"}
        window.system_level_selection = {name for name, enabled in receive["system_levels"].items() if enabled}
        window.save_source_selection = {source for source in OutputSource if receive["save_sources"][source.value]}
        window.save_system_level_selection = {name for name, enabled in receive["save_system_levels"].items() if enabled}
        window.global_replacement_rule = normalize_rule(state["send_settings"]["global_replacement_rule"])
        current_commands = json.loads(json.dumps(window.command_table.get_all_commands()))
        if state["commands"] != current_commands:
            window.command_table.clear_all()
            for row, command in enumerate(state["commands"]):
                window.command_table.add_command_row(*command, row)
            window.refresh_modules(silent=True)
        if state["command_replacement_rules"] != window.command_table.get_replacement_rules():
            window.command_table.load_replacement_rules(state["command_replacement_rules"], notify=False)
        for key, widget in (("jump_module", window.jump_module_combo), ("send_module", window.send_module_combo)):
            text = state["send_settings"][key]
            if widget.findText(text) < 0:
                raise ValueError(f"配置中的模块不存在: {key}")
            widget.setCurrentText(text)
        if state["ui_settings"]["h_splitter_sizes"] != window.h_splitter.sizes():
            window._set_h_splitter_sizes(state["ui_settings"]["h_splitter_sizes"])
        for name, expanded in state["tool_settings"]["left_group_expanded"].items():
            getattr(window, name + "_group").setExpanded(expanded)
        window._update_system_level_button_text()
        window.system_level_button.setEnabled(window.show_sys_source_check.isChecked())
        window._render_log_controls()
        window.update_replacement_mode_ui()
        window.output_manager.refresh_display()
        window.update_tools_state()
        window.update_remote_host_options()
    finally:
        for blocker in blockers:
            blocker.unblock()


def apply(window, patch):
    """验证并事务应用一批配置,持久化一次,失败恢复旧控件及配置对象.

    Args:
        window (SerialTool): 本机窗口;调用方须已通过 CLI 任务占用门禁.
        patch (dict): state/tools 的部分修改,具体语义见 merge_patch.

    Returns:
        bool: True 表示配置实际变化并保存,False 表示无变化且没有写盘.

    Raises:
        ValueError: 配置非法,或尝试修改活动连接的串口/远程设置.
        OSError: 原子保存失败,旧文件保持不变并恢复旧内存状态.
    """
    before, candidate = prepare(window, patch)
    if before == candidate:
        return False
    if before["state"]["basic_settings"] != candidate["state"]["basic_settings"] and (
            window.is_connected or window.serial_thread is not None or window.remote_mode == "client"):
        raise ValueError("修改串口配置前请关闭串口；控制端基本设置须通过设备端回执路径执行")
    if before["state"]["remote_settings"] != candidate["state"]["remote_settings"] and window.remote_thread is not None:
        raise ValueError("修改远程配置前请先执行 remote-stop")
    manager = window.config_manager
    original_config = manager.config
    dirty = window._state_dirty
    loading = window._loading_state
    window._loading_state = True
    try:
        manager.config = dict(copy.deepcopy(original_config), **candidate)
        apply_widgets(window, candidate)
        manager.config["state"] = window.snapshot_state()
        manager.save_config()
        window._state_dirty = False
    except Exception:
        manager.config = original_config
        apply_widgets(window, before)
        window._state_dirty = dirty
        raise
    finally:
        window._loading_state = loading
    window._apply_log_settings()
    window.gui_cli_bridge.publish_log_settings(candidate["state"])
    return True


def config_paths(window):
    """区分当前保存目标与启动时固定的桥接定位路径."""
    return dict(working_config_file=os.path.realpath(window.config_manager.config_file),
                bridge_config_file=os.path.realpath(window.gui_cli_bridge.config_file))


def switch_file(window, path):
    """只读加载工作 JSON 并切换后续保存目标,保留运行连接且不写任一文件.

    Args:
        window (SerialTool): GUI 所属线程中的窗口.
        path (str): 已有可写 JSON 文件;相对路径由调用者工作目录解析.
    """
    bridge = window.gui_cli_bridge
    if (bridge.active or window.serial_operation_busy() or window.is_continuous_sending
            or window._remote_device_pending or window.response_validation_manager.pending
            or QApplication.activeModalWidget() is not None
            or getattr(window, '_manual_module_generation', None) == window._command_generation
            or any(meta['scope'] for meta in bridge.machine_subscribers.values())):
        raise ValueError("GUI 当前有任务运行,不能切换工作 JSON")
    path = os.path.realpath(path)
    if os.path.samefile(path, window.config_manager.config_file):
        return dict(config_paths(window), switched=False, preserved_connection_settings=True)
    if not os.stat(path).st_mode & stat.S_IWRITE or not os.access(path, os.W_OK):
        raise ValueError("工作 JSON 为只读文件")
    with open(path, encoding="utf-8-sig") as stream:
        config = json.load(stream)
    manager = window.config_manager
    if not isinstance(config, dict):
        raise ValueError("工作 JSON 必须为对象")
    if manager._compare_versions(config.get("config_version", "0.0.0"), manager.tool_version) > 0:
        raise ValueError("工作 JSON 版本高于当前工具版本")
    tools = {name: dict(info['default_config'], **config.get('tools', {}).get(name, {}))
             for name, info in manager.REGISTERED_TOOLS.items()}
    def with_defaults(defaults, value):
        """只补缺省字段,文档中的显式 null 保留给 schema 拒绝,不能继承旧工作值."""
        if isinstance(defaults, dict) and isinstance(value, dict):
            return {key: with_defaults(defaults.get(key), item) for key, item in dict(defaults, **value).items()}
        return copy.deepcopy(value)
    state = with_defaults(window._default_work_state, config.get('state', config.get('last_state', {})))
    validate_document(window, dict(state=state, tools=tools))
    before, candidate = prepare(window, dict(state=state, tools=tools))
    # 运行连接不属于工作文件切换,避免控件声称新文件的连接参数已经生效.
    for section in ("basic_settings", "remote_settings"):
        candidate["state"][section] = before["state"][section]
    original, old_path = manager.config, manager.config_file
    dirty, loading = window._state_dirty, window._loading_state
    timer_active = window._state_save_timer.isActive()
    window._state_save_timer.stop()
    window._loading_state = True
    try:
        manager.config = dict(copy.deepcopy(config), **candidate)
        apply_widgets(window, candidate)
        manager.config["state"] = window.snapshot_state()
        manager.config_file = path
        window._state_dirty = False
    except Exception:
        manager.config, manager.config_file = original, old_path
        try:
            apply_widgets(window, before)
        finally:
            window._state_dirty = dirty
            if timer_active:
                window._state_save_timer.start()
        raise
    finally:
        window._loading_state = loading
    window._apply_log_settings()
    window.gui_cli_bridge.publish_log_settings(candidate["state"])
    return dict(config_paths(window), switched=True, preserved_connection_settings=True)
