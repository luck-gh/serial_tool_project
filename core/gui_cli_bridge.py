"""CLI 与已运行 GUI 的本机桥接:JSON 行消息,不开放 TCP 监听或执行任意代码."""
import base64
import json
import os
import re
import threading
import time
import uuid

from PyQt5.QtCore import QObject, QDir, QLockFile, QTimer, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from core.command_types import SpecialCommandType, OutputSource
from core.command_parser import Command, command_parts, parse_input, unescape_text

from core.cli_protocol import endpoint_name, MAX_MESSAGE, MAX_CONFIG_RESULT_MESSAGE, MAX_PENDING_OUTPUT, DATA_CHUNK_SIZE, encode_json_line


class GuiConsoleTransport(QObject):
    """将共享控制台执行器的阻塞调用交回 GUI 线程,不从工作线程操作窗口.

    Args:
        bridge (GuiCliBridge): 持有窗口和会话的本机桥接.

    每次调用使用自己的完成事件;远程写入等待设备端回执,固件等待既有完成
    回调.窗口关闭会唤醒等待者,不阻塞 Qt 事件循环或强杀工作线程.
    """

    requested = pyqtSignal(object)

    def __init__(self, bridge):
        super().__init__(bridge)
        self.bridge = bridge
        self.closed = threading.Event()
        self.requested.connect(self.invoke)

    def call(self, operation, *arguments):
        """在执行线程等待 GUI 原语完成,关闭后拒绝继续提交设备操作."""
        if self.closed.is_set():
            raise ConnectionError("GUI 已关闭")
        call = dict(operation=operation, arguments=arguments, done=threading.Event())
        self.requested.emit(call)
        while not call["done"].wait(0.05):
            if self.closed.is_set():
                raise ConnectionError("GUI 已关闭，操作结果未知")
        if call.get("error") is not None:
            raise call["error"]
        return call.get("result")

    def snapshot(self):
        """读取执行时 GUI 的内存配置;不从硬盘覆盖窗口状态."""
        return self.call("snapshot")

    def write(self, data, display):
        """提交已由共享执行器生成的字节,不再次添加结尾或应用替换规则."""
        return self.call("write", data, display)

    def set_setting(self, name, parameter):
        """复用窗口配置入口并等待远程设置的实际回执."""
        return self.call("set_setting", name, parameter)

    def open(self):
        """打开当前 GUI 后端的串口."""
        return self.call("open")

    def close(self):
        """关闭当前 GUI 后端的串口,不关闭窗口."""
        return self.call("close")

    def status(self):
        """返回与独立控制台共用的连接/忙状态信息."""
        return self.call("status")

    def download(self, parameter, cancel_event):
        """启动 GUI 原有固件流程,取消只请求停止并等待资源恢复."""
        return self.call("download", parameter, cancel_event)

    def current_status(self):
        """仅在 GUI 线程读取当前设备状态."""
        window = self.bridge.window
        return dict(connected=window.can_send_serial_data(), serial=window.get_current_serial_config(),
                    busy=bool(self.bridge.active or window.serial_operation_busy() or window.is_continuous_sending),
                    remote_mode=window.remote_mode)

    def invoke(self, call):
        """GUI 线程执行单次原语;异步设备操作通过完成事件交回调用者."""
        window = self.bridge.window

        def finish(result=None, error=None):
            if not call["done"].is_set():
                call.update(result=result, error=error)
                call["done"].set()

        def wait_remote():
            if self.closed.is_set():
                finish(error=ConnectionError("GUI 已关闭，操作结果未知"))
            elif window._remote_device_pending:
                QTimer.singleShot(20, wait_remote)
            elif window._remote_sequence_failed:
                from core.console_execution import OperationUnknown
                finish(error=OperationUnknown("设备端操作失败或结果未知；未自动重试"))
            else:
                finish()

        try:
            if self.closed.is_set() or window._closing_logs:
                raise ConnectionError("GUI 已关闭")
            operation = call["operation"]
            arguments = call["arguments"]
            self.bridge.guard_machine("target_setting" if operation == "set_setting"
                                      and arguments[0] in ("ComPort", "BaudRate") else operation)
            if operation == "snapshot":
                from core import gui_config
                finish(gui_config.snapshot(window))
                return
            if operation == "status":
                finish(self.current_status())
                return
            if operation == "download":
                parameter, cancel_event = arguments
                machine_task = self.bridge.active
                if machine_task and machine_task.get("machine_session"):
                    machine_task["machine_download"] = True
                    self.bridge.check_machine_sessions()
                def completed(success):
                    message = getattr(window, "_last_firmware_result", (False, "下载未启动"))[1]
                    from core.console_execution import OperationUnknown
                    if machine_task and machine_task.get("machine_session"):
                        machine_task["machine_download"] = False
                        current = self.bridge.machine_snapshot()
                        if success and (current["target"] != machine_task.get("machine_target")
                                        or not current["connection"]["serial_connected"]
                                        or current["connection"]["network_connected"] is False):
                            success, message = False, "结果未知:下载结束但原调试连接未恢复"
                        self.bridge.check_machine_sessions()
                    error_type = OperationUnknown if "未知" in message else RuntimeError
                    finish(error=None if success else error_type(message))
                window.start_firmware_download_command(parameter, completed)
                def check_cancel():
                    if call["done"].is_set() or self.closed.is_set():
                        return
                    if cancel_event.is_set():
                        window.cancel_firmware_download()
                    QTimer.singleShot(50, check_cancel)
                check_cancel()
                return
            if operation == "write":
                data, display = arguments
                if not window.can_send_serial_data():
                    raise RuntimeError("请先 Open 打开串口")
                if window._write_serial_data(data) != len(data):
                    from core.console_execution import OperationUnknown
                    raise OperationUnknown("串口写入失败；结果未知，未重发")
                window.send_count += len(data)
                window.update_statistics()
                window.output_manager.reset_receive_timestamp()
                window.output_manager.append_text(display, OutputSource.SEND)
            elif operation == "set_setting":
                name, parameter = arguments
                if name == "BaudRate":
                    ok = window.update_baudrate(int(parameter))
                elif name == "ComPort":
                    ok = window.update_com_port(parameter)
                elif name == "SetEndlog":
                    ok = window.set_ending(parameter)
                    if ok:
                        ok = window.save_state()
                else:
                    raise ValueError(f"不支持的设置: {name}")
                if not ok:
                    raise RuntimeError(f"{name} 执行失败")
            elif operation in ("open", "close"):
                if window.remote_mode == "client":
                    connected = window.remote_serial_connected
                    if connected == (operation == "open"):
                        finish()
                        return
                    config = window.get_current_serial_config() if operation == "open" else {}
                    if not window.request_remote_device(operation, config):
                        raise RuntimeError("远程连接未就绪")
                else:
                    if operation == "open" and window.is_connected:
                        finish()
                        return
                    ok = window.open_serial() if operation == "open" else window.close_serial()
                    if not ok:
                        raise RuntimeError("串口操作失败")
            else:
                raise ValueError("不支持的控制台设备操作")
            wait_remote()
        except Exception as exc:
            finish(error=exc)


class GuiCliBridge(QObject):
    """在 GUI 线程接受同用户 CLI 请求,并交给现有窗口执行入口.

    每个连接只提交一个请求,订阅使用独立长连接;单次仅执行一个设备操作.
    status/cancel 可并行查询或取消.既有 GUI/远程串口占用检查仍为最终门禁.
    QLockFile 防止多个窗口抢占同一配置的桥接端点;仅首次获得锁的窗口监听.
    请求 ID 在进程内去重,异常断线不重放;控制台脱离后 GUI 任务继续,
    旧一次性请求断线则保留取消它持有的下载/命令链的契约.
    人工在状态栏看到来源及结果,在接收区看到完整命令和已有下载/串口日志.
    """

    console_event = pyqtSignal(object)
    console_finished = pyqtSignal(object)

    def __init__(self, window, config_file):
        """初始化本机桥接及生命周期对象.

        Args:
            window (SerialTool): 正在运行的主窗口;命令在它的 GUI 线程执行,
                使用当前本地/远程模式及配置,不创建另一个串口后端.
            config_file (str): 主窗口配置文件路径;规范化后生成端点及互斥锁名,
                CLI 必须以相同路径定位此窗口,不读取或修改配置内容.
        """
        super().__init__(window)
        self.window = window
        self.config_file = config_file
        self.name = endpoint_name(config_file)
        self.lock = QLockFile(os.path.join(QDir.tempPath(), self.name + ".lock"))
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self.accept)
        self.connections = {}
        self.subscribers = {}
        self.machine_subscribers = {}
        self.rx_sequence = 0
        self.active = None
        self.machine_timer = QTimer(self)
        self.machine_timer.timeout.connect(self.check_machine_sessions)
        self.machine_timer.start(50)
        self.results = {}
        self.owns_lock = False
        self.console_transport = GuiConsoleTransport(self)
        self.console_event.connect(self.on_console_event)
        self.console_finished.connect(self.on_console_finished)

    def machine_snapshot(self):
        """只读 GUI 当前目标和连接;不读取磁盘配置或发 status."""
        from core.cli_protocol import bridge_target
        serial = self.window.get_current_serial_config()
        remote = self.remote_status()
        task = self.active or {}
        return dict(bridge_target(self.config_file),
                    target=dict(serial={key: str(serial[key]) for key in
                                        ("port", "baudrate", "databits", "parity", "stopbits")},
                                remote_mode=self.window.remote_mode, remote_host=remote["host"],
                                remote_port=remote["port"],
                                remote_session=getattr(self.window.remote_thread, "session", "")),
                    connection=dict(serial_connected=bool(serial["connected"]),
                                    network_connected=bool(remote["connected"])
                                    if self.window.remote_mode == "client" else None,
                                    phase="download_exclusive" if task.get("machine_download") else "connected"),
                    idle=not bool(self.active or self.window.serial_operation_busy()
                                  or self.window.is_continuous_sending or self.window._remote_device_pending),
                    capabilities=remote["capabilities"])

    def machine_meta(self, session_id):
        """查找仍连接的机器订阅;不允许冒用其它配置的会话."""
        for sock, meta in self.machine_subscribers.items():
            if meta["id"] == session_id:
                return sock, meta
        raise ValueError("机器订阅不存在或已结束")

    def check_machine_sessions(self):
        """GUI 事件循环主动报告漂移;不取消下载或重连."""
        if not self.machine_subscribers:
            return
        snapshot = self.machine_snapshot()
        for sock, meta in list(self.machine_subscribers.items()):
            if meta["failed"]:
                continue
            current = dict(snapshot, connection=dict(snapshot["connection"]))
            if (current["connection"]["phase"] == "download_exclusive"
                    and (self.active or {}).get("machine_session") != meta["id"]):
                current["connection"]["phase"] = "connected"
            kind, message = None, None
            if current["target"] != meta["target"]:
                kind, message = "target_changed", "GUI 目标已改变,停止机器会话"
            elif current["connection"]["network_connected"] is False:
                kind, message = "connection", "远程链路已断开,结果未知"
            elif (not current["connection"]["serial_connected"]
                  and current["connection"]["phase"] != "download_exclusive"):
                kind, message = "connection", "调试串口已断开,结果未知"
            changed = current["connection"] != meta["connection"]
            meta["connection"] = current["connection"]
            if kind:
                meta["failed"] = True
                if self.active and self.active.get("machine_session") == meta["id"]:
                    self.active["machine_failed"] = True
                self.send(sock, dict(event=kind, id=meta["scope"], scope_id=meta["scope"], fatal=True,
                                     status="unknown", message=message, target=current["target"],
                                     connection=current["connection"]))
            elif changed:
                self.send(sock, dict(event="connection", id=meta["scope"], scope_id=meta["scope"],
                                     target=current["target"], connection=current["connection"]))

    def guard_machine(self, operation=None):
        """设备原语前同步核对已绑定目标,模块展开也不能切换连接."""
        task = self.active or {}
        session_id = task.get("machine_session")
        if not session_id:
            return
        current = self.machine_snapshot()
        connection = current["connection"]
        if (task.get("machine_failed") or current["target"] != task.get("machine_target")
                or connection["network_connected"] is False
                or (not connection["serial_connected"] and not task.get("machine_download"))):
            task["machine_failed"] = True
            from core.console_execution import OperationUnknown
            raise OperationUnknown("机器会话目标或连接已经失效,后续原语未执行")
        if operation in ("open", "close", "target_setting"):
            raise ValueError("机器会话禁止修改目标或连接,包括模块中的原语")
        try:
            _, meta = self.machine_meta(session_id)
        except ValueError:
            # 健康 EOF 仅脱离订阅;原绑定仍在上面逐原语核对.
            return
        self.check_machine_sessions()
        if meta["failed"]:
            from core.console_execution import OperationUnknown
            raise OperationUnknown("机器会话目标或连接已失效,未继续提交设备操作")

    def close_machine_window(self, sock, request):
        """同订阅流先发已有 RX 再确认关闭,管理回执不冒充设备终态."""
        subscription, meta = self.machine_meta(request.get("machine_session"))
        self.check_machine_sessions()
        scope_id = request.get("scope_id")
        result = self.results.get(scope_id, {})
        current = self.machine_snapshot()
        if (meta["failed"] or not scope_id or meta["scope"] != scope_id or self.active
                or result.get("success") is not True or result.get("status") != "success"
                or not current["idle"] or not current["connection"]["serial_connected"]):
            raise ValueError("窗口必须已有成功终态且原目标连接已恢复并空闲")
        meta["scope"] = None
        self.send(subscription, dict(event="window_closed", id=scope_id, scope_id=scope_id,
                                     rx_sequence=meta["rx_sequence"]))
        self.results.pop(scope_id, None)
        self.send(sock, dict(event="result", id=request["id"], success=True, status="success"))
        sock.disconnectFromServer()

    def start(self):
        """获得文件锁后监听本机端点,返回是否启动成功.

        仅在已持锁时清理遗留端点.锁文件用于单实例互斥,命令不经文件中转;
        监听失败释放锁并在状态栏说明,不影响普通 GUI 使用.
        """
        if not self.lock.tryLock(0):
            self.window.statusBar().showMessage("CLI 桥接未启用：同一配置已有其他窗口")
            return False
        self.owns_lock = True
        # 锁已证明无活动桥接所有者;清理 Unix 上异常退出遗留的 socket 文件.
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.window.statusBar().showMessage("CLI 桥接启动失败：" + self.server.errorString())
            self.lock.unlock()
            self.owns_lock = False
            return False
        self.window.statusBar().showMessage("CLI 桥接就绪：使用 --via-gui，配置 " + os.path.abspath(self.window.config_manager.config_file))
        return True

    def accept(self):
        """接收本机连接并绑定 Qt 信号;五秒内未提交完整请求的连接将被断开."""
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self.connections[sock] = bytearray()
            sock.readyRead.connect(lambda peer=sock: self.read(peer))
            sock.disconnected.connect(lambda peer=sock: self.disconnected(peer))
            # 半帧请求不可无限持有连接;已执行任务不受该接收超时影响.
            timer = QTimer(sock)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda peer=sock: peer.disconnectFromServer()
                                  if self.connections.get(peer) is not None else None)
            timer.start(5000)

    def read(self, sock):
        """累计连接上的 JSON 行并分发唯一请求,拒绝超限及同批多个请求.

        Args:
            sock (QLocalSocket): 已由 accept 登记的客户端连接.

        超过 128 KiB 时直接断开;解析/分发失败时返回失败结果并结束连接.
        已提交请求的连接不再接受后续命令.
        """
        buffer = self.connections.get(sock)
        if buffer is None:
            sock.readAll()
            return
        buffer.extend(bytes(sock.readAll()))
        if len(buffer) > MAX_MESSAGE:
            sock.abort()
            return
        if b"\n" not in buffer:
            return
        raw, tail = bytes(buffer).split(b"\n", 1)
        self.connections[sock] = None
        request = None
        try:
            if tail.strip():
                raise ValueError("每个 CLI 连接仅允许一个请求")
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("CLI 请求必须为 JSON 对象")
            self.dispatch(sock, request)
        except Exception as exc:
            fields = {}
            if isinstance(request, dict) and request.get('action') == 'config-switch':
                from core.gui_config import config_paths
                fields = dict(config_paths(self.window), switched=False)
            self.send(sock, dict(event="result", id=request.get("id") if isinstance(request, dict) else None,
                                 success=False, status="failed", message=str(exc), **fields))
            sock.disconnectFromServer()

    def send(self, sock, message, max_bytes=MAX_MESSAGE, config_get_result_id=None):
        """向仍连接的客户端写入 UTF-8 JSON 行;flush 只推进传输,不代表操作完成."""
        if sock in self.connections and sock.state() == QLocalSocket.ConnectedState:
            from core.output_rules import event_timestamp
            message.setdefault("timestamp", event_timestamp())
            allow_config_get_result = (
                config_get_result_id is not None
                and max_bytes == MAX_CONFIG_RESULT_MESSAGE
                and message.get("event") == "result"
                and message.get("id") == config_get_result_id
                and message.get("success") is True
                and message.get("status") == "success"
                and isinstance(message.get("config"), dict)
                and type(message.get("max_config_result_bytes")) is int
                and message["max_config_result_bytes"] == MAX_CONFIG_RESULT_MESSAGE
            )
            try:
                payload = encode_json_line(message, max_bytes)
            except ValueError:
                payload = encode_json_line(dict(event="result", id=message.get("id"), success=False,
                    status="failed", message=f"回执超过 {max_bytes} 字节上限，未截断返回配置"))
                allow_config_get_result = False
            pending_limit = MAX_CONFIG_RESULT_MESSAGE if allow_config_get_result else MAX_PENDING_OUTPUT
            if sock.bytesToWrite() + len(payload) > pending_limit:
                # 订阅者停读不能拖累 GUI 或积累无限发送缓冲;客户端必须报告断线.
                sock.abort()
                return
            sock.write(payload)
            sock.flush()

    def broadcast(self, event):
        """向订阅者发布原始数据或状态;订阅连接不持有设备任务."""
        from core.output_rules import event_timestamp
        event.setdefault("timestamp", event_timestamp())
        for sock, task_id in list(self.subscribers.items()):
            if sock in self.machine_subscribers:
                meta = self.machine_subscribers[sock]
                if meta["failed"]:
                    continue
                outgoing = dict(event, id=event.get("id"), subscription_id=task_id,
                                task_id=event.get("task_id", event.get("id")),
                                scope_id=meta["scope"],
                                scope="request_window" if meta["scope"] else "ambient")
                if event.get("event") == "receive":
                    meta["rx_sequence"] += 1
                    outgoing["rx_sequence"] = meta["rx_sequence"]
                self.send(sock, outgoing)
            else:
                self.send(sock, dict(event, id=task_id))

    def publish_state(self, message, error=False, **fields):
        """发布连接及故障状态,不经过 GUI 的显示来源筛选."""
        from core.output_rules import event_timestamp
        fields.setdefault("timestamp", event_timestamp())
        event = dict(event="progress", source="error" if error else "system", message=message, **fields)
        self.broadcast(event)
        self.check_machine_sessions()
        # 一次性请求没有订阅连接,仍需收到同源错误;控制台已由订阅显示,不双发.
        if (error and self.active and self.active.get("action") != "console-send"
                and self.active["socket"] not in self.subscribers):
            self.send(self.active["socket"], dict(event, id=self.active["id"]))

    def publish_log_settings(self, state):
        """成功配置提交后仅向人工终端同步日志/发送偏好,不改变机器事件协议."""
        event = dict(event="log-settings", log_settings=state["log_settings"],
                     send_settings={key: state["send_settings"][key] for key in ("show_send", "send_color")})
        for sock, task_id in list(self.subscribers.items()):
            if sock not in self.machine_subscribers:
                self.send(sock, dict(event, id=task_id))

    def transmit(self, data):
        """发布已写入本地或已提交远程请求的原始 TX 字节,不添加显示转义."""
        for offset in range(0, len(data), DATA_CHUNK_SIZE):
            self.broadcast(dict(event="progress", source="send",
                                id=self.active["id"] if self.active else None,
                                data=base64.b64encode(data[offset:offset + DATA_CHUNK_SIZE]).decode("ascii"),
                                remote_queued=self.window.remote_mode == "client"))

    def visible(self, task_id, message, error=False, timestamp=None):
        """更新状态栏;正常操作记为系统调试信息,异常记为错误,均带请求 ID."""
        text = f"[CLI {task_id[:8]}] {message}"
        self.window.statusBar().showMessage(text)
        from core.output_rules import event_timestamp
        timestamp = timestamp or event_timestamp()
        self.window.output_manager.append_text(
            text, OutputSource.ERROR if error else OutputSource.SYSTEM,
            system_level="debug" if not error else "normal", timestamp=timestamp)
        return timestamp

    def dispatch(self, sock, request):
        """校验请求并在 GUI 线程调用现有窗口操作入口.

        Args:
            sock (QLocalSocket): 请求来源连接,用于进度及结果回传.
            request (dict): id 为 32 位小写十六进制 UUID;action 为 status/cancel/
                send/open/close/remote-connect/remote-listen/remote-stop;command
                为字符串;read_timeout 为 0-60000 ms,缺省 300.remote_host,
                remote_port,remote_password 可覆盖窗口远程配置,省略则沿用.
                设备动作只允许一个活动请求,查询及取消可单独受理.

        已缓存的操作 ID 返回原终态;accepted 只表示受理.下载和远程设备请求
        等待各自完成回执;普通写入完成不等于设备执行成功.

        Raises:
            ValueError: ID,动作,参数无效,或存在任务冲突,结果缓存已满.
        """
        if self.window._closing_logs:
            raise ValueError("GUI 正在保存退出日志，暂不接受新请求")
        task_id = request.get("id", "")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("CLI 请求 ID 无效")
        action = request.get("action", "send")
        if action == "machine-window-close":
            self.close_machine_window(sock, request)
            return
        query = action in ("subscribe", "config-get", "config-schema", "config-check", "status", "receive-save-state", "log-get", "log-check", "log-status", "log-recover")
        machine_session = request.get("machine_session")
        if not query:
            for meta in self.machine_subscribers.values():
                if meta["scope"]:
                    raise ValueError("机器 RX 窗口尚未关闭,拒绝其它设备任务")
        if machine_session:
            _, meta = self.machine_meta(machine_session)
            self.check_machine_sessions()
            if action != "console-send" or meta["failed"] or meta["scope"] is not None:
                raise ValueError("机器会话不可提交此请求")
            if not self.machine_snapshot()["idle"]:
                raise ValueError("GUI 当前并非空闲")
            meta["scope"] = task_id
        if task_id in self.results:
            self.send(sock, self.results[task_id])
            sock.disconnectFromServer()
            return
        action = request.get("action", "send")
        if action == "send":
            from core.command_parser import require_device_command
            require_device_command(request.get("command", ""))
        detach = request.get("detach_on_disconnect", False)
        if type(detach) is not bool:
            raise ValueError("detach_on_disconnect 必须为布尔值")
        remote_options = any(key in request for key in ("remote_host", "remote_port", "remote_password"))
        if remote_options and action not in ("remote-connect", "remote-listen"):
            raise ValueError("远程参数仅用于 remote-connect 或 remote-listen")
        if action not in ("send", "console-send") and request.get("command"):
            raise ValueError("--gui-action 与 --send 必须分次执行")
        if ("config_patch" in request) != (action in ("config-check", "config-apply")):
            raise ValueError("config-check/config-apply 必须提供 --config-patch，其他操作不接受该参数")
        if ("log_patch" in request) != (action in ("log-check", "log-apply")):
            raise ValueError("log-check/log-apply必须提供日志补丁,其他操作不接受")
        if action in ("log-get", "log-check", "log-apply", "log-status", "log-recover"):
            from core import gui_config
            from core.log_settings import prepare_settings
            result = dict(event="result", id=task_id, success=True, status="success", message="日志操作完成")
            if action == "log-status":
                result["log_status"] = self.window.log_archive_status()
            elif action == "log-recover":
                path = request.get("path")
                if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 32767 or any(c in path for c in "\r\n\x00"):
                    raise ValueError("日志恢复需要有效的绝对新文件路径")
                def completed(success, message):
                    reply = dict(result, success=success, status="success" if success else "failed",
                                 message=message, log_status=self.window.log_archive_status())
                    self.send(sock, reply)
                    if sock in self.connections:
                        sock.disconnectFromServer()
                self.window.recover_log_archive(path, completed)
                return
            else:
                current = gui_config.snapshot(self.window)["state"]["log_settings"]
                if action == "log-get":
                    result["log_settings"] = current
                else:
                    candidate = prepare_settings({"log_settings": current}, request["log_patch"])
                    patch = {"state": {"log_settings": candidate}}
                    gui_config.prepare(self.window, patch)
                    if action == "log-apply":
                        if self.active or self.window._template_task is not None:
                            raise ValueError("GUI当前有任务运行,请稍后修改日志配置")
                        gui_config.apply(self.window, patch)
                    result["log_settings"] = candidate
            self.send(sock, result)
            sock.disconnectFromServer()
            return
        if ('config_switch' in request) != (action == 'config-switch'):
            raise ValueError("config-switch 必须提供工作 JSON 路径")
        if action == 'config-switch':
            from core import gui_config
            path = request['config_switch']
            if not isinstance(path, str) or not os.path.isabs(path):
                raise ValueError("工作 JSON 路径必须为绝对路径")
            fields = gui_config.switch_file(self.window, path)
            message = '工作 JSON 已切换;当前连接设置保持不变' if fields['switched'] else '工作 JSON 未变化'
            self.send(sock, dict(event='result', id=task_id, success=True, status='success',
                                 message=message, timestamp=self.visible(task_id, message), **fields))
            sock.disconnectFromServer()
            return
        if action == "receive-save-state":
            from core.output_rules import file_operation_event
            from datetime import datetime
            fields = request.get("receive_save")
            if (not isinstance(fields, dict) or set(fields) != {"path", "outcome", "timestamp", "file_event_id", "failure"}
                    or fields["outcome"] not in ("started", "stopped", "failed")
                    or fields["failure"] not in (None, "open", "write")
                    or not isinstance(fields["path"], str) or len(fields["path"]) > 4096
                    or not os.path.isabs(fields["path"])):
                raise ValueError("RX 保存反馈字段无效")
            datetime.fromisoformat(fields["timestamp"])
            if len(self.results) >= 1024:
                raise ValueError("CLI 请求记录已满，请重启窗口")
            if not isinstance(fields["file_event_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", fields["file_event_id"]):
                raise ValueError("RX 保存事件 ID 无效")
            event = file_operation_event("receive-save", fields["path"], fields["outcome"], timestamp=fields["timestamp"], failure=fields["failure"])
            event["file_event_id"] = fields["file_event_id"]
            event = self.window.publish_file_event(event)
            result = dict(event, event="result", id=task_id, success=True, status="success")
            self.results[task_id] = result
            self.send(sock, result)
            sock.disconnectFromServer()
            return
        if action in ("template-import", "template-export"):
            from core.output_rules import file_operation_event
            path = request.get("path")
            if not isinstance(path, str) or not os.path.isabs(path) or len(path) > 4096:
                raise ValueError("模板路径必须为有效绝对路径")
            if (self.active or self.window.serial_operation_busy() or self.window.is_continuous_sending
                    or self.window._remote_device_pending or len(self.results) >= 1024):
                event = self.window.publish_file_event(file_operation_event(action, path, "failed", error="GUI 当前有任务运行或记录已满"))
                self.send(sock, dict(event, event="result", id=task_id, success=False, status="failed"))
                sock.disconnectFromServer()
                return
            self.active = dict(id=task_id, socket=sock, action=action, detach_on_disconnect=detach,
                               read_timeout=0, matcher=None, disconnected=False, command="")
            self.send(sock, dict(event="accepted", id=task_id, message="GUI 已接收模板操作"))
            self.window.start_template_operation(action, path,
                lambda event: self.complete(event["outcome"] == "success", event["message"], file_event=event), owner=self.active)
            return
        if self.window._template_task is not None and action in ("subscribe", "config-get", "config-schema", "config-check"):
            raise ValueError("模板事务进行中，配置尚未提交，请稍后查询")
        if action == "subscribe":
            if "machine_protocol" in request:
                if request["machine_protocol"] != 1 or type(request["machine_protocol"]) is not int:
                    raise ValueError("不支持的机器协议版本")
                current = self.machine_snapshot()
                if (not current["idle"] or not current["connection"]["serial_connected"]
                        or current["connection"]["network_connected"] is False):
                    raise ValueError("机器启动要求目标已连接且 GUI 空闲,未自动 Open")
                self.subscribers[sock] = task_id
                self.machine_subscribers[sock] = dict(id=task_id, scope=None, failed=False, rx_sequence=0,
                                                     target=current["target"], connection=current["connection"])
                self.send(sock, dict(event="accepted", id=task_id, machine_protocol=1, **current))
                return
            from core import gui_config
            snapshot = gui_config.redacted(gui_config.snapshot(self.window))
            self.send(sock, dict(event="accepted", id=task_id, console_protocol=1,
                                message="GUI 收发订阅已建立", state=snapshot["state"], tools=snapshot["tools"]))
            self.subscribers[sock] = task_id
            return
        if action == "console-send":
            self.dispatch_console(sock, request)
            return
        if action in ("config-get", "config-schema", "config-check"):
            from core import gui_config
            result = dict(event="result", id=task_id, success=True, status="success")
            if action == "config-get":
                result.update(message="当前窗口配置", config=gui_config.redacted(gui_config.snapshot(self.window)),
                              **gui_config.config_paths(self.window),
                              omitted_paths=["/state/remote_settings/token"],
                              max_config_result_bytes=MAX_CONFIG_RESULT_MESSAGE)
            elif action == "config-schema":
                result.update(message="可配置字段规则", schema=gui_config.schema(self.window))
            else:
                before, candidate = gui_config.prepare(self.window, request["config_patch"])
                result.update(message="配置校验通过，未应用", changed=before != candidate)
            result["timestamp"] = self.visible(task_id, result["message"])
            if action == "config-get":
                self.send(sock, result, max_bytes=MAX_CONFIG_RESULT_MESSAGE, config_get_result_id=task_id)
            else:
                self.send(sock, result)
            sock.disconnectFromServer()
            return
        if action == "status":
            from core.gui_config import config_paths
            result = dict(event="result", id=task_id, success=True, status="success",
                          message="GUI 当前状态", remote_mode=self.window.remote_mode,
                          network_connected=self.window.remote_client_connected,
                          serial=self.window.get_current_serial_config(),
                          busy=self.window.serial_operation_busy(), cli_busy=self.active is not None,
                           remote=self.remote_status(), **config_paths(self.window))
            result["timestamp"] = self.visible(task_id, "状态查询")
            self.send(sock, result)
            sock.disconnectFromServer()
            return
        if action == "cancel":
            if self.active and self.active.get("executor"):
                self.active["executor"].cancel()
            self.stop_owned_remote_start()
            self.window._remote_sequence_failed = True
            self.window.cancel_template_operation()
            self.window.cancel_firmware_download()
            self.window.stop_continuous_sending()
            if self.active and not self.active.get("executor") and not self.window.serial_operation_busy():
                self.complete(False, "CLI 命令链已取消；已写出的设备命令无法撤回")
            self.send(sock, dict(event="result", id=task_id, success=True, status="accepted",
                                 message="已请求取消；原任务的最终结果单独返回"))
            self.visible(task_id, "请求取消当前任务")
            sock.disconnectFromServer()
            return
        if action not in ("send", "open", "close", "remote-connect", "remote-listen", "remote-stop", "config-apply"):
            raise ValueError("不支持的 GUI 操作")
        if self.active or self.window.serial_operation_busy() or self.window.is_continuous_sending or self.window._remote_device_pending:
            raise ValueError("GUI 当前有任务运行，请等待结束或显式取消")
        if len(self.results) >= 1024:
            raise ValueError("CLI 请求记录已满，请重启窗口")
        read_timeout = request.get("read_timeout", 300)
        if type(read_timeout) is not int or not 0 <= read_timeout <= 60000:
            raise ValueError("read-timeout 必须为 0–60000 ms")
        command = request.get("command", "")
        if not isinstance(command, str):
            raise ValueError("command 必须为字符串")
        matcher = None
        if "expect" in request:
            if action != "send" or not isinstance(request["expect"], str):
                raise ValueError("expect 仅支持发送请求，且必须为正则表达式字符串")
            from core.receive_text import ExpectMatcher
            matcher = ExpectMatcher(request["expect"])
        self.active = dict(id=task_id, socket=sock, command=command, action=action,
                           read_timeout=read_timeout, disconnected=False, download=False,
                           detach_on_disconnect=detach, matcher=matcher)
        self.window._remote_sequence_failed = False
        self.visible(task_id, f"{action}: {command}")
        self.send(sock, dict(event="accepted", id=task_id, message="GUI 已接收请求，等待实际执行结果"))
        try:
            if action == "config-apply":
                from core import gui_config
                before, candidate = gui_config.prepare(self.window, request["config_patch"])
                if self.window.remote_mode == "client" and before["state"]["basic_settings"] != candidate["state"]["basic_settings"]:
                    local_only = gui_config.merge_patch(candidate, {"state": {"basic_settings": before["state"]["basic_settings"]}})
                    if local_only != before:
                        raise ValueError("远程串口基本设置与本机其他配置请分两次应用，避免跨电脑部分成功")
                    if not self.window.remote_capabilities.get("serial_config"):
                        raise ValueError("设备端尚未支持完整串口配置回执，请更新设备端源码")
                    accepted = self.window.request_remote_device("configure", candidate["state"]["basic_settings"],
                        lambda ok: self.complete(ok, "设备端串口参数已保存，打开串口后生效" if ok else "设备端串口配置失败或结果未知"))
                    if not accepted:
                        self.complete(False, "远程连接未就绪或串口被占用")
                    return
                changed = gui_config.apply(self.window, request["config_patch"])
                self.complete(True, "配置已应用并保存" if changed else "配置未变化")
                return
            if action.startswith("remote-"):
                self.control_remote(request)
                return
            if action in ("open", "close"):
                if self.window.remote_mode == "client":
                    config = self.window.get_current_serial_config() if action == "open" else {}
                    accepted = self.window.request_remote_device(action, config,
                        lambda ok: self.complete(ok, "设备端串口操作完成" if ok else "设备端串口操作失败或结果未知"))
                    if not accepted:
                        self.complete(False, "远程连接未就绪")
                else:
                    ok = self.window.open_serial() if action == "open" else self.window.close_serial()
                    self.complete(bool(ok), "串口操作完成" if ok else "串口操作失败")
                return
            kind, parameter = command_parts(command)
            if kind == "firmwaredownload":
                self.active["download"] = True
                self.window.start_firmware_download_command(parameter, self.download_finished)
                return
            if kind == "sendmode":
                self.window.special_command_manager.execute_sendmode_inline(
                    parameter, self.window, lambda: self.after_command(not self.window._remote_sequence_failed))
                return
            if kind == "delay":
                delay = int(parameter)
                if not 0 <= delay <= 60000:
                    raise ValueError("桥接 delay 必须为 0–60000 ms")
                QTimer.singleShot(delay, lambda current=task_id: self.after_command(True)
                                  if self.active and self.active["id"] == current else None)
                return
            if kind in ("mode", "modeend", "stopcontinuous"):
                raise ValueError("模块标记不能单独作为设备操作；停止请使用 --gui-action cancel")
            if kind:
                enum = next((item for item in SpecialCommandType if item.value == kind), None)
                if enum is None:
                    raise ValueError(f"不支持的特殊命令: {kind}")
                ok = self.window.special_command_manager.execute(enum, parameter, self.window)
            else:
                ok = self.window.send_command(unescape_text(command))
            self.after_command(bool(ok))
        except Exception as exc:
            self.complete(False, str(exc))

    def remote_status(self):
        """返回窗口远程角色,目标,监听状态和能力;不包含共享口令."""
        window = self.window
        return dict(mode=window.remote_mode, host=window.remote_host_input.currentText().strip(),
                    port=window.remote_port_spin.value(), connected=window.remote_client_connected,
                    listening=bool(getattr(window.remote_thread, "listening", False)),
                    capabilities={key: window.remote_capabilities.get(key, 0)
                                  for key in ("device_results", "firmware_upload", "serial_config")})

    def dispatch_console(self, sock, request):
        """接受统一控制台执行请求;控制命令可在设备任务运行时提交.

        Args:
            sock (QLocalSocket): 仅回传该次执行终态的连接;收发由独立订阅显示.
            request (dict): command 为原输入,detach_on_disconnect 为断开后继续策略.

        执行器在工作线程组织模块与延时,设备原语始终由 Qt 信号交回 GUI.
        不复用旧一次性路径的后端能力差异,不重复实现模块及固件命令解析.
        """
        from core.console_execution import ConsoleExecutor
        task_id = request["id"]
        command = request.get("command", "")
        if not isinstance(command, str):
            raise ValueError("command 必须为字符串")
        parsed = parse_input(command)
        display = f"{parsed.name}:{parsed.parameter}" if isinstance(parsed, Command) else command
        self.visible(task_id, f"console: {display}")
        if isinstance(parsed, Command) and parsed.name in ("Status", "Cancel", "StopContinuous"):
            executor = self.active.get("executor") if self.active else None
            control_executor = executor or ConsoleExecutor(self.console_transport, lambda event: None)
            if parsed.name == "Status":
                control_executor._no_parameter(parsed)
            self.send(sock, dict(event="accepted", id=task_id, message="GUI 已接收控制请求"))
            if parsed.name == "Status":
                status = self.console_transport.current_status()
                outcome = dict(success=True, status="success", message="当前串口状态", serial=status,
                               busy=bool(self.active or self.window.serial_operation_busy() or self.window.is_continuous_sending))
            else:
                outcome = control_executor.execute(command)
                if not outcome.get("success"):
                    self.send(sock, dict(outcome, event="result", id=task_id))
                    self.visible(task_id, outcome["message"], True)
                    sock.disconnectFromServer()
                    return
                if parsed.name == "Cancel":
                    self.window.cancel_firmware_download()
                    self.window.stop_continuous_sending()
                    self.window._remote_sequence_failed = True
                    if self.active and not executor and not self.window.serial_operation_busy():
                        self.complete(False, "CLI 命令链已取消；已写出的设备命令无法撤回")
                else:
                    mode = int(parsed.parameter.strip() or "0")
                    self.window.special_command_manager.execute(SpecialCommandType.STOPCONTINUOUS, str(mode), self.window)
            result = dict(outcome, event="result", id=task_id)
            self.send(sock, result)
            self.visible(task_id, outcome["message"])
            sock.disconnectFromServer()
            return
        if (self.active or self.window.serial_operation_busy() or self.window.is_continuous_sending
                or self.window._remote_device_pending):
            raise ValueError("当前有任务运行，请先 Cancel 或等待结束")
        if len(self.results) >= 1024:
            raise ValueError("CLI 请求记录已满，请重启窗口")
        executor = ConsoleExecutor(self.console_transport, lambda event: self.console_event.emit((task_id, event))
                                   if not self.console_transport.closed.is_set() else None)
        self.active = dict(id=task_id, socket=sock, command=command, action="console-send", executor=executor,
                           detach_on_disconnect=request.get("detach_on_disconnect", False),
                           disconnected=False, download=False,
                           machine_session=request.get("machine_session"),
                           machine_target=self.machine_meta(request["machine_session"])[1]["target"]
                           if request.get("machine_session") else None)
        self.window._remote_sequence_failed = False
        self.send(sock, dict(event="accepted", id=task_id, message="GUI 已接收请求，等待实际执行结果"))

        def execute():
            try:
                result = executor.execute(command)
            except Exception as exc:
                result = dict(success=False, status="failed", message=str(exc))
            if not self.console_transport.closed.is_set():
                self.console_finished.emit((task_id, result))
        thread = threading.Thread(target=execute, name="gui-console-command", daemon=True)
        self.active["thread"] = thread
        thread.start()
        # started 在任何 Qt 原语调用前置位,避免受理后立即 Cancel 被执行器初始化清除.
        executor.started.wait(0.5)

    def on_console_event(self, item):
        """把执行线程的日志送回 Qt 线程,订阅者与请求分别接收进度."""
        task_id, event = item
        if not self.active or self.active["id"] != task_id:
            return
        event = dict(event)
        event.setdefault("event", "progress")
        event.setdefault("source", "system")
        event["id"] = task_id
        self.send(self.active["socket"], event)
        self.broadcast(event)
        if event.get("message"):
            self.visible(task_id, event["message"], event.get("source") == "error", timestamp=event.get("timestamp"))

    def on_console_finished(self, item):
        """只结算匹配请求,丢弃窗口关闭或旧任务的迟到回调."""
        task_id, result = item
        if self.active and self.active["id"] == task_id:
            fields = {key: value for key, value in result.items() if key not in ("success", "message", "event", "id")}
            self.complete(bool(result.get("success")), result.get("message", "操作完成"), **fields)

    def stop_owned_remote_start(self):
        """只停止当前 CLI 尚在等待启动的远程线程,不关闭已完成连接或其他任务."""
        task = self.active
        if task and task.get("remote_thread") is not None and self.window.remote_thread is task["remote_thread"]:
            self.window.stop_remote_control()

    def control_remote(self, request):
        """复用窗口启停远程连接;验证全部参数后更新控件,异步等待实际就绪.

        Args:
            request (dict): action 为 remote-connect/remote-listen/remote-stop.
                remote_host 为非空设备端地址,仅用于连接;remote_port 为 1-65535
                的整数;remote_password 为共享口令.省略参数沿用窗口值.

        连接须完成认证,设备结果能力协商及串口配置同步;监听须 bind/listen
        成功.十秒内未就绪则停止本次线程并失败,不打开串口,不重连.切换
        设备端须先 remote-stop;不抢占当前连接,本地串口或运行任务.
        """
        window = self.window
        task = self.active
        if request["action"] == "remote-stop":
            window.stop_remote_control()
            self.complete(window.remote_thread is None,
                          "远程控制已关闭" if window.remote_thread is None else "远程线程尚未退出")
            return
        if window.remote_thread is not None or window.remote_mode != "off":
            raise ValueError("远程控制已启用；切换设备端前请先执行 remote-stop")
        client = request["action"] == "remote-connect"
        if client and window.is_connected:
            raise ValueError("连接设备端前请先关闭本地串口")
        if not client and "remote_host" in request:
            raise ValueError("remote-listen 使用现有 GUI 的全部 IPv4 接口监听规则，不接受 --remote-host")
        host = request.get("remote_host", window.remote_host_input.currentText())
        host = host.strip() if isinstance(host, str) else ""
        port = request.get("remote_port", window.remote_port_spin.value())
        password = request.get("remote_password", window.remote_token_input.text())
        if client and (not host or len(host) > 253 or any(c.isspace() or c == '\0' for c in host)):
            raise ValueError("设备端地址无效")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("远程端口必须为 1–65535 的整数")
        if not isinstance(password, str) or len(password) > 4096:
            raise ValueError("远程口令必须为不超过 4096 字符的字符串")
        window.remote_mode_combo.setCurrentIndex(1 if client else 0)
        if client:
            window.remote_host_input.setCurrentText(host)
        window.remote_port_spin.setValue(port)
        window.remote_token_input.setText(password)
        window.start_remote_control()
        thread = window.remote_thread
        if thread is None:
            self.complete(False, "远程控制未启动")
            return
        task["remote_thread"] = thread
        task["remote_serial_synced"] = False
        deadline = time.monotonic() + 10

        def on_error(message, fatal):
            """仅记录本次启动线程的错误,最终清理由就绪检查统一执行."""
            if self.active is task and fatal:
                task["remote_error"] = message

        def on_message(message):
            """记录本次连接已收到设备端串口配置,避免刚认证就使用旧端口设置."""
            if self.active is task and message.get("type") == "serial_config" and isinstance(message.get("config"), dict):
                task["remote_serial_synced"] = True

        def await_ready():
            """轮询实际监听或完整认证状态,失败时清理本次线程,不自动重试."""
            if self.active is not task:
                return
            ready = (window.remote_client_connected and task["remote_serial_synced"]
                     and window.remote_capabilities.get("device_results") == 1) if client else thread.listening
            if task.get("remote_error") or window.remote_thread is not thread or thread.isFinished():
                self.stop_owned_remote_start()
                self.complete(False, task.get("remote_error", "远程连接在就绪前已关闭"))
            elif ready:
                self.complete(True, "已连接设备端并同步状态" if client else "设备端监听已启用")
            elif time.monotonic() >= deadline:
                self.stop_owned_remote_start()
                self.complete(False, "远程启动超时，请检查地址、端口、密码及两端版本")
            else:
                QTimer.singleShot(20, await_ready)

        thread.error_occurred.connect(on_error)
        thread.message_received.connect(on_message)
        task["remote_callbacks"] = (on_error, on_message)
        QTimer.singleShot(20, await_ready)

    def after_command(self, accepted):
        """普通发送只确认串口写入;等待远程回执后再开始响应观察窗口."""
        if not self.active:
            return
        if not accepted:
            self.complete(False, "命令未执行或参数无效")
            return
        task_id = self.active["id"]

        def await_result():
            """按请求 ID 等待设备端回执;失败结束任务,成功后开始接收观察窗口."""
            if not self.active or self.active["id"] != task_id:
                return
            if self.window._remote_device_pending:
                QTimer.singleShot(20, await_result)
            elif self.window._remote_sequence_failed:
                self.complete(False, "操作失败或结果未知；未自动重试")
            else:
                task = self.active
                matcher = task.get("matcher")
                if matcher is None:
                    QTimer.singleShot(task["read_timeout"], lambda: self.complete(
                        True, "操作完成") if self.active is task else None)
                else:
                    task["waiting_expect"] = True
                    if matcher.matched:
                        self.complete(True, "响应已匹配", expect_matched=True)
                    else:
                        QTimer.singleShot(task["read_timeout"], lambda: self.expect_timeout(task))
        await_result()

    def expect_timeout(self, task):
        """观察期结束后冲刷解码残片;仅成功执行但未匹配返回退出码 4."""
        if self.active is not task:
            return
        matched = task["matcher"].finish()
        self.complete(matched, task["matcher"].result_message(),
                      expect_matched=matched, expect_timeout=not matched)

    def download_finished(self, success):
        """把当前下载的最终说明交给 CLI;success 包含下载及调试恢复结果."""
        if self.active:
            message = getattr(self.window, "_last_firmware_result", (False, "下载未启动"))[1]
            if success and self.active.get("matcher") is not None:
                self.after_command(True)
            else:
                self.complete(success, message)

    def progress(self, current, total, message):
        """向 CLI 和窗口状态栏同步进度;current/total 的单位由 message 所示阶段决定."""
        if self.active:
            self.send(self.active["socket"], dict(event="progress", id=self.active["id"],
                      current=current, total=total, message=message))
            self.window.statusBar().showMessage(f"[CLI {self.active['id'][:8]}] {message} ({current}/{total})")
        self.broadcast(dict(event="progress", source="system", current=current, total=total, message=message))

    def receive(self, data):
        """发布原始 RX 字节;单次观察和长期订阅分别持有自己的连接."""
        task = self.active
        for offset in range(0, len(data), DATA_CHUNK_SIZE):
            encoded = base64.b64encode(data[offset:offset + DATA_CHUNK_SIZE]).decode("ascii")
            if task:
                self.send(task["socket"], dict(event="receive", id=task["id"], data=encoded))
            self.rx_sequence += 1
            self.broadcast(dict(event="receive", source="receive", data=encoded,
                                task_id=task["id"] if task else None, rx_sequence=self.rx_sequence))
        if task and task.get("matcher") is not None:
            matched = task["matcher"].feed(data)
            if matched and task.get("waiting_expect") and self.active is task:
                self.complete(True, "响应已匹配", expect_matched=True)

    def template_progress(self, current, total):
        """模板进度来自 Qt 所有者;GUI 弹窗和 CLI 请求观察同一工作阶段."""
        if self.active and self.active["action"] in ("template-import", "template-export"):
            self.send(self.active["socket"], dict(event="progress", id=self.active["id"],
                current=current, total=total, source="system", message=f"模板处理进度: {current}/{total}"))

    def complete(self, success, message, **fields):
        """保存操作终态,更新可见日志并回传结果,然后结束请求连接.

        Args:
            success (bool): 当前操作是否按其完成契约成功.
            message (str): 最终说明;失败且包含'未知'时映射为 unknown,否则为 failed.

        没有活动请求时忽略迟到回调.普通串口操作的 success 不代表设备确认.
        """
        task = self.active
        if not task:
            return
        self.active = None
        if task.get("remote_callbacks"):
            error_callback, message_callback = task.pop("remote_callbacks")
            task["remote_thread"].error_occurred.disconnect(error_callback)
            task["remote_thread"].message_received.disconnect(message_callback)
        status = "success" if success else ("unknown" if "未知" in message else "failed")
        result = dict(event="result", id=task["id"], success=bool(success), status=status, message=message)
        file_event = fields.pop("file_event", None)
        result.update(fields)
        if file_event is not None:
            result.update(file_event)
            result.update(event="result", id=task["id"], success=bool(success), status=status)
        if task.get("matcher") is not None:
            task["matcher"].finish()
            result.update(task["matcher"].result_fields())
        if task["action"].startswith("remote-"):
            result["remote"] = self.remote_status()
        self.results[task["id"]] = result
        if file_event is None:
            result["timestamp"] = self.visible(task["id"], f"{status}: {message}", not success, timestamp=result.get("timestamp"))
        if task.get("machine_session"):
            current = self.machine_snapshot()
            result.update(target=current["target"], connection=current["connection"])
            try:
                subscription, meta = self.machine_meta(task["machine_session"])
            except ValueError:
                pass
            else:
                # RX、状态、终态沿同一订阅流排序,任务连接只确认传输结束.
                result["machine_result_routed"] = True
                self.send(subscription, result)
        self.send(task["socket"], result)
        if task["socket"] in self.connections:
            task["socket"].disconnectFromServer()

    def remote_disconnected(self):
        """远程连接结束时停止后续命令,已提交操作按未知返回,下载仍等资源释放."""
        task = self.active
        if task and task["action"] in ("send", "console-send", "open", "close"):
            if task.get("executor"):
                task["executor"].cancel()
            elif not self.window.serial_operation_busy():
                self.complete(False, "结果未知：远程连接已断开；未自动重试")

    def disconnected(self, sock):
        """清理连接;若其拥有活动任务则请求取消后续操作,不撤回已发送数据.

        下载仍占用串口时等待实际完成回调,否则立即记为结果未知并结束请求.
        """
        self.connections.pop(sock, None)
        self.subscribers.pop(sock, None)
        self.machine_subscribers.pop(sock, None)
        if self.active and self.active["socket"] is sock:
            self.active["disconnected"] = True
            if self.active.get("detach_on_disconnect"):
                self.active["socket"] = None
                self.visible(self.active["id"], "控制台已退出；已受理任务继续执行，可重新连接后 Cancel")
                sock.deleteLater()
                return
            if self.active["action"] in ("template-import", "template-export"):
                self.window.cancel_template_operation()
                sock.deleteLater()
                return
            self.stop_owned_remote_start()
            self.visible(self.active["id"], "CLI 已断开，取消后续操作；已发送操作结果可能未知", True)
            self.window._remote_sequence_failed = True
            self.window.cancel_firmware_download()
            self.window.stop_continuous_sending()
            if not self.window.serial_operation_busy():
                self.complete(False, "结果未知：CLI 在操作完成前断开")
        sock.deleteLater()

    def close(self):
        """窗口正常关闭时停止监听并释放端点锁;下载仍运行时由窗口拒绝关闭."""
        self.server.close()
        self.console_transport.closed.set()
        if self.active and self.active.get("executor"):
            self.active["executor"].cancel()
        for sock in list(self.connections):
            if sock in self.subscribers:
                self.send(sock, dict(event="result", id=self.subscribers[sock], success=False,
                                     status="failed", message="GUI 已关闭，收发订阅已断开"))
                if sock.bytesToWrite():
                    sock.waitForBytesWritten(100)
                sock.disconnectFromServer()
            else:
                sock.abort()
        if self.owns_lock:
            self.lock.unlock()
            self.owns_lock = False
