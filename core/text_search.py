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
    return {
        "find": str(rule.get("find", "")),
        "replace": str(rule.get("replace", "")),
        "options": MatchOptions.from_dict(rule.get("options")).to_dict(),
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
