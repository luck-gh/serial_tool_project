"""GUI 与 CLI 共用的 CSV/TXT 命令模板格式及原子文件读写,不依赖窗口."""
import copy
import csv
import io
import os
import re
import tempfile


def escape_text(text):
    """
    转义不可见字符和反斜杠,用于CSV导出等场景
    将 \t, \n, \r 等不可见字符转义为可见的字符串表示
    同时将已存在的反斜杠转义为双反斜杠
    """
    if not text:
        return ""

    # 先转义反斜杠(必须首先处理,避免二次转义)
    result = text.replace('\\', '\\\\')

    # 然后转义不可见字符
    replacements = {
        '\t': '\\t',
        '\n': '\\n',
        '\r': '\\r',
        '\0': '\\0',
        '\b': '\\b',
        '\f': '\\f',
        '\v': '\\v'
    }
    for char, escaped in replacements.items():
        result = result.replace(char, escaped)
    return result

def unescape_csv_text(text):
    """
    反转义CSV导入的文本
    将 \\t, \\n, \\r 等转义序列还原为不可见字符
    将 \\\\ 还原为单个反斜杠
    """
    if not text:
        return ""

    # 使用正则表达式处理转义序列
    import re

    def replace_escape(match):
        escape_char = match.group(1)
        escape_map = {
            't': '\t',
            'n': '\n',
            'r': '\r',
            '0': '\0',
            'b': '\b',
            'f': '\f',
            'v': '\v',
            '\\': '\\'
        }
        return escape_map.get(escape_char, '\\' + escape_char)

    # 匹配 \\ 后跟一个字符
    result = re.sub(r'\\(.)', replace_escape, text)
    return result



class CommandRowsTextParser:
    """把模板文本或纯文本解析成命令表格行."""
    FORMAT_TEMPLATE = "模板格式 (CSV)"
    FORMAT_PLAIN_TEXT = "纯文本"

    TEMPLATE_HELP = (
        "模板格式每行应为: True,串口需要发送的数据,注释\n"
        "或: False,串口需要发送的数据,注释\n"
        "注释列可以为空；以 // 开头的行会被当作说明跳过。"
    )

    @classmethod
    def parse_rows(cls, text, input_format):
        """按明确选择的纯文本或 CSV 格式解析输入,返回 (启用, 命令, 注释) 行列表."""
        if input_format not in (cls.FORMAT_TEMPLATE, cls.FORMAT_PLAIN_TEXT):
            raise ValueError("请先选择输入格式。")

        if input_format == cls.FORMAT_PLAIN_TEXT:
            return cls.parse_plain_text_rows(text)

        return cls.parse_template_rows(text)

    @classmethod
    def parse_plain_text_rows(cls, text):
        """将每个非空文本行解析为默认不启用的命令;全空输入抛 ValueError."""
        rows = []
        for line in text.splitlines():
            command = line.strip()
            if command:
                rows.append((False, command, ""))
        if not rows:
            raise ValueError("请输入至少一行非空文本。")
        return rows

    @classmethod
    def parse_template_rows(cls, text):
        """解析 CSV 模板的启用标志,命令与注释;忽略说明行,格式错误抛 ValueError."""
        rows = []
        try:
            reader = list(csv.reader(io.StringIO(text), strict=True))
        except csv.Error as exc:
            raise ValueError(f"CSV 模板格式不正确: {exc}") from exc
        for line_number, row in enumerate(reader, start=1):
            if not row or not any(cell.strip() for cell in row):
                continue

            first_cell = row[0].strip()
            if first_cell.startswith("//") or first_cell.startswith("#"):
                continue

            enable_str = first_cell.lower()
            if enable_str not in ("true", "false"):
                raise ValueError(
                    f"第 {line_number} 行格式不正确，第一列必须是 True 或 False。"
                )
            if len(row) < 2:
                raise ValueError(f"第 {line_number} 行格式不正确，缺少发送字符串列。")

            enable = enable_str == "true"
            command = unescape_csv_text(row[1].strip())
            if len(row) > 2:
                comment = ",".join(cell.strip() for cell in row[2:])
                comment = unescape_csv_text(comment)
            else:
                comment = ""
            rows.append((enable, command, comment))

        if not rows:
            raise ValueError("没有解析到可添加的命令行。")
        return rows


def template_path(path):
    """按调用进程工作目录解析文件路径;控制台允许成对的外层引号."""
    path = os.fspath(path).strip()
    if len(path) >= 2 and path[0] == path[-1] and path[0] in "\"'":
        path = path[1:-1]
    if not path:
        raise ValueError("文件路径不能为空")
    return os.path.abspath(os.path.expanduser(path))


def load_template(path):
    """读取带可选 BOM 的 UTF-8 CSV/TXT 模板,完整解析后返回三列命令行."""
    with open(template_path(path), encoding="utf-8-sig", newline="") as stream:
        return CommandRowsTextParser.parse_template_rows(stream.read())


def export_template(path, rows, progress=None):
    """原子替换 UTF-8 模板;progress(index) 返回 False 时取消并保留旧文件."""
    path = template_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                dir=os.path.dirname(path), suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            writer = csv.writer(stream, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(["// 选择框,串口需要发送的数据,注释"])
            for index, (enabled, command, comment) in enumerate(rows, 1):
                writer.writerow([str(enabled), escape_text(command), escape_text(comment)])
                if progress is not None and progress(index) is False:
                    raise InterruptedError("导出已取消")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.remove(temporary)


def apply_local_template(manager, rows):
    """通过既有 ConfigManager 原子保存替换后的命令表,失败恢复内存,保留其他配置."""
    from core.cli_runner import get_state
    original = manager.config
    candidate = copy.deepcopy(original)
    manager.config = candidate
    try:
        state = get_state(manager)
        state["commands"] = rows
        state["command_replacement_rules"] = {}
        state.setdefault("send_settings", {}).update(jump_module="全部", send_module="全部")
        candidate["state"] = state
        manager.save_config(candidate)
        manager.config = candidate
    except Exception:
        manager.config = original
        raise


def run_local_template(manager, operation, path):
    """独立 CLI 的同步模板操作,复用 GUI 同一纯读写函数和系统结果语义."""
    from core.cli_runner import get_state
    from core.output_rules import file_operation_event
    path = template_path(path)
    try:
        if operation == "template-import":
            rows = load_template(path)
            apply_local_template(manager, rows)
        else:
            rows = get_state(manager).get("commands", [])
            export_template(path, rows)
        return file_operation_event(operation, path, "success", len(rows))
    except Exception as exc:
        return file_operation_event(operation, path, "failed", error=exc)
