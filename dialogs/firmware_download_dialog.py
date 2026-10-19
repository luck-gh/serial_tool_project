"""复用下载器完整 UI,将任务交给串口助手管理串口及远程生命周期."""
from PyQt5.QtWidgets import QLabel, QMessageBox, QPushButton, QLayout
from firmware_downloader_project.firmware_downloader_dialog import FirmwareDownloaderDialog
from core.serial_config import serial_kwargs


class ManagedFirmwareDownloadDialog(FirmwareDownloaderDialog):
    """主窗口拥有的下载编辑窗口;只有点击开始才提交一次任务.

    Args:
        owner (SerialTool): 提供本机/远程下载入口,统一执行占用、取消和恢复.
    """

    def __init__(self, owner):
        self.owner = owner
        self._running = False
        self._remote = owner.remote_mode == "client"
        params = owner.config_manager.get_firmware_downloader_params()
        params.pop("always_on_top", None)
        port = owner._firmware_download_port_config()
        if port is None:
            port = serial_kwargs(owner.get_current_serial_config())
        super().__init__(parent=owner, **port, **params)
        # 拖放区的样式有最小高度,分割器不得压缩到路径行与其重叠.
        self.file_group.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.file_group.parentWidget().layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.log_group.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.log_group.parentWidget().layout().setSizeConstraint(QLayout.SetMinimumSize)
        # 按端口标识精确预选,避免 COM7 被枚举中的 COM70 抢先匹配.
        if port["port"]:
            index = next((i for i in range(self.port_combo.count())
                          if self.port_combo.itemText(i).split(" ")[0] == port["port"]), -1)
            if index < 0:
                self.port_combo.addItem(port["port"])
                index = self.port_combo.count() - 1
            self.port_combo.setCurrentIndex(index)
        self.stopbits_combo.setCurrentText(f'{port["stopbits"]:g}')
        # 主窗口的串口契约不支持 Space,此入口不提供无法提交的选项.
        self.parity_combo.removeItem(self.parity_combo.findText("Space"))
        self.baudrate_combo.setEditable(True)
        self.baudrate_combo.setCurrentText(str(port["baudrate"]))
        self.progress_widget.speed_label.hide()
        self.progress_widget.time_label.hide()
        if self._remote:
            self.port_group.hide()
            for button in self.findChildren(QPushButton):
                if button.text() == "下载设置":
                    button.hide()
            hint = QLabel("远程下载：选择本机 BIN 文件；串口、下载协议和 ACK 设置使用设备端配置。")
            hint.setWordWrap(True)
            self.layout().insertWidget(0, hint)
        owner.firmware_log.connect(self._task_log)
        owner.firmware_progress.connect(self._task_progress)

    def _create_port_group(self):
        """保留串口分组引用,供远程模式隐藏及任务期间锁定."""
        self.port_group = super()._create_port_group()
        return self.port_group

    def _create_file_group(self):
        """保留文件分组引用,执行期间保持当前文件选择不变."""
        self.file_group = super()._create_file_group()
        return self.file_group

    def _create_log_group(self):
        """保留日志分组引用,使分割器尊重过滤栏和日志控件的最小尺寸."""
        self.log_group = super()._create_log_group()
        return self.log_group

    def _download_params(self):
        """读取本次 UI 草稿,不改写工具默认配置."""
        params = dict(
            initial_file=self.file_path_edit.text().strip(),
            packet_size=self.packet_size_spin.value(), start_command=self.start_cmd_edit.text(),
            add_packet_crc=self.packet_crc_check.isChecked(), packet_crc_type=self.packet_crc_type_combo.currentText(),
            send_end_string=self.send_end_string_check.isChecked(), end_string=self.end_string_edit.text())
        for prefix in ("start_ack", "packet_ack", "last_packet_ack"):
            params.update({
                f"wait_{prefix}": getattr(self, f"{prefix}_check").isChecked(),
                f"{prefix}_timeout": getattr(self, f"{prefix}_timeout_spin").value(),
                f"{prefix}_check_length": getattr(self, f"{prefix}_length_check").isChecked(),
                f"{prefix}_expected_length": getattr(self, f"{prefix}_length_spin").value(),
                f"{prefix}_check_data": getattr(self, f"{prefix}_data_check").isChecked(),
                f"{prefix}_expected_data": getattr(self, f"{prefix}_data_edit").text(),
                f"{prefix}_data_format": getattr(self, f"{prefix}_format_combo").currentText(),
                f"{prefix}_check_mode": "AND" if "AND" in getattr(self, f"{prefix}_mode_combo").currentText() else "OR",
            })
            if prefix != "start_ack":
                params[f"{prefix}_check_crc"] = getattr(self, f"{prefix}_crc_check").isChecked()
                params[f"{prefix}_crc_type"] = getattr(self, f"{prefix}_crc_type_combo").currentText()
        return params

    def _toggle_download(self):
        """启停按钮只操作当前窗口提交的任务."""
        if self._running:
            self.stop_download()
        else:
            self.start_download()

    def start_download(self):
        """确认文件及本次参数后提交共用入口,不启动独立串口线程."""
        if self._running:
            return
        if (self.owner.remote_mode == "client") != self._remote:
            QMessageBox.warning(self, "下载模式已改变", "请关闭并重新打开下载窗口，确认当前下载目标。")
            return
        path = self.file_path_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "固件下载", "请先选择固件文件。")
            return
        options = {}
        if not self._remote:
            options = dict(port_settings=dict(
                port=self.port_combo.currentText().split(" ")[0],
                baudrate=self.baudrate_combo.currentText(), databits=self.bytesize_combo.currentText(),
                parity=self.parity_combo.currentText(), stopbits=self.stopbits_combo.currentText()),
                download_params=self._download_params())
        self._running = True
        self._set_download_button_running(True)
        self.port_group.setEnabled(False)
        self.file_group.setEnabled(False)
        self.download_settings_dialog.setEnabled(False)
        self.progress_widget.reset()
        self.owner.start_firmware_download_command(
            path, self._task_finished, show_progress=False, **options)

    def stop_download(self):
        """请求取消,完成回执前保持窗口及停止按钮的忙碌状态."""
        if self._running:
            self.owner.cancel_firmware_download()

    def _task_log(self, level, message):
        """仅转发本窗口当前任务的日志."""
        if self._running:
            self.log_widget.append_log(level, message)

    def _task_progress(self, current, total, message):
        """上传和下载进度不等同成功,终态前最多显示 99%."""
        if self._running:
            self.progress_widget.progress_bar.setValue(min(99, int(current * 100 / max(total, 1))))
            self.progress_widget.packets_label.setText(f"{message} ({current}/{total})")

    def _task_finished(self, success):
        """共用入口完成资源恢复后再解锁窗口并展示最终结果."""
        self._running = False
        self._set_download_button_running(False)
        self.port_group.setEnabled(True)
        self.file_group.setEnabled(True)
        self.download_settings_dialog.setEnabled(True)
        message = self.owner._last_firmware_result[1]
        self.progress_widget.packets_label.setText(message)
        if success:
            self.progress_widget.progress_bar.setValue(100)
            QMessageBox.information(self, "下载完成", message)
        else:
            QMessageBox.warning(self, "下载未完成", message)

    def reject(self):
        """Esc 与关闭按钮共用取消及等待策略."""
        if self._running:
            self.stop_download()
        else:
            super().reject()

    def closeEvent(self, event):
        """在任务结束前保持窗口存活,不阻塞等待工作线程."""
        if self._running:
            self.stop_download()
            event.ignore()
        else:
            super().closeEvent(event)
