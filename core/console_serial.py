"""独立控制台的串口拥有者;普通接收与下载 ACK 读取严格交接."""
import base64
import copy
import os
import subprocess
import sys
import threading

from core.console_execution import OperationUnknown


def write_serial_once(port, data):
    """按pySerial实际写入计数确认一次发送,异常/短写不补发剩余数据."""
    try:
        count = port.write(data)
    except OSError as exc:
        raise OperationUnknown(f"发送异常，设备结果未知: {exc}；未重发") from exc
    if count != len(data):
        raise OperationUnknown(f"串口短写 {count}/{len(data)}，设备结果未知；未重发")
    return count


class LocalConsoleTransport:
    """管理本进程的串口与配置,不需要创建 Qt 窗口.

    Args:
        config_manager: 当前配置文件的 ConfigManager.
        emit: 接收 Base64 RX/TX 及错误进度事件的线程安全回调.
    """

    def __init__(self, config_manager, emit):
        self.config_manager = config_manager
        self.emit = emit
        self.serial = None
        self.reader = None
        self._stop = threading.Event()
        self._failure = None

    def snapshot(self):
        from core.cli_runner import get_state
        return copy.deepcopy(dict(state=get_state(self.config_manager), tools=self.config_manager.config.get("tools", {})))

    def status(self):
        serial = dict(self.snapshot()["state"].get("basic_settings", {}))
        connected = self.serial is not None and self.serial.is_open
        serial["connected"] = connected
        if connected:
            serial["active_port"] = self.serial.port
            serial["active_baudrate"] = self.serial.baudrate
        return dict(connected=connected, serial=serial, busy=False, remote_mode="local")

    def open(self):
        """按所选配置打开串口,只启动一个读线程;已打开时幂等."""
        from core.cli_runner import open_serial_from_config, get_basic_settings
        if self.serial is not None and self.serial.is_open:
            return
        _, basic = get_basic_settings(self.config_manager)
        try:
            self.serial = open_serial_from_config(basic)
            self.serial.timeout = 0.1
            self.serial.write_timeout = 1.0
        except Exception as exc:
            self.serial = None
            raise RuntimeError(f"串口打开失败: {exc}；若由 GUI 占用，请使用 --via-gui 并核对 --config") from exc
        self._failure = None
        self._stop.clear()
        self.reader = threading.Thread(target=self._read, name="console-serial-reader")
        self.reader.start()

    def _read(self):
        port = self.serial
        try:
            while not self._stop.is_set():
                data = port.read(1)
                if data:
                    waiting = min(port.in_waiting, 65535)
                    if waiting:
                        data += port.read(waiting)
                    self.emit(dict(event="receive", source="receive", data=base64.b64encode(data).decode("ascii")))
        except Exception as exc:
            if not self._stop.is_set():
                self._failure = str(exc)
                self.emit(dict(event="progress", source="error", fatal=True, message=f"串口读取失败: {exc}"))
        finally:
            port.close()
            if self.serial is port:
                self.serial = None

    def close(self):
        """请求取消读取后等待唯一读线程释放句柄,绝不并发 close."""
        self._stop.set()
        port = self.serial
        if port is not None:
            try:
                port.cancel_read()
            except Exception:
                pass
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(2)
            if self.reader.is_alive():
                raise RuntimeError("串口读线程未能退出，未启动其他串口拥有者")
            self.reader = None

    def write(self, data, display):
        """写一次实际字节,失败或短写不重发;发送来源不影响事件镜像."""
        port = self.serial
        if port is None or not port.is_open:
            raise RuntimeError("请先 Open 打开串口")
        write_serial_once(port, data)
        self.emit(dict(event="progress", source="send", data=base64.b64encode(data).decode("ascii"), message=display))

    def set_setting(self, name, parameter):
        """应用 ConsoleExecutor 已校验和规范化的设置,波特率立即生效.

        Args:
            name: BaudRate,ComPort 或 SetEndlog,端口只影响下一次打开.
            parameter: 执行器提供的规范字符串;结束符已展开,波特率为十进制整数.
        """
        from core.cli_runner import get_state, get_basic_settings, save_state, set_com_port
        state, basic = get_basic_settings(self.config_manager)
        if name == "BaudRate":
            value = int(parameter)
            if self.serial is not None:
                self.serial.baudrate = value
            basic["baudrate"] = str(value)
        elif name == "ComPort":
            set_com_port(self.config_manager, parameter)
        elif name == "SetEndlog":
            state.setdefault("send_settings", {})["ending"] = parameter
        else:
            raise ValueError(f"不支持设置: {name}")
        save_state(self.config_manager, get_state(self.config_manager))

    def download(self, parameter, cancel_event):
        """暂停调试读者,下载器独占自己的句柄,释放后恢复之前的调试连接."""
        from core.firmware_download import (resolve_firmware_path, build_download_config,
            get_firmware_downloader_class, build_external_firmware_arguments)
        from core.cli_runner import get_basic_settings
        from core.serial_config import serial_kwargs
        manager = self.config_manager
        if not manager.is_tool_enabled("firmware_downloader"):
            raise ValueError("固件下载工具未启用")
        params = manager.get_firmware_downloader_params()
        path, error = resolve_firmware_path(parameter, params)
        if error:
            raise ValueError(error)
        if not path.lower().endswith(".bin") or not 0 < os.path.getsize(path) <= 64 * 1024 * 1024:
            raise ValueError("请选择 1 字节至 64 MiB 的 BIN 文件")
        config = build_download_config(params)
        downloader_class = get_firmware_downloader_class()
        downloader_class.validate_config(config)
        _, basic = get_basic_settings(manager)
        port_config = serial_kwargs(basic)
        restore = self.serial is not None
        if restore:
            # ComPort 仅改所选端口,下载及恢复必须使用当前活动句柄的真实参数.
            active = self.serial
            port_config = dict(port=active.port, baudrate=active.baudrate, bytesize=active.bytesize,
                               parity=active.parity, stopbits=active.stopbits)
        self.close()
        downloader = None
        watcher_stop = threading.Event()
        watcher = None
        try:
            custom_path = manager.get_tool_path("firmware_downloader")
            if custom_path:
                arguments = build_external_firmware_arguments(path, port_config, params)
                command = [sys.executable, custom_path, *arguments] if custom_path.lower().endswith(".py") else [custom_path, *arguments]
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      cwd=os.path.dirname(custom_path) or None,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
                    while True:
                        try:
                            output, _ = process.communicate(timeout=0.1)
                            if output:
                                self.emit(dict(event="progress", source="system", message=output.decode("utf-8", errors="replace")))
                            break
                        except subprocess.TimeoutExpired:
                            if cancel_event.is_set():
                                process.terminate()
                                try:
                                    process.communicate(timeout=1.5)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    process.communicate()
                                raise OperationUnknown("结果未知：外部下载器已请求取消，无法核实已发送设备操作")
                    raise OperationUnknown(f"结果未知：外部下载器退出码 {process.returncode}，无法核实其完成确认语义")
            downloader = downloader_class(port_config, config)
            success, message = downloader.open_port()
            if not success:
                raise RuntimeError(message)
            def watch_cancel():
                while not watcher_stop.wait(0.025):
                    if cancel_event.is_set():
                        downloader.stop()
                        return
            watcher = threading.Thread(target=watch_cancel, name="console-download-cancel")
            watcher.start()
            if cancel_event.is_set():
                downloader.stop()
            success, message = downloader.download(path,
                progress_callback=lambda current, total, text: self.emit(dict(event="progress", source="system", current=current, total=total, message=text)),
                log_callback=lambda level, text: self.emit(dict(event="progress", source="error" if level == "ERROR" else "system", message=text)))
            if not success:
                if downloader.result_status == "cancelled":
                    raise InterruptedError()
                if downloader.result_status == "unknown":
                    raise OperationUnknown(message)
                raise RuntimeError(message)
        finally:
            watcher_stop.set()
            if watcher is not None:
                watcher.join(1)
            if downloader is not None:
                downloader.close_port()
            if restore:
                basic.update(port=port_config["port"], port_node=port_config["port"], baudrate=str(port_config["baudrate"]),
                             databits=str(port_config["bytesize"]),
                             parity={"N": "None", "E": "Even", "O": "Odd", "M": "Mark"}[port_config["parity"]],
                             stopbits=str(port_config["stopbits"]).removesuffix(".0"))
                self.open()
                from core.cli_runner import save_state, get_state
                save_state(manager, get_state(manager))
