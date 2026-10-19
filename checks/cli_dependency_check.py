"""在独立进程阻止 GUI 导入,验证 CLI 的依赖边界与基本功能."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class CliDependencyTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows token identity")
    def test_actual_windows_identity_ignores_environment_username(self):
        sys.path.insert(0, str(ROOT))
        from core.cli_protocol import current_user_identity, bridge_target
        before = current_user_identity()
        with mock.patch.dict(os.environ, USERNAME="invented-account", USER="invented-account", LOGNAME="invented-account"):
            self.assertEqual(current_user_identity(), before)
            self.assertEqual(bridge_target("test.json")["endpoint_user"], "invented-account")

    def test_cli_commands_and_firmware_without_gui_modules(self):
        """真实本地回环,参数设置与下载核心加载不应触及 GUI 服务端."""
        program = r'''
import importlib.abc
import sys

class NoGui(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('PyQt5.QtWidgets', 'PyQt5.QtGui', 'PyQt5.QtQml',
                   'PyQt5.QtQuick', 'core.gui_cli_bridge', 'core.gui_config',
                   'core.remote_control', 'core.firmware_worker', 'utils.ui_utils')
        if fullname in blocked:
            raise AssertionError('CLI imported GUI module: ' + fullname)
sys.meta_path.insert(0, NoGui())

import base64
import tempfile
from pathlib import Path
from unittest.mock import patch
import serial
from core.cli_runner import build_parser, run_cli
from core.cli_bridge_client import run_gui_cli
from core.cli_console import LocalConsoleBackend, GuiConsoleBackend
from core.console_execution import ConsoleExecutor
from core.console_serial import LocalConsoleTransport
from core.firmware_download import get_firmware_downloader_class
from core.command_templates import export_template, load_template
from core.receive_text import ReceivedFile
from managers.config_manager import ConfigManager

assert get_firmware_downloader_class().__name__ == 'FirmwareDownloader'
with tempfile.TemporaryDirectory() as folder:
    template = Path(folder) / 'commands.csv'
    export_template(template, [[True, 'Mode:A', '模块'], [False, 'a,"b"', '注释']])
    for arguments in (['--import-template', str(template)], ['--export-template', str(template)]):
        args = build_parser().parse_args(['--config', str(Path(folder) / 'template.json'), *arguments])
        assert run_cli(args, 'dependency_check', '0.0.0', 'N/A') == 0
    assert load_template(template)[1][1] == 'a,"b"'
    recorder = ReceivedFile(Path(folder) / 'rx.bin')
    recorder.feed(b'RX\x00\xff')
    recorder.close()
    assert (Path(folder) / 'rx.bin').read_bytes() == b'RX\x00\xff'
    manager = ConfigManager(config_file=str(Path(folder) / 'config.json'))
    events = []
    transport = LocalConsoleTransport(manager, events.append)
    loop = serial.serial_for_url('loop://', timeout=0.05)
    with patch('core.cli_runner.open_serial_from_config', return_value=loop):
        transport.open()
        executor = ConsoleExecutor(transport, events.append)
        try:
            for command in ('BaudRate:9600', 'SetEndlog:rn', 'SendHex:41', 'Status'):
                result = executor.execute(command)
                assert result['success'], result
            data = b''.join(base64.b64decode(event['data']) for event in events
                            if event.get('source') == 'send')
            assert data == bytes.fromhex('41 0d 0a'), data
        finally:
            transport.close()
    args = build_parser().parse_args(['--config', str(Path(folder) / 'config.json'), '--baudrate', '19200'])
    assert run_cli(args, 'dependency_check', '0.0.0', 'N/A') == 0
assert 'jsonschema' not in sys.modules
print('CLI boundary, real loopback, settings and firmware import passed')
'''
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-c', textwrap.dedent(program)],
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
