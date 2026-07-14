#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pytest

from core.text_search import MatchOptions, can_replace, find_matches, replace_text


def test_literal_search_defaults_to_case_insensitive():
    assert find_matches("Abc abc", "abc") == [(0, 3), (4, 7)]


def test_case_sensitive_and_whole_word_search():
    options = MatchOptions(case_sensitive=True, whole_word=True)
    assert find_matches("abc ABC abc1 abc", "abc", options) == [(0, 3), (13, 16)]


def test_regex_replace_supports_capture_groups():
    rule = {
        "find": r"ID=(\d+)",
        "replace": r"VALUE=\1",
        "options": {"use_regex": True},
    }
    assert replace_text("ID=12;ID=34", rule) == ("VALUE=12;VALUE=34", True)


def test_regex_replace_supports_vscode_dollar_groups():
    rule = {
        "find": r"(ID)=(\d+)",
        "replace": r"$1:${2}:$&:$$",
        "options": {"use_regex": True},
    }
    assert replace_text("ID=12", rule) == ("ID:12:ID=12:$", True)


def test_literal_replace_keeps_backslashes_literal():
    rule = {"find": "path", "replace": r"C:\temp", "options": {}}
    assert replace_text("path/path", rule) == (r"C:\temp/C:\temp", True)


def test_non_matching_rule_returns_original_text():
    rule = {"find": "missing", "replace": "x", "options": {}}
    assert can_replace("source", rule) is False
    assert replace_text("source", rule) == ("source", False)


def test_invalid_regex_reports_validation_error():
    with pytest.raises(ValueError, match="正则表达式无效"):
        find_matches("abc", "[", MatchOptions(use_regex=True))


def test_invalid_regex_replacement_reports_validation_error():
    rule = {"find": "(abc)", "replace": r"\2", "options": {"use_regex": True}}
    with pytest.raises(ValueError, match="正则替换字符串无效"):
        replace_text("abc", rule)
