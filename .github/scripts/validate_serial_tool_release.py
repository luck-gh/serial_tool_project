#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
发布校验脚本, 负责检查版本标签, README 版本和工具版本文件是否一致。

Author: GuoHowe
E-Mail: 844396800@qq.com
Website: www.GuoHowe.com
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")
README_PATTERN = re.compile(r"版本\s+([0-9]+\.[0-9]+\.[0-9]+)")
MAIN_PATTERN = re.compile(r'^TOOL_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def parse_tag_version(tag: str) -> str:
    match = TAG_PATTERN.match(tag)
    if not match:
        raise ValueError(f"标签格式错误: {tag}，必须为 vX.Y.Z")
    return match.group(1)


def parse_readme_version(readme_text: str) -> str:
    match = README_PATTERN.search(readme_text)
    if not match:
        raise ValueError("README 版本提取失败")
    return match.group(1)


def parse_tool_version(version_text: str) -> str:
    match = MAIN_PATTERN.search(version_text)
    if not match:
        raise ValueError("版本文件中 TOOL_VERSION 提取失败")
    return match.group(1)


def main() -> int:
    if len(sys.argv) != 4:
        return fail(
            "用法: python .github/scripts/validate_serial_tool_release.py <tag> <readme_path> <version_file_path>"
        )

    tag = sys.argv[1]
    readme_path = Path(sys.argv[2])
    version_path = Path(sys.argv[3])

    if not readme_path.exists():
        return fail(f"README 文件不存在: {readme_path}")
    if not version_path.exists():
        return fail(f"版本文件不存在: {version_path}")

    try:
        tag_version = parse_tag_version(tag)
        readme_version = parse_readme_version(readme_path.read_text(encoding="utf-8"))
        tool_version = parse_tool_version(version_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return fail(str(exc))

    if tag_version != readme_version or tag_version != tool_version:
        return fail(
            "版本不一致: "
            f"tag={tag_version}, README={readme_version}, TOOL_VERSION={tool_version}"
        )

    print(f"tag_version={tag_version}")
    print(f"readme_version={readme_version}")
    print(f"tool_version={tool_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
