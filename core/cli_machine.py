"""GUI 桥接控制台的 UTF-8 机器会话;匹配和设备完成判据由调用者负责."""
import json
import os
import queue
import re
import sys
import threading

from core.cli_protocol import MAX_MESSAGE
from core.cli_console import GuiConsoleBackend
from core.command_parser import Command, CONSOLE_LOCAL, parse_input
from core.output_rules import event_timestamp


class MachineSession:
    """串行维护请求、采集窗口和唯一终态,不拥有串口或设备匹配器.

    Args:
        backend_factory: 接收配置路径与事件回调的 GUI 后端工厂.
        config_file (str): 显式 GUI 配置绝对路径.
        stream: 仅写入 UTF-8 JSONL 的文本输出流.
    """

    def __init__(self, backend_factory, config_file, stream):
        self.stream = stream
        self.lock = threading.RLock()
        self.backend = backend_factory(config_file, self.emit, machine=True)
        self.used = set()
        self.pending = None
        self.scope = None
        self.terminal = None
        self.closing = False
        self.stopped = threading.Event()
        self.code = 0
        self.closed = False
        self.started = False
        self.before_ready = []
        self.output_failed = False
        self.stderr_failed = False

    def write(self, event):
        """原子输出完整机器帧,不经过人工显示过滤."""
        if self.output_failed:
            return
        event = dict(event)
        event.setdefault("timestamp", event_timestamp())
        try:
            self.stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.stream.flush()
        except OSError as exc:
            # 坏stdout无法再承载fatal/result/session_end;只停止会话并脱离,不取消GUI任务.
            self.output_failed = True
            self.code = 2
            self.stopped.set()
            try:
                print(f"machine stdout failed: {exc}; stopped without cancelling GUI tasks", file=sys.stderr, flush=True)
            except OSError:
                # stderr也关闭时只剩退出码可用,不能递归输出同一故障.
                self.stderr_failed = True

    def fail(self, message, code=2):
        """停止会话且不取消在途 GUI 任务;未知传输只结算一次."""
        with self.lock:
            if self.stopped.is_set():
                return
            if self.pending is not None:
                self.write(dict(event="result", id=self.pending, success=False, status="unknown",
                                completion="transport", target=None, connection=None, message=message))
                self.pending = None
            self.write(dict(event="fatal", fatal=True, status="unknown", message=message,
                            scope_id=self.scope))
            self.code = code
            self.stopped.set()

    def emit(self, event):
        """消费后端原事件,任务终态与订阅窗口事件分别核对."""
        with self.lock:
            if self.closed or self.stopped.is_set():
                return
            event = dict(event)
            if not self.started:
                self.before_ready.append(event)
                return
            kind = event.get("event")
            if kind == "result":
                if event.get("id") != self.pending:
                    self.fail("GUI 终态请求 ID 不匹配")
                    return
                event.pop("machine_result_routed", None)
                event["completion"] = "transport"
                event.setdefault("target", None)
                event.setdefault("connection", None)
                self.terminal = event
                self.pending = None
                self.write(event)
                if event.get("success") is not True or event.get("status") != "success":
                    self.code = 2 if event.get("fatal") else 1
                    self.stopped.set()
                return
            if kind == "window_closed":
                if (not self.closing or event.get("id") != self.scope
                        or event.get("scope_id") != self.scope):
                    self.fail("GUI 接收窗口关闭确认不匹配")
                    return
                self.write(event)
                self.scope = None
                self.terminal = None
                self.closing = False
                return
            if event.get("fatal"):
                self.write(event)
                if self.pending is not None:
                    self.write(dict(event="result", id=self.pending, success=False, status="unknown",
                                    completion="transport", target=None, connection=None,
                                    message=event.get("message", "GUI 连接异常")))
                    self.pending = None
                self.code = 2
                self.stopped.set()
                return
            if kind == "receive" or (kind == "progress" and event.get("source") == "send"):
                event["id"] = event.get("scope_id")
                if kind == "receive":
                    if event.get("scope_id") != self.scope:
                        self.fail("GUI RX 采集窗口不匹配")
                        return
                elif event.get("task_id") != self.scope or self.scope is None:
                    self.fail("观测到其它写者发送,停止机器会话")
                    return
            self.write(event)

    def start(self):
        """仅在真实 GUI 目标已连接且空闲时发布首个 ready."""
        info = self.backend.start()
        if info.get("machine_protocol") != 1:
            raise ConnectionError("GUI 不支持机器会话协议 1")
        with self.lock:
            self.write(dict(event="ready", protocol=1, auto_open=False,
                            **{key: info[key] for key in ("config_path", "endpoint", "endpoint_user",
                                                         "target", "connection", "idle", "capabilities")}))
            self.started = True
            for event in self.before_ready:
                self.emit(event)
            self.before_ready.clear()

    def consume(self, raw):
        """处理一帧输入;窗口确认前拒绝新请求,不预提交下一条设备命令."""
        with self.lock:
            if self.stopped.is_set():
                return
            if len(raw) > MAX_MESSAGE:
                raise ValueError("机器输入超过 128 KiB")
            frame = json.loads(raw.decode("utf-8"))
            if not isinstance(frame, dict):
                raise ValueError("机器输入必须为 JSON 对象")
            request_id = frame.get("id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
                raise ValueError("id 必须为 32 位小写十六进制 UUID")
            if frame.get("action") == "close-window":
                if (set(frame) != {"action", "id"} or request_id != self.scope or self.pending
                        or self.closing or not self.terminal or self.terminal.get("success") is not True):
                    raise ValueError("只能关闭已有成功终态的当前 RX 窗口")
                self.closing = True
                self.backend.close_window(request_id)
                return
            if set(frame) != {"id", "command"} or not isinstance(frame["command"], str):
                raise ValueError("设备帧只接受 id 和 command")
            if self.scope is not None:
                raise ValueError("当前 RX 窗口尚未确认关闭,禁止下一条命令")
            if request_id in self.used:
                raise ValueError("请求 ID 不得重复,未重发")
            parsed = parse_input(frame["command"])
            if isinstance(parsed, Command) and parsed.name in (
                    set(CONSOLE_LOCAL) | {"Open", "Close", "ComPort", "Com", "BaudRate",
                                          "Status", "Cancel", "StopContinuous"}):
                raise ValueError("机器会话禁止修改目标/连接或使用人工会话命令")
            self.used.add(request_id)
            self.pending = self.scope = request_id
            self.backend.submit(frame["command"], request_id=request_id)

    def end(self, eof=False):
        """EOF 保留未知窗口/任务,关闭 IPC 不取消已受理动作."""
        with self.lock:
            pending, scope = self.pending, self.scope
            status = "unknown" if pending or scope else "success"
            if self.code:
                status = "failed"
            elif status == "unknown":
                self.code = 3
            self.closed = True
        try:
            self.backend.close()
        except Exception as exc:
            self.code = self.code or 2
            status = "unknown"
            with self.lock:
                self.write(dict(event="fatal", fatal=True, message=str(exc)))
        with self.lock:
            self.write(dict(event="session_end", status=status, pending_id=pending, scope_id=scope))
        return self.code


def run_machine(args, config_file):
    """驱动单进程机器事件循环;输入 EOF 不伪造设备完成."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    invalid = (not args.console or not args.via_gui or not args.config or not os.path.isabs(args.config)
               or args.port or args.baudrate or args.gui_action or args.command
               or any(getattr(args, key, None) is not None for key in
                      ("remote_host", "remote_port", "remote_password", "config_patch",
                       "expect", "save_received", "import_template", "export_template")))
    if invalid:
        print(json.dumps(dict(event="fatal", fatal=True,
                              message="--machine 必须单独组合 --console --via-gui --config"), ensure_ascii=False), flush=True)
        return 2
    from PyQt5.QtCore import QCoreApplication
    application = QCoreApplication.instance() or QCoreApplication([])
    session = MachineSession(GuiConsoleBackend, config_file, sys.stdout)
    inputs = queue.Queue(maxsize=8)

    def read_input():
        try:
            pending = bytearray()
            while True:
                # 不持有 Python buffered stdin 锁,故障退出不等用户关闭管道.
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    if pending:
                        inputs.put(bytes(pending))
                    inputs.put(b"")
                    return
                pending.extend(chunk)
                while b"\n" in pending:
                    raw, _, tail = pending.partition(b"\n")
                    pending[:] = tail
                    inputs.put(bytes(raw) + b"\n")
                    if len(raw) + 1 > MAX_MESSAGE:
                        return
                if len(pending) > MAX_MESSAGE:
                    inputs.put(bytes(pending))
                    return
        except Exception as exc:
            inputs.put(exc)

    try:
        session.start()
        threading.Thread(target=read_input, name="machine-stdin", daemon=True).start()
        while not session.stopped.is_set():
            try:
                raw = inputs.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(raw, Exception):
                raise raw
            if not raw:
                break
            session.consume(raw)
    except Exception as exc:
        session.fail(str(exc))
    code = session.end()
    if session.output_failed:
        # Python退出还会flush标准流;按官方SIGPIPE模式将失败FD改接null,不再写坏管道.
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
            if session.stderr_failed:
                os.dup2(sink.fileno(), sys.stderr.fileno())
    return code
