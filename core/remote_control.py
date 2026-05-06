#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
远程控制模块, 负责基于局域网 TCP 的主控端和远程端串口数据转发。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

import base64
import json
import queue
import socket
import time

from PyQt5.QtCore import QThread, pyqtSignal


class RemoteControlBase(QThread):
    """TCP remote serial transport using newline-delimited JSON messages."""
    data_received = pyqtSignal(bytes)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str, bool)
    connected_changed = pyqtSignal(bool)
    baudrate_requested = pyqtSignal(int)
    serial_config_received = pyqtSignal(dict)
    serial_control_requested = pyqtSignal(str, dict)

    def __init__(self, token=""):
        super().__init__()
        self.token = token or ""
        self.is_running = False
        self._send_queue = queue.Queue()
        self._sock = None

    def send_serial_data(self, data):
        self._queue_message({
            "type": "serial_data",
            "data": base64.b64encode(data).decode("ascii")
        })

    def send_baudrate(self, baudrate):
        self._queue_message({
            "type": "baudrate",
            "value": int(baudrate)
        })

    def send_serial_config(self, config):
        self._queue_message({
            "type": "serial_config",
            "config": dict(config)
        })

    def send_error(self, message, fatal=False):
        self._queue_message({
            "type": "error",
            "message": message,
            "fatal": bool(fatal)
        })

    def send_serial_control(self, action, config=None):
        self._queue_message({
            "type": "serial_control",
            "action": action,
            "config": dict(config or {})
        })

    def stop(self):
        self.is_running = False
        self._close_socket()
        self.wait(1000)

    def _queue_message(self, message):
        self._send_queue.put(message)

    def _send_message(self, sock, message):
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        sock.sendall(payload)

    def _drain_send_queue(self, sock):
        while True:
            try:
                message = self._send_queue.get_nowait()
            except queue.Empty:
                return
            self._send_message(sock, message)

    def _handle_message(self, message):
        msg_type = message.get("type")
        if msg_type == "serial_data":
            encoded = message.get("data", "")
            self.data_received.emit(base64.b64decode(encoded))
        elif msg_type == "baudrate":
            self.baudrate_requested.emit(int(message.get("value")))
        elif msg_type == "serial_config":
            config = message.get("config", {})
            if isinstance(config, dict):
                self.serial_config_received.emit(config)
        elif msg_type == "serial_control":
            action = str(message.get("action", ""))
            config = message.get("config", {})
            self.serial_control_requested.emit(action, config if isinstance(config, dict) else {})
        elif msg_type == "status":
            text = message.get("message", "")
            if text:
                self.status_changed.emit(text)
        elif msg_type == "error":
            text = message.get("message", "")
            if text:
                self.error_occurred.emit(text, bool(message.get("fatal", False)))

    def _process_incoming(self, buffer):
        messages = []
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if not raw:
                continue
            messages.append(json.loads(raw.decode("utf-8")))
        return buffer, messages

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class RemoteControlServer(RemoteControlBase):
    """Listens for one LAN client and forwards serial data."""

    def __init__(self, host="0.0.0.0", port=8765, token=""):
        super().__init__(token)
        self.host = host
        self.port = int(port)

    def run(self):
        server_sock = None
        client_sock = None
        self.is_running = True
        authed = False
        buffer = b""

        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((self.host, self.port))
            server_sock.listen(1)
            server_sock.settimeout(0.2)
            self.status_changed.emit(f"远程控制服务已启动，端口 {self.port}")

            while self.is_running:
                if client_sock is None:
                    try:
                        client_sock, address = server_sock.accept()
                        client_sock.settimeout(0.05)
                        self._sock = client_sock
                        authed = False
                        buffer = b""
                        self.status_changed.emit(f"远程客户端已连接: {address[0]}:{address[1]}")
                        if not self.token:
                            authed = True
                            self.connected_changed.emit(True)
                            self._send_message(client_sock, {"type": "status", "message": "远程串口已连接"})
                    except socket.timeout:
                        continue

                try:
                    chunk = client_sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("远程客户端已断开")
                    buffer += chunk
                    buffer, messages = self._process_incoming(buffer)
                    for message in messages:
                        if not authed:
                            if message.get("type") == "hello" and message.get("token", "") == self.token:
                                authed = True
                                self.connected_changed.emit(True)
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
                    self.status_changed.emit(str(exc))
                    self.connected_changed.emit(False)
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
            self.connected_changed.emit(False)
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
    """Connects to a remote serial server."""

    def __init__(self, host, port=8765, token=""):
        super().__init__(token)
        self.host = host
        self.port = int(port)

    def run(self):
        self.is_running = True
        buffer = b""

        try:
            sock = socket.create_connection((self.host, self.port), timeout=5)
            sock.settimeout(0.05)
            self._sock = sock
            self._send_message(sock, {"type": "hello", "token": self.token})
            self.status_changed.emit(f"已建立 TCP 连接，等待远程主控端认证: {self.host}:{self.port}")
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
                            self.connected_changed.emit(True)
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
            self.connected_changed.emit(False)
            self._close_socket()
            self.is_running = False
