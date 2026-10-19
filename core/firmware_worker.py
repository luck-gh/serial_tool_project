"""GUI 与远程设备端的固件下载 Qt 线程适配器."""
import threading

from PyQt5.QtCore import QThread, pyqtSignal
from core.firmware_download import get_firmware_downloader_class


class FirmwareDownloadWorker(QThread):
    """下载核心的 Qt 工作线程适配器,供本地任务和远程设备端复用.

    run 独占下载器及串口句柄,cancel 只设置线程安全的取消标志;创建核心
    前的取消同样有效.result/result_status 保存最终结果,日志与进度通过
    Qt 信号回到 GUI 线程.调用方必须等待 QThread.finished 后再恢复调试
    串口或释放本对象,不能仅凭业务 finished_signal 提前销毁仍运行的线程.
    """

    log_signal = pyqtSignal(str, str)
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, firmware_path: str, port_config: dict, download_config: dict, parent=None):
        """创建尚未启动的下载线程.

        Args:
            firmware_path (str): 执行端可读取的 BIN 路径;远程任务传入设备端临时文件.
            port_config (dict): 执行端串口参数,字段同 FirmwareDownloader.port_config.
            download_config (dict): 下载协议与 ACK 配置,字段同下载核心构造函数.
            parent (QObject | None): Qt 父对象;必须存活至线程结束,默认 None.
        """
        super().__init__(parent)
        self.firmware_path = firmware_path
        self.port_config = dict(port_config)
        self.download_config = dict(download_config)
        self.downloader = None
        self._cancel = threading.Event()
        self.result_status = "failed"
        self.result = (False, "下载未启动")

    def cancel(self):
        """记录取消请求;核心已创建时同时调用 stop,尚未创建时保留取消事件."""
        self._cancel.set()
        if self.downloader is not None:
            self.downloader.stop()

    def run(self):
        """创建共享核心,打开串口并下载,在 finally 中关闭句柄.

        result 保存 (success, message),result_status 保存核心终态;创建前取消不
        访问设备.业务 finished_signal 在清理后发出,但调用方仍应等待 QThread
        的 finished 信号,再销毁对象或恢复调试串口.
        """
        try:
            downloader_class = get_firmware_downloader_class()
            self.downloader = downloader_class(self.port_config, self.download_config)
            if self._cancel.is_set():
                self.result_status = "cancelled"
                self.result = (False, "下载被用户中断（未开始）")
                return
            success, message = self.downloader.open_port()
            if not success:
                self.result = (False, message)
                return
            if self._cancel.is_set():
                self.downloader.stop()
            self.log_signal.emit("INFO", message)
            success, message = self.downloader.download(
                self.firmware_path,
                progress_callback=lambda current, total, text: self.progress_signal.emit(current, total, text),
                log_callback=lambda level, text: self.log_signal.emit(level, text),
            )
            self.result_status = self.downloader.result_status
            self.result = (success, message)
        except Exception as exc:
            self.result = (False, f"下载器启动失败: {exc}")
        finally:
            if self.downloader is not None:
                try:
                    self.downloader.close_port()
                except Exception as exc:
                    self.result_status = "unknown"
                    self.result = (False, f"结果未知：串口释放失败: {exc}")
            self.finished_signal.emit(*self.result)
