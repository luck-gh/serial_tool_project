"""GUI/终端共用的归档状态机;适配器只负责单写者的调度和通知."""
import os
import sys
from pathlib import Path


def write_batch(path, entries, create):
    """整批写入并同步;部分写入失败保留整批,绝不自动重试."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb" if create else "ab") as stream:
        for _sequence, text in entries:
            data = text.encode("utf-8")
            if stream.write(data) != len(data):
                raise OSError("日志文件短写")
        stream.flush()
        os.fsync(stream.fileno())


class ArchiveBuffer:
    """无线程依赖的阈值缓冲;拥有者串行调用,不保存显示历史.

    Args:
        path (Path): 唯一日志文件路径.
        limit (int): 内存预算字节数,80%启动批次.
        create (bool): True独占新建,False显式Log命令追加已有文件.
    """

    def __init__(self, path, limit, create=True):
        self.path = Path(path)
        self.limit = limit
        self.pending = []
        self.pending_bytes = 0
        self.worker = None
        self.error = ""
        self.committed_sequence = -1
        self._force = False
        self._failed_entries = ()
        self._created = not create

    @property
    def busy(self):
        return self.worker is not None

    def append(self, sequence, text):
        """预算不足显式停存,保留已接纳的未确认数据."""
        if self.error:
            return False
        chunk_size = max(1, self.limit // 16)
        for offset in range(0, len(text), chunk_size):
            entry = (sequence, text[offset:offset + chunk_size])
            size = self._entry_bytes(entry)
            if self.pending_bytes + size > self.limit:
                self.error = "日志写入速度不足，缓冲预算已满，保存已停止；本事件剩余及后续事件未收录，已接纳未确认批次可另存"
                self._failed(self.error)
                return False
            self.pending.append(entry)
            self.pending_bytes += size
            self.flush()
        return True

    def flush(self, force=False):
        """80%阈值或显式补尾时交给单写者."""
        self._force |= force
        if self.busy or self.error:
            return
        if not self.pending:
            self._force = False
            return
        if not self._force and self.pending_bytes < max(1, self.limit * 4 // 5):
            return
        entries, self.pending = tuple(self.pending), []
        self.pending_bytes = 0
        self._force = False
        self._start(entries, not self._created)
        self._changed()

    def complete(self, entries, error):
        """写者退出后才确认整批或保留失败批次."""
        if error:
            self._failed_entries = entries
            self.error = error + "；文件尾部可能部分写入，未自动重试，未确认批次可另存"
            self._failed(self.error)
        else:
            self._created = True
            self.committed_sequence = entries[-1][0]
        self.flush()
        self._changed()

    def unsaved_entries(self):
        if self.busy:
            raise ValueError("日志批次尚未结束，请稍后另存")
        return self._failed_entries + tuple(self.pending)

    @staticmethod
    def _entry_bytes(entry):
        return sys.getsizeof(entry) + sum(sys.getsizeof(value) for value in entry)

    def recover_to(self, path):
        """显式另存至全新文件,不追加可能部分写入的原文件."""
        entries = self.unsaved_entries()
        if not entries:
            raise ValueError("没有未确认日志可另存")
        self.path = Path(path)
        self.pending = list(entries)
        self.pending_bytes = sum(self._entry_bytes(entry) for entry in entries)
        self._failed_entries = ()
        self.error = ""
        self._created = False
        self.flush(force=True)

    def status(self):
        """返回元数据,不复制正文或读取磁盘."""
        return dict(path=str(self.path), busy=self.busy, error=self.error,
                    committed_sequence=self.committed_sequence, pending_bytes=self.pending_bytes,
                    pending_entries=len(self.pending), failed_entries=len(self._failed_entries),
                    recoverable=bool(self._failed_entries or self.pending), limit_bytes=self.limit)
