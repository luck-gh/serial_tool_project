"""GUI归档的Qt线程适配;状态机与终端共用."""
import os
from PyQt5.QtCore import QObject, QThread, pyqtSignal
from core.archive_buffer import ArchiveBuffer, write_batch


class ArchiveBatch(QThread):
    """后台写不可变批次;拥有者只在finished后消费结果并销毁.

    Args:
        path (Path): 日志路径.
        entries (tuple): (序号,文本)批次.
        parent (QObject): GUI生命周期拥有者.
        create (bool): True独占新建文件.
    """

    def __init__(self, path, entries, parent, create=False):
        super().__init__(parent)
        self.path, self.entries, self.create = path, entries, create
        self.error = ""

    def run(self):
        try:
            write_batch(self.path, self.entries, self.create)
        except (OSError, UnicodeError) as exc:
            self.error = f"日志写入失败: {self.path}: {exc}"


class LogArchive(ArchiveBuffer, QObject):
    """GUI线程持有状态,finished后才完成记账.

    Args:
        path (Path): 会话唯一文件.
        limit (int): 内存预算字节数.
        parent (QObject): GUI生命周期拥有者.
    """
    failed = pyqtSignal(str)
    idle = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self, path, limit, parent):
        QObject.__init__(self, parent)
        ArchiveBuffer.__init__(self, path, limit)

    def _start(self, entries, create):
        self.worker = ArchiveBatch(self.path, entries, self, create=create)
        self.worker.finished.connect(self._completed)
        self.worker.start()

    def _failed(self, error):
        self.failed.emit(error)

    def _changed(self):
        self.changed.emit()

    def _completed(self):
        worker, self.worker = self.worker, None
        self.complete(worker.entries, worker.error)
        worker.deleteLater()
        if not self.busy:
            self.idle.emit()
