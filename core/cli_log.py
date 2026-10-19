"""纯Python终端日志归档;无Qt事件循环,不保留显示历史."""
from datetime import datetime
from pathlib import Path
import threading
import uuid

from core.archive_buffer import ArchiveBuffer, write_batch
from core.log_settings import settings_from_state, capture_enabled
from core.output_rules import OutputRules
from core.command_types import OutputSource


class ThreadArchive(ArchiveBuffer):
    """单后台写者;调用线程join后推进共享状态机.

    Args:
        path (Path): 目标文件.
        limit (int): 缓冲预算字节数.
        create (bool): True新建会话文件,False显式追加.
    """

    def __init__(self, path, limit, create=True):
        super().__init__(path, limit, create)
        self._done = threading.Event()
        self._result = None

    def _start(self, entries, create):
        self._done.clear()
        def write():
            error = ""
            try:
                write_batch(self.path, entries, create)
            except (OSError, UnicodeError) as exc:
                error = f"日志写入失败: {self.path}: {exc}"
            self._result = (entries, error)
            self._done.set()
        self.worker = threading.Thread(target=write, name="cli-log-writer")
        self.worker.start()

    def _failed(self, error):
        pass

    def _changed(self):
        pass

    def reap(self, wait=False):
        """只有写者真正退出才更新完成序号."""
        if self.worker is not None and (wait or self._done.is_set()):
            self.worker.join()
            self.worker = None
            entries, error = self._result
            self._result = None
            self.complete(entries, error)

    def drain(self):
        """停止生产后补写尾部并等待所有已受理批次,失败不自动重试."""
        self.flush(force=True)
        while self.worker is not None:
            self.reap(wait=True)
        if self.error:
            raise OSError(self.error)


class CliLog:
    """终端输出事件的独立收录和归档,调用者负责串行访问.

    Args:
        state (dict): 应用状态快照.
        automatic (bool): 独立后端按save_full_log启动,桥接后端只允许显式Log.
        config_file (str | None): 工作配置路径,默认日志位于其旁的logs目录.
    """

    def __init__(self, state, automatic=True, config_file=None):
        self.state = state
        self.automatic = automatic
        self.default_directory = (Path(config_file).resolve().parent if config_file else Path.cwd()) / "logs"
        self.settings = settings_from_state(state)
        self.archive = None
        self.enabled = False
        self.explicit = False
        self.sequence = 0
        self.rules = OutputRules(lambda: self.settings["keep_timestamp"], lambda: True, lambda: "red")
        self.configure(state)

    def validate(self, settings):
        if self.archive:
            self.archive.reap()
            if settings["log_directory"] != self.settings["log_directory"] and (
                    self.enabled or self.archive.busy or self.archive.pending or self.archive.error):
                raise ValueError("日志开启或存在未确认批次时不能更改目录")
            if settings["save_full_log"] and self.archive.error:
                raise ValueError("请先另存未确认日志")

    def configure(self, state):
        settings = settings_from_state(state)
        # 桥接只同步收录偏好,GUI自动保存状态不能接管本机显式Log的启停/恢复.
        if self.automatic and not self.explicit:
            self.validate(settings)
        self.state, self.settings = state, settings
        if self.archive:
            self.archive.limit = settings["limit_mb"] * 1_000_000
        if self.explicit or not self.automatic:
            return
        if settings["save_full_log"]:
            directory = Path(settings["log_directory"] or self.default_directory).resolve()
            if self.archive is None or self.archive.path.parent != directory:
                name = datetime.now().strftime("serial-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".log"
                self.archive = ThreadArchive(directory / name, settings["limit_mb"] * 1_000_000)
            self.enabled = True
        else:
            self.enabled = False
            if self.archive:
                self.archive.drain()

    def capture(self, text, source, level, timestamp, ending):
        self.sequence += 1
        if self.archive:
            self.archive.reap()
        if not self.enabled:
            return
        if self.archive.error:
            self.enabled = False
            raise OSError(self.archive.error)
        if capture_enabled(self.state, self.settings, source, level):
            source_type = OutputSource(source)
            first_receive = self.rules.last_receive_timestamp
            rendered = (self.rules.current_timestamp(timestamp) if self.settings["keep_timestamp"] and
                        (source_type != OutputSource.RECEIVE or first_receive) else "")
            self.rules.before_append(OutputSource(source))
            if source_type == OutputSource.RECEIVE and text:
                self.rules.last_receive_timestamp = False
            if not self.archive.append(self.sequence, f"[{source.upper()}] {rendered}{text}{ending}"):
                self.enabled = False
                raise OSError(self.archive.error)

    def start_explicit(self, path):
        """显式Log保持追加语义;旧归档补尾成功后才切换."""
        target = Path(path).resolve()
        with target.open("ab"):
            pass
        self.close()
        self.archive = ThreadArchive(target, self.settings["limit_mb"] * 1_000_000, create=False)
        self.explicit = True
        self.enabled = True

    def status(self):
        if self.archive:
            self.archive.reap()
            if self.archive.error:
                self.enabled = False
        return dict(scope="current-process", enabled=self.enabled, explicit=self.explicit,
                    archive=self.archive.status() if self.archive else None)

    def recover(self, path):
        if self.archive is None:
            raise ValueError("当前进程没有归档可恢复")
        self.archive.reap()
        self.enabled = False
        self.archive.recover_to(Path(path).resolve())
        self.archive.drain()
        return self.status()

    def close(self):
        self.enabled = False
        if self.archive:
            self.archive.drain()
