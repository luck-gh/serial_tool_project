"""串口文本的增量解码,终端安全显示与响应正则匹配."""
import codecs
import re
import threading


class ReceiveSaveError(OSError):
    """RX 文件写入失败;设备操作可能已经执行,不代表串口操作失败."""


class ReceivedFile:
    """流式追加原始设备 RX,不受显示规则影响,也不保存 TX 或历史缓存.

    Args:
        path: CLI 当前工作目录下的文件路径;None 表示尚未启用.
        emit: 可选文件状态回调;与 GUI/CLI 共用系统事件,不混入 RX 字节.
    """

    def __init__(self, path=None, emit=None):
        self.lock = threading.RLock()
        self.stream = None
        self.path = None
        self.emit = emit
        self.last_event = None
        if path is not None:
            self.start(path)

    def start(self, path):
        """打开新目标后才替换旧记录器,打开失败保留旧目标."""
        from core.command_templates import template_path
        path = template_path(path)
        try:
            new_stream = open(path, "ab", buffering=0)
        except OSError as exc:
            self._publish("failed", path, exc, failure="open")
            exc.file_event = self.last_event
            raise
        with self.lock:
            previous = self.stream
            self.stream = new_stream
            self.path = path
            if previous is not None:
                previous.close()
            self._publish("started", path)

    def _publish(self, outcome, path, error=None, **fields):
        from core.output_rules import file_operation_event
        self.last_event = dict(file_operation_event("receive-save", path, outcome, error=error,
                                                  failure=fields.get("failure")), **fields)
        if self.emit is not None:
            self.emit(self.last_event)

    def feed(self, data):
        """同步写入一块原始 RX;错误关闭记录器并抛出明确的保存故障."""
        with self.lock:
            if self.stream is None:
                return
            try:
                if self.stream.write(data) != len(data):
                    raise OSError("文件短写")
            except OSError as exc:
                path = self.path or "未知文件"
                self.close(notify=False)
                self._publish("failed", path, exc, failure="write")
                raise ReceiveSaveError(self.last_event["message"]) from exc

    def close(self, notify=True):
        """关闭文件;无缓冲写入保证此前已写 RX 在退出,超时或断线后保留."""
        with self.lock:
            if self.stream is not None:
                stream, self.stream = self.stream, None
                stream.close()
                if notify:
                    self._publish("stopped", self.path)


class ReceiveDecoder:
    """跨数据块保留 UTF-8 半个字符,非法字节显示为可检查的转义."""

    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")("backslashreplace")

    def feed(self, data: bytes, final: bool = False) -> str:
        """返回新增文本;final 只在流结束时冲刷未完成的多字节序列."""
        return self.decoder.decode(data, final=final)


def terminal_text(text: str) -> str:
    """保留换行和制表,其他控制字符可视化以避免设备数据控制终端."""
    return "".join(character if character in "\r\n\t" or character.isprintable()
                   else f"\\x{ord(character):02x}" for character in text)


class ExpectMatcher:
    """在完整的增量 UTF-8 文本上查找已编译正则,不截断跨块匹配."""

    def __init__(self, pattern: str):
        self.pattern = re.compile(pattern)
        self.decoder = ReceiveDecoder()
        self.text = ""
        self.matched = self.pattern.search("") is not None
        self.received_bytes = 0
        self.preview = bytearray()

    def feed(self, data: bytes) -> bool:
        """接收原始字节并更新匹配结果,显示转义不参与匹配."""
        self.received_bytes += len(data)
        # 摘要仅保留前 128 字节;任意跨块正则仍复用既有完整匹配文本.
        self.preview.extend(data[:max(0, 128 - len(self.preview))])
        self.text += self.decoder.feed(data)
        self.matched = self.matched or self.pattern.search(self.text) is not None
        return self.matched

    def finish(self) -> bool:
        """流结束时冲刷残留字节并返回最终匹配结果."""
        self.text += self.decoder.feed(b"", final=True)
        self.matched = self.matched or self.pattern.search(self.text) is not None
        return self.matched

    def result_fields(self):
        """返回有界设备 RX 摘要;IPC 流量和其他订阅事件不参与计数."""
        return dict(expect_matched=self.matched, received_bytes=self.received_bytes,
                    received_preview=bytes(self.preview).decode("utf-8", errors="backslashreplace"),
                    received_preview_hex=self.preview.hex(" "),
                    received_preview_truncated=self.received_bytes > len(self.preview))

    def result_message(self):
        """区分已匹配,有响应但不匹配以及完全没有设备响应."""
        if self.matched:
            return "响应已匹配"
        if self.received_bytes:
            return f"已接收 {self.received_bytes} 字节，但响应不匹配 --expect"
        return "设备无响应：观察窗口内未收到串口数据，--expect 未匹配"
