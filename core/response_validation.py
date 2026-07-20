#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""发送后响应表达式的流式匹配与超时管理。"""

from dataclasses import dataclass, field
import re
import time


@dataclass
class PendingResponseValidation:
    sequence: int
    row_index: int
    row_widget_ref: object
    pattern: object
    expanded_expression: str
    start_offset: int
    deadline: float
    on_failure: str
    color_policy: str
    diagnostic_pattern: object = None
    diagnostic_checks: object = None
    show_error: bool = False
    replaced_command: str = ""


@dataclass
class ResponseValidationResult:
    pending: PendingResponseValidation
    passed: bool
    reason: str
    matched_text: str = ""
    received_text: str = ""
    comparisons: list = field(default_factory=list)


class ResponseValidationManager:
    """把任意串口分包合并成接收流，并为多个待校验任务分配匹配。"""

    def __init__(self, max_buffer_chars=262144, clock=None):
        self.max_buffer_chars = max(1024, int(max_buffer_chars))
        self.clock = clock or time.monotonic
        self.buffer = ""
        self.base_offset = 0
        self.pending = []
        self.consumed_ranges = []
        self._next_sequence = 0

    @property
    def end_offset(self):
        return self.base_offset + len(self.buffer)

    def add_pending(
        self,
        *,
        row_index,
        row_widget_ref,
        pattern,
        expanded_expression,
        timeout_ms,
        on_failure,
        color_policy,
        diagnostic_pattern=None,
        diagnostic_checks=None,
        show_error=False,
        replaced_command="",
        start_offset=None,
    ):
        self._next_sequence += 1
        now = self.clock()
        pending = PendingResponseValidation(
            sequence=self._next_sequence,
            row_index=row_index,
            row_widget_ref=row_widget_ref,
            pattern=pattern,
            expanded_expression=expanded_expression,
            start_offset=self.end_offset if start_offset is None else int(start_offset),
            deadline=now + max(10, int(timeout_ms)) / 1000.0,
            on_failure=on_failure,
            color_policy=color_policy,
            diagnostic_pattern=diagnostic_pattern,
            diagnostic_checks=list(diagnostic_checks or []),
            show_error=bool(show_error),
            replaced_command=str(replaced_command or ""),
        )
        self.pending.append(pending)
        return pending

    def feed(self, text):
        if text:
            self.buffer += str(text)
        results = self._match_available()
        self._trim_buffer()
        return results

    def expire(self, now=None):
        now = self.clock() if now is None else now
        expired = [item for item in self.pending if item.deadline <= now]
        if not expired:
            return []

        results = []
        for item in sorted(expired, key=lambda value: value.sequence):
            if item not in self.pending:
                continue
            self.pending.remove(item)
            diagnostic_match = None
            comparisons = []
            if item.diagnostic_pattern is not None:
                diagnostic_match = self._first_unconsumed_match(
                    item,
                    item.diagnostic_pattern,
                )
                if diagnostic_match is not None:
                    absolute_range = (
                        self.base_offset + diagnostic_match.start(),
                        self.base_offset + diagnostic_match.end(),
                    )
                    self.consumed_ranges.append(absolute_range)
                    self.consumed_ranges.sort()
                    ignore_case = bool(
                        item.diagnostic_pattern.flags & re.IGNORECASE
                    )
                    for check in item.diagnostic_checks or []:
                        actual = diagnostic_match.group(check["capture_name"]) or ""
                        expected = check["expected"]
                        is_equal = (
                            actual.casefold() == expected.casefold()
                            if ignore_case
                            else actual == expected
                        )
                        if not is_equal:
                            comparisons.append({
                                "reference": check["reference"],
                                "expected": expected,
                                "actual": actual,
                            })
            results.append(
                ResponseValidationResult(
                    pending=item,
                    passed=False,
                    reason=(
                        "value_mismatch"
                        if diagnostic_match is not None and comparisons
                        else "timeout"
                    ),
                    matched_text=(
                        diagnostic_match.group(0) if diagnostic_match else ""
                    ),
                    received_text=self._text_since(item.start_offset),
                    comparisons=comparisons,
                )
            )
        self._trim_buffer()
        return results

    def clear(self):
        self.buffer = ""
        self.base_offset = 0
        self.pending = []
        self.consumed_ranges = []

    def _match_available(self):
        results = []
        while self.pending:
            candidates = []
            for item in self.pending:
                match = self._first_unconsumed_match(item)
                if match is not None:
                    absolute_start = self.base_offset + match.start()
                    absolute_end = self.base_offset + match.end()
                    candidates.append((absolute_start, item.sequence, absolute_end, item, match))
            if not candidates:
                break

            absolute_start, _sequence, absolute_end, item, match = min(candidates)
            self.consumed_ranges.append((absolute_start, absolute_end))
            self.consumed_ranges.sort()
            self.pending.remove(item)
            results.append(
                ResponseValidationResult(
                    pending=item,
                    passed=True,
                    reason="matched",
                    matched_text=match.group(0),
                )
            )
        return results

    def _first_unconsumed_match(self, item, pattern=None):
        pattern = pattern or item.pattern
        relative_start = max(0, item.start_offset - self.base_offset)
        search_position = relative_start
        while search_position <= len(self.buffer):
            match = pattern.search(self.buffer, search_position)
            if match is None:
                return None
            if match.end() <= match.start():
                return None

            absolute_range = (
                self.base_offset + match.start(),
                self.base_offset + match.end(),
            )
            if not self._overlaps_consumed(absolute_range):
                return match
            search_position = max(match.end(), match.start() + 1)
        return None

    def _overlaps_consumed(self, candidate):
        start, end = candidate
        return any(start < used_end and end > used_start for used_start, used_end in self.consumed_ranges)

    def _text_since(self, absolute_offset):
        relative_start = max(0, int(absolute_offset) - self.base_offset)
        return self.buffer[relative_start:]

    def _trim_buffer(self):
        if not self.pending:
            self.base_offset = self.end_offset
            self.buffer = ""
            self.consumed_ranges = []
            return

        keep_from = min(item.start_offset for item in self.pending)
        cap_from = self.end_offset - self.max_buffer_chars
        keep_from = max(self.base_offset, keep_from, cap_from)
        if keep_from <= self.base_offset:
            return

        drop_count = keep_from - self.base_offset
        self.buffer = self.buffer[drop_count:]
        self.base_offset = keep_from
        self.consumed_ranges = [
            (start, end)
            for start, end in self.consumed_ranges
            if end > self.base_offset
        ]
