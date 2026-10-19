"""命令输入的唯一词法解析器;注册表不依赖窗口或控制台会话."""
from dataclasses import dataclass
import re


COMMAND_NAMES = (
    "Mode", "ModeEnd", "Delay", "SendHex", "BaudRate", "ComPort", "Com",
    "SetEndlog", "SendMode", "StopContinuous", "FirmwareDownload",
    "ShowHex", "Timestamp", "Clear", "Help", "Log", "LogGet", "LogCheck", "LogApply", "LogStatus", "LogRecover", "SaveReceived", "ImportTemplate", "ExportTemplate", "Quit",
    "Status", "Open", "Close", "Cancel",
)
COMMANDS = {}
for _name in COMMAND_NAMES:
    if _name.casefold() in COMMANDS:
        raise ValueError(f"命令名冲突: {_name}")
    COMMANDS[_name.casefold()] = _name

FILE_COMMANDS = frozenset(("SaveReceived", "ImportTemplate", "ExportTemplate"))
LOG_COMMANDS = frozenset(("LogGet", "LogCheck", "LogApply", "LogStatus", "LogRecover"))
CONSOLE_LOCAL = frozenset(("ShowHex", "Timestamp", "Clear", "Help", "Log", "Quit")) | FILE_COMMANDS | LOG_COMMANDS
BARE_COMMANDS = CONSOLE_LOCAL | {"Status", "Open", "Close", "Cancel"}


@dataclass(frozen=True)
class Command:
    """已注册命令及未改写的参数;name 始终使用规范名."""
    name: str
    parameter: str


@dataclass(frozen=True)
class RawData:
    """反转义一次的设备文本;prefix 保留未知命令候选的词法证据."""
    text: str
    prefix: str | None = None


def unescape_text(text: str) -> str:
    """处理 /:,// 和既有的 \\:,其他斜杠保持原文."""
    result = []
    index = 0
    while index < len(text):
        character = text[index]
        if index + 1 < len(text):
            following = text[index + 1]
            if (character == "/" and following in ":/") or (character == "\\" and following == ":"):
                result.append(following)
                index += 2
                continue
        result.append(character)
        index += 1
    return "".join(result)


def parse_hex_bytes(text: str) -> bytes:
    """解码 SendHex 参数,移除空格并为奇数位前补 0;空文本返回空字节.

    Args:
        text: 十六进制参数;是否允许空数据和追加结束符由执行入口决定.
    """
    value = text.strip().replace(" ", "")
    if len(value) % 2:
        value = "0" + value
    return bytes.fromhex(value)


def normalize_ending_text(text: str) -> str:
    """将结束符缩写 none/rn/r/n 转为配置文本,其他输入保持原样."""
    return {"none": "None", "rn": r"\r\n", "r": r"\r", "n": r"\n"}.get(text.casefold(), text)


def get_ending_bytes(ending_text: str) -> bytes:
    """将配置中的结束符转义文本编码为 UTF-8 字节;None 表示无结束符."""
    if ending_text == "None":
        return b""
    return ending_text.encode("utf-8").decode("unicode_escape").encode("utf-8")


def parse_input(text: str) -> Command | RawData:
    """识别未转义的冒号和注册名,参数不反转义以保留 Windows 路径."""
    text = text or ""
    index = 0
    colon = None
    while index < len(text):
        character = text[index]
        if index + 1 < len(text):
            following = text[index + 1]
            if (character == "/" and following in ":/") or (character == "\\" and following == ":"):
                index += 2
                continue
        if character == ":":
            colon = index
            break
        index += 1
    if colon is not None:
        prefix = text[:colon]
        if re.fullmatch(r"\w+", prefix):
            name = COMMANDS.get(prefix.casefold())
            if name:
                return Command(name, text[colon + 1:])
            return RawData(unescape_text(text), prefix)
    name = COMMANDS.get(text.casefold())
    if name in BARE_COMMANDS:
        return Command(name, "")
    return RawData(unescape_text(text))


def command_parts(text: str) -> tuple[str | None, str | None]:
    """投影既有执行器需要的词法候选,不改变各后端的未知命令策略."""
    parsed = parse_input(text)
    if isinstance(parsed, Command):
        # 裸命令在既有执行接口中只作词法投影; 设备入口仍须先调用 require_device_command 拒绝文件操作.
        if ":" not in text and parsed.name in BARE_COMMANDS:
            return None, None
        return parsed.name.casefold(), parsed.parameter
    if parsed.prefix:
        return parsed.prefix.casefold(), text.split(":", 1)[1]
    return None, None


def require_device_command(text):
    """拒绝将控制台文件操作误发设备;显式字面转义仍按设备文本处理."""
    parsed = parse_input(text)
    if isinstance(parsed, Command) and parsed.name in FILE_COMMANDS | LOG_COMMANDS:
        raise ValueError(f"{parsed.name} 仅用于控制台文件操作，不能通过 --send 或设备模块执行")
