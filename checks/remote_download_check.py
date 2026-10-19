"""远程下载离线回归:只使用假串口,临时文件和 127.0.0.1,不连接真实设备."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "serial_tool_project"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from core.remote_control import RemoteControlBase, RemoteControlServer, RemoteControlClient
from core.remote_firmware import RemoteFirmwareHost, RemoteFirmwareClient, MAX_SIZE
from core.firmware_worker import FirmwareDownloadWorker
from firmware_downloader_project.core.downloader import FirmwareDownloader
from firmware_downloader_project.core.protocol import ProtocolHandler

APP = QApplication.instance() or QApplication([])


def pump_until(predicate, timeout=4):
    """泵送 Qt 事件直到 predicate 成立;timeout 为秒,超时断言失败."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    if not predicate():
        raise AssertionError("offline operation timed out")


def config(**updates):
    """创建固定 ACK 的下载配置,再应用 overrides 指定的故障或协议参数."""
    value = dict(packet_size=4, start_command="S", wait_start_ack=True,
                 start_ack_timeout=150, start_ack_config=dict(check_data=True, expected_data="OK", data_format="ASCII"),
                 wait_packet_ack=True, packet_ack_timeout=150,
                 packet_ack_config=dict(check_data=True, expected_data="OK", data_format="ASCII"))
    value.update(updates)
    return value


PORT = dict(port="FAKE", baudrate=115200, bytesize=8, parity="N", stopbits=1)


class FakeSerial:
    """确定性假设备:记录每次写入,以分段 ACK/短写/超时注入协议故障.

    实现下载器和调试线程需要的最小 pySerial 接口;永远不调用操作系统串口.
    writes 可证明没有自动重发;closed 可检查最终回执是否晚于资源释放.
    """
    def __init__(self, *args, ack=b"OK", short=False, read_size=1, **kwargs):
        """建立纯内存 pySerial 替身,允许注入 ACK 与读写故障.

        Args:
            *args: 接受但不使用的位置参数,便于替换 serial.Serial 构造器.
            ack (bytes): 完整写入后加入接收队列的应答,默认 b'OK'.
            short (bool): True 时模拟少写一个字节且不生成 ACK,默认 False.
            read_size (int): 每次最多读取的字节数,默认 1.
            **kwargs: 从 timeout 和 baudrate 读取模拟参数,缺省为 0.01 s 和
                115200;其余串口构造参数被忽略,不访问任何设备.
        """
        self.is_open = True
        self.timeout = kwargs.get("timeout", 0.01)
        self.ack = ack
        self.short = short
        self.read_size = read_size
        self.buffer = bytearray()
        self.writes = []
        self.closed = False
        self.baudrate = kwargs.get("baudrate", 115200)
        self.lock = threading.Lock()

    @property
    def in_waiting(self):
        """返回内存接收队列中尚未消费的字节数."""
        return len(self.buffer)

    def write(self, data):
        """加锁记录写入,按 short 开关返回短写或为完整写入生成 ACK."""
        with self.lock:
            self.writes.append(bytes(data))
            if self.short:
                return len(data) - 1
            self.buffer.extend(self.ack)
            return len(data)

    def read(self, size):
        """加锁读取限定长度的字节;无数据时短暂等待以模拟串口超时."""
        with self.lock:
            count = min(size, self.read_size, len(self.buffer))
            data = bytes(self.buffer[:count])
            del self.buffer[:count]
        if not data:
            time.sleep(min(self.timeout, 0.005))
        return data

    def reset_input_buffer(self):
        """清空假设备当前的接收队列."""
        self.buffer.clear()

    def cancel_read(self):
        """保留串口取消接口;假设备使用短读取等待,无需额外唤醒."""
        pass

    def close(self):
        """记录句柄已关闭,以验证最终结果和恢复动作的执行顺序."""
        self.closed = True
        self.is_open = False


class DownloaderTests(unittest.TestCase):
    """直接验证复用核心,确保本地下载和设备端下载有相同协议语义."""
    def setUp(self):
        """为每个下载核心用例创建独立的临时 BIN."""
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "app.bin"
        self.path.write_bytes(b"abcdefghij")

    def tearDown(self):
        """清理本例临时固件及目录."""
        self.temp.cleanup()

    def run_download(self, cfg=None, serial=None):
        """将串口构造替换为指定假设备,执行一次核心下载并关闭句柄."""
        downloader = FirmwareDownloader(PORT, cfg or config())
        downloader.serial_port = serial or FakeSerial()
        result = downloader.download(str(self.path))
        return downloader, result

    def test_fragmented_ack_and_last_ordinary_ack(self):
        """分段 ACK 累计后验证通过,末包使用普通确认."""
        downloader, result = self.run_download()
        self.assertTrue(result[0], result)
        self.assertEqual(downloader.serial_port.writes, [b"S", b"abcd", b"efgh", b"ij"])
        self.assertEqual(downloader.serial_port.buffer, b"")

    def test_crc_and_special_last_ack(self):
        """既有分包 CRC 和专用末包 ACK 能通过共享核心完成."""
        cfg = config(add_packet_crc=True, packet_crc_type="CRC16-MODBUS",
                     wait_last_packet_ack=True, last_packet_ack_timeout=100,
                     last_packet_ack_config=config()["packet_ack_config"])
        downloader, result = self.run_download(cfg)
        self.assertTrue(result[0])
        self.assertEqual(downloader.serial_port.writes[-1], ProtocolHandler.build_data_packet(b"ij", True, "CRC16-MODBUS"))

    def test_short_write_never_retried(self):
        """检测到短写后返回未知,原包不重发."""
        downloader, result = self.run_download(serial=FakeSerial(short=True))
        self.assertFalse(result[0])
        self.assertEqual(downloader.result_status, "unknown")
        self.assertEqual(len(downloader.serial_port.writes), 1)

    def test_partial_ack_timeout_is_unknown(self):
        """只到达部分 ACK 时超时返回未知,不误认作完整应答."""
        downloader, result = self.run_download(serial=FakeSerial(ack=b"O"))
        self.assertFalse(result[0])
        self.assertIn("ACK 超时", result[1])
        self.assertEqual(downloader.result_status, "unknown")

    def test_invalid_ack_is_not_success(self):
        """格式完整但内容错误的 ACK 不能通过下载确认."""
        downloader, result = self.run_download(serial=FakeSerial(ack=b"NO"))
        self.assertFalse(result[0])
        self.assertEqual(downloader.result_status, "unknown")

    def test_no_ack_and_end_without_ack_are_unknown(self):
        """缺少末包确认或结束命令没有 ACK 时不报告成功."""
        for cfg in (config(wait_packet_ack=False), config(send_end_string=True, end_string="E")):
            with self.subTest(cfg=cfg):
                downloader, result = self.run_download(cfg)
                self.assertFalse(result[0])
                self.assertEqual(downloader.result_status, "unknown")

    def test_cancel_before_start_does_not_write(self):
        """开始前取消保留到 download,不产生设备写入."""
        downloader = FirmwareDownloader(PORT, config())
        downloader.serial_port = FakeSerial()
        downloader.stop()
        self.assertFalse(downloader.download(str(self.path))[0])
        self.assertEqual(downloader.result_status, "cancelled")
        self.assertFalse(downloader.serial_port.writes)

    def test_cancel_during_ack_is_prompt(self):
        """ACK 等待可及时响应跨线程取消请求."""
        downloader = FirmwareDownloader(PORT, config(start_ack_timeout=10000))
        downloader.serial_port = FakeSerial(ack=b"")
        result = []
        worker = threading.Thread(target=lambda: result.append(downloader.download(str(self.path))))
        worker.start()
        time.sleep(0.02)
        start = time.monotonic()
        downloader.stop()
        worker.join(0.3)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertFalse(result[0][0])

    def test_coalesced_ack_bytes_preserved(self):
        """读取一帧 ACK 后保留缓冲中的后续帧."""
        downloader = FirmwareDownloader(PORT, config())
        fake = FakeSerial(read_size=999)
        fake.buffer.extend(b"OKOK")
        downloader.serial_port = fake
        self.assertTrue(downloader._wait_for_ack("first")[0])
        self.assertEqual(fake.buffer, b"OK")
        self.assertTrue(downloader._wait_for_ack("second")[0])

    def test_empty_and_invalid_config_do_not_write(self):
        """空 BIN 和无效协议参数在写设备前被拒绝."""
        self.path.write_bytes(b"")
        downloader, result = self.run_download()
        self.assertFalse(result[0])
        self.assertFalse(downloader.serial_port.writes)
        for cfg in (config(packet_size=0), config(packet_size=4097),
                    config(packet_ack_config=dict(check_crc=True, crc_type="CRC32"))):
            with self.assertRaises(ValueError):
                FirmwareDownloader.validate_config(cfg)

    def test_worker_closes_before_result_and_prestart_cancel(self):
        """工作线程在结果回传前关闭句柄,启动前取消不创建设备连接."""
        fake = FakeSerial()
        worker = FirmwareDownloadWorker(str(self.path), PORT, config())
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake):
            worker.run()
        self.assertTrue(worker.result[0])
        self.assertTrue(fake.closed)
        worker2 = FirmwareDownloadWorker(str(self.path), PORT, config())
        worker2.cancel()
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial") as constructor:
            worker2.run()
        constructor.assert_not_called()
        self.assertEqual(worker2.result_status, "cancelled")


class HostTests(unittest.TestCase):
    """验证设备端任务门禁,文件完整性,重复请求和断线资源回收."""
    def setUp(self):
        """创建带资源回调记录的设备端协调器和独立任务 ID."""
        self.messages = []
        self.released = []
        self.acquired = []
        self.host = RemoteFirmwareHost(lambda message, session: self.messages.append(message),
                                       lambda: (PORT, config()),
                                       lambda: self.acquired.append(True) or "restore",
                                       self.released.append, lambda *_: None)
        self.task_id = uuid.uuid4().hex
        self.data = b"abcdefghij"

    def tearDown(self):
        """取消并等待活动任务释放资源,再销毁设备端测试对象."""
        self.host.disconnect()
        if self.host.active and self.host.active["worker"]:
            pump_until(lambda: not self.host.busy)
        self.host.timer.stop()
        self.host.deleteLater()

    def send(self, action, **fields):
        """为当前任务补齐 ID,session 和动作后交给设备端协调器处理."""
        self.host.handle(dict(type="firmware", id=self.task_id, _session="test", action=action, **fields))

    def begin(self, **fields):
        """提交本例固件的名称,大小和 SHA-256,允许字段覆盖以测试校验."""
        params = dict(name="client.bin", size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())
        params.update(fields)
        self.send("begin", **params)

    def upload(self):
        """以单块 Base64 消息上传测试固件."""
        self.send("chunk", offset=0, data=base64.b64encode(self.data).decode())

    def test_integrity_failure_never_opens_device(self):
        """长度或哈希不匹配时拒绝 commit,不打开设备."""
        self.begin(sha256="0" * 64)
        path = self.host.active["path"]
        self.upload()
        self.send("commit")
        self.assertFalse(self.acquired)
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.messages[-1]["status"], "failed")

    def test_upload_size_and_offset_limits(self):
        """拒绝越界文件大小和不匹配的上传偏移."""
        for size in (0, MAX_SIZE + 1, -1, True):
            self.begin(size=size)
            self.assertFalse(self.host.busy)
        self.begin()
        self.send("chunk", offset=1, data="YQ==")
        self.assertFalse(self.host.busy)
        self.assertFalse(self.acquired)

    def test_busy_and_duplicate_chunks(self):
        """活动任务拒绝并行 begin,重复块终止原上传."""
        self.begin()
        self.begin()
        self.assertEqual(self.messages[-1]["event"], "running")
        self.upload()
        self.upload()
        self.assertFalse(self.host.busy)
        self.assertFalse(self.acquired)

    def test_disconnect_upload_removes_file(self):
        """上传连接断开后清理临时文件和占用状态."""
        self.begin()
        path = self.host.active["path"]
        self.host.disconnect()
        self.assertFalse(self.host.busy)
        self.assertFalse(Path(path).exists())
        self.assertFalse(self.acquired)

    def test_upload_timeout(self):
        """上传空闲到期时释放任务,尚未获取设备串口."""
        self.begin()
        self.host.active["updated"] -= 31
        self.host.tick()
        self.assertFalse(self.host.busy)
        self.assertFalse(self.acquired)

    def test_duplicate_commit_download_once_and_restore_after_close(self):
        """重复 commit 只下载一次,恢复回调在下载句柄关闭之后运行."""
        self.begin()
        self.upload()
        fake = FakeSerial()
        self.host.release = lambda state: self.assertTrue(fake.closed) or self.released.append(state)
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake) as constructor:
            self.send("commit")
            self.send("commit")
            pump_until(lambda: not self.host.busy)
            self.send("commit")
            self.begin()
        self.assertEqual(constructor.call_count, 1)
        self.assertEqual(self.released, ["restore"])
        self.assertEqual(self.messages[-1]["status"], "success")

    def test_disconnect_running_cancels_and_releases(self):
        """下载中断线会取消 ACK 等待,关闭句柄并保留未知终态."""
        self.host.prepare = lambda: (PORT, config(start_ack_timeout=10000))
        self.begin()
        self.upload()
        fake = FakeSerial(ack=b"")
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake):
            self.send("commit")
            pump_until(lambda: bool(fake.writes))
            self.host.disconnect()
            pump_until(lambda: not self.host.busy)
        self.assertTrue(fake.closed)
        self.assertEqual(self.released, ["restore"])
        self.assertEqual(self.host.results[self.task_id]["status"], "unknown")

    def test_acquire_failure_and_restore_failure(self):
        """获取串口失败时结束任务,不调用尚未获取资源的恢复函数."""
        self.host.acquire = mock.Mock(side_effect=RuntimeError("busy"))
        self.begin()
        self.upload()
        self.send("commit")
        self.assertFalse(self.host.busy)
        self.assertFalse(self.released)


class TransportTests(unittest.TestCase):
    """验证 TCP 字节流边界及真实回环网络上的上传/任务/结果闭环."""
    def test_client_stop_during_run_finally_cannot_escape_qthread(self):
        """真实 QThread 的 run/stop 关闭交错必须正常退出,原生故障隔离在子进程."""
        probe = textwrap.dedent('''
            import ctypes
            import os
            import threading
            from unittest import mock
            from PyQt5.QtCore import QCoreApplication
            from core.remote_control import RemoteControlClient

            if os.name == "nt":
                # 仅诊断子进程禁错误对话框；不改异常处理或机器设置，保留真实退出码。
                ctypes.windll.kernel32.SetErrorMode(3)
            app = QCoreApplication([])
            closing, stop_closed = threading.Event(), threading.Event()
            main_thread = threading.get_ident()
            closed_by = []

            class Socket:
                def settimeout(self, seconds):
                    pass
                def sendall(self, payload):
                    pass
                def recv(self, size):
                    return b""
                def shutdown(self, how):
                    if threading.get_ident() != main_thread:
                        closing.set()
                        if not stop_closed.wait(3):
                            raise RuntimeError("stop did not finish closing")
                def close(self):
                    closed_by.append(threading.get_ident())

            peer = RemoteControlClient("unused.invalid")
            original_wait = peer.wait
            def wait(milliseconds):
                # stop 已完成共享句柄的关闭部分，才允许 run 的 finally 继续。
                stop_closed.set()
                return original_wait(milliseconds)
            peer.wait = wait
            with mock.patch("core.remote_control.socket.create_connection", return_value=Socket()):
                peer.start()
                if not closing.wait(3):
                    raise RuntimeError("client did not enter finally shutdown")
                if not peer.stop():
                    raise RuntimeError("client did not finish")
            assert peer.isFinished() and not peer.is_running
            assert peer._sock is None
            assert len(closed_by) == 1 and closed_by[0] != main_thread, closed_by
            print("normal QThread exit; captured socket closed once by worker")
        ''')
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", probe],
            cwd=ROOT / "serial_tool_project", capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("normal QThread exit", result.stdout)

    def test_socket_close_keeps_captured_handle_during_shutdown_races(self):
        """shutdown 期间共享字段可能清空或被新连接替换,只能关闭已捕获句柄."""
        transport = RemoteControlBase()
        handle = mock.Mock()
        transport._sock = handle
        handle.shutdown.side_effect = lambda *_: setattr(transport, "_sock", None)
        transport._close_socket()
        handle.close.assert_called_once()
        old_handle, new_handle = mock.Mock(), mock.Mock()
        transport._sock = old_handle
        old_handle.shutdown.side_effect = lambda *_: setattr(transport, "_sock", new_handle)
        transport._close_socket()
        old_handle.close.assert_called_once()
        new_handle.close.assert_not_called()
        self.assertIs(transport._sock, new_handle)
        transport._close_socket()
        new_handle.close.assert_called_once()
        self.assertIsNone(transport._sock)
        transport._close_socket()
        handle.close.assert_called_once()

    def test_split_coalesced_and_oversized_frames(self):
        """分段及粘连 JSON 能正确解析,超长或非对象帧被拒绝."""
        transport = RemoteControlBase()
        buffer, messages = transport._process_incoming(b'{"type":"status","message":"a"}\n{"type":')
        self.assertEqual(len(messages), 1)
        buffer, messages = transport._process_incoming(buffer + b'"status","message":"b"}\n')
        self.assertEqual(messages[0]["message"], "b")
        self.assertEqual(buffer, b"")
        with self.assertRaises(ValueError):
            transport._process_incoming(b"x" * (128 * 1024 + 1))
        with self.assertRaises(ValueError):
            transport._process_incoming(b"[]\n")

    def test_stale_queue_never_replayed(self):
        """旧 session 的出站消息不会发给新连接."""
        transport = RemoteControlBase()
        transport.session = "old"
        transport._queue_message({"type": "serial_data", "data": "YQ=="})
        transport.session = "new"
        sock = mock.Mock()
        transport._drain_send_queue(sock)
        sock.sendall.assert_not_called()

    def test_client_disconnect_before_and_after_commit(self):
        """提交前断线是上传失败,提交后断线必须说明结果未知."""
        for committed in (False, True):
            client = RemoteFirmwareClient(lambda _: None)
            result = []
            client.finished.connect(lambda ok, message: result.append((ok, message)))
            client.active = dict(committed=committed)
            client.disconnect()
            self.assertFalse(result[0][0])
            self.assertEqual("结果未知" in result[0][1], committed)

    def test_loopback_upload_download_and_cancel(self):
        """真实回环 TCP 覆盖多块上传,正常下载及取消,串口始终为假设备."""
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "app.bin"
                path.write_bytes(b"a" * 33000)  # 覆盖多个网络上传分块
                # 只绑定回环地址; 测试不使用局域网地址或实际串口.
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
                listener.close()
                server = RemoteControlServer("127.0.0.1", port, "offline")
                peer = RemoteControlClient("127.0.0.1", port, "offline")
                connected = []
                peer.connected_changed.connect(lambda value: connected.append(value))
                host = RemoteFirmwareHost(
                    lambda message, session: server._queue_message(message),
                    lambda: (PORT, config(packet_size=4096, start_ack_timeout=10000 if cancel else 150)),
                    lambda: None, lambda _: None, lambda *_: None)
                client = RemoteFirmwareClient(peer._queue_message)
                server.message_received.connect(host.handle)
                peer.message_received.connect(client.handle)
                server.connected_changed.connect(lambda value: None if value else host.disconnect())
                peer.connected_changed.connect(lambda value: None if value else client.disconnect())
                finished = []
                client.finished.connect(lambda ok, message: finished.append((ok, message)))
                fake = FakeSerial(ack=b"" if cancel else b"OK")
                try:
                    with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake):
                        server.start()
                        QTest.qWait(70)
                        peer.start()
                        pump_until(lambda: True in connected)
                        client.start(str(path))
                        if cancel:
                            pump_until(lambda: bool(fake.writes))
                            client.cancel()
                        pump_until(lambda: bool(finished), 8)
                        self.assertEqual(finished[0][0], not cancel, finished)
                        self.assertTrue(fake.closed)
                        self.assertFalse(host.busy)
                finally:
                    peer.stop()
                    server.stop()
                    host.disconnect()
                    pump_until(lambda: not host.busy)
                    host.timer.stop()
                    client.timer.stop()
                    # 局部信号对象离开作用域前先排空跨线程 Qt 回调.
                    QTest.qWait(50)
                    server.message_received.disconnect()
                    peer.message_received.disconnect()
                    server.connected_changed.disconnect()
                    peer.connected_changed.disconnect()
                    host.deleteLater()
                    client.deleteLater()
                    server.deleteLater()
                    peer.deleteLater()
                    QTest.qWait(20)


class GuiIntegrationTests(unittest.TestCase):
    """验证实际窗口入口,统一占用和连续命令回执;配置写入隔离临时目录."""
    def setUp(self):
        """创建临时配置窗口和模拟远程会话,避免修改用户配置."""
        from main_window import SerialTool
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.window = SerialTool(exe_name="offline")
        self.window.remote_thread = mock.Mock(session="session")
        self.window.remote_mode = "server"
        self.window.remote_client_connected = True

    def tearDown(self):
        """等待任务结束并销毁窗口,恢复原工作目录并删除临时配置."""
        self.window.remote_firmware_host.disconnect()
        pump_until(lambda: not self.window.remote_firmware_host.busy)
        self.window.firmware_download_running = False
        self.window.remote_thread = None
        self.window.remote_mode = "off"
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        os.chdir(self.cwd)
        self.temp.cleanup()

    def test_all_serial_mutations_rejected_during_upload(self):
        """上传预留期间所有冲突串口操作被拒绝,已有任务不被新请求清理."""
        self.window.remote_firmware_host.prepare = lambda: (PORT, config())
        self.window.remote_firmware_host.handle(dict(id=uuid.uuid4().hex, _session="session", action="begin",
            name="x.bin", size=1, sha256=hashlib.sha256(b"a").hexdigest()))
        for action in ("open", "close", "baudrate", "comport", "write"):
            self.window._execute_remote_device(dict(id=uuid.uuid4().hex, _session="session",
                action=action, config=dict(value=9600, port="FAKE", data="YQ==")))
            reply = self.window.remote_thread._queue_message.call_args[0][0]
            self.assertFalse(reply["success"], reply)
        self.assertFalse(self.window.open_serial())
        self.assertFalse(self.window.close_serial())
        self.assertEqual(self.window._write_serial_data(b"x"), 0)
        self.assertFalse(self.window.start_firmware_download_command("absent.bin"))

    def test_comport_remote_path_and_result_wait(self):
        """ComPort 经远程请求执行,命令链等待对应回执再继续."""
        window = self.window
        window.remote_mode = "client"
        window.remote_capabilities = dict(device_results=1)
        self.assertTrue(window.update_com_port("COM99"))
        request = window.remote_thread._queue_message.call_args[0][0]
        self.assertEqual(request["action"], "comport")
        completed = []
        window.schedule_command_next(0, lambda: completed.append(True))
        QTest.qWait(30)
        self.assertFalse(completed)
        window.on_remote_message(dict(type="device_result", _session="session", id=request["id"],
                                      success=True, message="applied"))
        pump_until(lambda: bool(completed))

    def test_failed_device_result_stops_sequence(self):
        """设备失败回执使当前命令链停止."""
        window = self.window
        window.remote_mode = "client"
        window.remote_capabilities = dict(device_results=1)
        self.assertTrue(window.update_baudrate(9600))
        request = window.remote_thread._queue_message.call_args[0][0]
        completed = []
        window.schedule_command_next(0, lambda: completed.append(True))
        window.on_remote_message(dict(type="device_result", _session="session", id=request["id"],
                                      success=False, message="busy"))
        QTest.qWait(40)
        self.assertFalse(completed)

    def test_device_write_attempts_once_per_frame(self):
        """每帧只尝试一次,不重试短写/异常,相同 ID 的新帧也不是历史回放."""
        window = self.window
        window.serial_thread = mock.Mock()
        request = dict(id=uuid.uuid4().hex, _session="session", action="write", config=dict(data="YQ=="))
        for outcome in (1, 0, OSError("offline write failed")):
            with self.subTest(outcome=outcome):
                window.serial_thread.write_data.reset_mock()
                window.serial_thread.write_data.side_effect = (
                    outcome if isinstance(outcome, Exception) else lambda data, count=outcome: count)
                window._execute_remote_device(request)
                window.serial_thread.write_data.assert_called_once_with(b"a")
                window._execute_remote_device(request)
                self.assertEqual(window.serial_thread.write_data.call_count, 2)
        window.serial_thread = None

    def test_disconnected_firmware_does_not_open_local_device(self):
        """远程未连接时下载被拒绝,不转为操作本机串口."""
        path = Path(self.temp.name) / "app.bin"
        path.write_bytes(b"a")
        self.window.remote_mode = "client"
        self.window.remote_client_connected = False
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial") as constructor:
            self.assertFalse(self.window.start_firmware_download_command(str(path)))
        constructor.assert_not_called()

    def test_button_uses_same_entry(self):
        """固件窗口确认开始后,才与脚本调用同一个下载入口."""
        from dialogs.firmware_download_dialog import ManagedFirmwareDownloadDialog
        from PyQt5.QtWidgets import QDialog
        path = str(Path(self.temp.name) / "client.bin")
        self.window.remote_mode = "client"
        def edit_and_start(dialog):
            start.assert_not_called()
            dialog.file_path_edit.setText(path)
            dialog.download_btn.click()
            dialog._running = False
            return QDialog.Rejected
        with mock.patch.object(ManagedFirmwareDownloadDialog, "exec_", edit_and_start), \
             mock.patch("PyQt5.QtWidgets.QFileDialog.getOpenFileName") as choose, \
             mock.patch.object(self.window, "start_firmware_download_command") as start:
            self.window.open_firmware_downloader()
        choose.assert_not_called()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0], path)
        self.assertEqual(start.call_args.kwargs, {"show_progress": False})


if __name__ == "__main__":
    unittest.main()
