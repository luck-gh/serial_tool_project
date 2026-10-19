"""固件按钮的完整 UI 和共用下载任务入口检查,禁止访问真实串口."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QDialog
from main_window import SerialTool
from core.firmware_download import get_firmware_downloader_class

get_firmware_downloader_class()
from dialogs.firmware_download_dialog import ManagedFirmwareDownloadDialog

APP = QApplication.instance() or QApplication([])


class FirmwareDialogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.ports = patch("serial.tools.list_ports.comports", return_value=[Mock(device="COM7", description="test")])
        self.serial = patch("serial.Serial", side_effect=AssertionError("禁止访问真实串口"))
        self.ports.start()
        self.serial.start()
        self.window = SerialTool(config_file=str(Path(self.folder.name) / "settings.json"))
        self.path = Path(self.folder.name) / "firmware.bin"
        self.path.write_bytes(b"offline firmware")
        self.dialog = None

    def tearDown(self):
        if self.dialog:
            self.dialog.reject()
            self.dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.serial.stop()
        self.ports.stop()
        self.folder.cleanup()

    def create_dialog(self):
        self.dialog = ManagedFirmwareDownloadDialog(self.window)
        return self.dialog

    def test_button_opens_full_ui_without_choosing_file_or_starting_download(self):
        def inspect(dialog):
            self.assertEqual(dialog.download_btn.text(), "开始下载")
            self.assertTrue(dialog.file_path_edit.isEnabled())
            self.assertFalse(dialog._running)
            return QDialog.Rejected
        with patch.object(ManagedFirmwareDownloadDialog, "exec_", inspect), \
             patch("PyQt5.QtWidgets.QFileDialog.getOpenFileName") as choose, \
             patch.object(self.window, "start_firmware_download_command") as start, \
             patch.object(self.window, "_acquire_download_serial") as acquire:
            self.window.open_firmware_downloader()
        choose.assert_not_called()
        start.assert_not_called()
        acquire.assert_not_called()

    def test_current_port_is_matched_exactly_and_stopbits_are_preserved(self):
        with patch("serial.tools.list_ports.comports", return_value=[
                Mock(device="COM70", description="other"), Mock(device="COM7", description="current")]), \
             patch.object(self.window, "_firmware_download_port_config", return_value=dict(
                 port="COM7", baudrate=123456, bytesize=7, parity="E", stopbits=2.0)):
            d = self.create_dialog()
        self.assertEqual(d.port_combo.currentText().split(" ")[0], "COM7")
        self.assertEqual(d.baudrate_combo.currentText(), "123456")
        self.assertEqual(d.stopbits_combo.currentText(), "2")
        self.assertEqual(d.bytesize_combo.currentText(), "7")
        self.assertEqual(d.parity_combo.currentText(), "Even")

    def test_file_selection_only_prefills_and_start_uses_edited_parameters(self):
        w, d = self.window, self.create_dialog()
        defaults = w.config_manager.get_firmware_downloader_params()
        defaults.pop("always_on_top", None)
        self.assertEqual(d._download_params(), defaults)
        with patch("PyQt5.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(self.path), "")), \
             patch.object(w, "start_firmware_download_command", wraps=w.start_firmware_download_command) as start:
            d._browse_file()
            start.assert_not_called()
        d.packet_size_spin.setValue(512)
        d.baudrate_combo.setCurrentText("230400")
        def execute(path, port, config):
            self.assertEqual(path, str(self.path))
            self.assertEqual(port["port"], "COM7")
            self.assertEqual(port["baudrate"], 230400)
            self.assertEqual(config["packet_size"], 512)
            w._remote_download_progress(1, 1, "完成包确认")
            self.assertEqual(d.progress_widget.progress_bar.value(), 99)
            w._complete_firmware_download(True, "download OK")
        with patch.object(w, "_acquire_download_serial", return_value=True) as acquire, \
             patch.object(w, "_release_download_serial") as release, \
             patch.object(w, "_start_builtin_firmware_download", side_effect=execute), \
             patch.object(w, "_show_download_progress") as popup, \
             patch("PyQt5.QtWidgets.QMessageBox.information"):
            d.download_btn.click()
        acquire.assert_called_once()
        release.assert_called_once_with(True)
        popup.assert_not_called()
        self.assertFalse(d._running)
        self.assertEqual(d.progress_widget.progress_bar.value(), 100)
        self.assertEqual(w.config_manager.get_firmware_downloader_params()["packet_size"], 256)

    def test_remote_ui_uploads_selected_file_without_local_parameter_overrides(self):
        w = self.window
        w.remote_mode = "client"
        w.remote_capabilities = {"firmware_upload": 1}
        d = self.create_dialog()
        self.assertTrue(d.port_group.isHidden())
        d.file_path_edit.setText(str(self.path))
        def upload(path):
            self.assertEqual(path, str(self.path))
            w._complete_firmware_download(True, "remote OK")
        with patch.object(w, "is_remote_client_active", return_value=True), \
             patch.object(w.remote_firmware_client, "start", side_effect=upload) as start, \
             patch.object(w, "_acquire_download_serial") as acquire, \
             patch("PyQt5.QtWidgets.QMessageBox.information"):
            d.download_btn.click()
        start.assert_called_once()
        acquire.assert_not_called()
        self.assertFalse(d._running)

    def test_rejected_start_and_cancel_wait_for_terminal_result(self):
        w, d = self.window, self.create_dialog()
        with patch("PyQt5.QtWidgets.QMessageBox.warning"), \
             patch.object(w, "_acquire_download_serial") as acquire:
            d.file_path_edit.setText(str(self.path) + ".missing")
            d.start_download()
        acquire.assert_not_called()
        self.assertFalse(d._running)
        d.file_path_edit.setText(str(self.path))
        with patch.object(w, "_acquire_download_serial", return_value=False), \
             patch.object(w, "_start_builtin_firmware_download"), \
             patch.object(w, "cancel_firmware_download") as cancel, \
             patch("PyQt5.QtWidgets.QMessageBox.warning"):
            d.start_download()
            d.reject()
            cancel.assert_called_once()
            self.assertTrue(d._running)
            w._complete_firmware_download(False, "cancelled")
        self.assertFalse(d._running)
        d.show()
        self.assertTrue(d.close())
        self.assertFalse(d.isVisible())


if __name__ == "__main__":
    unittest.main()
