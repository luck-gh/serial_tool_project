"""有界日志历史、产生时筛选、流显示与可选后台归档."""
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import time
import sys
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat

from core.command_types import OutputSource
from core.log_archive import LogArchive
from core.output_rules import OutputRules, event_timestamp


@dataclass(frozen=True, slots=True)
class OutputRecord:
    text: str
    source_type: OutputSource
    color_name: str
    system_level: str = "normal"
    timestamp: str = ""
    sequence: int = 0

    @property
    def memory_bytes(self):
        """记录及字段的内存预算;共享字段也计入,不采样整个进程."""
        return sys.getsizeof(self) + sum(sys.getsizeof(value) for value in (
            self.text, self.source_type, self.color_name, self.system_level,
            self.timestamp, self.sequence))


class OutputManager(QObject):
    """GUI 线程中的有界历史和增量显示,文件批次交给 LogArchive.

    Args:
        text_browser (CustomTextBrowser): 接收区只读纯文本控件,拥有本对象.
        timestamp_check (callable): 当前是否显示已保留时间戳.
        send_color_getter (callable): 当前发送颜色.
        source_filter_getter (callable | None): 当前来源显示规则.
        system_level_filter_getter (callable | None): 系统级别显示规则.
        capture_filter (callable): 来源及级别是否收录,与显示独立.
        keep_timestamp (callable): 是否保留事件时间,与显示独立.
        limit (int): 记录内存预算,包括正文/时间戳/元数据,默认4 MB.
    """
    SOURCE_PROPERTY = QTextFormat.UserProperty + 1
    LOG_GRACE_MS = 20
    RENDER_INTERVAL_MS = 20
    CHUNK_CHARS = 4096
    archive_failed = pyqtSignal(str)
    archive_idle = pyqtSignal()
    archive_changed = pyqtSignal()

    def __init__(self, text_browser, timestamp_check, send_color_getter,
                 source_filter_getter=None, system_level_filter_getter=None,
                 capture_filter=lambda source, level: True,
                 keep_timestamp=lambda: True, limit=4_000_000):
        super().__init__(text_browser)
        self.text_browser = text_browser
        self.limit = limit
        self.capture_filter = capture_filter
        self.keep_timestamp = keep_timestamp
        self.system_level_filter_getter = system_level_filter_getter
        self._records = deque()
        self._history_bytes = 0
        self._sequence = 0
        self._display_records = deque()
        self._render_queue = deque()
        self._rendered_records = deque()
        self._pending_logs = []
        self._receive_pending_cr = False
        self._receive_line_open = False
        self.archive = None
        self.archive_enabled = False
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.timeout.connect(self.flush_pending_output)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(self.RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render_batch)
        self.rules = OutputRules(timestamp_check, lambda: True, send_color_getter,
                                 source_filter_getter, "[%Y-%m-%d %H:%M:%S.%f]", True)

    @property
    def records(self):
        """提供有界历史快照;外部查询不能改变内部队列."""
        return list(self._records)

    def set_limit(self, limit):
        """应用已由 UI/schema 校验的字节预算,缩小时立即淘汰旧历史."""
        self.limit = limit
        if self.archive:
            self.archive.limit = limit
            self.archive.flush()
        self._trim_history()

    def configure_archive(self, enabled, directory):
        """切换后续信息是否归档;关闭补写尾部,重新开启不回灌旧历史."""
        directory = Path(directory).resolve()
        if enabled:
            if self.archive and self.archive.error:
                raise ValueError("请先另存未确认日志，再重新启用保存完整日志")
            if self.archive is None or self.archive.path.parent != directory:
                if self.archive and (self.archive.busy or self.archive.pending):
                    raise ValueError("上一日志尚未写完，暂不能更换日志目录")
                if self.archive:
                    self.archive.deleteLater()
                name = datetime.now().strftime("serial-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".log"
                self.archive = LogArchive(directory / name, self.limit, self)
                self.archive.failed.connect(self._archive_failed)
                self.archive.idle.connect(self.archive_idle)
                self.archive.changed.connect(self.archive_changed)
        self.archive_enabled = enabled
        if not enabled and self.archive:
            self.archive.flush(force=True)

    def _archive_failed(self, message):
        self.archive_enabled = False
        self.archive_failed.emit(message)

    def append_text(self, text, source_type, color=None, system_level="normal", timestamp=None):
        """按产生时设置决定是否保留,先归档入队再裁剪历史;返回原事件记录.

        未保留的记录只作为调用结果存在,不进入历史/显示/文件. 原始 RX 和
        CLI 协议由上游独立处理,不会因这里的筛选或显示分片改变.
        """
        if source_type == OutputSource.RECEIVE:
            text = self._coalesce_receive_crlf(text)
        text = self._normalize_document_text(text)
        if source_type != OutputSource.RECEIVE:
            text += "\n"
        level = self._normalize_system_level(system_level)
        color_name = color or self.rules.color_for(source_type)
        if color_name == "white":
            color_name = "black"
        admitted = self.capture_filter(source_type, level)
        first_rx = self.rules.last_receive_timestamp
        self.rules.before_append(source_type)
        if source_type == OutputSource.RECEIVE and text:
            self.rules.last_receive_timestamp = False
        keep_time = admitted and self.keep_timestamp()
        stamp = (timestamp or event_timestamp()) if keep_time and (
            source_type != OutputSource.RECEIVE or first_rx) else ""
        result = OutputRecord(text, source_type, color_name, level, stamp, self._sequence)
        if not admitted or not text:
            return result
        # 单个巨型响应也分片入库/绘制,不把一次 Qt 调用变成无界工作.
        step = min(self.CHUNK_CHARS, max(1, self.limit // 16))
        for offset in range(0, len(text), step):
            record = replace(result, text=text[offset:offset + step],
                             timestamp=stamp if offset == 0 else "", sequence=self._sequence)
            self._sequence += 1
            self._records.append(record)
            self._history_bytes += record.memory_bytes
            if self.archive_enabled:
                self.archive.append(record.sequence, self._record_text(record, stored=True))
            self._trim_history()
            self._queue_record(record)
        return result

    def _queue_record(self, record):
        if record.source_type == OutputSource.RECEIVE:
            if self._pending_logs and "\n" in record.text:
                first, _, rest = record.text.partition("\n")
                self._display_record(replace(record, text=first + "\n"))
                self.flush_pending_output()
                if rest:
                    self._display_record(replace(record, text=rest, timestamp=""))
            else:
                self._display_record(record)
            self._receive_line_open = not record.text.endswith("\n")
            if not self._receive_line_open:
                self.flush_pending_output()
        elif record.source_type in (OutputSource.SYSTEM, OutputSource.ERROR) and self._receive_line_open:
            self._pending_logs.append(record)
            if not self._log_timer.isActive():
                self._log_timer.start(self.LOG_GRACE_MS)
        else:
            self.flush_pending_output()
            self._display_record(record)

    def _trim_history(self):
        if self._history_bytes <= self.limit:
            return
        # 一次淘汰到 75%,避免每新增一个字符就全量重绘或移动容器.
        target = self.limit * 3 // 4
        while self._records and self._history_bytes > target:
            old = self._records.popleft()
            self._history_bytes -= old.memory_bytes
        floor = self._records[0].sequence if self._records else self._sequence
        self._display_records = deque(r for r in self._display_records if r.sequence >= floor)
        self._pending_logs = [r for r in self._pending_logs if r.sequence >= floor]
        self._render_queue = deque(r for r in self._render_queue if r.sequence >= floor)
        self._remove_expired_display(floor)

    def _remove_expired_display(self, floor):
        """按实际插入的UTF-16区间删除淘汰记录,流动时不反复重绘整个历史.

        宽限日志可能排在较新RX片段后面,因此按序号删除对应区间,不能简单
        裁掉最后一个旧记录之前的全部文字. 逆序删除保持尚未处理的位置有效.
        """
        ranges, kept = [], deque()
        offset = 0
        for sequence, length in self._rendered_records:
            if sequence < floor:
                if ranges and ranges[-1][1] == offset:
                    ranges[-1] = (ranges[-1][0], offset + length)
                else:
                    ranges.append((offset, offset + length))
            else:
                kept.append((sequence, length))
            offset += length
        self._rendered_records = kept
        cursor = QTextCursor(self.text_browser.document())
        cursor.beginEditBlock()
        try:
            for start, end in reversed(ranges):
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
        finally:
            cursor.endEditBlock()

    def _display_record(self, record):
        self._display_records.append(record)
        if self._record_enabled(record):
            self._render_queue.append(record)
            if not self._render_timer.isActive():
                self._render_timer.start()

    def flush_pending_output(self):
        """结束日志展示宽限;实际排版仍由有界刷新批次处理."""
        self._log_timer.stop()
        self._receive_line_open = False
        pending, self._pending_logs = self._pending_logs, []
        for record in pending:
            self._display_record(record)

    def refresh_display(self):
        """仅从仍保留的历史分批重绘,不补回产生时已舍弃的内容."""
        self.text_browser.clear()
        self._rendered_records.clear()
        self._render_queue = deque(r for r in self._display_records if self._record_enabled(r))
        if self._render_queue:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    def _render_batch(self):
        """每批按 64K 字符/8ms 预算推进,成批通知查找与滚动,让出事件循环."""
        deadline = time.monotonic() + 0.008
        scrollbar = self.text_browser.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum()
        cursor = QTextCursor(self.text_browser.document())
        cursor.movePosition(QTextCursor.End)
        count = 0
        cursor.beginEditBlock()
        try:
            while self._render_queue and count < 65536 and time.monotonic() < deadline:
                record = self._render_queue.popleft()
                start = cursor.position()
                self._insert_record(cursor, record)
                self._rendered_records.append((record.sequence, cursor.position() - start))
                count += len(record.text)
        finally:
            cursor.endEditBlock()
        if follow:
            scrollbar.setValue(scrollbar.maximum())
        if not self._render_queue:
            self._render_timer.stop()

    def _insert_record(self, cursor, record):
        char_format = QTextCharFormat()
        char_format.setProperty(self.SOURCE_PROPERTY, record.source_type.value)
        char_format.setForeground(QColor(record.color_name))
        cursor.setCharFormat(char_format)
        if record.source_type != OutputSource.RECEIVE and not cursor.atBlockStart():
            cursor.insertText("\n")
        # 无换行设备流也不能形成无限长Qt块. 先组合显示分段,一次插入整块,
        # 避免多行寄存器响应每一行都触发一次Qt编辑. 原历史/文件不插显示换行.
        parts = self._record_text(record).split("\n")
        pieces = []
        column = cursor.positionInBlock()
        for index, segment in enumerate(parts):
            while segment:
                if column >= self.CHUNK_CHARS:
                    pieces.append("\n")
                    column = 0
                room = self.CHUNK_CHARS - column
                piece, segment = segment[:room], segment[room:]
                pieces.append(piece)
                column += len(piece)
            if index < len(parts) - 1:
                pieces.append("\n")
                column = 0
        cursor.insertText("".join(pieces))

    def _record_text(self, record, stored=False):
        if record.timestamp and (stored or self.rules.timestamp_enabled()):
            return self.rules.current_timestamp(record.timestamp) + record.text
        return record.text

    def _source_enabled(self, source, level):
        return self.rules.source_enabled(source) and (
            source != OutputSource.SYSTEM or self.system_level_filter_getter is None
            or self.system_level_filter_getter(level))

    def _record_enabled(self, record):
        return self._source_enabled(record.source_type, record.system_level)

    @staticmethod
    def _normalize_system_level(level):
        level = str(level or "normal").lower()
        return level if level in {"normal", "warning", "info", "debug"} else "normal"

    def text_for_sources(self, source_types=None, system_levels=None):
        """导出当前仍保留的历史,不读取或改写已归档文件."""
        selected = None if source_types is None else {source.value for source in source_types}
        return "".join(self._record_text(r, stored=True) for r in self._records
                       if (selected is None or r.source_type.value in selected)
                       and (r.source_type != OutputSource.SYSTEM or system_levels is None
                            or r.system_level in system_levels))

    def reset_receive_timestamp(self):
        self.flush_pending_output()
        self.rules.reset_receive_timestamp()

    def _coalesce_receive_crlf(self, text):
        if not text:
            return text
        if self._receive_pending_cr and text.startswith("\n"):
            text = text[1:]
        self._receive_pending_cr = text.endswith("\r")
        return text

    @staticmethod
    def _normalize_document_text(text):
        return str(text).replace("\r\n", "\n").replace("\r", "\n")

    def clear(self):
        """清空临时历史及显示,文件保存的尾部仍补写,累计序号不复用."""
        if self.archive:
            self.archive.flush(force=True)
        self._records.clear()
        self._history_bytes = 0
        self._display_records.clear()
        self._render_queue.clear()
        self._rendered_records.clear()
        self._pending_logs.clear()
        self._receive_line_open = False
        self._log_timer.stop()
        self._render_timer.stop()
        self.text_browser.clear()
        self._receive_pending_cr = False
        self.rules.reset_receive_timestamp()
