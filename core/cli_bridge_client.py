"""CLI 侧本机 IPC 客户端;不导入 GUI 服务端或界面控件."""
import base64
import json
import os
import re
import sys
import time
import uuid

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtNetwork import QLocalSocket
from core.cli_protocol import bridge_target, bridge_connection_failure, MAX_MESSAGE, MAX_CONFIG_RESULT_MESSAGE
from core.command_parser import command_parts


def decode_response_line(raw, request):
    """按请求类型校验一条 GUI JSONL 回执,避免把其它任务事件当作查询结果.

    Args:
        raw (bytes): 不含行末换行的 UTF-8 JSON 帧.
        request (dict): 已发送请求,包含动作及请求 ID.

    Returns:
        dict: 已解码并通过关联及容量校验的消息.

    Raises:
        ValueError: 帧超限、JSON 无效、请求 ID 不匹配或 config-get 收到非终态事件.
    """
    config_get = request["action"] == "config-get"
    frame_size = len(raw) + 1
    if frame_size > (MAX_CONFIG_RESULT_MESSAGE if config_get else MAX_MESSAGE):
        raise ValueError("GUI 回执超过允许的 UTF-8 字节上限")
    message = json.loads(raw.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("GUI 回执必须为 JSON 对象")
    if message.get("event") == "result" and message.get("id") != request["id"]:
        raise ValueError("GUI result ID 与请求不匹配")
    if config_get and frame_size > MAX_MESSAGE:
        if message.get("event") != "result" or message.get("id") != request["id"]:
            raise ValueError("超过 128 KiB 的 config-get 帧必须是当前请求的 result")
        if (message.get("success") is not True or message.get("status") != "success"
                or not isinstance(message.get("config"), dict)
                or type(message.get("max_config_result_bytes")) is not int
                or message["max_config_result_bytes"] != MAX_CONFIG_RESULT_MESSAGE):
            raise ValueError("超过 128 KiB 的 config-get 回执缺少有效容量声明")
    return message


def run_gui_cli(args, config_file, *, emit=None, received_file=None, receive_save=None, log_request=None):
    """通过本机 Qt socket 执行一次 CLI 请求,JSON 行输出供 Agent/脚本读取.

    Args:
        args (argparse.Namespace): CLI 参数;包含 command/gui_action/gui_timeout/
            read_timeout;remote_host/remote_port/remote_password 传给远程启停
            操作.port/baudrate 使用已有特殊命令单独设置,不隐式改配置.
        config_file (str): 已运行 GUI 的配置文件路径,仅用于确定本机端点.

    Returns:
        int: 0=收到完成/查询/取消受理回执,1=操作失败,2=未找到 GUI/参数无效,
            3=请求发出后断线或超时导致结果未知, 4=期望响应超时或未匹配.
            结果未知时绝不改走独立串口.
    """
    def output(message):
        if message.get("event") == "result" and received_file is not None:
            received_file.close()
        if emit is not None:
            emit(message)
        else:
            print(json.dumps(message, ensure_ascii=False), flush=True)

    # Agent 重定向管道与 Windows 控制台使用相同的 UTF-8 JSON 编码.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.port or args.baudrate:
        print(json.dumps(dict(event="result", success=False,
              message="桥接模式请用 --send ComPort:... 或 BaudRate:...，不使用 --port/--baudrate"), ensure_ascii=False))
        return 2
    if args.gui_timeout <= 0:
        return 2
    expect = getattr(args, "expect", None)
    matcher = None
    if expect is not None:
        if args.command is None or args.gui_action:
            print(json.dumps(dict(event="result", success=False,
                  message="--expect 必须与 --send 一起使用"), ensure_ascii=False))
            return 2
        try:
            from core.receive_text import ExpectMatcher
            matcher = ExpectMatcher(expect)
        except (ValueError, TypeError, re.error) as exc:
            print(json.dumps(dict(event="result", success=False, message=f"--expect 无效: {exc}"), ensure_ascii=False))
            return 2
    remote_options = {key: getattr(args, key, None) for key in ("remote_host", "remote_port", "remote_password")
                      if getattr(args, key, None) is not None}
    if ((remote_options and args.gui_action not in ("remote-connect", "remote-listen"))
            or (args.gui_action and args.command is not None)):
        print(json.dumps(dict(event="result", success=False,
              message="远程参数仅用于 remote-connect/remote-listen；--gui-action 与 --send 必须分次执行"), ensure_ascii=False))
        return 2
    patch_path = getattr(args, "config_patch", None)
    if (patch_path is not None) != (args.gui_action in ("config-check", "config-apply")):
        print(json.dumps(dict(event="result", success=False,
            message="config-check/config-apply 必须提供 --config-patch，其他操作不接受该参数"), ensure_ascii=False))
        return 2
    switch_path = getattr(args, "config_switch", None)
    if (switch_path is not None) != (args.gui_action == "config-switch"):
        output(dict(event="result", success=False, status="failed", message="config-switch 必须配合 --config-switch 路径"))
        return 2
    config_patch = None
    if patch_path is not None:
        try:
            if patch_path == "-":
                raw = sys.stdin.buffer.read(MAX_MESSAGE + 1)
            else:
                with open(patch_path, "rb") as patch_file:
                    raw = patch_file.read(MAX_MESSAGE + 1)
            if len(raw) > MAX_MESSAGE:
                raise ValueError("配置修改文件超过 128 KiB")
            config_patch = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(config_patch, dict):
                raise ValueError("配置修改必须为 JSON 对象")
        except (OSError, ValueError, UnicodeError):
            print(json.dumps(dict(event="result", success=False,
                message="配置修改读取失败：需要不超过 128 KiB 的 UTF-8 JSON 对象"), ensure_ascii=False))
            return 2
    application = QCoreApplication.instance() or QCoreApplication([])
    sock = QLocalSocket()
    target = bridge_target(config_file)
    sock.connectToServer(target["endpoint"])
    if not sock.waitForConnected(1500):
        failure = bridge_connection_failure(target, sock.errorString(), sock.error())
        if matcher is not None:
            failure.update(matcher.result_fields())
        output(failure)
        sock.abort()
        return 2
    command = args.command
    if command:
        kind, parameter = command_parts(command)
        if kind == "firmwaredownload" and parameter:
            from core.firmware_download import normalize_firmware_path
            command = "FirmwareDownload:" + normalize_firmware_path(parameter)
    request = dict(id=uuid.uuid4().hex, action=args.gui_action or ("send" if command is not None else "status"),
                   command=command or "", read_timeout=args.read_timeout)
    if log_request is not None:
        request.update(log_request)
    from core.command_templates import template_path
    if getattr(args, "import_template", None) is not None or getattr(args, "export_template", None) is not None:
        request["action"] = "template-import" if args.import_template is not None else "template-export"
        request["path"] = template_path(args.import_template or args.export_template)
    if receive_save is not None:
        request["action"] = "receive-save-state"
        request["receive_save"] = {key: receive_save.get(key) for key in ("path", "outcome", "timestamp", "file_event_id", "failure")}
    if received_file is not None and received_file.stream is not None:
        request["detach_on_disconnect"] = True
    if expect is not None:
        request["expect"] = expect
    if switch_path is not None:
        request["config_switch"] = os.path.realpath(switch_path)
    request.update(remote_options)
    if config_patch is not None:
        request["config_patch"] = config_patch
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_MESSAGE:
        sock.abort()
        output(dict(event="result", success=False, status="failed",
                    message="CLI 请求超过 128 KiB，未提交 GUI；请缩小模板或配置补丁"))
        return 2
    sock.write(payload)
    sock.flush()
    deadline = time.monotonic() + args.gui_timeout
    buffer = b""
    try:
        while time.monotonic() < deadline:
            if not sock.bytesAvailable():
                sock.waitForReadyRead(min(200, max(1, int((deadline - time.monotonic()) * 1000))))
            buffer += bytes(sock.readAll())
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                message = decode_response_line(raw, request)
                if message.get("event") == "receive" and message.get("id") == request["id"]:
                    data = base64.b64decode(message.get("data", ""), validate=True)
                    if matcher is not None:
                        matcher.feed(data)
                    if received_file is not None:
                        received_file.feed(data)
                if matcher is not None and message.get("event") == "result":
                    confirmed_expect = message.get("expect_matched")
                    matcher.finish()
                    message.update(matcher.result_fields())
                    if message.get("expect_timeout"):
                        message["message"] = matcher.result_message()
                output(message)
                if message.get("event") == "result":
                    if matcher is not None and message.get("expect_timeout"):
                        return 0 if matcher.matched else 4
                    if matcher is not None and message.get("success"):
                        if confirmed_expect is not True:
                            print(json.dumps(dict(event="error", message="GUI 未确认 --expect 匹配，请更新 GUI"), ensure_ascii=False))
                            return 2
                        return 0 if matcher.matched else 4
                    return 0 if message.get("success") else (3 if message.get("status") == "unknown" else 1)
            response_limit = MAX_CONFIG_RESULT_MESSAGE if request["action"] == "config-get" else MAX_MESSAGE
            if len(buffer) > response_limit:
                raise ValueError(f"GUI 回执超过 {response_limit} 字节上限")
            if sock.state() == QLocalSocket.UnconnectedState:
                break
    except (ValueError, OSError) as exc:
        from core.receive_text import ReceiveSaveError
        if isinstance(exc, ReceiveSaveError):
            failure = dict(received_file.last_event or {}, event="result", success=False, status="unknown", message=str(exc))
            if matcher is not None:
                matcher.finish()
                failure.update(matcher.result_fields())
            output(failure)
            return 3
        output(dict(event="error", message=str(exc)))
    finally:
        sock.abort()
    result = dict(event="result", success=False, status="unknown",
                  message="结果未知：请求发出后连接中断或等待超时；未自动重试")
    if matcher is not None:
        matcher.finish()
        result.update(matcher.result_fields())
    output(result)
    return 3


def report_receive_save(config_file, event):
    """把有限 RX 保存状态告知 GUI;不传任意文本,不重试任何设备操作."""
    from core.cli_runner import build_parser
    args = build_parser().parse_args([])
    args.gui_timeout = 3
    replies = []
    code = run_gui_cli(args, config_file, emit=replies.append, receive_save=event)
    return code == 0, replies
