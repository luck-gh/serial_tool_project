#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
远程控制模块, 负责基于局域网 TCP 的设备端和控制端串口数据转发.

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import base64
import json
import queue
import socket
import time
import uuid

from PyQt5.QtCore import QThread, pyqtSignal


class RemoteControlBase(QThread):
    """远程 TCP 传输线程,采用 UTF-8 换行 JSON 和 Base64 二进制字段.

    run 负责收发;UI 将消息放入线程安全队列,通过 Qt 信号接收事件.stop
    可在调用线程关闭 socket 以解除阻塞.此层不执行串口操作,也不判断设备
    命令成功.队列携带本地 session,旧连接消息不发给新连接;入站单帧最多
    128 KiB,保留半帧并拆解粘包.sendall 失败后不重发不确定消息.
    """
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str, bool)
    connected_changed = pyqtSignal(bool, str)
    message_received = pyqtSignal(dict)

    def __init__(self, token=""):
        """初始化尚未启动的传输线程.

        Args:
            token (str): 两端配置的共享口令,空字符串表示不校验口令;不提供加密.
        """
        super().__init__()
        self.token = token or ""
        self.is_running = False
        self._send_queue = queue.Queue()
        self._sock = None
        self.session = ""

    def send_serial_data(self, data):
        """将设备端收到的字节按最多 32768 字节分块,Base64 编码并入队;不等待回执."""
        for offset in range(0, len(data), 32768):
            self._queue_message({
                "type": "serial_data",
                "data": base64.b64encode(data[offset:offset + 32768]).decode("ascii")
            })

    def send_serial_config(self, config):
        """复制串口状态字典并入队同步;该事件不代表任何设备操作已完成."""
        self._queue_message({
            "type": "serial_config",
            "config": dict(config)
        })

    def send_error(self, message, fatal=False):
        """将错误说明及 fatal 标志入队,由接收方决定是否断开连接."""
        self._queue_message({
            "type": "error",
            "message": message,
            "fatal": bool(fatal)
        })

    def stop(self):
        """请求停止,关闭当前 socket 并等待至多 5500 ms,返回线程是否退出.

        等待上限覆盖正在进行的五秒 connect 超时;返回 False 时不能销毁线程.
        """
        self.is_running = False
        self._close_socket()
        return self.wait(5500)  # 等待上限包含尚未完成的五秒 connect 超时

    def _queue_message(self, message):
        """将消息与当前 session 一起入队;会话改变后该消息会被丢弃."""
        self._send_queue.put((self.session, message))

    def _send_message(self, sock, message):
        """把单个字典编码为换行结尾 JSON 并 sendall;异常向上传播,不重发."""
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        sock.sendall(payload)

    def _drain_send_queue(self, sock):
        """取出待发消息,仅发送属于当前连接的项目;发送异常向上传播."""
        while True:
            try:
                session, message = self._send_queue.get_nowait()
            except queue.Empty:
                return
            if session == self.session:
                self._send_message(sock, message)

    def _handle_message(self, message):
        """按类型向 GUI 分发消息并附加本地 session,拒绝旧的无 ID 设备控制消息."""
        msg_type = message.get("type")
        if msg_type in ("capabilities", "firmware", "device_request", "device_result", "serial_data", "serial_config"):
            self.message_received.emit(dict(message, _session=self.session))
            return
        if msg_type in ("baudrate", "serial_control"):
            self.send_error("设备操作必须使用带请求 ID 的 device_request；请更新两端程序")
        elif msg_type == "status":
            text = message.get("message", "")
            if text:
                self.status_changed.emit(text)
        elif msg_type == "error":
            text = message.get("message", "")
            if text:
                self.error_occurred.emit(text, bool(message.get("fatal", False)))

    def _process_incoming(self, buffer):
        """从 TCP 字节流提取完整 JSON 帧,保留末尾未完成帧.

        Args:
            buffer (bytes): 上次残留与本次接收数据的拼接结果.

        Returns:
            tuple[bytes, list[dict]]: 未完成帧及按到达顺序解析出的消息.

        Raises:
            ValueError: 完整帧或残留超过 128 KiB,JSON 无效或消息不是字典.
            UnicodeDecodeError: 帧不是合法 UTF-8.
        """
        messages = []
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if len(raw) > 128 * 1024:
                raise ValueError("远程消息超过 128 KiB")
            if not raw:
                continue
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("远程消息必须为 JSON 对象")
            messages.append(message)
        if len(buffer) > 128 * 1024:
            raise ValueError("远程消息超过 128 KiB")
        return buffer, messages

    def _close_socket(self):
        """尽力 shutdown/close 当前连接并清空引用,忽略关闭阶段的 OSError."""
        # stop 与 run 的 finally 可同时到达;IO 释放 GIL 后不能再次解引用共享字段.
        current, self._sock = self._sock, None
        if current:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                current.close()
            except OSError:
                pass


class RemoteControlServer(RemoteControlBase):
    """单控制端设备端监听线程:认证后转交消息,不执行串口或文件操作.

    每次 accept 创建新会话,断线发送 connected_changed(False),由主窗口
    取消该连接持有的任务.监听地址与端口由调用方明确提供,不从 IP 推断.
    listening 仅在 bind/listen 成功后为 True,退出时清除,供调用方确认启动结果.
    """

    def __init__(self, host="0.0.0.0", port=8765, token=""):
        """初始化设备端监听参数,调用 start 后才绑定 socket.

        Args:
            host (str): 本机监听地址,默认所有 IPv4 接口;离线测试使用回环地址.
            port (int): 配置的 TCP 端口,默认 8765;测试可传 0 申请临时端口.
            token (str): 共享口令,默认空字符串;必须与控制端配置一致.
        """
        super().__init__(token)
        self.host = host
        self.port = int(port)
        self.listening = False

    def run(self):
        """绑定监听地址,顺序服务一个控制端并执行现有口令认证.

        每次接受连接生成 session;认证后才分发业务消息.接收断线通知主窗口
        取消所属任务,循环结束时清理连接与监听 socket.异常不自动重放消息.
        """
        server_sock = None
        client_sock = None
        self.is_running = True
        authed = False
        buffer = b""

        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            self.port = server_sock.getsockname()[1]
            server_sock.listen(1)
            self.listening = True
            server_sock.settimeout(0.2)
            self.status_changed.emit(f"远程控制服务已启动，端口 {self.port}")

            while self.is_running:
                if client_sock is None:
                    try:
                        client_sock, address = server_sock.accept()
                        client_sock.settimeout(0.05)
                        self._sock = client_sock
                        self.session = uuid.uuid4().hex
                        authed = False
                        buffer = b""
                        self.status_changed.emit(f"控制端已连接: {address[0]}:{address[1]}")
                        if not self.token:
                            authed = True
                            self.connected_changed.emit(True, self.session)
                            self._send_message(client_sock, {"type": "status", "message": "远程串口已连接"})
                    except socket.timeout:
                        continue

                try:
                    chunk = client_sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("控制端已断开")
                    buffer += chunk
                    buffer, messages = self._process_incoming(buffer)
                    for message in messages:
                        if not authed:
                            if message.get("type") == "hello" and message.get("token", "") == self.token:
                                authed = True
                                self.connected_changed.emit(True, self.session)
                                self._send_message(client_sock, {"type": "status", "message": "远程串口已连接"})
                            else:
                                self._send_message(client_sock, {
                                    "type": "error",
                                    "message": "远程控制密码错误",
                                    "fatal": True
                                })
                                raise ConnectionError("远程控制密码错误")
                        else:
                            self._handle_message(message)
                    if authed:
                        self._drain_send_queue(client_sock)
                except socket.timeout:
                    if authed:
                        self._drain_send_queue(client_sock)
                except Exception as exc:
                    if not self.is_running:
                        break
                    self.error_occurred.emit(f"控制端连接中断: {exc}", False)
                    self.connected_changed.emit(False, self.session)
                    try:
                        client_sock.close()
                    except OSError:
                        pass
                    client_sock = None
                    self._sock = None
                    authed = False
                    time.sleep(0.1)

        except Exception as exc:
            if self.is_running:
                self.error_occurred.emit(f"远程控制服务错误: {exc}", True)
        finally:
            self.listening = False
            self.connected_changed.emit(False, self.session)
            if client_sock:
                try:
                    client_sock.close()
                except OSError:
                    pass
            if server_sock:
                try:
                    server_sock.close()
                except OSError:
                    pass
            self._sock = None
            self.is_running = False


class RemoteControlClient(RemoteControlBase):
    """控制端连接线程:等待设备端认证后发送队列,断线不自动重连或重放.

    TCP 建连不代表串口打开;connected_changed 仅报告网络认证状态,实际
    设备操作结果由上层 device_result / firmware result 回执解释.
    """

    def __init__(self, host, port=8765, token=""):
        """初始化控制端连接参数,不从 IP 推断端口或设备配置.

        Args:
            host (str): 设备端电脑的主机名或 IP,由用户现有配置提供.
            port (int): 设备端实际监听的 TCP 端口,默认 8765.
            token (str): 共享口令,默认空字符串;必须与设备端配置一致.
        """
        super().__init__(token)
        self.host = host
        self.port = int(port)

    def run(self):
        """连接配置的设备端并提交口令,循环收发当前会话消息.

        连接超时五秒;连接结束通知 GUI 并清理 socket,不自动重连或重发请求.
        """
        self.is_running = True
        buffer = b""

        try:
            sock = socket.create_connection((self.host, self.port), timeout=5)
            sock.settimeout(0.05)
            self._sock = sock
            self.session = uuid.uuid4().hex
            self._send_message(sock, {"type": "hello", "token": self.token})
            self.status_changed.emit(f"已建立 TCP 连接，等待设备端认证: {self.host}:{self.port}")
            authed = False

            while self.is_running:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("远程串口连接已断开")
                    buffer += chunk
                    buffer, messages = self._process_incoming(buffer)
                    for message in messages:
                        if message.get("type") == "status" and message.get("message") == "远程串口已连接" and not authed:
                            authed = True
                            self.connected_changed.emit(True, self.session)
                        if message.get("type") == "error":
                            self._handle_message(message)
                            if message.get("fatal", False):
                                self.is_running = False
                                break
                            continue
                        self._handle_message(message)
                    if not self.is_running:
                        break
                    if authed:
                        self._drain_send_queue(sock)
                except socket.timeout:
                    if authed:
                        self._drain_send_queue(sock)

        except Exception as exc:
            if self.is_running:
                self.error_occurred.emit(f"远程串口错误: {exc}", True)
        finally:
            self.connected_changed.emit(False, self.session)
            self._close_socket()
            self.is_running = False
