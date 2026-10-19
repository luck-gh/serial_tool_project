"""真实本机 IPC 与 CLI 子进程回归;串口使用假设备,配置隔离到临时目录."""
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "serial_tool_project"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from main_window import SerialTool
from core.gui_cli_bridge import GuiCliBridge
from core.cli_protocol import endpoint_name
from core.cli_runner import build_parser, should_run_cli, run_cli
from core.command_types import OutputSource
from remote_download_check import FakeSerial, pump_until

APP = QApplication.instance() or QApplication([])


class GuiCliBridgeTests(unittest.TestCase):
    """运行真实 CLI 入口和 Qt 本机管道,验证窗口可见状态及最终结果.

    CLI 子进程仅使用 --via-gui,不执行独立串口入口;GUI 串口构造器在测试
    中默认禁止,下载测试才替换为 FakeSerial,所有文件均位于临时目录.
    """
    def setUp(self):
        """创建隔离配置的离屏窗口,默认禁止构造物理串口."""
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.temp.name)
        self.guard = mock.patch("serial.Serial", side_effect=AssertionError("physical serial forbidden"))
        self.guard.start()
        self.window = SerialTool(exe_name="bridge_test")
        self.processes = []

    def tearDown(self):
        """停止测试子进程与任务,关闭窗口并恢复工作目录和串口替身."""
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()
        self.window.cancel_firmware_download()
        pump_until(lambda: not self.window.serial_operation_busy())
        self.window.serial_thread = None
        self.window.is_connected = False
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(20)
        self.guard.stop()
        os.chdir(self.previous)
        self.temp.cleanup()

    def launch(self, *arguments, config=None, input_text=None):
        """启动真实 CLI 子进程,通过本机桥接提交参数.

        Args:
            *arguments: 追加给 main.py 的 CLI 参数字符串.
            config (str | None): GUI 配置的绝对路径;默认使用本例临时配置.
            input_text (str | None): 可选 UTF-8 标准输入,用于不经补丁文件的配置操作.

        Returns:
            subprocess.Popen: 标准输出和错误均重定向的子进程,创建时不显示终端窗口.
        """
        command = [sys.executable, str(ROOT / "serial_tool_project" / "main.py"),
                   "--cli", "--via-gui", "--config",
                   config or str(Path(self.temp.name) / "bridge_test_config.json"),
                   "--gui-timeout", "5", *arguments]
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        process.test_input = input_text.encode("utf-8") if input_text is not None else None
        self.processes.append(process)
        return process

    def collect(self, process):
        """持续排空子进程管道并泵送 Qt 事件,避免完整 schema 超过 Windows 管道容量."""
        captured = []
        reader = threading.Thread(target=lambda: captured.append(process.communicate(input=process.test_input)), daemon=True)
        reader.start()
        pump_until(lambda: bool(captured), 9)
        reader.join()
        output, errors = captured[0]
        lines = [json.loads(line) for line in output.decode("utf-8").splitlines() if line]
        self.assertFalse(errors, errors.decode("utf-8", errors="replace"))
        return process.returncode, lines

    def config_request(self, action, patch):
        """将测试补丁写入临时文件,由真实 CLI 读取后经 IPC 交给窗口."""
        path = Path(self.temp.name) / "patch.json"
        path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
        return self.collect(self.launch("--gui-action", action, "--config-patch", str(path)))

    def work_json(self, name="工作 目录/候选.json"):
        """生成隔离工作文件,与运行中的连接选择故意不同."""
        from core import gui_config
        config = gui_config.snapshot(self.window)
        config['state']['commands'] = [[True, 'NEW', '中文 注释']]
        config['state']['command_replacement_rules'] = {'0': {'find': 'NEW', 'replace': 'ACTUAL'}}
        config['state']['basic_settings'].update(port='COM88', port_node='COM88')
        path = Path(self.temp.name) / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        return path

    def test_work_json_switch_cli_preserves_connection_and_redirects_save(self):
        """真实 CLI 切换,旧文件和候选不写;后续自动保存只写新文件."""
        from core import gui_config
        window = self.window
        old = Path(window.config_manager.config_file).resolve()
        old_bytes = old.read_bytes()
        manager = window.config_manager
        window.apply_serial_config_to_basic_settings(dict(port='COM9'))
        # 模拟现有远端句柄;任何断开/重连/设备写入均禁止.
        thread = mock.Mock()
        window.remote_thread = thread
        window.remote_mode = 'client'
        window.remote_client_connected = True
        window.remote_serial_connected = True
        window.schedule_state_save()
        before = window.snapshot_state()
        target = self.work_json()
        target_bytes = target.read_bytes()
        # 请求受理前进程启动不属于切换事务,避免旧timer先于请求执行.
        window._state_save_timer.stop()
        code, events = self.collect(self.launch('--gui-action', 'config-switch', '--config-switch', str(target)))
        result = events[-1]
        self.assertEqual(code, 0, events)
        self.assertTrue(result['switched'])
        self.assertEqual(result['working_config_file'], str(target.resolve()))
        self.assertEqual(result['bridge_config_file'], str(old))
        self.assertTrue(result['preserved_connection_settings'])
        self.assertIs(window.config_manager, manager)
        self.assertIs(window.remote_thread, thread)
        self.assertEqual(window.snapshot_state()['basic_settings'], before['basic_settings'])
        self.assertEqual(window.snapshot_state()['remote_settings'], before['remote_settings'])
        self.assertEqual(window.command_table.get_all_commands(), [(True, 'NEW', '中文 注释')])
        self.assertEqual(window.command_table.get_replacement_rules()['0']['replace'], 'ACTUAL')
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertEqual(target.read_bytes(), target_bytes)
        self.assertFalse(window._state_save_timer.isActive())
        self.assertFalse(window._state_dirty)
        for action in ('status', 'config-get'):
            code, events = self.collect(self.launch('--gui-action', action))
            self.assertEqual(code, 0, events)
            self.assertEqual(events[-1]['working_config_file'], str(target.resolve()))
            self.assertEqual(events[-1]['bridge_config_file'], str(old))
        window.command_table.cellWidget(0, 1).setText('EDIT')
        window._flush_pending_state()
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['state']['commands'][0][1], 'EDIT')
        self.assertEqual(old.read_bytes(), old_bytes)
        # 大小写与相对路径同实体不重新加载,避免覆盖当前未保存编辑.
        window.command_table.cellWidget(0, 1).setText('UNSAVED')
        self.assertFalse(gui_config.switch_file(window, os.path.relpath(target).upper())['switched'])
        self.assertEqual(window.command_table.cellWidget(0, 1).text(), 'UNSAVED')
        thread.assert_not_called()
        self.assertFalse(thread.method_calls)
        window.remote_thread = None
        window.remote_mode = 'off'

    def test_work_json_reject_and_apply_failure_are_atomic(self):
        """坏文件/只读/应用异常不改路径或文件,旧dirty及timer原状态保留."""
        from core import gui_config
        window = self.window
        original = window.config_manager.config
        before = gui_config.snapshot(window)
        old_path = window.config_manager.config_file
        target = self.work_json()
        for name, content in [('坏 JSON.json', '{'), ('非法.json', '{"state":{"commands":42}}'), ('null.json', '{"state":{"commands":null}}'), ('secret.json', '{"state":{"bad":1,"remote_settings":{"token":"SECRET"}}}')]:
            path = Path(self.temp.name) / name
            path.write_text(content, encoding='utf-8')
            code, events = self.collect(self.launch('--gui-action', 'config-switch', '--config-switch', str(path)))
            self.assertEqual(code, 1, events)
            self.assertFalse(events[-1]['switched'])
            self.assertNotIn('SECRET', json.dumps(events))
            self.assertEqual(events[-1]['working_config_file'], str(Path(old_path).resolve()))
            self.assertEqual(path.read_text(encoding='utf-8'), content)
        missing = Path(self.temp.name) / '不存在.json'
        with self.assertRaises(OSError):
            gui_config.switch_file(window, str(missing))
        self.assertFalse(missing.exists())
        import stat
        target.chmod(stat.S_IREAD)
        try:
            with self.assertRaisesRegex(ValueError, '只读'):
                gui_config.switch_file(window, str(target))
        finally:
            target.chmod(stat.S_IREAD | stat.S_IWRITE)
        window.schedule_state_save()
        apply_widgets = gui_config.apply_widgets
        def apply_then_fail(w, c):
            apply_widgets(w, c)
            if c['state']['commands'][0][1] == 'NEW':
                raise RuntimeError('injected apply failure')
        with mock.patch.object(gui_config, 'apply_widgets', side_effect=apply_then_fail), mock.patch.object(window.config_manager, 'save_config') as save:
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                gui_config.switch_file(window, str(target))
            save.assert_not_called()
        self.assertIs(window.config_manager.config, original)
        self.assertEqual(window.config_manager.config_file, old_path)
        self.assertEqual(gui_config.snapshot(window), before)
        self.assertTrue(window._state_dirty)
        self.assertTrue(window._state_save_timer.isActive())
        # 当前有效格式允许省略state字段;缺省来自初始控件,不来自旧工作表.
        target.write_text('{}', encoding='utf-8')
        gui_config.switch_file(window, str(target))
        self.assertEqual(window.command_table.rowCount(), 10)
        self.assertFalse(window._state_dirty)

    def test_work_json_busy_and_gui_choose_cancel(self):
        """真实手动模块Delay/模态编辑及现有任务占用均拒绝切换."""
        from core import gui_config
        from PyQt5.QtWidgets import QDialog
        window = self.window
        target = self.work_json()
        for extra in (('--console',), ('--console', '--machine')):
            code, events = self.collect(self.launch('--gui-action', 'config-switch', '--config-switch', str(target), *extra))
            self.assertEqual(code, 2, events)
            self.assertFalse(events[-1]['success'])
        cases = [('_template_task', {}), ('firmware_download_running', True),
                 ('is_continuous_sending', True), ('_remote_device_pending', {'pending': {}})]
        for name, value in cases:
            with self.subTest(name=name), mock.patch.object(window, name, value):
                with self.assertRaisesRegex(ValueError, '任务'):
                    gui_config.switch_file(window, str(target))
        with mock.patch.object(window.gui_cli_bridge, 'active', {'id': 'busy', 'socket': object()}):
            code, events = self.collect(self.launch('--gui-action', 'config-switch', '--config-switch', str(target)))
            self.assertEqual(code, 1, events)
            self.assertFalse(events[-1]['switched'])
        with mock.patch.object(window.response_validation_manager, 'pending', [object()]):
            with self.assertRaises(ValueError):
                gui_config.switch_file(window, str(target))
        with mock.patch.object(window.gui_cli_bridge, 'machine_subscribers', {'socket': {'scope': 'window'}}):
            with self.assertRaises(ValueError):
                gui_config.switch_file(window, str(target))
        dialog = QDialog(window)
        dialog.setModal(True)
        dialog.show()
        try:
            with self.assertRaises(ValueError):
                gui_config.switch_file(window, str(target))
        finally:
            dialog.reject()
        window.command_table.clear_all()
        for row, command in enumerate(['Mode:wait', 'PING', 'Delay:100', 'ModeEnd:0']):
            window.command_table.add_command_row(True, command, '', row)
        with mock.patch.object(window, 'can_send_serial_data', return_value=True), mock.patch.object(window, '_write_serial_data', side_effect=lambda data, **kwargs: len(data)):
            self.assertTrue(window.special_command_manager.execute_sendmode_inline('wait', window))
            with self.assertRaises(ValueError):
                gui_config.switch_file(window, str(target))
            pump_until(lambda: window._manual_module_generation is None)
            self.assertTrue(window.special_command_manager.execute_sendmode_inline('wait', window))
            window.on_send_clicked(1)
            # 普通发送取消旧模块代号后,不能把旧marker推进成永久busy.
            self.assertNotEqual(window._manual_module_generation, window._command_generation)
            gui_config.switch_file(window, str(target))
        window.schedule_state_save()
        def cancel_while_pumping(*args):
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, window._flush_pending_state)
            QTest.qWait(30)
            self.assertTrue(window._loading_state)
            self.assertFalse(window._state_save_timer.isActive())
            return '', ''
        with mock.patch('main_window.QFileDialog.getOpenFileName', side_effect=cancel_while_pumping), mock.patch.object(gui_config, 'switch_file') as switch, mock.patch.object(window.config_manager, 'save_config') as save:
            window.choose_work_config()
            switch.assert_not_called()
            save.assert_not_called()
        self.assertTrue(window._state_dirty)
        self.assertTrue(window._state_save_timer.isActive())
        with mock.patch('main_window.QFileDialog.getOpenFileName', return_value=(str(target), '')):
            window.choose_work_config()
        self.assertEqual(Path(window.config_manager.config_file), target)

    def test_config_get_schema_and_check_do_not_write(self):
        """查询,字段规则和预检查只读内存,不写盘,不导出口令."""
        from core import gui_config
        self.window.remote_token_input.setText("config-secret")
        self.window.save_state()
        before = gui_config.snapshot(self.window)
        with mock.patch.object(self.window.config_manager, "save_config") as save:
            code, lines = self.collect(self.launch("--gui-action", "config-get"))
            self.assertEqual(code, 0, lines)
            self.assertNotIn("config-secret", json.dumps(lines))
            self.assertEqual(set(lines[-1]["config"]), {"state", "tools"})
            code, lines = self.collect(self.launch("--gui-action", "config-schema"))
            self.assertEqual(code, 0, lines)
            rules = lines[-1]["schema"]
            gui_config.Draft202012Validator.check_schema(rules)
            self.assertEqual(set(rules["properties"]["state"]["properties"]), set(before["state"]))
            code, lines = self.config_request("config-check", {"state": {"remote_settings": {"port": 12345}}})
            self.assertEqual(code, 0, lines)
            save.assert_not_called()
        self.assertEqual(gui_config.snapshot(self.window), before)


    def test_config_get_large_utf8_result_and_limit_metadata(self):
        """完整配置结果可超过常规帧上限,仍按 UTF-8 字节数有界传输."""
        from core import gui_config
        from core.cli_protocol import MAX_MESSAGE, MAX_CONFIG_RESULT_MESSAGE, MAX_PENDING_OUTPUT

        commands = [[True, f"ReadReg:{index:05d}_" + "x" * 15, ""] for index in range(91585)]
        config = {"state": {"remote_settings": {"token": "secret"}, "commands": commands}, "tools": {}}
        with mock.patch.object(gui_config, "snapshot", return_value=config):
            code, lines = self.collect(self.launch("--gui-action", "config-get"))
        self.assertEqual(code, 0, lines)
        result = lines[-1]
        self.assertEqual(result["max_config_result_bytes"], MAX_CONFIG_RESULT_MESSAGE)
        self.assertRegex(result["id"], r"^[a-f0-9]{32}$")
        self.assertEqual(result["config"]["state"]["commands"], commands)
        self.assertNotIn("secret", json.dumps(result, ensure_ascii=False))
        frame = json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n"
        self.assertGreater(len(frame), MAX_MESSAGE)
        self.assertGreater(len(frame), MAX_PENDING_OUTPUT)
        self.assertGreater(len(frame), 3_800_000)
        self.assertLessEqual(len(frame), MAX_CONFIG_RESULT_MESSAGE)
        self.assertEqual(len(lines), 1)

    def test_config_get_over_limit_returns_correlated_failure(self):
        """超大快照只返回带原请求 ID 的失败帧,不发送部分配置."""
        from core import gui_config
        from core.cli_protocol import MAX_CONFIG_RESULT_MESSAGE

        config = {"state": {"remote_settings": {"token": ""}, "commands": [[True, "x" * MAX_CONFIG_RESULT_MESSAGE, ""]]}, "tools": {}}
        with mock.patch.object(gui_config, "snapshot", return_value=config):
            code, lines = self.collect(self.launch("--gui-action", "config-get"))
        self.assertEqual(code, 1, lines)
        self.assertEqual(len(lines), 1)
        result = lines[0]
        self.assertRegex(result["id"], r"^[a-f0-9]{32}$")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("未截断返回配置", result["message"])
        self.assertNotIn("config", result)

    def test_json_frame_limit_counts_utf8_and_line_terminator(self):
        """JSON 行上限计入中文 UTF-8 字节及换行符,并精确拒绝越界."""
        from core.cli_protocol import encode_json_line

        message = {"event": "result", "message": "中😀"}
        expected = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        self.assertEqual(encode_json_line(message, len(expected)), expected)
        with self.assertRaisesRegex(ValueError, "字节上限"):
            encode_json_line(message, len(expected) - 1)

    def test_config_get_pending_allowance_is_scoped_to_matching_success_result(self):
        """只有显式关联 ID 的 config-get 成功终态可越过普通累计待发送上限."""
        from PyQt5.QtNetwork import QLocalSocket
        from core.cli_protocol import MAX_CONFIG_RESULT_MESSAGE, MAX_MESSAGE, MAX_PENDING_OUTPUT

        class FakeSocket:
            def __init__(self, pending):
                self.pending = pending
                self.written = []
                self.aborted = False

            def state(self):
                return QLocalSocket.ConnectedState

            def bytesToWrite(self):
                return self.pending

            def write(self, payload):
                self.written.append(bytes(payload))
                return len(payload)

            def flush(self):
                pass

            def abort(self):
                self.aborted = True

        bridge = self.window.gui_cli_bridge
        request_id = "a" * 32
        matching = FakeSocket(MAX_PENDING_OUTPUT - 1)
        bridge.connections[matching] = None
        bridge.send(matching, {"event": "result", "id": request_id, "success": True, "status": "success",
                               "config": {"state": {"commands": []}, "tools": {}},
                               "max_config_result_bytes": MAX_CONFIG_RESULT_MESSAGE},
                   max_bytes=MAX_CONFIG_RESULT_MESSAGE, config_get_result_id=request_id)
        self.assertFalse(matching.aborted)
        self.assertEqual(len(matching.written), 1)

        for message, result_id in (
            ({"event": "result", "id": "b" * 32, "success": True, "status": "success",
              "config": {}, "max_config_result_bytes": MAX_CONFIG_RESULT_MESSAGE}, request_id),
            ({"event": "progress", "id": request_id}, request_id),
            ({"event": "result", "id": request_id, "success": False, "status": "failed",
              "config": {}, "max_config_result_bytes": MAX_CONFIG_RESULT_MESSAGE}, request_id),
        ):
            sock = FakeSocket(MAX_PENDING_OUTPUT - 1)
            bridge.connections[sock] = None
            bridge.send(sock, message, max_bytes=MAX_CONFIG_RESULT_MESSAGE, config_get_result_id=result_id)
            self.assertTrue(sock.aborted)
            self.assertEqual(sock.written, [])
            bridge.connections.pop(sock, None)

        ordinary = FakeSocket(MAX_PENDING_OUTPUT - 1)
        bridge.connections[ordinary] = None
        bridge.send(ordinary, {"event": "progress", "id": request_id})
        self.assertTrue(ordinary.aborted)
        self.assertEqual(ordinary.written, [])
        self.assertEqual(MAX_MESSAGE, 128 * 1024)
        self.assertEqual(MAX_PENDING_OUTPUT, 512 * 1024)
        self.assertLess(MAX_CONFIG_RESULT_MESSAGE, 5 * 1024 * 1024)
        bridge.connections.pop(matching, None)
        bridge.connections.pop(ordinary, None)

    def test_config_apply_all_groups_and_noop(self):
        """批量修改所有配置分组,窗口和 JSON 一致;无变化不重复写盘."""
        from core import gui_config
        patch = {"state": {
            "basic_settings": {"port": "FAKE", "baudrate": "9600", "databits": "7", "parity": "Even", "stopbits": "2"},
            "remote_settings": {"mode": "远程端", "host": "127.0.0.1", "port": 12345, "token": "config-secret"},
            "receive_settings": {"show_timestamp": True, "system_levels": {"debug": False}, "save_sources": {"send": False}},
            "send_settings": {"ending": "None", "loop_interval": 200, "continuous_interval": 30, "jump_module": "A", "send_module": "A",
                              "global_replacement_rule": None},
            "ui_settings": {"h_splitter_sizes": [500, 700]},
            "tool_settings": {"left_group_expanded": {"remote": True}},
            "commands": [[True, "mode:A", ""], [True, "AT", "test"]],
            "command_replacement_rules": {"1": {"find": "AT", "replace": "ATI"}}},
            "tools": {"firmware_downloader": {"packet_size": 128}, "bin_hex_converter": {"bytes_per_row": 8},
                      "number_conversion_dialog": {"data_width": "WORD"}}}
        with mock.patch.object(self.window.config_manager, "save_config", wraps=self.window.config_manager.save_config) as save:
            code, lines = self.config_request("config-apply", patch)
            self.assertEqual(code, 0, lines)
            self.assertEqual(save.call_count, 1)
            self.assertIsNone(self.window.serial_thread)
            self.assertIsNone(self.window.remote_thread)
            now = gui_config.snapshot(self.window)
            stored = json.loads(Path(self.window.config_manager.config_file).read_text(encoding="utf-8"))
            self.assertEqual(now["state"], json.loads(json.dumps(stored["state"])))
            self.assertEqual(now["tools"], stored["tools"])
            self.assertEqual(now["state"]["basic_settings"]["port"], "FAKE")
            self.assertEqual(self.window.send_module_combo.currentText(), "A")
            self.assertEqual(self.window.command_table.rowCount(), 2)
            code, lines = self.config_request("config-apply", {})
            self.assertEqual(code, 0, lines)
            self.assertEqual(save.call_count, 1)

    def test_config_invalid_batch_and_save_failure_preserve_original(self):
        """类型,范围和未知键整批拒绝;保存失败恢复控件,内存及旧文件."""
        from core import gui_config
        self.window.save_state()
        before = gui_config.snapshot(self.window)
        path = Path(self.window.config_manager.config_file)
        old_file = path.read_bytes()
        invalid = [
            {"config_version": "999"}, {"state": {"unknown": True}},
            {"state": {"remote_settings": {"port": True}}},
            {"state": {"remote_settings": {"port": 65536}}},
            {"state": {"send_settings": {"loop_interval": 0}}},
            {"state": {"commands": [["yes", "AT", ""]]}},
            {"tools": {"firmware_downloader": {"packet_size": 9000}}},
            {"state": {"basic_settings": {"parity": "invalid"}, "receive_settings": {"show_timestamp": True}}},
        ]
        for patch in invalid:
            with self.subTest(patch=patch):
                code, lines = self.config_request("config-apply", patch)
                self.assertEqual(code, 1, lines)
                self.assertEqual(gui_config.snapshot(self.window), before)
                self.assertEqual(path.read_bytes(), old_file)
        with mock.patch("managers.config_manager.os.replace", side_effect=OSError("simulated disk failure")):
            code, lines = self.config_request("config-apply", {"state": {"remote_settings": {"port": 12345}}})
            self.assertEqual(code, 1, lines)
        self.assertEqual(gui_config.snapshot(self.window), before)
        self.assertEqual(path.read_bytes(), old_file)

    def test_config_busy_and_connected_serial_guards(self):
        """配置应用不抢占任务;串口已打开时只能修改不影响设备的设置."""
        self.window.is_continuous_sending = True
        code, lines = self.config_request("config-apply", {"state": {"remote_settings": {"port": 12345}}})
        self.assertEqual(code, 1, lines)
        self.window.is_continuous_sending = False
        self.window.is_connected = True
        code, lines = self.config_request("config-apply", {"state": {"basic_settings": {"baudrate": "9600"}}})
        self.assertEqual(code, 1, lines)
        code, lines = self.config_request("config-apply", {"state": {"receive_settings": {"system_levels": {"debug": False}}}})
        self.assertEqual(code, 0, lines)

    def test_config_array_replacement_and_rule_removal(self):
        """数组整体替换,null 清除规则;清空命令表不会执行旧命令."""
        code, lines = self.config_request("config-apply", {"state": {"commands": [[True, "AT", ""]],
            "command_replacement_rules": {"0": {"find": "AT", "replace": "ATI"}}}})
        self.assertEqual(code, 0, lines)
        code, lines = self.config_request("config-apply", {"state": {"commands": [], "command_replacement_rules": {"0": None}}})
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.window.command_table.rowCount(), 0)
        self.assertEqual(self.window.command_table.get_replacement_rules(), {})

    def test_config_stdin_and_malformed_input(self):
        """UTF-8 补丁可经标准输入直接应用;非法或超大输入不改变窗口."""
        from core import gui_config
        patch = {"state": {"commands": [[True, "AT", "中文注释"]]}}
        code, lines = self.collect(self.launch("--gui-action", "config-apply", "--config-patch", "-",
            input_text=json.dumps(patch, ensure_ascii=False)))
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.window.command_table.get_all_commands()[0][2], "中文注释")
        before = gui_config.snapshot(self.window)
        for invalid in ('{"state":', '[]', ' ' * (128 * 1024 + 1)):
            code, lines = self.collect(self.launch("--gui-action", "config-apply", "--config-patch", "-", input_text=invalid))
            self.assertEqual(code, 2, lines)
            self.assertEqual(gui_config.snapshot(self.window), before)

    def test_status_and_visible_request_id(self):
        """查询真实窗口状态,并确认状态栏及日志保留 CLI 请求标识."""
        code, lines = self.collect(self.launch("--gui-action", "status"))
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1]["remote_mode"], "off")
        self.assertIn("[CLI ", self.window.statusBar().currentMessage())
        self.assertTrue(any("[CLI " in record.text for record in self.window.output_manager.records))

    def test_no_gui_does_not_fall_back_to_serial(self):
        """目标窗口不存在时返回错误,不创建配置或转为独立串口操作."""
        code, lines = self.collect(self.launch("--send", "AT", config=str(Path(self.temp.name) / "absent.json")))
        self.assertEqual(code, 2)
        self.assertIn("无法连接对应 GUI", lines[-1]["message"])
        self.assertEqual(lines[-1]["bridge"]["config_path"], str(Path(self.temp.name) / "absent.json"))
        self.assertTrue(lines[-1]["bridge"]["user"])
        self.assertIn("同一用户", lines[-1]["message"])
        if os.name == "nt":
            self.assertTrue(lines[-1]["bridge"]["user_sid"].startswith("S-1-"))
        self.assertFalse((Path(self.temp.name) / "absent.json").exists())

    def test_send_visible_and_serial_response(self):
        """桥接发送使用窗口串口后端,观察到的接收字节同时回传 CLI."""
        fake_thread = mock.Mock()
        fake_thread.write_data.return_value = 4
        self.window.serial_thread = fake_thread
        self.window.is_connected = True
        process = self.launch("--send", "AT", "--read-timeout", "80")
        pump_until(lambda: bool(fake_thread.write_data.call_count))
        self.window.on_data_received(b"OK\r\n")
        code, lines = self.collect(process)
        self.assertEqual(code, 0, lines)
        fake_thread.write_data.assert_called_once_with(b"AT\r\n")
        self.assertTrue(any(line["event"] == "receive" for line in lines))
        self.assertEqual("操作完成", lines[-1]["message"])
        records = [record for record in self.window.output_manager.records if "[CLI " in record.text]
        self.assertTrue(records)
        self.assertTrue(all(record.source_type == OutputSource.SYSTEM and record.system_level == "debug"
                            for record in records))
        self.window.system_level_selection.discard("debug")
        self.window.output_manager.refresh_display()
        pump_until(lambda: not self.window.output_manager._render_timer.isActive())
        displayed = self.window.output_manager.text_browser.toPlainText()
        self.assertNotIn("[CLI ", displayed)
        self.assertIn("OK", displayed)
        self.assertIn("操作完成", self.window.statusBar().currentMessage())

    def test_disconnected_send_fails_and_is_visible(self):
        """未连接串口的发送失败在 CLI 和窗口状态栏均可见."""
        code, lines = self.collect(self.launch("--send", "AT"))
        self.assertEqual(code, 1, lines)
        self.assertIn("failed", self.window.statusBar().currentMessage())
        self.window.system_level_selection.discard("debug")
        self.window.output_manager.refresh_display()
        pump_until(lambda: not self.window.output_manager._render_timer.isActive())
        self.assertIn("failed", self.window.output_manager.text_browser.toPlainText())

    def test_short_write_does_not_report_success(self):
        """GUI 串口写入失败不会被桥接转换为成功."""
        self.window.serial_thread = mock.Mock()
        self.window.serial_thread.write_data.return_value = 0
        self.window.is_connected = True
        code, lines = self.collect(self.launch("--send", "AT"))
        self.assertEqual(code, 1, lines)

    def test_firmware_reuses_local_core_and_reports_progress(self):
        """假设备确认本地桥接下载复用核心,上报进度并最终关闭串口."""
        path = Path(self.temp.name) / "app.bin"
        path.write_bytes(b"abcdefgh")
        self.window.port_combo.setCurrentText("FAKE")
        self.window.baud_combo.setCurrentText("9600")
        self.window.data_bits_combo.setCurrentText("7")
        self.window.parity_combo.setCurrentText("Odd")
        self.window.stop_bits_combo.setCurrentText("1.5")
        self.window.config_manager.set_tool_config(
            "firmware_downloader", True, "", packet_size=4, start_command="S",
            wait_start_ack=True, start_ack_check_data=True, start_ack_expected_data="OK",
            start_ack_data_format="ASCII", wait_packet_ack=True, packet_ack_check_data=True,
            packet_ack_expected_data="OK", packet_ack_data_format="ASCII", wait_last_packet_ack=False)
        fake = FakeSerial()
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake) as opened:
            code, lines = self.collect(self.launch("--send", "FirmwareDownload:" + str(path)))
        for key, value in dict(port="FAKE", baudrate=9600, bytesize=7, parity="O", stopbits=1.5).items():
            self.assertEqual(opened.call_args.kwargs[key], value)
        self.assertEqual(code, 0, lines)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.writes, [b"S", b"abcd", b"efgh"])
        self.assertTrue(any(line["event"] == "progress" for line in lines))
        self.assertIn("ACK", lines[-1]["message"])

    def test_cancel_delay_and_concurrent_request_rejected(self):
        """延时期间拒绝第二个变更请求,显式取消能结束原任务."""
        # 覆盖子进程启动和取消往返,不让 CLI 自身超时冒充取消结果.
        waiting = self.launch("--gui-timeout", "20", "--send", "delay:60000")
        pump_until(lambda: self.window.gui_cli_bridge.active is not None)
        code, _ = self.collect(self.launch("--send", "AT"))
        self.assertEqual(code, 1)
        code, messages = self.collect(self.launch("--gui-action", "cancel"))
        self.assertEqual(code, 0, messages)
        code, messages = self.collect(waiting)
        self.assertEqual(code, 1, messages)

    def test_same_config_second_bridge_does_not_steal_endpoint(self):
        """第二个桥接不能抢占同一配置端点,原监听继续可用."""
        second = GuiCliBridge(self.window, self.window.config_manager.config_file)
        self.assertFalse(second.start())
        self.assertTrue(self.window.gui_cli_bridge.server.isListening())
        second.close()
        second.deleteLater()

    def test_cli_timeout_cancels_owned_download_without_retry(self):
        """CLI 超时返回未知并促使所属下载取消,开始命令不重发."""
        path = Path(self.temp.name) / "app.bin"
        path.write_bytes(b"a")
        self.window.port_combo.setCurrentText("FAKE")
        self.window.config_manager.set_tool_config(
            "firmware_downloader", True, "", start_command="S",
            wait_start_ack=True, start_ack_timeout=10000, start_ack_check_data=True,
            start_ack_expected_data="OK", start_ack_data_format="ASCII")
        fake = FakeSerial(ack=b"")
        with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake):
            code, lines = self.collect(self.launch("--gui-timeout", "0.15", "--send", "FirmwareDownload:" + str(path)))
            pump_until(lambda: not self.window.serial_operation_busy())
        self.assertEqual(code, 3, lines)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.writes, [b"S"])

    def test_bridge_parser_and_identity(self):
        """桥接参数支持等号形式,等价配置路径生成相同端点."""
        args = build_parser().parse_args(["--via-gui", "--gui-action", "status"])
        self.assertTrue(args.via_gui)
        self.assertTrue(should_run_cli(["--gui-action=status"]))
        self.assertTrue(should_run_cli(["--remote-host=127.0.0.1"]))
        independent = build_parser().parse_args(["--remote-host", "127.0.0.1"])
        with mock.patch("core.cli_runner.ConfigManager") as config, mock.patch("sys.stderr"):
            self.assertEqual(run_cli(independent, "unused", "1.2.6", "2026-10-14"), 2)
            config.assert_not_called()
        self.assertEqual(endpoint_name("bridge_test_config.json"),
                         endpoint_name(str(Path(self.temp.name) / "bridge_test_config.json")))

    def test_sendmode_waits_for_commands_and_stops_after_failure(self):
        """模块结果必须反映实际写入,短写后不得继续下一条命令."""
        self.window.serial_thread = mock.Mock()
        self.window.is_connected = True
        self.window.modules = {"A": []}
        self.window.interval_spin.setValue(1)
        commands = [(0, "AT", False), (1, "NEXT", False)]
        with mock.patch.object(self.window, "refresh_modules"), mock.patch(
                "managers.special_command_manager.collect_module_commands", return_value=commands):
            self.window.serial_thread.write_data.side_effect = lambda data: len(data)
            code, lines = self.collect(self.launch("--send", "SendMode:A", "--read-timeout", "0"))
            self.assertEqual(code, 0, lines)
            self.assertEqual(self.window.serial_thread.write_data.call_count, 2)
            self.window.serial_thread.write_data.reset_mock()
            self.window.serial_thread.write_data.side_effect = lambda data: 0
            code, lines = self.collect(self.launch("--send", "SendMode:A", "--read-timeout", "0"))
            self.assertEqual(code, 1, lines)
            self.window.serial_thread.write_data.assert_called_once()

    def test_sendmode_cycle_fails_without_writing(self):
        """递归调用自身的模块返回失败,不向设备写入."""
        from core.command_types import SpecialCommandType
        self.window.serial_thread = mock.Mock()
        self.window.is_connected = True
        self.window.modules = {"A": []}
        commands = [(0, "SendMode:A", True, SpecialCommandType.SENDMODE, "A")]
        with mock.patch.object(self.window, "refresh_modules"), mock.patch(
                "managers.special_command_manager.collect_module_commands", return_value=commands):
            code, lines = self.collect(self.launch("--send", "SendMode:A"))
        self.assertEqual(code, 1, lines)
        self.window.serial_thread.write_data.assert_not_called()

    def test_cli_remote_gui_host_download_end_to_end(self):
        """CLI 启用监听并认证连接,发送与下载走回环设备端,失败口令不泄露."""
        from core.remote_control import RemoteControlServer
        host = SerialTool(exe_name="bridge_host")
        client = self.window
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        host.port_combo.setCurrentText("FAKE")
        host.config_manager.set_tool_config(
            "firmware_downloader", True, "", packet_size=4, start_command="S",
            wait_start_ack=True, start_ack_check_data=True, start_ack_expected_data="OK",
            start_ack_data_format="ASCII", wait_packet_ack=True, packet_ack_check_data=True,
            packet_ack_expected_data="OK", packet_ack_data_format="ASCII", wait_last_packet_ack=False)
        host.remote_port_spin.setValue(port)
        client.remote_mode_combo.setCurrentText("控制端（远程操作）")
        client.remote_host_input.setCurrentText("127.0.0.1")
        client.remote_port_spin.setValue(port)
        path = Path(self.temp.name) / "remote.bin"
        path.write_bytes(b"abcdefgh")
        fake = FakeSerial()
        try:
            with mock.patch("main_window.RemoteControlServer", side_effect=lambda _host, p, token: RemoteControlServer("127.0.0.1", p, token)):
                code, lines = self.collect(self.launch("--gui-action", "remote-listen", "--remote-port", str(port),
                    "--remote-password", "loopback-secret", config=host.config_manager.config_file))
            self.assertEqual(code, 0, lines)
            self.assertTrue(lines[-1]["remote"]["listening"])
            code, lines = self.collect(self.launch("--gui-action", "remote-connect", "--remote-host", "127.0.0.1",
                "--remote-port", str(port), "--remote-password", "wrong-secret"))
            self.assertEqual(code, 1, lines)
            self.assertIn("密码", lines[-1]["message"])
            self.assertIsNone(client.remote_thread)
            code, lines = self.collect(self.launch("--gui-action", "remote-connect", "--remote-host", "127.0.0.1",
                "--remote-port", str(port), "--remote-password", "loopback-secret"))
            self.assertEqual(code, 0, lines)
            self.assertTrue(lines[-1]["remote"]["connected"])
            self.assertEqual(client.get_current_serial_config()["port"], host.get_current_serial_config()["port"])
            active_thread = client.remote_thread
            code, _ = self.collect(self.launch("--gui-action", "remote-connect", "--remote-port", str(port + 1)))
            self.assertEqual(code, 1)
            self.assertIs(client.remote_thread, active_thread)
            parameters = {"port": "FAKE", "baudrate": "57600", "databits": "7", "parity": "Even", "stopbits": "2"}
            code, lines = self.config_request("config-apply", {"state": {"basic_settings": parameters}})
            self.assertEqual(code, 0, lines)
            for key, value in parameters.items():
                self.assertEqual(host.get_current_serial_config()[key], value)
            code, lines = self.config_request("config-apply", {"state": {
                "basic_settings": {"baudrate": "9600"}, "receive_settings": {"show_timestamp": True}}})
            self.assertNotEqual(code, 0, lines)
            self.assertEqual(host.baud_combo.currentText(), "57600")

            def open_fake():
                """模拟设备端打开串口,后续发送和关闭仍走窗口及协议路径."""
                host.serial_thread = mock.Mock()
                host.serial_thread.write_data.side_effect = len
                host.serial_thread.stop.return_value = True
                host.is_connected = True
                host.send_remote_serial_config()
                return True

            with mock.patch.object(host, "open_serial", side_effect=open_fake) as opened:
                code, lines = self.collect(self.launch("--gui-action", "open"))
            self.assertEqual(code, 0, lines)
            opened.assert_called_once()
            pump_until(lambda: client.remote_serial_connected)
            code, lines = self.config_request("config-apply", {"state": {"basic_settings": {"databits": "8"}}})
            self.assertNotEqual(code, 0, lines)
            self.assertEqual(host.data_bits_combo.currentText(), "7")
            code, lines = self.collect(self.launch("--send", "AT", "--read-timeout", "0"))
            self.assertEqual(code, 0, lines)
            host.serial_thread.write_data.assert_called_once_with(b"AT\r\n")
            code, lines = self.collect(self.launch("--gui-action", "close"))
            self.assertEqual(code, 0, lines)
            self.assertIsNone(host.serial_thread)
            pump_until(lambda: not client.remote_serial_connected)
            with mock.patch("firmware_downloader_project.core.downloader.serial.Serial", return_value=fake) as constructor:
                code, lines = self.collect(self.launch("--send", "FirmwareDownload:" + str(path)))
            self.assertEqual(code, 0, lines)
            self.assertEqual(constructor.call_count, 1)
            self.assertEqual(fake.writes, [b"S", b"abcd", b"efgh"])
            self.assertTrue(fake.closed)
            self.assertIsNone(client.serial_thread)
            self.assertFalse(host.remote_firmware_host.busy)
            code, lines = self.collect(self.launch("--gui-action", "status"))
            self.assertEqual(code, 0, lines)
            self.assertEqual(lines[-1]["remote"]["host"], "127.0.0.1")
            for secret in ("loopback-secret", "wrong-secret"):
                self.assertNotIn(secret, json.dumps(lines))
                self.assertFalse(any(secret in record.text for window in (client, host)
                                     for record in window.output_manager.records))
            code, lines = self.collect(self.launch("--gui-action", "remote-stop"))
            self.assertEqual(code, 0, lines)
            self.assertEqual(lines[-1]["remote"]["mode"], "off")
            # 省略连接参数复用窗口现有配置,不从设备端地址推断端口或口令.
            code, lines = self.collect(self.launch("--gui-action", "remote-connect"))
            self.assertEqual(code, 0, lines)
        finally:
            client.stop_remote_control()
            host.stop_remote_control()
            pump_until(lambda: not host.serial_operation_busy())
            host.close()
            host.deleteLater()
            QTest.qWait(50)

    def test_remote_parameters_validate_before_changing_settings(self):
        """非法连接参数和不适用的参数组合不启动网络,不修改窗口设置."""
        before = self.window.gui_cli_bridge.remote_status()
        cases = [
            ("remote-connect", "--remote-port", "0"),
            ("remote-connect", "--remote-port", "65536"),
            ("remote-connect", "--remote-host", "bad host"),
            ("remote-listen", "--remote-host", "127.0.0.1"),
            ("status", "--remote-port", "12345"),
            ("remote-connect", "--send", "AT"),
        ]
        with mock.patch("main_window.RemoteControlClient") as client, mock.patch("main_window.RemoteControlServer") as server:
            for args in cases:
                with self.subTest(args=args):
                    code, _ = self.collect(self.launch("--gui-action", *args))
                    self.assertNotEqual(code, 0)
                    self.assertEqual(self.window.gui_cli_bridge.remote_status(), before)
            client.assert_not_called()
            server.assert_not_called()

    def test_remote_start_and_stop_refuse_busy_window(self):
        """本地串口及运行中的命令链不被远程角色切换抢占."""
        with mock.patch("main_window.RemoteControlClient") as client:
            self.window.is_connected = True
            code, _ = self.collect(self.launch("--gui-action", "remote-connect"))
            self.assertEqual(code, 1)
            self.window.is_connected = False
            self.window.is_continuous_sending = True
            for action in ("remote-connect", "remote-listen", "remote-stop"):
                code, _ = self.collect(self.launch("--gui-action", action))
                self.assertEqual(code, 1)
            self.window.is_continuous_sending = False
            client.assert_not_called()

    def test_remote_listen_bind_failure_is_not_success(self):
        """回环端口被占用时监听失败并释放线程,不能把启动请求当成功."""
        from core.remote_control import RemoteControlServer
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            port = occupied.getsockname()[1]
            with mock.patch("main_window.RemoteControlServer", side_effect=lambda _host, p, token: RemoteControlServer("127.0.0.1", p, token)):
                code, lines = self.collect(self.launch("--gui-action", "remote-listen", "--remote-port", str(port)))
            self.assertEqual(code, 1, lines)
            self.assertIsNone(self.window.remote_thread)

    def test_remote_auth_wait_timeout_cleans_up_connection(self):
        """TCP 可连接但没有认证回执时,CLI 超时返回未知且取消本次连接."""
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen(1)
            code, lines = self.collect(self.launch("--gui-action", "remote-connect", "--remote-host", "127.0.0.1",
                "--remote-port", str(silent.getsockname()[1]), "--gui-timeout", "0.3"))
            self.assertEqual(code, 3, lines)
            pump_until(lambda: self.window.remote_thread is None)
            self.assertEqual(self.window.remote_mode, "off")

    def test_remote_connection_refused_is_reported(self):
        """未监听的回环端口返回连接失败,清理窗口远程状态."""
        with socket.socket() as closed_port:
            closed_port.bind(("127.0.0.1", 0))
            code, lines = self.collect(self.launch("--gui-action", "remote-connect", "--remote-host", "127.0.0.1",
                "--remote-port", str(closed_port.getsockname()[1])))
            self.assertEqual(code, 1, lines)
            self.assertIsNone(self.window.remote_thread)


class ConfigGetFramePolicyTests(unittest.TestCase):
    """配置查询的增大帧只接受当前请求唯一结果."""

    def setUp(self):
        from core.cli_protocol import MAX_CONFIG_RESULT_MESSAGE
        self.request = {"id": "a" * 32, "action": "config-get"}
        self.max_result = MAX_CONFIG_RESULT_MESSAGE

    def encode(self, message):
        return json.dumps(message, ensure_ascii=False).encode("utf-8")

    def test_large_config_result_requires_current_id_and_declared_capacity(self):
        from core.cli_bridge_client import decode_response_line
        from core.cli_protocol import MAX_MESSAGE

        message = {"event": "result", "id": self.request["id"], "success": True, "status": "success",
                   "config": {"commands": "中" * MAX_MESSAGE},
                   "max_config_result_bytes": self.max_result}
        raw = self.encode(message)
        self.assertGreater(len(raw) + 1, MAX_MESSAGE)
        self.assertLessEqual(len(raw) + 1, self.max_result)
        self.assertEqual(decode_response_line(raw, self.request), message)
        message["id"] = "b" * 32
        with self.assertRaisesRegex(ValueError, "ID 与请求不匹配"):
            decode_response_line(self.encode(message), self.request)

    def test_config_get_accepts_exact_capacity_and_rejects_one_byte_over(self):
        from core.cli_bridge_client import decode_response_line

        message = {"event": "result", "id": self.request["id"], "success": True, "status": "success",
                   "config": {"payload": ""}, "max_config_result_bytes": self.max_result}
        base_size = len(self.encode(message)) + 1
        message["config"]["payload"] = "x" * (self.max_result - base_size)
        raw = self.encode(message)
        self.assertEqual(len(raw) + 1, self.max_result)
        self.assertEqual(decode_response_line(raw, self.request), message)
        message["config"]["payload"] += "x"
        with self.assertRaisesRegex(ValueError, "超过允许的 UTF-8 字节上限"):
            decode_response_line(self.encode(message), self.request)

    def test_config_get_rejects_oversized_non_result_events(self):
        from core.cli_bridge_client import decode_response_line
        from core.cli_protocol import MAX_MESSAGE

        for event in ("progress", "receive"):
            with self.subTest(event=event):
                message = {"event": event, "id": self.request["id"], "data": "x" * MAX_MESSAGE}
                with self.assertRaisesRegex(ValueError, "超过 128 KiB"):
                    decode_response_line(self.encode(message), self.request)

    def test_config_get_preserves_small_events_and_rejects_unclaimed_large_result(self):
        from core.cli_bridge_client import decode_response_line
        from core.cli_protocol import MAX_MESSAGE

        for event in ("progress", "receive", "accepted"):
            with self.subTest(event=event):
                message = {"event": event, "id": self.request["id"]}
                self.assertEqual(decode_response_line(self.encode(message), self.request), message)
        message = {"event": "result", "id": self.request["id"], "success": True, "status": "success",
                   "config": {"commands": "x" * MAX_MESSAGE}}
        with self.assertRaisesRegex(ValueError, "缺少有效容量声明"):
            decode_response_line(self.encode(message), self.request)


    def run_fake_client(self, response_factory, timeout=1.0):
        from types import SimpleNamespace
        from core.cli_bridge_client import run_gui_cli

        class FakeSocket:
            ConnectedState = 1
            UnconnectedState = 0

            def __init__(self):
                self.response = b""

            def connectToServer(self, _endpoint):
                pass

            def waitForConnected(self, _timeout):
                return True

            def write(self, payload):
                request = json.loads(payload.decode("utf-8"))
                self.response = b"".join(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
                                          for message in response_factory(request))
                return len(payload)

            def flush(self):
                pass

            def bytesAvailable(self):
                return len(self.response)

            def readAll(self):
                result, self.response = self.response, b""
                return result

            def waitForReadyRead(self, _timeout):
                return bool(self.response)

            def state(self):
                return self.ConnectedState

            def abort(self):
                pass

        events = []
        args = SimpleNamespace(port=None, baudrate=None, gui_timeout=timeout, command=None,
                               gui_action="config-get", read_timeout=300)
        started = time.monotonic()
        with mock.patch("core.cli_bridge_client.QLocalSocket", return_value=FakeSocket()), \
                mock.patch("core.cli_bridge_client.bridge_target", return_value={"endpoint": "isolated"}):
            code = run_gui_cli(args, "isolated-config.json", emit=events.append)
        return code, events, time.monotonic() - started

    def test_config_get_returns_before_peer_closes_and_preserves_small_events(self):
        """完整关联结果即时交付,小型历史事件保持原行为."""
        def responses(request):
            return [
                {"event": "accepted", "id": request["id"]},
                {"event": "progress", "id": request["id"], "current": 1},
                {"event": "receive", "id": request["id"], "data": "b2s="},
                {"event": "result", "id": request["id"], "success": True, "status": "success",
                 "config": {"state": {"commands": []}, "tools": {}},
                 "max_config_result_bytes": self.max_result},
            ]

        code, events, elapsed = self.run_fake_client(responses)
        self.assertEqual(code, 0, events)
        self.assertEqual([event["event"] for event in events], ["accepted", "progress", "receive", "result"])
        self.assertLess(elapsed, 0.5)

    def test_config_get_unknown_result_keeps_exit_code_three(self):
        """当前请求的 unknown 终态仍按原接口返回退出码 3."""
        def responses(request):
            return [{"event": "result", "id": request["id"], "success": False,
                     "status": "unknown", "message": "结果未知"}]

        code, events, elapsed = self.run_fake_client(responses)
        self.assertEqual(code, 3, events)
        self.assertEqual(events[-1]["status"], "unknown")
        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()
