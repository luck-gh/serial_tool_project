"""远程与固件契约的离线检查, 不访问实际串口或局域网设备.

在 serial_tool_project 根目录运行:
    python -m unittest discover -s checks -p 'remote_capability_check.py' -v
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "serial_tool_project"))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from core.remote_control import RemoteControlBase, RemoteControlClient, RemoteControlServer
from core.remote_firmware import RemoteFirmwareClient, RemoteFirmwareHost, CHUNK_SIZE
from firmware_downloader_project.core.downloader import FirmwareDownloader

APP = QApplication.instance() or QApplication([])


def pump_until(predicate, timeout=3):
    """在超时内泵送 Qt 事件直到 predicate 为真,否则断言失败;timeout 单位为秒."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.005)
    assert predicate(), "offline operation did not finish"


def ack_config(**overrides):
    """创建固定 OK 应答的最小下载配置,按 overrides 覆盖指定字段."""
    config = dict(packet_size=2, start_command="", wait_packet_ack=True,
                  packet_ack_timeout=80,
                  packet_ack_config=dict(check_data=True, expected_data="OK", data_format="ASCII"))
    config.update(overrides)
    return config


class FakeDevice:
    """纯内存假串口,记录写入并按配置生成 ACK,支持短读,短写和等待故障.

    不会打开物理串口;closed 和 read_started 用于验证关闭顺序与取消时机.
    """
    def __init__(self, reply=b"OK", read_size=1, short_write=False):
        """初始化可控故障的内存串口.

        Args:
            reply (bytes): 每次写入后加入接收队列的应答,默认 b'OK'.
            read_size (int): 每次 read 最多返回的字节数,默认 1.
            short_write (bool): 是否返回少一个字节的写入计数,默认 False.
        """
        self.is_open = True
        self.reply = reply
        self.read_size = read_size
        self.short_write = short_write
        self.buffer = bytearray()
        self.writes = []
        self.timeout = .01
        self.closed = False
        self.read_started = threading.Event()

    def write(self, data):
        """记录原始写入并加入配置的应答,按短写开关返回实际模拟计数."""
        self.writes.append(bytes(data))
        self.buffer.extend(self.reply)
        return len(data) - 1 if self.short_write else len(data)

    def read(self, size):
        """标记读取已开始并返回限制长度的缓冲字节;空缓冲模拟短暂阻塞."""
        self.read_started.set()
        if not self.buffer:
            time.sleep(min(self.timeout, .01))
        count = min(size, self.read_size, len(self.buffer))
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def close(self):
        """将内存句柄置为关闭状态,供资源释放断言使用."""
        self.is_open = False
        self.closed = True


class OfflineCase(unittest.TestCase):
    """离线用例基类,使用临时 BIN 并禁止真实串口构造及端口枚举."""
    def setUp(self):
        """创建四字节 BIN 和临时目录,安装物理串口及枚举保护."""
        self.temp = tempfile.TemporaryDirectory()
        self.bin = Path(self.temp.name) / "client.bin"
        self.bin.write_bytes(b"abcd")
        self.serial_guard = patch("serial.Serial", side_effect=AssertionError("physical serial forbidden"))
        self.serial_guard.start()
        self.ports_guard = patch("serial.tools.list_ports.comports", return_value=[])
        self.ports_guard.start()

    def tearDown(self):
        """撤销串口与枚举替身,清理临时文件."""
        self.ports_guard.stop()
        self.serial_guard.stop()
        self.temp.cleanup()


class DownloadChecks(OfflineCase):
    """共享下载核心的设备确认,边界和取消契约检查."""
    def downloader(self, device, **config):
        """用指定假设备创建核心,合并配置覆盖项,不调用 open_port."""
        downloader = FirmwareDownloader(dict(port="FAKE"), ack_config(**config))
        downloader.serial_port = device
        return downloader

    def test_local_short_reads_and_final_packet_ack(self):
        """分段 ACK 能组成完整帧,最后一包也必须通过普通确认."""
        device = FakeDevice()
        dl = self.downloader(device)
        progress = []
        self.assertTrue(dl.download(str(self.bin), lambda *args: progress.append(args))[0])
        self.assertEqual(device.writes, [b"ab", b"cd"])
        self.assertEqual(device.buffer, b"")
        self.assertEqual(progress[-1][:2], (2, 2))

    def test_final_ack_timeout_does_not_report_success_or_retry(self):
        """末包缺少应答时返回未知,写入不重试."""
        device = FakeDevice(reply=b"OK")
        dl = self.downloader(device)
        original_write = device.write
        def write(data):
            """只给首包 ACK,刻意让最后一包等待超时."""
            if device.writes:
                device.reply = b""
            return original_write(data)
        device.write = write
        self.assertFalse(dl.download(str(self.bin))[0])
        self.assertEqual(dl.result_status, "unknown")
        self.assertEqual(len(device.writes), 2)

    def test_short_write_stops_without_retry(self):
        """短写立刻终止下载,不再次发送该包."""
        device = FakeDevice(short_write=True)
        dl = self.downloader(device)
        self.assertFalse(dl.download(str(self.bin))[0])
        self.assertEqual(dl.result_status, "unknown")
        self.assertEqual(device.writes, [b"ab"])

    def test_cancel_during_ack_is_prompt(self):
        """等待 ACK 时取消能在短读取周期内生效."""
        device = FakeDevice(reply=b"")
        dl = self.downloader(device, packet_ack_timeout=600000)
        worker = threading.Thread(target=dl.download, args=(str(self.bin),))
        worker.start()
        self.assertTrue(device.read_started.wait(1))
        started = time.monotonic()
        dl.stop()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, .3)
        self.assertEqual(dl.result_status, "unknown")
        self.assertEqual(len(device.writes), 1)

    def test_cancel_before_start_never_writes(self):
        """启动前的取消标志被保留,不发生设备写入."""
        device = FakeDevice()
        dl = self.downloader(device)
        dl.stop()
        self.assertFalse(dl.download(str(self.bin))[0])
        self.assertEqual(dl.result_status, "cancelled")
        self.assertFalse(device.writes)

    def test_ack_coalescing_consumes_only_current_frame(self):
        """粘在一起的 ACK 按单帧长度读取,不吞掉后续帧."""
        device = FakeDevice(read_size=100)
        device.buffer.extend(b"OKNEXT")
        dl = self.downloader(device)
        self.assertTrue(dl._wait_for_ack("test")[0])
        self.assertEqual(device.buffer, b"NEXT")

    def test_distinct_final_ack_and_start_ack(self):
        """开始,普通和专用末包 ACK 分别使用对应配置."""
        device = FakeDevice()
        responses = iter((b"READY", b"OK", b"END"))
        original_write = device.write
        def write(data):
            """按开始命令与包顺序注入不同的 ACK 数据."""
            device.reply = next(responses)
            return original_write(data)
        device.write = write
        dl = self.downloader(device, start_command="begin", wait_start_ack=True,
                             start_ack_timeout=80,
                             start_ack_config=dict(check_data=True, expected_data="READY", data_format="ASCII"),
                             wait_last_packet_ack=True, last_packet_ack_timeout=80,
                             last_packet_ack_config=dict(check_data=True, expected_data="END", data_format="ASCII"))
        self.assertTrue(dl.download(str(self.bin))[0])
        self.assertEqual(device.writes, [b"begin", b"ab", b"cd"])

    def test_no_confirmation_is_unknown(self):
        """未配置有效末包确认时不能报告成功."""
        dl = self.downloader(FakeDevice(), wait_packet_ack=False)
        self.assertFalse(dl.download(str(self.bin))[0])
        self.assertEqual(dl.result_status, "unknown")

    def test_unconfirmed_end_command_is_unknown(self):
        """结束命令无完成确认契约时,即使包 ACK 通过也返回未知."""
        dl = self.downloader(FakeDevice(), send_end_string=True, end_string="end")
        self.assertFalse(dl.download(str(self.bin))[0])
        self.assertEqual(dl.result_status, "unknown")

    def test_bad_ack_and_invalid_config(self):
        """错误 ACK 与不合法配置不会导致虚假成功."""
        dl = self.downloader(FakeDevice(reply=b"NO"))
        self.assertFalse(dl.download(str(self.bin))[0])
        for config in (dict(packet_size=0), dict(packet_ack_timeout=0),
                       dict(packet_ack_config=dict(check_crc=True)),
                       dict(packet_ack_config=dict(check_data=True, expected_data=""))):
            with self.subTest(config=config), self.assertRaises(ValueError):
                FirmwareDownloader.validate_config(ack_config(**config))


class FramingChecks(unittest.TestCase):
    """换行 JSON 流的分帧,长度限制和旧会话隔离检查."""
    def test_split_and_coalesced_json(self):
        """半帧保留到下一次解析,多帧一次到达时顺序拆分."""
        bridge = RemoteControlBase()
        data = b'{"type":"status","message":"one"}\n{"type":"status","message":"two"}\n'
        for split in range(len(data) + 1):
            tail, first = bridge._process_incoming(data[:split])
            tail, last = bridge._process_incoming(tail + data[split:])
            self.assertEqual([m["message"] for m in first + last], ["one", "two"])
            self.assertEqual(tail, b"")

    def test_invalid_and_oversized_frames(self):
        """非字典消息,损坏 JSON 和过大帧被拒绝."""
        bridge = RemoteControlBase()
        for raw in (b"[]\n", b"{bad}\n", b"x" * (128 * 1024 + 1)):
            with self.subTest(raw=raw[:10]), self.assertRaises(ValueError):
                bridge._process_incoming(raw)

    def test_old_session_queue_is_not_replayed(self):
        """切换 session 后旧连接待发消息不进入新连接."""
        bridge = RemoteControlBase()
        bridge.session = "old"
        bridge._queue_message(dict(type="device_request"))
        bridge.session = "new"
        sock = Mock()
        bridge._drain_send_queue(sock)
        sock.sendall.assert_not_called()


class UploadChecks(OfflineCase):
    """设备端上传任务的完整性,去重,占用和资源释放检查."""
    def setUp(self):
        """建立使用假回传函数的设备端协调器及可观察的资源回调."""
        super().setUp()
        self.messages = []
        self.acquire = Mock(return_value="restore")
        self.release = Mock()
        self.host = RemoteFirmwareHost(lambda m, s: self.messages.append(m),
                                      lambda: (dict(port="FAKE"), ack_config()),
                                      self.acquire, self.release, lambda *_: None)
        self.task_id = "a" * 32

    def tearDown(self):
        """取消并等待设备端任务结束,再清理临时文件和测试替身."""
        self.host.cancel()
        if self.host.active and self.host.active["worker"]:
            pump_until(lambda: self.host.active is None)
        self.host.timer.stop()
        super().tearDown()

    def request(self, action, **fields):
        """给本例任务和会话构造消息,再合并 fields 中的动作参数."""
        self.host.handle(dict(type="firmware", id=self.task_id, _session="s", action=action, **fields))

    def begin(self):
        """提交已知 BIN 的 begin 消息,允许 fields 覆盖边界测试参数."""
        self.request("begin", name="../../client.bin", size=4, sha256=hashlib.sha256(b"abcd").hexdigest())

    def upload(self):
        """把当前测试 BIN 作为一块上传到已准备的设备端任务."""
        self.begin()
        self.request("chunk", offset=0, data=base64.b64encode(b"abcd").decode())

    def test_upload_cancel_cleans_file_without_opening_device(self):
        """上传阶段取消只清理临时文件,不获取设备串口."""
        self.begin()
        path = self.host.active["path"]
        self.request("cancel")
        self.assertFalse(os.path.exists(path))
        self.acquire.assert_not_called()
        self.assertEqual(self.messages[-1]["status"], "cancelled")

    def test_hash_failure_never_acquires(self):
        """哈希错误在获取串口前终止."""
        self.begin()
        self.request("chunk", offset=0, data=base64.b64encode(b"xxxx").decode())
        self.request("commit")
        self.acquire.assert_not_called()
        self.assertEqual(self.messages[-1]["status"], "failed")

    def test_duplicate_chunk_aborts_without_device_retry(self):
        """重复偏移块中断上传,不进入设备下载."""
        self.upload()
        self.request("chunk", offset=0, data=base64.b64encode(b"abcd").decode())
        self.acquire.assert_not_called()
        self.assertIsNone(self.host.active)

    def test_disconnect_upload_and_idle_timeout(self):
        """上传断线及空闲超时都能清理任务和文件."""
        self.begin()
        path = self.host.active["path"]
        self.host.disconnect()
        self.assertFalse(os.path.exists(path))
        self.task_id = "b" * 32
        self.begin()
        self.host.active["updated"] -= 31
        self.host.tick()
        self.assertIsNone(self.host.active)
        self.acquire.assert_not_called()

    def test_port_conflict_and_restore_failure(self):
        """占用冲突被拒绝,调试恢复失败不能作为整体成功."""
        self.upload()
        self.acquire.side_effect = RuntimeError("busy")
        self.request("commit")
        self.assertEqual(self.messages[-1]["status"], "failed")
        self.release.assert_not_called()

    def test_duplicate_commit_and_completed_request_never_redownload(self):
        """重复提交及完成 ID 重放只返回原状态,不重复启动下载."""
        device = FakeDevice()
        with patch("serial.Serial", return_value=device):
            self.upload()
            self.request("commit")
            self.request("commit")
            pump_until(lambda: self.host.active is None)
            self.request("commit")
        self.assertEqual(device.writes, [b"ab", b"cd"])
        self.assertTrue(device.closed)
        self.acquire.assert_called_once()
        self.release.assert_called_once_with("restore")
        self.assertEqual(self.messages[-1]["status"], "success")

    def test_client_disconnect_before_and_after_commit(self):
        """客户端断线结果按是否提交启动请求区分上传失败与结果未知."""
        client = RemoteFirmwareClient(lambda _: None)
        results = []
        client.finished.connect(lambda *args: results.append(args))
        for committed in (False, True):
            client.start(str(self.bin))
            client.active["committed"] = committed
            client.disconnect()
        self.assertIn("未发出", results[0][1])
        self.assertIn("结果未知", results[1][1])


class LoopbackChecks(OfflineCase):
    """带口令认证的真实 TCP 回环传输检查,只使用假串口."""
    def test_authenticated_upload_download_over_tcp(self):
        """多块 BIN 经认证回环连接上传并下载,最终恢复回调晚于串口关闭."""
        # 仅连接回环地址并使用临时端口, 不使用配置中的局域网地址.
        server = RemoteControlServer("127.0.0.1", 0, "offline")
        messages = []
        server.status_changed.connect(messages.append)
        server.start()
        try:
            pump_until(lambda: bool(server._sock) or bool(messages))
            # run() 绑定成功后提供实际监听端口.
            client = RemoteControlClient("127.0.0.1", server.port, "offline")
            connected = []
            client.connected_changed.connect(lambda value, *_: connected.append(value))
            device = FakeDevice()
            released = []
            host = RemoteFirmwareHost(lambda m, _: server._queue_message(m),
                                      lambda: (dict(port="FAKE"), ack_config(packet_size=4096)),
                                      lambda: "restore", released.append, lambda *_: None)
            uploader = RemoteFirmwareClient(client._queue_message)
            server.message_received.connect(host.handle)
            client.message_received.connect(uploader.handle)
            results = []
            uploader.finished.connect(lambda *args: results.append(args))
            client.start()
            try:
                pump_until(lambda: True in connected)
                self.bin.write_bytes(bytes(range(256)) * 260)  # 覆盖多个上传分块
                with patch("serial.Serial", return_value=device):
                    uploader.start(str(self.bin))
                    pump_until(lambda: bool(results), timeout=10)
                self.assertTrue(results[0][0], results)
                self.assertEqual(b"".join(device.writes), self.bin.read_bytes())
                self.assertEqual(released, ["restore"])
                self.assertTrue(device.closed)
            finally:
                uploader.disconnect()
                host.disconnect()
                if host.active:
                    pump_until(lambda: host.active is None)
                host.timer.stop()
                client.stop()
                # 排空跨线程信号后再销毁测试对象,避免回调引用离开作用域的对象.
                server.stop()
                uploader.timer.stop()
                QTest.qWait(50)
                server.message_received.disconnect()
                client.message_received.disconnect()
                client.connected_changed.disconnect()
                host.deleteLater()
                uploader.deleteLater()
                client.deleteLater()
                QTest.qWait(20)
        finally:
            server.stop()
            server.deleteLater()
            QTest.qWait(20)


class CommandChecks(OfflineCase):
    """实际窗口的远程命令校验,回执关联和恢复参数检查."""
    def setUp(self):
        """创建隔离配置窗口,使用模拟远程线程提供设备端会话."""
        super().setUp()
        from main_window import SerialTool
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.window = SerialTool(exe_name="offline-contract")
        self.window.remote_thread = Mock(session="session")
        self.window.remote_client_connected = True
        self.window.remote_mode = "server"

    def tearDown(self):
        """移除模拟后端并关闭窗口,恢复目录后执行离线基类清理."""
        self.window.serial_thread = None
        self.window.is_connected = False
        self.window.remote_thread = None
        self.window.remote_mode = "off"
        self.window.remote_firmware_host.cancel()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        os.chdir(self.cwd)
        super().tearDown()

    def request(self, action, *, request_id="a" * 32, **config):
        """向窗口提交本例远程设备动作,返回发送队列捕获的回执."""
        self.window._execute_remote_device(dict(id=request_id, _session="session", action=action, config=config))
        return self.window.remote_thread._queue_message.call_args[0][0]

    def test_send_paths_timestamp_once_per_successful_command(self):
        """每次成功发送开启新 RX 时刻,隐藏发送及分片/CRLF 不改变边界."""
        from core.command_types import OutputSource
        window = self.window
        manager = window.output_manager
        stamp = "2026-10-19T01:02:03.456+08:00"
        for path in ("remote", "local", "raw"):
            for enabled in (False, True):
                for show_send in (False, True):
                    with self.subTest(path=path, timestamp=enabled, show_send=show_send):
                        window.remote_mode = "server" if path == "remote" else "off"
                        window.serial_thread = Mock(write_data=Mock(side_effect=lambda data: len(data)))
                        window.is_connected = True
                        window.timestamp_check.setChecked(enabled)
                        window.show_send_check.setChecked(show_send)
                        manager.clear()
                        window.on_data_received(b"prior\r\n")
                        with patch("managers.output_manager.event_timestamp", return_value=stamp):
                            for request_id in ("a" * 32, "b" * 32):
                                start = len(manager.records)
                                if path == "remote":
                                    self.assertTrue(self.request("write", request_id=request_id, data="YQ==")["success"])
                                else:
                                    self.assertTrue(window.send_command("a") if path == "local" else window.send_raw_data(b"a"))
                                for chunk in (b"echo\r", b"\nvalue = 0", b"0000000\r\n"):
                                    window.on_data_received(chunk)
                                rx_records = [r for r in manager.records[start:] if r.source_type == OutputSource.RECEIVE]
                                self.assertEqual([r.timestamp for r in rx_records if r.timestamp], [stamp])
                                rx = "".join(manager._record_text(r) for r in rx_records)
                                prefix = "[2026-10-19 01:02:03.456] " if enabled else ""
                                self.assertEqual(rx, prefix + "echo\nvalue = 00000000\n")
                                if path == "remote":
                                    writes = window.serial_thread.write_data.call_count
                                    self.request("write", request_id=request_id, data="YQ==")
                                    self.assertEqual(window.serial_thread.write_data.call_count, writes + 1)
                                    window.on_data_received(b"ambient\r\n")
                                    self.assertEqual(manager._record_text(manager.records[-1]), prefix + "ambient\n")

    def test_long_session_has_no_completed_request_limit(self):
        """同一连接超过旧上限仍逐帧写入并回传,不保留完成历史."""
        self.window.serial_thread = Mock(write_data=Mock(return_value=1))
        with patch.object(self.window, "send_remote_serial_config"):
            for index in range(4100):
                task_id = f"{index:032x}"
                result = self.request("write", request_id=task_id, data="YQ==")
                self.assertEqual(result["id"], task_id)
                self.assertTrue(result["success"])
        self.assertEqual(self.window.serial_thread.write_data.call_count, 4100)
        self.assertFalse(hasattr(self.window, "_remote_device_results"))

    def test_failed_remote_write_does_not_reset_receive_timestamp(self):
        """零写入、短写和异常均不伪造成功发送边界."""
        self.window.timestamp_check.setChecked(True)
        self.window.show_send_check.setChecked(False)
        self.window.serial_thread = Mock()
        self.window.on_data_received(b"prior\r\n")
        for index, outcome in enumerate((0, 1, OSError("offline write failed"))):
            with self.subTest(outcome=outcome), patch.object(self.window.serial_thread, "write_data", side_effect=
                    outcome if isinstance(outcome, Exception) else lambda data, count=outcome: count), \
                    patch.object(self.window.output_manager, "reset_receive_timestamp") as reset:
                self.assertFalse(self.request("write", request_id=f"{index:032x}", data="YWI=")["success"])
                reset.assert_not_called()
                self.assertFalse(self.window.output_manager.rules.last_receive_timestamp)

    def client(self):
        """将测试窗口设为已协商设备结果能力的远程客户端."""
        self.window.remote_mode = "client"
        self.window.remote_capabilities = dict(device_results=1)
        self.window.remote_serial_connected = True

    def replacement_case(self, remote=True):
        """为实际发送/校验事件配置截图中的命令及可控校验时钟."""
        window = self.window
        window.command_table.clear_all()
        window.command_table.add_command_row(True, "write 0 0x40108048 0x0803C0C8", "")
        window.global_replacement_rule["response_validation"].update(enabled=True, show_error=True, timeout_ms=100)
        window.replace_send_check.setChecked(True)
        window.reset_statistics()
        window._remote_sequence_failed = False
        window.remote_thread.reset_mock()
        clock = [0.0]
        window.response_validation_manager.clock = lambda: clock[0]
        if remote:
            self.client()
        else:
            window.remote_mode = "off"
            window.is_connected = True
            window.serial_thread = Mock(write_data=Mock(side_effect=lambda data: len(data)))
        return clock, window.command_table.get_row_data(0)[1]

    def test_replacement_validation_starts_at_confirmed_write(self):
        """排队不消耗响应超时,旧 RX 不误配,本地/远程分片匹配和真实超时一致."""
        import re
        window = self.window
        response = b"read 0 0x40108048 1\r\n40108048 = 0803C0C8\r\n"
        for remote in (False, True):
            for outcome in ("match", "mismatch", "wrong_channel", "wrong_echo_address", "wrong_value_address", "empty"):
                with self.subTest(remote=remote, outcome=outcome):
                    clock, command = self.replacement_case(remote)
                    manager = window.response_validation_manager
                    if remote:
                        older = dict(window._prepare_outgoing_command(command, 0)[1])
                        older.update(pattern=re.compile("OLD_RX" if outcome == "match" else "NEVER"),
                                     diagnostic_pattern=None, timeout_ms=60000)
                        window._register_response_validation(older, None, manager.end_offset)
                    self.assertTrue(window.send_command(command, 0))
                    if remote:
                        self.assertEqual(len(manager.pending), 1)  # 只有旧校验,排队请求尚未登记.
                        task_id = next(iter(window._remote_device_pending))
                        clock[0] = 1.0
                        window._expire_response_validations()
                        window.on_remote_message(dict(type="serial_data", _session="session",
                            data=base64.b64encode(b"OLD_RX\n" + response).decode("ascii")))
                        # 旧任务正常完成可清空同一列表;另两例保留旧缓冲以检查新 offset.
                        before_complete, before_pass = window.validation_complete_count, window.validation_pass_count
                        window.on_remote_message(dict(type="device_result", _session="session", id=task_id,
                            success=True, message="设备端串口已写入"))
                    else:
                        before_complete = before_pass = 0
                    self.assertAlmostEqual(manager.pending[-1].deadline, clock[0] + 0.1)
                    payload = {
                        "match": response,
                        "mismatch": response.replace(b"0803C0C8", b"0803C0C9"),
                        "wrong_channel": response.replace(b"read 0", b"read 1"),
                        "wrong_echo_address": response.replace(b"0x40108048", b"0x4010804C"),
                        "wrong_value_address": response.replace(b"\n40108048", b"\n4010804C"),
                        "empty": b"",
                    }[outcome]
                    if outcome != "empty":
                        for chunk in (payload[:-6], payload[-6:]):
                            if remote:
                                window.on_remote_message(dict(type="serial_data", _session="session",
                                    data=base64.b64encode(chunk).decode("ascii")))
                            else:
                                window.on_data_received(chunk)
                    clock[0] += 0.101
                    window._expire_response_validations()
                    self.assertEqual(window.validation_complete_count, before_complete + 1)
                    self.assertEqual(window.validation_pass_count, before_pass + int(outcome == "match"))
                    self.assertEqual(window.command_table.cellWidget(0, 1).validation_state,
                                     "passed" if outcome == "match" else "failed")
                    if remote:
                        window.remote_thread._queue_message.assert_called_once()

    def test_remote_validation_failure_and_changed_row_lifecycle(self):
        """失败/未知不登记或重发,改行/删行不误染新命令,复位不复活旧校验."""
        window = self.window
        for change in ("failed", "unknown", "edit", "delete", "reset"):
            with self.subTest(change=change):
                _clock, command = self.replacement_case()
                self.assertTrue(window.send_command(command, 0))
                task_id = next(iter(window._remote_device_pending))
                if change == "edit":
                    window.command_table.cellWidget(0, 1).setText("changed command")
                elif change == "delete":
                    window.command_table.clear_all()
                    window.command_table.add_command_row(True, command, "new row")
                elif change == "reset":
                    window.reset_statistics()
                if change == "unknown":
                    window._remote_device_timeout(task_id)
                else:
                    window.on_remote_message(dict(type="device_result", _session="session", id=task_id,
                        success=change != "failed", message="write result"))
                window.on_remote_message(dict(type="serial_data", _session="session", data=base64.b64encode(
                    b"read 0 0x40108048 1\r\n40108048 = 0803C0C8\r\n").decode("ascii")))
                self.assertEqual(window.validation_complete_count, int(change in ("edit", "delete")))
                self.assertIsNone(window.command_table.cellWidget(0, 1).validation_state)
                self.assertFalse(window.response_validation_manager.pending)
                window.remote_thread._queue_message.assert_called_once()

    def test_host_write_ack_precedes_cross_thread_rx_and_coalesced_frames(self):
        """设备可在 write 返回前回包,GUI 排队和同一 TCP 流仍先派发写入回执."""
        from core.serial_thread import SerialThread
        window = self.window
        transport = RemoteControlBase()
        transport.session = "session"
        window.remote_thread = transport
        serial_thread = SerialThread("FAKE", 115200, 8, "N", 1)
        serial_thread.data_received.connect(window.on_data_received)
        window.serial_thread = serial_thread
        def write(data):
            emitter = threading.Thread(target=serial_thread.data_received.emit, args=(b"early hardware RX\r\n",))
            emitter.start()
            emitter.join(1)
            self.assertFalse(emitter.is_alive())
            return len(data)
        with patch.object(serial_thread, "write_data", side_effect=write):
            window._execute_remote_device(dict(id="a" * 32, _session="session", action="write", config=dict(data="YQ==")))
        before = [message for _session, message in transport._send_queue.queue]
        self.assertEqual(before[0]["type"], "device_result")
        self.assertTrue(before[0]["success"])
        self.assertNotIn("serial_data", [message["type"] for message in before])
        APP.processEvents()
        messages = [message for _session, message in transport._send_queue.queue]
        self.assertEqual(messages[-1]["type"], "serial_data")
        buffer, decoded = transport._process_incoming(b"".join(json.dumps(message).encode() + b"\n" for message in messages))
        self.assertFalse(buffer)
        self.assertEqual(decoded, messages)

    def test_invalid_open_does_not_change_widgets_or_open_serial(self):
        """非法开串口参数在修改控件或访问串口前被拒绝."""
        before = self.window.get_current_serial_config()
        with patch.object(self.window, "open_serial") as opened:
            result = self.request("open", port="FAKE", baudrate=115200, databits=9, parity="bad", stopbits=1)
        self.assertFalse(result["success"])
        opened.assert_not_called()
        self.assertEqual(self.window.get_current_serial_config(), before)

    def test_invalid_baudrate_is_rejected_without_coercion(self):
        """非法波特率不能被静默转换成可接受的设备参数."""
        for value in (True, -1, 0, 1.5, "nan", 4000001):
            self.assertFalse(self.request("baudrate", value=value)["success"])

    def test_invalid_base64_does_not_write(self):
        """非法 Base64 不进入设备写入."""
        self.window.serial_thread = Mock()
        result = self.request("write", data="!bad!")
        self.assertFalse(result["success"])
        self.window.serial_thread.write_data.assert_not_called()

    def test_host_automation_conflict_rejected(self):
        """设备端连续发送期间拒绝冲突的远程设备动作."""
        self.window.is_continuous_sending = True
        self.window.serial_thread = Mock()
        self.assertFalse(self.request("write", data="YQ==")["success"])
        self.window.serial_thread.write_data.assert_not_called()
        self.window.is_continuous_sending = False

    def test_failed_result_without_callback_stops_next_command(self):
        """没有显式回调的请求收到失败结果后也必须停止后续命令."""
        self.client()
        self.assertTrue(self.window.request_remote_device("write", dict(data="YQ==")))
        task_id = next(iter(self.window._remote_device_pending))
        self.window.on_remote_message(dict(type="device_result", _session="session", id=task_id,
                                           success=False, message="short write"))
        completed = []
        self.window.schedule_command_next(0, lambda: completed.append(True))
        APP.processEvents()
        self.assertTrue(self.window._remote_sequence_failed)
        self.assertFalse(completed)

    def test_success_waits_for_correlated_result(self):
        """下一条命令等待匹配 ID 的实际回执,不能按入队成功提前执行."""
        self.client()
        self.window.request_remote_device("baudrate", dict(value=9600))
        task_id = next(iter(self.window._remote_device_pending))
        completed = []
        self.window.schedule_command_next(0, lambda: completed.append(True))
        APP.processEvents()
        self.assertFalse(completed)
        self.window.on_remote_message(dict(type="device_result", _session="session", id="other", success=True))
        self.assertIn(task_id, self.window._remote_device_pending)
        self.window.on_remote_message(dict(type="device_result", _session="session", id=task_id,
                                           success=True, message="written"))
        pump_until(lambda: bool(completed))
        self.window.on_remote_message(dict(type="device_result", _session="session", id=task_id,
                                           success=True, message="late duplicate"))
        APP.processEvents()
        self.assertEqual(completed, [True])
        self.window.remote_thread._queue_message.assert_called_once()

    def test_unknown_timeout_does_not_requeue(self):
        """请求超时被标为未知,不再次进入发送队列."""
        self.client()
        self.window.request_remote_device("write", dict(data="YQ=="))
        task_id = next(iter(self.window._remote_device_pending))
        self.window._remote_device_timeout(task_id)
        self.assertTrue(self.window._remote_sequence_failed)
        self.window.remote_thread._queue_message.assert_called_once()

    def test_old_session_data_and_control_do_not_cross_reconnect(self):
        """旧 session 的接收数据和控制请求在重连后被忽略."""
        self.client()
        with patch.object(self.window, "on_data_received") as receive:
            self.window.on_remote_message(dict(type="serial_data", _session="old", data="YQ=="))
            receive.assert_not_called()
            self.window.on_remote_message(dict(type="serial_data", _session="session", data="YQ=="))
            receive.assert_called_once_with(b"a")
        self.window.on_remote_connected_changed(False, "old")
        self.assertTrue(self.window.remote_client_connected)

    def test_stop_invalidates_already_scheduled_command(self):
        """停止命令链后已排队的旧代回调不能继续发送."""
        completed = []
        self.window.schedule_command_next(0, lambda: completed.append(True))
        self.window.stop_continuous_sending()
        APP.processEvents()
        self.assertFalse(completed)

    def test_legacy_unassociated_write_is_rejected(self):
        """无请求 ID 的旧式串口数据消息不能在设备端执行写入."""
        self.window.serial_thread = Mock()
        self.window.on_remote_message(dict(type="serial_data", _session="session", data="YQ=="))
        self.window.serial_thread.write_data.assert_not_called()
        self.window.remote_thread.send_error.assert_called_once()

    def test_restore_captures_open_handle_not_pending_comport_selection(self):
        """下载恢复使用实际打开句柄参数,不采用尚未生效的 COM 下拉选择."""
        self.window.is_connected = True
        self.window.serial_thread = Mock(port="FAKE1", baudrate=9600, bytesize=7, parity="E", stopbits=2)
        self.window.update_com_port("FAKE2")
        with patch.object(self.window, "close_serial", return_value=True):
            restore = self.window._acquire_download_serial()
        self.assertEqual(restore, dict(port="FAKE1", baudrate="9600", databits="7", parity="Even", stopbits="2"))


if __name__ == "__main__":
    unittest.main()
