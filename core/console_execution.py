"""两种控制台共用的命令执行与模块循环;设备访问由实际串口拥有者提供."""
import threading

from core.command_parser import (
    Command, RawData, parse_input, COMMANDS, CONSOLE_LOCAL,
    parse_hex_bytes, normalize_ending_text, get_ending_bytes,
)
from core.command_executor import collect_module_commands, ConfigCommandProvider


class OperationUnknown(RuntimeError):
    """设备可能已收到数据,但执行端无法确认最终结果."""


class _EndRound(Exception):
    pass


class ConsoleExecutor:
    """串行执行设备命令,控制请求可从输入线程取消后台任务.

    Args:
        transport: 提供 snapshot/write/set_setting/open/close/status/download 的后端.
        emit: 接收进度事件字典的线程安全回调,不直接操作窗口控件.
    """

    def __init__(self, transport, emit):
        self.transport = transport
        self.emit = emit
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._skip_round = threading.Event()
        self.started = threading.Event()

    @property
    def busy(self):
        return self._lock.locked()

    def cancel(self):
        """只请求取消本执行器任务,不关闭 GUI 或其他进程的串口."""
        self._cancel.set()

    def stop_continuous(self, mode):
        """0 结束当前模块轮次并保留循环,1 停止全部后续轮次."""
        if mode not in (0, 1):
            raise ValueError("StopContinuous 参数必须为 0 或 1")
        if mode:
            self.cancel()
        else:
            self._skip_round.set()

    @staticmethod
    def control(text):
        """返回绕过任务占用检查的控制命令,普通文本返回 None."""
        parsed = parse_input(text)
        if isinstance(parsed, Command) and parsed.name in {"Status", "Cancel", "StopContinuous"}:
            return parsed
        return None

    def execute(self, text):
        """执行一次请求并返回终态;长任务应由调用方在后台线程运行."""
        parsed = parse_input(text)
        if isinstance(parsed, Command) and parsed.name in {"Status", "Cancel", "StopContinuous"}:
            try:
                if parsed.name == "Status":
                    self._no_parameter(parsed)
                    return dict(success=True, status="success", message="当前串口状态", serial=self.transport.status(), busy=self.busy)
                if parsed.name == "Cancel":
                    self._no_parameter(parsed)
                    self.cancel()
                else:
                    self.stop_continuous(int(parsed.parameter or "0"))
                return dict(success=True, status="accepted", message=f"{parsed.name}: 已请求停止后续操作；已发送字节无法撤回")
            except (ValueError, RuntimeError) as exc:
                return dict(success=False, status="failed", message=str(exc))
        if not self._lock.acquire(blocking=False):
            return dict(success=False, status="failed", message="已有任务运行，请先 Cancel")
        self._cancel.clear()
        self._skip_round.clear()
        self.started.set()
        try:
            self._execute_one(parsed)
            return dict(success=True, status="success", message="操作完成")
        except InterruptedError:
            return dict(success=False, status="cancelled", message="任务已取消；已发送字节无法撤回")
        except OperationUnknown as exc:
            return dict(success=False, status="unknown", message=str(exc))
        except Exception as exc:
            return dict(success=False, status="failed", message=str(exc))
        finally:
            self._lock.release()

    @staticmethod
    def _no_parameter(command):
        if command.parameter.strip() not in ("", "0"):
            raise ValueError(f"{command.name} 不接受该参数")

    def _wait(self, milliseconds):
        if self._cancel.wait(milliseconds / 1000):
            raise InterruptedError()

    @staticmethod
    def _delay(parameter):
        value = float(parameter)
        if not 0 <= value <= 60000:
            raise ValueError("Delay 必须为 0–60000 ms")
        return value

    def _execute_one(self, parsed, stack=()):
        if self._cancel.is_set():
            raise InterruptedError()
        if isinstance(parsed, RawData):
            if parsed.prefix:
                raise ValueError(f"未知命令: {parsed.prefix}；字面冒号请写 /:")
            self._write(parsed.text.encode("utf-8"), parsed.text)
            return
        name, parameter = parsed.name, parsed.parameter
        if name in CONSOLE_LOCAL:
            raise ValueError(f"{name} 仅用于控制台会话，不能放入设备模块")
        if name in {"Mode", "SendMode"}:
            self._module(parameter, stack)
        elif name == "Delay":
            self._wait(self._delay(parameter))
        elif name == "SendHex":
            data = parse_hex_bytes(parameter)
            if not data:
                raise ValueError("SendHex 数据不能为空")
            self._write(data, f"SendHex: {data.hex(' ').upper()}")
        elif name in {"BaudRate", "ComPort", "Com", "SetEndlog"}:
            parameter = parameter.strip()
            if name == "BaudRate":
                from core.serial_config import validate_baudrate
                parameter = str(validate_baudrate(parameter))
            elif name == "SetEndlog":
                parameter = normalize_ending_text(parameter)
                if parameter not in ("None", r"\r\n", r"\r", r"\n"):
                    raise ValueError("SetEndlog 支持 None、rn、r、n 或对应结束符文本")
            elif not parameter:
                raise ValueError("ComPort 不能为空")
            self.transport.set_setting("ComPort" if name == "Com" else name, parameter)
        elif name in {"Open", "Close"}:
            self._no_parameter(parsed)
            getattr(self.transport, name.casefold())()
        elif name == "FirmwareDownload":
            self.transport.download(parameter, self._cancel)
        elif name == "StopContinuous":
            self.stop_continuous(int(parameter or "0"))
            raise _EndRound()
        elif name == "ModeEnd":
            if not stack:
                raise ValueError("ModeEnd 仅用于模块结构，控制台不支持定义模块")
            if parameter.strip() in ("", "0"):
                raise _EndRound()
        else:
            raise ValueError(f"{name} 不能作为模块设备操作")

    def _write(self, data, display):
        state = self.transport.snapshot()["state"]
        ending = state.get("send_settings", {}).get("ending", r"\r\n")
        self.transport.write(data + get_ending_bytes(ending), display)

    def _module(self, name, stack):
        name = name.strip()
        if name in stack:
            raise ValueError("SendMode 循环调用: " + " -> ".join((*stack, name)))
        snapshot = self.transport.snapshot()["state"]
        commands = collect_module_commands(ConfigCommandProvider(snapshot), name, parsed=True)
        if not commands:
            raise ValueError(f"SendMode: 模块 '{name}' 不存在或没有启用的命令")
        settings = snapshot.get("send_settings", {})
        repeat = not stack and bool(settings.get("loop_send", False))
        interval = self._delay(str(settings.get("continuous_interval", 100)))
        loop_interval = self._delay(str(settings.get("loop_interval", 1000)))
        while True:
            self._skip_round.clear()
            self.emit(dict(event="progress", source="system", message=f"SendMode: 执行模块 {name}"))
            try:
                for _row, parsed in commands:
                    if self._cancel.is_set():
                        raise InterruptedError()
                    if self._skip_round.is_set():
                        break
                    if isinstance(parsed, RawData) and parsed.prefix:
                        # 既有模块中的未知设备命令仍按字面发送;已注册会话命令不能走此路径.
                        parsed = RawData(parsed.text)
                    self._execute_one(parsed, (*stack, name))
                    self._wait(interval)
            except _EndRound:
                pass
            if not repeat or self._cancel.is_set():
                if self._cancel.is_set():
                    raise InterruptedError()
                return
            self._wait(loop_interval)
