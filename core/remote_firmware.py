"""远程 BIN 文件传输和任务生命周期;串口下载算法统一复用 FirmwareDownloader.

协议采用带任务 ID 的 begin/chunk/commit/cancel 消息.上传块逐块确认,
只有长度与 SHA-256 全部匹配才启动设备任务;任何阶段都不自动重新烧录.
"""
import base64
import hashlib
import os
import re
import tempfile
import time
import uuid

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from core.firmware_worker import FirmwareDownloadWorker

MAX_SIZE = 64 * 1024 * 1024
CHUNK_SIZE = 32 * 1024


class RemoteFirmwareHost(QObject):
    """设备端单任务协调器,负责上传文件,串口租约和下载线程的生命周期.

    所有方法/Qt 槽运行于 GUI 线程;只有 FirmwareDownloadWorker 接触下载串口.
    active 从 begin 起占用串口操作权限,经过 upload → download → 终态.
    prepare 快照设备端参数,acquire 停止调试读取,release 恢复原调试连接.
    下载线程真正结束并关闭句柄后才清理临时文件,释放租约和发送最终结果.
    断线/心跳超时请求取消,不重试设备数据;已完成任务保存结果用于去重.
    status 为 success/failed/cancelled/unknown,未知状态绝不转换为成功.
    """
    state_changed = pyqtSignal()

    def __init__(self, send, prepare, acquire, release, log, parent=None):
        """注入主窗口的操作函数,不在构造阶段打开串口.

        Args:
            send (Callable): send(message: dict, session: str),只发送到指定连接.
            prepare (Callable): 无参数;校验可用性并返回 (port_config, download_config).
            acquire (Callable): 无参数;停止调试串口并返回恢复快照,失败时抛异常.
            release (Callable): release(snapshot),线程结束后恢复原连接;失败时抛异常.
            log (Callable): log(level: str, message: str),向设备端窗口输出任务日志.
            parent (QObject | None): Qt 父对象,默认 None;生命周期须覆盖下载线程.
        """
        super().__init__(parent)
        self.send = send
        self.prepare = prepare
        self.acquire = acquire
        self.release = release
        self.log = log
        self.active = None
        self.results = {}
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    @property
    def busy(self):
        """是否有上传或下载任务持有设备操作权限;清理和恢复完成后才变为 False."""
        return self.active is not None

    def reply(self, task, event, **fields):
        """仅向仍连接的任务所属 session 回传固件事件.

        Args:
            task (dict): 至少包含 id,session,可用 connected=False 抑制回传.
            event (str): ready/chunk_ack/running/progress/rejected/result 等设备端事件.
            **fields: 该事件的附加字段,例如 offset,status 或 message.
        """
        if task.get("connected", True):
            self.send(dict(type="firmware", id=task["id"], event=event, **fields), task["session"])

    def handle(self, message):
        """处理当前连接的上传与任务请求,校验完整性后才启动下载器.

        Args:
            message (dict): 网络层附加 _session 的固件消息,包含 id 和 action.
                begin 使用 name/size/sha256;chunk 使用 offset/data(Base64);
                commit/cancel/heartbeat 不需要额外参数.

        同一活动 ID 的 begin/commit 不重复创建任务;完成 ID 返回缓存结果.
        上传块必须顺序到达,重复块也作为偏移错误终止.begin 快照设备端配置并
        预留权限,commit 校验长度和 SHA-256 后才停止调试串口,启动工作线程.
        非法 ID 被忽略,其他可报告的错误通过 rejected 或 result 回传.
        """
        task_id = message.get("id", "")
        session = message.get("_session", "")
        if not isinstance(task_id, str) or not re.fullmatch(r"[a-f0-9]{32}", task_id):
            return
        stub = dict(id=task_id, session=session)
        action = message.get("action")
        if task_id in self.results:
            self.reply(stub, "result", **self.results[task_id])
            return
        task = self.active
        if action == "begin":
            if task:
                if task["id"] == task_id and task["session"] == session:
                    self.reply(task, "running")  # 重复 begin 不会创建另一任务
                    return
                self.reply(stub, "rejected", message="串口已被固件任务占用")
                return
            if len(self.results) >= 1024:
                self.reply(stub, "rejected", message="任务记录已达上限，请重启设备端后再操作")
                return
            try:
                size = message.get("size")
                digest = message.get("sha256", "")
                if type(size) is not int or not 0 < size <= MAX_SIZE:
                    raise ValueError("BIN 大小必须为 1 字节至 64 MiB")
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("SHA-256 无效")
                if not str(message.get("name", "")).lower().endswith(".bin"):
                    raise ValueError("只支持 BIN 文件")
                # 在此快照设备端配置, 不接受控制端提供的 COM 或文件路径.
                port, config = self.prepare()
                temp = tempfile.NamedTemporaryFile(prefix="ghowe-", suffix=".bin", delete=False)
                self.active = dict(stub, size=size, digest=digest, file=temp, path=temp.name,
                                   hash=hashlib.sha256(), offset=0, phase="upload",
                                   updated=time.monotonic(), port=port, config=config,
                                   worker=None, restore=None, acquired=False, connected=True)
                self.state_changed.emit()
                self.reply(self.active, "ready", offset=0)
            except Exception as exc:
                self.reply(stub, "rejected", message=str(exc))
            return
        if not task or task["id"] != task_id or task["session"] != session:
            self.reply(stub, "rejected", message="任务不存在或不属于当前连接")
            return
        if action == "cancel":
            self.cancel()
            return
        if action == "heartbeat":
            task["updated"] = time.monotonic()
            return
        if task["phase"] != "upload":
            self.reply(task, "running")
            return
        try:
            if action == "chunk":
                data = base64.b64decode(message.get("data", ""), validate=True)
                if type(message.get("offset")) is not int or message["offset"] != task["offset"]:
                    raise ValueError("上传偏移不匹配；不接受重复或乱序分块")
                if not 0 < len(data) <= CHUNK_SIZE or task["offset"] + len(data) > task["size"]:
                    raise ValueError("上传分块长度越界")
                if task["file"].write(data) != len(data):
                    raise IOError("临时文件短写")
                task["hash"].update(data)
                task["offset"] += len(data)
                task["updated"] = time.monotonic()
                self.reply(task, "chunk_ack", offset=task["offset"])
            elif action == "commit":
                if task["offset"] != task["size"] or task["hash"].hexdigest() != task["digest"]:
                    raise ValueError("BIN 长度或 SHA-256 校验失败")
                task["file"].close()
                task["phase"] = "download"
                task["restore"] = self.acquire()
                task["acquired"] = True
                worker = FirmwareDownloadWorker(task["path"], task["port"], task["config"], self)
                task["worker"] = worker
                worker.log_signal.connect(self.log)
                worker.progress_signal.connect(self.progress)
                # 只在线程真正退出并关闭句柄后释放所有权.
                worker.finished.connect(self.worker_finished)
                self.reply(task, "running")
                worker.start()
            else:
                raise ValueError("未知固件操作")
        except Exception as exc:
            self.finish("failed", str(exc))

    def progress(self, current, total, message):
        """把工作线程的包进度转成固件 progress 事件;没有活动任务时忽略."""
        if self.active:
            self.reply(self.active, "progress", current=current, total=total, message=message)

    def worker_finished(self):
        """仅在 QThread.finished 后读取核心结果,开始清理和恢复调试状态."""
        task = self.active
        if task:
            worker = task["worker"]
            self.finish(worker.result_status, worker.result[1])

    def finish(self, status, message):
        """清理上传临时文件,恢复曾占用的调试连接并缓存,回传终态.

        Args:
            status (str): 核心或上传阶段的 success/failed/cancelled/unknown 状态.
            message (str): 原始结果说明;清理或恢复异常会追加到该说明.

        调用方须保证工作线程已退出.restored=False 表示文件清理或调试恢复
        失败,控制端此时不会把整个操作视为成功;设备下载状态本身保持原值.
        结果缓存用于去重,不跨进程持久化,释放 active 后发出 state_changed.
        """
        task = self.active
        if not task:
            return
        restore_error = ""
        try:
            task["file"].close()
            os.unlink(task["path"])
        except OSError as exc:
            restore_error = f"；临时文件清理失败: {exc}"
        if task["acquired"]:
            try:
                self.release(task["restore"])
            except Exception as exc:
                restore_error += f"；恢复调试串口失败: {exc}"
        result = dict(status=status, message=message + restore_error, restored=not bool(restore_error))
        self.results[task["id"]] = result
        self.log("INFO" if status == "success" else "ERROR", result["message"])
        self.reply(task, "result", **result)
        if task["worker"]:
            task["worker"].deleteLater()
        self.active = None
        self.state_changed.emit()

    def cancel(self):
        """请求取消下载线程,或立即清理尚未启动设备下载的上传任务."""
        if not self.active:
            return
        if self.active["worker"]:
            self.active["worker"].cancel()
        else:
            self.finish("cancelled", "上传已取消；未启动设备下载")

    def disconnect(self):
        """标记任务原连接失效并取消,避免将迟到结果发给新连接."""
        if self.active:
            self.active["connected"] = False
            self.cancel()

    def tick(self):
        """检查上传 30 秒空闲或下载 15 秒心跳超时,下载阶段回传运行心跳."""
        if not self.active:
            return
        if self.active["phase"] == "upload":
            if time.monotonic() - self.active["updated"] > 30:
                self.finish("failed", "上传空闲超时；未启动设备下载")
        else:
            if time.monotonic() - self.active["updated"] > 15:
                self.cancel()
            self.reply(self.active, "running")


class RemoteFirmwareClient(QObject):
    """控制端上传状态机,只持有控制端文件快照,不打开任何本机设备串口.

    在 GUI 线程逐块上传并等待设备端确认,限制内存为 64 MiB,避免无界发送队列.
    committed 在启动请求入队前置位:此后丢失连接/最终回执均为结果未知.
    progress 区分上传字节与下载包数;finished 的布尔值仅在设备端确认成功且
    调试状态恢复成功时为 True.取消等待设备端终态,不自行推断设备已停止.
    """
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, send, parent=None):
        """创建逐块上传的控制端;串口参数和下载协议由设备端提供.

        Args:
            send (Callable): send(message: dict),向当前设备端连接入队;入队不代表完成.
            parent (QObject | None): Qt 父对象,默认 None;定时器随本对象销毁.
        """
        super().__init__(parent)
        self.send = send
        self.active = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)

    def start(self, path):
        """读取控制端 BIN 快照并开始逐块上传,当前调用不打开串口.

        Args:
            path (str): 控制端可读的 .bin 路径,文件必须为 1 字节至 64 MiB.

        控制端只发送 basename,大小和 SHA-256;设备端临时文件路径由设备端生成.

        Raises:
            ValueError: 已有活动任务,扩展名不符或文件长度越界.
            OSError: 文件无法打开或读取.
        """
        if self.active:
            raise ValueError("已有远程下载任务")
        if not path.lower().endswith(".bin"):
            raise ValueError("只支持 BIN 文件")
        # 声明摘要前先快照控制端文件, 大小限制为 64 MiB.
        with open(path, "rb") as source:
            data = source.read(MAX_SIZE + 1)
        if not 0 < len(data) <= MAX_SIZE:
            raise ValueError("BIN 大小必须为 1 字节至 64 MiB")
        self.active = dict(id=uuid.uuid4().hex, data=data, offset=0, expected=0,
                           committed=False, cancelling=False, updated=time.monotonic())
        self.timer.start()
        self.request("begin", name=os.path.basename(path), size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def request(self, action, **fields):
        """向当前连接提交固件动作;发送异常按断线处理,不自动重试.

        Args:
            action (str): begin/chunk/commit/cancel/heartbeat 等动作.
            **fields: 与动作对应的消息字段;id 取自当前活动任务.
        """
        try:
            self.send(dict(type="firmware", id=self.active["id"], action=action, **fields))
        except (ConnectionError, OSError):
            self.disconnect()

    def handle(self, message):
        """消费匹配当前 ID 的设备端事件,推进单块确认上传或结束任务.

        Args:
            message (dict): 设备端回传的固件事件;offset 用于验证上传确认,progress
                携带 current/total/message,result 携带 status/message/restored.

        最后一个上传块确认后,在提交 commit 前标记 committed,避免启动请求
        发出后断线被误判为未开始.专用结果只有 success 且 restored 才报告成功.
        """
        task = self.active
        if not task or message.get("id") != task["id"]:
            return
        task["updated"] = time.monotonic()
        event = message.get("event")
        if event in ("result", "rejected"):
            success = message.get("status") == "success" and message.get("restored", True)
            self.finish(success, message.get("message", "任务被拒绝"))
        elif event in ("ready", "chunk_ack") and not task["cancelling"]:
            if message.get("offset") != task["expected"] or task["committed"]:
                self.cancel()
                return
            task["offset"] = message["offset"]
            data = task["data"]
            self.progress.emit(task["offset"], len(data), "上传 BIN")
            if task["offset"] == len(data):
                task["committed"] = True  # 入队前置位; 丢失回执时结果不确定
                self.request("commit")
                task["data"] = b""
            else:
                chunk = data[task["offset"]:task["offset"] + CHUNK_SIZE]
                task["expected"] = task["offset"] + len(chunk)
                self.request("chunk", offset=task["offset"], data=base64.b64encode(chunk).decode("ascii"))
        elif event == "progress":
            current, total = message.get("current"), message.get("total")
            if type(current) is int and type(total) is int and 0 <= current <= total <= MAX_SIZE and total > 0:
                self.progress.emit(current, total, str(message.get("message", "下载")))
            else:
                self.cancel()

    def cancel(self):
        """标记正在取消并发送 cancel;仍等待设备端终态,不自行认定设备已停止."""
        if self.active and not self.active["cancelling"]:
            self.active["cancelling"] = True
            self.request("cancel")

    def disconnect(self):
        """结束当前请求;已提交下载启动请求时报告未知,提交前只报告上传中断."""
        if self.active:
            message = "结果未知：启动请求发出后连接中断；不会自动重烧" if self.active["committed"] else "上传连接中断；未发出下载启动请求"
            self.finish(False, message)

    def tick(self):
        """十五秒无设备端事件时请求取消并结束本地任务;已提交期间发送心跳.

        此处结束本地任务不直接关闭 TCP,设备端另依据取消或心跳超时释放资源.
        """
        if self.active and time.monotonic() - self.active["updated"] > 15:
            self.cancel()
            self.disconnect()
        elif self.active and self.active["committed"]:
            self.request("heartbeat")

    def finish(self, success, message):
        """停止控制端计时器并释放 BIN 快照,向调用方发出 (success, message)."""
        self.timer.stop()
        self.active = None
        self.finished.emit(success, message)
