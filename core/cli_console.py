"""PromptSession 常驻控制台与自动发现;Qt socket 仅由客户端 IPC 线程访问."""
import base64
import copy
import json
import os
import queue
import sys
import threading
import time
import uuid
from collections import deque

from core.command_parser import Command, parse_input, COMMAND_NAMES, CONSOLE_LOCAL, LOG_COMMANDS
from core.console_execution import ConsoleExecutor
from core.console_serial import LocalConsoleTransport
from core.receive_text import ReceiveDecoder, terminal_text, ReceivedFile, ReceiveSaveError
from core.command_templates import run_local_template, template_path
from core.output_rules import rules_from_state, ANSI_RESET, event_timestamp
from core.command_types import OutputSource


class GuiNotFound(ConnectionError):
    """未发现目标端点及其定位锁;自动模式可选择独立后端."""


class GuiConsoleBackend:
    """一个 IPC 线程拥有订阅和所有任务连接,长任务不会堵住终端输入."""

    mode = "GUI"

    def __init__(self, config_file, emit, *, machine=False):
        self.machine = machine
        self.subscription_id = uuid.uuid4().hex
        self.config_file = config_file
        self.emit = emit
        self.commands = queue.Queue()
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.error = None
        self.config = None
        self.waiters = {}
        self.thread = threading.Thread(target=self._run, name="console-gui-ipc")

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):
            self.close()
            raise ConnectionError("GUI 桥接握手超时；未尝试独立打开串口")
        if self.error:
            self.thread.join(2)
            raise self.error
        return self.config

    def submit(self, text, *, request_id=None):
        parsed = parse_input(text)
        if isinstance(parsed, Command) and parsed.name == "FirmwareDownload" and parsed.parameter.strip():
            from core.firmware_download import normalize_firmware_path
            text = "FirmwareDownload:" + normalize_firmware_path(parsed.parameter)
        request_id = request_id or uuid.uuid4().hex
        request = dict(id=request_id, action="console-send", command=text, detach_on_disconnect=True, read_timeout=0)
        if self.machine:
            request["machine_session"] = self.subscription_id
        self.commands.put(request)
        return request_id

    def close_window(self, request_id):
        """管理帧仅关闭采集窗口,不发送串口命令."""
        self.commands.put(dict(id=uuid.uuid4().hex, action="machine-window-close",
                               machine_session=self.subscription_id, scope_id=request_id))

    def submit_template(self, command):
        """只提交绝对路径,真正文件 IO,进度和模板事务由 GUI 所有者执行."""
        request_id = uuid.uuid4().hex
        self.commands.put(dict(id=request_id, action="template-import" if command.name == "ImportTemplate" else "template-export",
                               path=template_path(command.parameter), detach_on_disconnect=True))
        return request_id

    def submit_log(self, command):
        """日志管理仅经同一GUI事务/归档拥有者,结果从请求连接返回."""
        request = dict(id=uuid.uuid4().hex, action="log-" + command.name[3:].casefold())
        if command.name in ("LogCheck", "LogApply"):
            request["log_patch"] = json.loads(command.parameter)
        elif command.name == "LogRecover":
            if not command.parameter.strip():
                raise ValueError("LogRecover需要新文件路径")
            request["path"] = os.path.realpath(command.parameter)
        else:
            ConsoleExecutor._no_parameter(command)
        self.commands.put(request)
        return request["id"]

    def report_file_event(self, event):
        """有限 RX 保存状态入队,不发送设备命令,也不阻塞原始 RX 的消费线程."""
        self.commands.put(dict(id=uuid.uuid4().hex, action="receive-save-state",
            receive_save={key: event.get(key) for key in ("path", "outcome", "timestamp", "file_event_id", "failure")}))

    def open_initial(self):
        """启动时等待同一 Open 命令,串口失败保留连接供用户修正配置."""
        request_id = uuid.uuid4().hex
        waiter = dict(done=threading.Event())
        self.waiters[request_id] = waiter
        self.commands.put(dict(id=request_id, action="console-send", command="Open", detach_on_disconnect=True, read_timeout=0))
        deadline = time.monotonic() + 12
        while not waiter["done"].wait(0.05):
            if self.stop.is_set() or time.monotonic() >= deadline:
                self.waiters.pop(request_id, None)
                self.emit(dict(source="error", message="Open 结果尚未确认，请用 Status 查看后再操作"))
                return

    def _run(self):
        from PyQt5.QtCore import QDir
        from PyQt5.QtNetwork import QLocalSocket
        from core.cli_protocol import bridge_target, bridge_connection_failure, MAX_MESSAGE
        target = bridge_target(self.config_file)
        name = target["endpoint"]
        peers = {}
        requests = {}
        subscription = None

        def connect(request):
            sock = QLocalSocket()
            sock.connectToServer(name)
            if not sock.waitForConnected(1200):
                error = sock.error()
                failure = bridge_connection_failure(target, sock.errorString(), error)
                sock.abort()
                if error == QLocalSocket.ServerNotFoundError and not os.path.exists(os.path.join(QDir.tempPath(), name + ".lock")):
                    raise GuiNotFound(failure["message"])
                raise ConnectionError(failure["message"] + "；未尝试独立打开串口")
            raw = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(raw) > MAX_MESSAGE:
                sock.abort()
                raise ValueError("CLI 请求超过 128 KiB")
            sock.write(raw)
            sock.flush()
            peers[sock] = bytearray()
            requests[sock] = request
            return sock

        try:
            subscription = connect(dict(id=self.subscription_id, action="subscribe",
                                        **({"machine_protocol": 1} if self.machine else {})))
            deadline = time.monotonic() + 3
            while not self.stop.is_set() or not self.commands.empty() or len(peers) > 1:
                if self.stop.is_set() and time.monotonic() > self.close_deadline:
                    break
                try:
                    request = self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        connect(request)
                    except (ValueError, OSError) as exc:
                        self.emit(dict(event="result", id=request["id"], success=False, status="failed", message=str(exc), fatal=isinstance(exc, OSError)))
                        if isinstance(exc, OSError):
                            return
                for sock in list(peers):
                    if not sock.bytesAvailable():
                        sock.waitForReadyRead(10)
                    buffer = peers[sock]
                    buffer.extend(bytes(sock.readAll()))
                    while b"\n" in buffer:
                        raw, _, remaining = buffer.partition(b"\n")
                        buffer[:] = remaining
                        if len(raw) > MAX_MESSAGE:
                            raise ValueError("GUI 回执超过 128 KiB")
                        event = json.loads(raw.decode("utf-8"))
                        if not isinstance(event, dict):
                            raise ValueError("GUI 回执必须为 JSON 对象")
                        if sock is subscription:
                            if event.get("event") == "accepted":
                                if self.machine:
                                    if event.get("machine_protocol") != 1:
                                        raise ConnectionError("GUI 不支持机器会话协议 1,请更新 GUI")
                                    self.config = event
                                else:
                                    if event.get("console_protocol") != 1 or not isinstance(event.get("state"), dict):
                                        raise ConnectionError("GUI 不支持当前控制台协议，请更新 GUI")
                                    self.config = dict(state=event["state"], tools=event.get("tools", {}))
                                self.ready.set()
                            else:
                                if event.get("event") == "result" and (
                                        not self.machine or event.get("id") == self.subscription_id):
                                    raise ConnectionError(event.get("message", "GUI 订阅已结束"))
                                self.emit(event)
                        elif event.get("event") == "result":
                            # 完成事件经订阅通道显示;请求 result 只确认终态,避免双显.
                            internal = requests[sock].get("action") == "machine-window-close"
                            if internal:
                                if not event.get("success"):
                                    self.emit(dict(event="fatal", fatal=True, message=event.get("message", "窗口关闭失败")))
                            elif "file_event_id" not in event and not (
                                    self.machine and event.get("machine_result_routed")):
                                self.emit(event)
                            waiter = self.waiters.pop(event.get("id"), None) if event is not None else None
                            if waiter:
                                waiter["done"].set()
                            sock.abort()
                            peers.pop(sock, None)
                            requests.pop(sock, None)
                        elif self.machine and event.get("event") == "accepted":
                            self.emit(event)
                    if len(buffer) > MAX_MESSAGE:
                        raise ValueError("GUI 不完整回执超过 128 KiB")
                    if sock is subscription and sock.state() == QLocalSocket.UnconnectedState:
                        raise ConnectionError("GUI 桥接已断开，控制台已退出")
                    if sock is not subscription and sock in peers and sock.state() == QLocalSocket.UnconnectedState:
                        lost = requests.pop(sock, {})
                        peers.pop(sock, None)
                        self.emit(dict(event="result", id=lost.get("id"), success=False, status="unknown", fatal=True,
                                       message="结果未知：GUI 任务连接中断；未重试"))
                        return
                if not self.ready.is_set() and time.monotonic() >= deadline:
                    raise ConnectionError("GUI 订阅握手超时；未尝试独立打开串口")
        except Exception as exc:
            if not self.ready.is_set():
                self.error = exc
            elif not self.stop.is_set():
                self.emit(dict(event="progress", source="error", fatal=True, message=str(exc)))
        finally:
            for sock in peers:
                # 控制台请求显式 detach;断开不会取消已受理的 GUI 任务.
                sock.abort()
            self.ready.set()
            for waiter in self.waiters.values():
                waiter["done"].set()

    def close(self):
        self.close_deadline = time.monotonic() + 1.5
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(4)
            if self.thread.is_alive():
                raise RuntimeError("GUI IPC 线程未退出")


class LocalConsoleBackend:
    """后台串行执行共享命令,主线程保留 PromptSession 和取消能力."""

    mode = "本机"

    def __init__(self, manager, emit):
        self.emit = emit
        self.transport = LocalConsoleTransport(manager, emit)
        self.executor = ConsoleExecutor(self.transport, emit)
        self.workers = []

    def start(self):
        return self.transport.snapshot()

    def open_initial(self):
        try:
            self.transport.open()
        except Exception as exc:
            self.emit(dict(source="error", message=str(exc) + "；当前未连接，可用 ComPort/BaudRate/Open 修正重试"))

    def submit(self, text):
        request_id = uuid.uuid4().hex
        def execute():
            self.emit(dict(event="result", id=request_id, **self.executor.execute(text)))
        if self.executor.control(text):
            execute()
        else:
            self.workers = [worker for worker in self.workers if worker.is_alive()]
            if self.workers:
                self.emit(dict(event="result", id=request_id, success=False, status="failed", message="已有任务运行，请先 Cancel"))
                return request_id
            self.executor.started.clear()
            worker = threading.Thread(target=execute, name="console-command")
            self.workers.append(worker)
            worker.start()
            self.executor.started.wait(0.5)
        return request_id

    def submit_template(self, command):
        """空闲时同步调用共享纯模板操作,不打开或关闭串口."""
        if self.executor.busy or any(worker.is_alive() for worker in self.workers):
            raise ValueError("已有任务运行，请先等待结束或 Cancel")
        event = run_local_template(self.transport.config_manager,
            "template-import" if command.name == "ImportTemplate" else "template-export", command.parameter)
        self.emit(event)

    def close(self):
        self.executor.cancel()
        for worker in self.workers:
            worker.join(5)
            if worker.is_alive():
                raise RuntimeError("设备任务尚未释放串口，控制台等待取消完成")
        self.transport.close()


class ConsoleOutput:
    """复用配置的过滤/时间戳/颜色;日志保存来源与屏幕显示过滤相互独立."""

    def __init__(self, config_file=None):
        self.config_file = config_file
        self.lock = threading.RLock()
        self.state = {}
        self.rules = rules_from_state({})
        self.hex = False
        self.log_service = None
        self.file_reporter = None
        self.file_events = deque(maxlen=256)
        self.received_file = ReceivedFile(emit=self.on_file_event)
        self.decoders = {source: ReceiveDecoder() for source in ("send", "receive")}
        self.fatal = threading.Event()
        self.session = None

    def on_file_event(self, event):
        """先本地呈现同一事件,再反馈 GUI;订阅回显依据事件 ID 去重."""
        self.emit(dict(event, fatal=event.get("failure") == "write"))
        if self.file_reporter is not None:
            self.file_reporter(event)

    def configure(self, state, *, automatic=None):
        with self.lock:
            self.state = copy.deepcopy(state)
            self.state.setdefault("receive_settings", {})
            self.rules = rules_from_state(self.state)
            from core.cli_log import CliLog
            if self.log_service is None:
                self.log_service = CliLog(self.state, automatic=True if automatic is None else automatic, config_file=self.config_file)
            else:
                if automatic is not None:
                    self.log_service.automatic = automatic
                self.log_service.configure(self.state)

    def emit(self, event):
        with self.lock:
            if event.get("event") == "log-settings":
                self.state["log_settings"] = event["log_settings"]
                self.state.setdefault("send_settings", {}).update(event["send_settings"])
                self.configure(self.state, automatic=False)
                return
            event_id = event.get("file_event_id")
            if event_id:
                if event_id in self.file_events:
                    return
                self.file_events.append(event_id)
            source = event.get("source", "system")
            if event.get("event") == "result":
                source = "system" if event.get("success") else "error"
            source_type = OutputSource(source)
            text = event.get("message", "")
            data = None
            if "data" in event:
                data = base64.b64decode(event["data"], validate=True)
                if event.get("event") == "receive" and source == "receive":
                    try:
                        self.received_file.feed(data)
                    except ReceiveSaveError:
                        return
                decoded = self.decoders[source].feed(data)
                text = data.hex(" ").upper() + "\n" if self.hex else terminal_text(decoded)
            if event.get("event") == "result" and "serial" in event:
                text += " " + json.dumps(event["serial"], ensure_ascii=False)
            text = terminal_text(text)
            occurred_at = event.get("timestamp") or event_timestamp()
            timestamp = self.rules.timestamp_for(source_type, occurred_at)
            self.rules.before_append(source_type)
            rendered = timestamp + text
            ending = "" if source == "receive" and data is not None and not self.hex else "\n"
            receive_settings = self.state.get("receive_settings", {})
            level = str(event.get("system_level", "normal")).casefold()
            if self.log_service:
                try:
                    self.log_service.capture(text, source, level, occurred_at, ending)
                except OSError as exc:
                    print(str(exc), file=sys.stderr, flush=True)
            for field in ("log_settings", "log_status"):
                if field in event:
                    rendered += " " + json.dumps(event[field], ensure_ascii=False)
            # 连接故障和任务失败必须可见,即使配置隐藏普通来源.
            level_enabled = source != "system" or receive_settings.get("system_levels", {}).get(level, True)
            if source == "error" or event.get("force") or "log_settings" in event or "log_status" in event or (level_enabled and self.rules.source_enabled(source_type)
                    ):
                color = self.rules.ansi_color_for(source_type)
                print(color + rendered + (ANSI_RESET if color else ""), end=ending, flush=True)
            if event.get("fatal"):
                self.fatal.set()
                if self.session is not None and self.session.app.is_running:
                    loop = self.session.app.loop
                    loop.call_soon_threadsafe(lambda: self.session.app.exit(exception=EOFError)
                                             if self.session.app.is_running else None)

    def local_command(self, command):
        """处理会话命令,返回 True 表示退出,均不修改配置文件."""
        name, parameter = command.name, command.parameter.strip()
        if name == "Quit":
            if parameter.casefold() not in ("", "0", "cli"):
                raise ValueError("Quit 仅支持退出控制台，可用 Quit 或 Quit:cli")
            return True
        if name in {"ShowHex", "Timestamp"}:
            if parameter.casefold() not in ("", "0", "on", "off"):
                raise ValueError(f"{name} 参数必须为 on 或 off")
            enabled = parameter.casefold() == "on"
            if name == "ShowHex":
                self.hex = enabled
            else:
                self.state["receive_settings"]["show_timestamp"] = enabled
            self.emit(dict(message=f"{name}: {'on' if enabled else 'off'}"))
        elif name in {"Help", "Clear"}:
            ConsoleExecutor._no_parameter(command)
            if name == "Help":
                self.emit(dict(force=True, message="所有连接方式使用相同命令：\n" + " / ".join(COMMAND_NAMES) +
                    "\nMode/SendMode:模块名；StopContinuous:0跳本轮、:1停止循环；Cancel取消当前任务。" +
                    "\nShowHex/Timestamp:on|off；Log:文件|off；LogGet/LogStatus；LogCheck/LogApply:JSON日志补丁；LogRecover:新文件；SaveReceived:文件|off 追加原始RX；ImportTemplate/ExportTemplate:文件；Quit退出本控制台。"))
            else:
                from prompt_toolkit.shortcuts import clear
                clear()
        elif name == "SaveReceived":
            if parameter.casefold() in ("", "0", "off"):
                self.received_file.close()
            else:
                self.received_file.start(command.parameter)
        elif name == "Log":
            with self.lock:
                if self.log_service is None:
                    self.configure(self.state)
                if parameter.casefold() in ("", "0", "off"):
                    self.log_service.close()
                else:
                    self.log_service.start_explicit(command.parameter)
            self.emit(dict(message="Log: " + parameter))
        return False

    def local_log(self, command, manager=None):
        """独立控制台查询/校验/保存当前日志配置,不操作设备."""
        from core.log_settings import settings_from_state, prepare_settings, apply_local
        with self.lock:
            fields = {}
            if command.name in ("LogGet", "LogStatus"):
                ConsoleExecutor._no_parameter(command)
                fields = ({"log_settings": settings_from_state(self.state)} if command.name == "LogGet"
                          else {"log_status": self.log_service.status()})
            elif command.name == "LogRecover":
                if not command.parameter.strip():
                    raise ValueError("LogRecover需要新文件路径")
                fields["log_status"] = self.log_service.recover(command.parameter)
            else:
                patch = json.loads(command.parameter)
                candidate = prepare_settings(self.state, patch)
                self.log_service.validate(candidate)
                if command.name == "LogApply":
                    apply_local(manager, patch, self.log_service.validate)
                    self.state["log_settings"] = candidate
                    self.configure(self.state)
                fields["log_settings"] = candidate
            self.emit(dict(event="result", force=True, success=True, message="日志操作完成", **fields))

    def close(self):
        with self.lock:
            self.received_file.close()
            if self.log_service:
                self.log_service.close()


def run_console(args, config_file, tool_version, tool_version_date):
    """自动选择同配置 GUI 或独立串口;已连接 GUI 失联时退出且不接管."""
    from PyQt5.QtCore import QCoreApplication
    from prompt_toolkit import PromptSession
    from prompt_toolkit.patch_stdout import patch_stdout
    from managers.config_manager import ConfigManager
    from core.cli_runner import get_state, set_com_port, update_basic_setting, save_state
    if args.gui_action or any(getattr(args, name, None) is not None for name in ("remote_host", "remote_port", "remote_password", "config_patch")):
        print("--console 通过输入命令操作，不与 --gui-action/远程参数/配置补丁组合", file=sys.stderr)
        return 2
    application = QCoreApplication.instance() or QCoreApplication([])
    output = ConsoleOutput(config_file)
    backend = GuiConsoleBackend(config_file, output.emit)
    session = PromptSession()
    output.session = session
    code = 0
    try:
        with patch_stdout(raw=True):
            if getattr(args, "save_received", None) is not None:
                output.received_file.start(args.save_received)
            try:
                config = backend.start()
            except GuiNotFound as exc:
                if args.via_gui:
                    raise ConnectionError(str(exc)) from exc
                manager = ConfigManager(tool_version, tool_version_date, config_file)
                if args.port:
                    set_com_port(manager, args.port)
                if args.baudrate:
                    update_basic_setting(manager, "baudrate", args.baudrate)
                if args.port or args.baudrate:
                    save_state(manager, get_state(manager))
                backend = LocalConsoleBackend(manager, output.emit)
                config = backend.start()
            else:
                if args.port or args.baudrate:
                    raise ValueError("GUI 已运行，请在控制台使用 ComPort/BaudRate，不与 --port/--baudrate 组合")
            output.configure(config["state"], automatic=not isinstance(backend, GuiConsoleBackend))
            if isinstance(backend, GuiConsoleBackend):
                output.file_reporter = backend.report_file_event
                if output.received_file.last_event is not None:
                    backend.report_file_event(output.received_file.last_event)
            backend.open_initial()
            output.emit(dict(message="输入 Help 查看命令；Quit、Ctrl+C、Ctrl+D 退出控制台。"))
            while not output.fatal.is_set():
                # 断连可能发生在 while 检查之后,prompt 启动之前;启动后再次检查同一事件.
                text = session.prompt(f"[{backend.mode}] > ", pre_run=lambda: session.app.exit(exception=EOFError)
                                      if output.fatal.is_set() else None)
                parsed = parse_input(text)
                if isinstance(parsed, Command) and parsed.name in CONSOLE_LOCAL:
                    try:
                        if parsed.name in LOG_COMMANDS:
                            local_archive = output.log_service.explicit and parsed.name in ("LogStatus", "LogRecover")
                            if isinstance(backend, GuiConsoleBackend) and not local_archive:
                                backend.submit_log(parsed)
                            else:
                                manager = None if isinstance(backend, GuiConsoleBackend) else backend.transport.config_manager
                                output.local_log(parsed, manager)
                        elif parsed.name in {"ImportTemplate", "ExportTemplate"}:
                            backend.submit_template(parsed)
                        elif output.local_command(parsed):
                            break
                    except (ValueError, OSError) as exc:
                        if parsed.name in LOG_COMMANDS or parsed.name == "Log":
                            code = 1
                        if not getattr(exc, "file_event", None):
                            output.emit(dict(source="error", message=str(exc)))
                else:
                    backend.submit(text)
            if output.fatal.is_set():
                code = 2
    except (KeyboardInterrupt, EOFError):
        pass
    except Exception as exc:
        output.emit(dict(source="error", message=str(exc)))
        code = 2
    finally:
        try:
            output.received_file.close()
            backend.close()
        except Exception as exc:
            output.emit(dict(source="error", message=str(exc)))
            code = 2
        try:
            output.close()
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            code = 2
        if output.fatal.is_set():
            code = 2
    return code
