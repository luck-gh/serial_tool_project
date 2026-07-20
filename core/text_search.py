#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""共享文本查找与临时替换逻辑。"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class MatchOptions:
    case_sensitive: bool = False
    use_regex: bool = False
    whole_word: bool = False

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        return cls(
            case_sensitive=bool(data.get("case_sensitive", False)),
            use_regex=bool(data.get("use_regex", False)),
            whole_word=bool(data.get("whole_word", False)),
        )

    def to_dict(self):
        return {
            "case_sensitive": self.case_sensitive,
            "use_regex": self.use_regex,
            "whole_word": self.whole_word,
        }


def normalize_rule(rule):
    if not rule or not str(rule.get("find", "")):
        return None
    normalized = {
        "find": str(rule.get("find", "")),
        "replace": str(rule.get("replace", "")),
        "options": MatchOptions.from_dict(rule.get("options")).to_dict(),
    }
    response_validation = normalize_response_validation(
        rule.get("response_validation")
    )
    if response_validation:
        normalized["response_validation"] = response_validation
    return normalized


def normalize_response_validation(config):
    config = config or {}
    expression = str(config.get("expression", ""))
    if not expression:
        return None

    try:
        timeout_ms = int(config.get("timeout_ms", 100))
    except (TypeError, ValueError):
        timeout_ms = 100
    timeout_ms = max(10, min(60000, timeout_ms))

    on_failure = str(config.get("on_failure", "continue"))
    if on_failure not in ("continue", "stop"):
        on_failure = "continue"

    color_policy = str(config.get("color_policy", "sticky_failure"))
    if color_policy not in ("latest", "sticky_failure"):
        color_policy = "sticky_failure"

    return {
        "enabled": bool(config.get("enabled", False)),
        "expression": expression,
        "timeout_ms": timeout_ms,
        "on_failure": on_failure,
        "color_policy": color_policy,
        "show_error": bool(config.get("show_error", False)),
    }


def compile_pattern(query, options=None):
    options = options if isinstance(options, MatchOptions) else MatchOptions.from_dict(options)
    if not query:
        return None

    expression = query if options.use_regex else re.escape(query)
    if options.whole_word:
        expression = rf"(?<!\w)(?:{expression})(?!\w)"

    flags = 0 if options.case_sensitive else re.IGNORECASE
    try:
        return re.compile(expression, flags)
    except re.error as exc:
        raise ValueError(f"正则表达式无效: {exc}") from exc


def find_matches(text, query, options=None):
    pattern = compile_pattern(query, options)
    if pattern is None:
        return []
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def can_replace(text, rule):
    rule = normalize_rule(rule)
    if not rule:
        return False
    return bool(find_matches(text, rule["find"], rule["options"]))


def replace_text(text, rule):
    rule = normalize_rule(rule)
    if not rule:
        return text, False

    pattern = compile_pattern(rule["find"], rule["options"])
    if not pattern.search(text):
        return text, False

    options = MatchOptions.from_dict(rule["options"])
    replacement = rule["replace"]
    if options.use_regex:
        try:
            return pattern.sub(_normalize_regex_replacement(replacement), text), True
        except re.error as exc:
            raise ValueError(f"正则替换字符串无效: {exc}") from exc
    return pattern.sub(lambda _match: replacement, text), True


_RESPONSE_REFERENCE_RE = re.compile(
    r"\$(?P<escaped>\$)|\$\{(?P<braced>[A-Za-z_]\w*|\d+)\}|\$(?P<number>\d+)"
)


def expand_response_expression(expression, source_match):
    """把响应正则中的 $1/${name} 展开为原始匹配值的安全字面量。"""
    if source_match is None:
        raise ValueError("响应表达式需要先匹配原始发送字符串。")

    def replace_reference(reference):
        if reference.group("escaped"):
            return re.escape("$")

        group_name = reference.group("braced") or reference.group("number")
        group_key = int(group_name) if group_name.isdigit() else group_name
        try:
            value = source_match.group(group_key)
        except (IndexError, KeyError) as exc:
            raise ValueError(f"响应表达式引用了不存在的捕获组: ${group_name}") from exc
        return re.escape(value or "")

    return _RESPONSE_REFERENCE_RE.sub(replace_reference, str(expression or ""))


def validate_response_expression(expression, source_pattern, options=None):
    """校验响应表达式语法和其中引用的原始捕获组。"""
    if not expression:
        return None
    if source_pattern is None:
        raise ValueError("响应表达式需要有效的查找表达式。")

    group_count = source_pattern.groups
    group_names = source_pattern.groupindex

    def replace_reference(reference):
        if reference.group("escaped"):
            return re.escape("$")
        group_name = reference.group("braced") or reference.group("number")
        if group_name.isdigit():
            if not 1 <= int(group_name) <= group_count:
                raise ValueError(f"响应表达式引用了不存在的捕获组: ${group_name}")
        elif group_name not in group_names:
            raise ValueError(f"响应表达式引用了不存在的捕获组: ${{{group_name}}}")
        return re.escape("GROUP_VALUE")

    expanded = _RESPONSE_REFERENCE_RE.sub(replace_reference, str(expression))
    flags = 0 if MatchOptions.from_dict(options).case_sensitive else re.IGNORECASE
    try:
        response_pattern = re.compile(expanded, flags)
    except re.error as exc:
        raise ValueError(f"响应表达式无效: {exc}") from exc
    if response_pattern.search("") is not None:
        raise ValueError("响应表达式不能匹配空字符串。")
    return response_pattern


def compile_expanded_response_pattern(expression, source_match, options=None):
    expanded = expand_response_expression(expression, source_match)
    flags = 0 if MatchOptions.from_dict(options).case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(expanded, flags)
    except re.error as exc:
        raise ValueError(f"响应表达式无效: {exc}") from exc
    if pattern.search("") is not None:
        raise ValueError("响应表达式不能匹配空字符串。")
    return expanded, pattern


def compile_diagnostic_response_pattern(expression, source_match, options=None):
    """生成用于区分“值不一致”和“未收到完整响应”的诊断表达式。

    严格表达式中的来源捕获组引用会被替换为相同来源捕获组的正则，
    并额外捕获实际响应值。该表达式仅用于失败说明，不参与通过判定。
    """
    if source_match is None:
        raise ValueError("响应表达式需要先匹配原始发送字符串。")

    source_group_expressions = _extract_capture_group_expressions(
        source_match.re.pattern
    )
    checks = []
    occurrence = 0

    def replace_reference(reference):
        nonlocal occurrence
        if reference.group("escaped"):
            return re.escape("$")

        group_name = reference.group("braced") or reference.group("number")
        group_key = int(group_name) if group_name.isdigit() else group_name
        try:
            expected = source_match.group(group_key) or ""
        except (IndexError, KeyError) as exc:
            raise ValueError(f"响应表达式引用了不存在的捕获组: ${group_name}") from exc

        group_index = (
            group_key
            if isinstance(group_key, int)
            else source_match.re.groupindex[group_key]
        )
        group_expression = source_group_expressions.get(group_index)
        if not group_expression:
            group_expression = _fallback_diagnostic_expression(expected)

        occurrence += 1
        capture_name = f"response_check_{group_index}_{occurrence}"
        reference_text = (
            f"${group_name}" if group_name.isdigit() else f"${{{group_name}}}"
        )
        checks.append({
            "capture_name": capture_name,
            "reference": reference_text,
            "expected": expected,
        })
        return f"(?P<{capture_name}>{group_expression})"

    diagnostic_expression = _RESPONSE_REFERENCE_RE.sub(
        replace_reference,
        str(expression or ""),
    )
    flags = 0 if MatchOptions.from_dict(options).case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(diagnostic_expression, flags)
    except re.error:
        # 复杂的来源捕获组可能包含不能安全嵌入的结构；退回宽松但有界的值捕获。
        checks = []
        occurrence = 0

        def replace_with_fallback(reference):
            nonlocal occurrence
            if reference.group("escaped"):
                return re.escape("$")
            group_name = reference.group("braced") or reference.group("number")
            group_key = int(group_name) if group_name.isdigit() else group_name
            expected = source_match.group(group_key) or ""
            group_index = (
                group_key
                if isinstance(group_key, int)
                else source_match.re.groupindex[group_key]
            )
            occurrence += 1
            capture_name = f"response_check_{group_index}_{occurrence}"
            reference_text = (
                f"${group_name}" if group_name.isdigit() else f"${{{group_name}}}"
            )
            checks.append({
                "capture_name": capture_name,
                "reference": reference_text,
                "expected": expected,
            })
            return (
                f"(?P<{capture_name}>"
                f"{_fallback_diagnostic_expression(expected)})"
            )

        diagnostic_expression = _RESPONSE_REFERENCE_RE.sub(
            replace_with_fallback,
            str(expression or ""),
        )
        try:
            pattern = re.compile(diagnostic_expression, flags)
        except re.error as exc:
            raise ValueError(f"响应诊断表达式无效: {exc}") from exc

    if pattern.search("") is not None:
        raise ValueError("响应诊断表达式不能匹配空字符串。")
    return diagnostic_expression, pattern, checks


def _extract_capture_group_expressions(expression):
    """提取常规/命名捕获组的组内表达式，键为 Python 捕获组序号。"""
    captures = {}
    stack = []
    group_index = 0
    in_character_class = False
    escaped = False
    index = 0

    while index < len(expression):
        char = expression[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "[":
            in_character_class = True
            index += 1
            continue
        if char == "]" and in_character_class:
            in_character_class = False
            index += 1
            continue
        if in_character_class:
            index += 1
            continue

        if char == "(":
            capture_number = None
            body_start = index + 1
            if expression.startswith("(?P<", index):
                name_end = expression.find(">", index + 4)
                if name_end != -1:
                    group_index += 1
                    capture_number = group_index
                    body_start = name_end + 1
            elif not expression.startswith("?", index + 1):
                group_index += 1
                capture_number = group_index
            stack.append((capture_number, body_start))
        elif char == ")" and stack:
            capture_number, body_start = stack.pop()
            if capture_number is not None:
                captures[capture_number] = expression[body_start:index]
        index += 1
    return captures


def _fallback_diagnostic_expression(expected):
    if re.fullmatch(r"[0-9A-Fa-f]+", expected or ""):
        return r"[0-9A-Fa-f]+"
    if re.fullmatch(r"\w+", expected or ""):
        return r"\w+"
    return r"\S+"


def _normalize_regex_replacement(replacement):
    """将 VSCode 风格的 $1/${1}/$& 转换为 Python 正则替换语法。"""
    sentinel = "\x00DOLLAR\x00"
    replacement = replacement.replace("$$", sentinel)

    def convert_group(match):
        group_name = match.group("braced") or match.group("number")
        if group_name is not None:
            return rf"\g<{group_name}>"
        return r"\g<0>"

    replacement = re.sub(
        r"\$(?:\{(?P<braced>[A-Za-z_]\w*|\d+)\}|(?P<number>\d+)|(?P<whole>&))",
        convert_group,
        replacement,
    )
    return replacement.replace(sentinel, "$")
