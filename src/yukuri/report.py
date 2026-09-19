from __future__ import annotations

from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time
from typing import TextIO

from .models import RunReport, Status, TestResult, escape_bytes


FAILURE_STATUSES = {
    Status.FAIL, Status.CRASH, Status.TIMEOUT, Status.OUTPUT_LIMIT,
    Status.BUILD_ERROR, Status.HARNESS_ERROR,
}


class Palette:
    def __init__(self, mode: str, stream: TextIO):
        self.enabled = (
            "NO_COLOR" not in os.environ and mode != "never"
            and (mode == "always" or stream.isatty())
        )

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def accent(self, text: str) -> str:
        return self.paint(text, "38;5;141")

    def cyan(self, text: str) -> str:
        return self.paint(text, "36")

    def good(self, text: str) -> str:
        return self.paint(text, "32")

    def warn(self, text: str) -> str:
        return self.paint(text, "33")

    def bad(self, text: str) -> str:
        return self.paint(text, "31")

    def status(self, status: Status) -> str:
        if status == Status.PASS:
            return self.good(status.value)
        if status in (Status.SKIP, Status.DIFFERENT_OBSERVATION):
            return self.warn(status.value)
        if status in FAILURE_STATUSES:
            return self.bad(status.value)
        return self.cyan(status.value)


class Progress:
    """A single-line animated indicator that is completely silent off a TTY."""

    def __init__(self, stream: TextIO, color: str, ascii_only: bool):
        self.stream = stream
        self.palette = Palette(color, stream)
        self.enabled = stream.isatty()
        self.ascii_only = ascii_only
        self.total = 0
        self.current = 0
        self.width = 0
        self.label = ""
        self.frame = 0
        self.started = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, total: int) -> None:
        self.total = total
        self.current = 0
        self.label = "building project"
        self.frame = 0
        self.started = time.monotonic()
        self._stop.clear()
        self._draw()
        if self.enabled:
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()

    def phase(self, label: str) -> None:
        with self._lock:
            self.label = label
            self._draw_locked()

    def update(self, case_id: str) -> None:
        with self._lock:
            self.current += 1
            self.label = case_id
            self._draw_locked()

    def finish(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        with self._lock:
            if self.enabled and self.width:
                self.stream.write("\r" + " " * self.width + "\r")
                self.stream.flush()
            self.width = 0

    def _animate(self) -> None:
        while not self._stop.wait(0.1):
            with self._lock:
                self.frame += 1
                self._draw_locked()

    def _draw(self) -> None:
        with self._lock:
            self._draw_locked()

    def _draw_locked(self) -> None:
        if not self.enabled:
            return
        frames = "|/-\\" if self.ascii_only else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        glyph = frames[self.frame % len(frames)]
        elapsed = time.monotonic() - self.started
        text = f" {glyph} {self.current}/{self.total} {self.label}  {elapsed:.1f}s"
        columns = shutil.get_terminal_size((80, 24)).columns
        plain = text[:max(10, columns - 1)]
        self.width = max(self.width, len(plain))
        self.stream.write("\r" + self.palette.cyan(plain.ljust(self.width)))
        self.stream.flush()


def first_difference(expected: bytes, actual: bytes) -> int | None:
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return index
    if len(expected) != len(actual):
        return min(len(expected), len(actual))
    return None


def render(report: RunReport, verbose: bool = False, *, stream: TextIO | None = None,
           color: str = "auto", ascii_only: bool = False) -> None:
    stream = stream or sys.stdout
    palette = Palette(color, stream)
    width = max(20, shutil.get_terminal_size((80, 24)).columns)
    dot = "-" if ascii_only else "·"

    print(palette.accent(f"yukari {dot} {report.suite}"), file=stream)
    print(_fit(f"project  {_safe_text(report.project)}", width), file=stream)
    build = palette.good(report.build_status) if report.build_status == "PASS" else palette.bad(report.build_status)
    if width < 50:
        print(f"profile  {report.profile}", file=stream)
        print(f"seed {report.seed}  {dot}  build {build}", file=stream)
    else:
        print(f"profile  {report.profile}  {dot}  seed {report.seed}  {dot}  build {build}", file=stream)

    for kind in ("required", "compatibility", "ub"):
        section = [result for result in report.results if result.kind == kind]
        if not section:
            continue
        print(f"\n{palette.cyan(_section_title(kind))}", file=stream)
        if kind == "ub":
            _render_ub_summary(section, palette, dot, stream)
        else:
            _render_category_summary(section, palette, dot, width, stream)
        details = section if verbose else [
            result for result in section
            if result.status in FAILURE_STATUSES or (kind == "compatibility" and result.status == Status.SKIP)
        ]
        for result in details:
            _render_result(result, palette, stream, verbose)

    required = [r for r in report.results if r.kind == "required"]
    compatibility = [r for r in report.results if r.kind == "compatibility"]
    ub_results = [r for r in report.results if r.kind == "ub"]
    failures = [r for r in required if r.status != Status.PASS]
    infra = [r for r in report.results if r.status in (Status.BUILD_ERROR, Status.HARNESS_ERROR)]
    outcome = "INFRASTRUCTURE ERROR" if infra else ("FAILED" if failures else "PASSED")
    outcome_text = palette.bad(outcome) if infra or failures else palette.good(outcome)
    assertions = sum(r.assertions for r in required)
    overall_counts = (f"{sum(r.status == Status.PASS for r in required)}/{len(required)} required cases  "
                      f"{dot}  {assertions} assertions  {dot}  {report.elapsed:.2f}s")
    if width < 60:
        print(f"\n{palette.accent('Overall')}  {outcome_text}", file=stream)
        print(f"  {sum(r.status == Status.PASS for r in required)}/{len(required)} required cases", file=stream)
        print(f"  {assertions} assertions  {dot}  {report.elapsed:.2f}s", file=stream)
    else:
        print(f"\n{palette.accent('Overall')}  {outcome_text}  {dot}  {overall_counts}", file=stream)
    if compatibility:
        print(f"compatibility  {sum(r.status == Status.PASS for r in compatibility)} matched, "
              f"{sum(r.status == Status.SKIP for r in compatibility)} differed/skipped", file=stream)
    if ub_results:
        print(f"UB observations  {sum(r.status == Status.SAME_OBSERVATION for r in ub_results)} same, "
              f"{sum(r.status == Status.DIFFERENT_OBSERVATION for r in ub_results)} different", file=stream)
        print("sanitizers  no instrumentation added; findings in captured stderr are labeled", file=stream)
    if failures or infra:
        print(palette.warn("Rerun commands are shown with each expanded problem above."), file=stream)
    if report.artifacts:
        print(f"artifacts  {_safe_text(report.artifacts)}", file=stream)


def _section_title(kind: str) -> str:
    return {
        "required": "Required tests",
        "compatibility": "Compatibility checks",
        "ub": "Undefined-behavior observations",
    }.get(kind, kind.replace("_", " ").title())


def _render_category_summary(results: list[TestResult], palette: Palette, dot: str,
                             width: int, stream: TextIO) -> None:
    grouped: dict[str, list[TestResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)
    for category, items in grouped.items():
        passed = sum(item.status == Status.PASS for item in items)
        skipped = sum(item.status == Status.SKIP for item in items)
        failed = sum(item.status in FAILURE_STATUSES for item in items)
        elapsed = sum(item.elapsed for item in items)
        if width < 54:
            label = category[:max(8, width - 12)]
            ratio_text = f"{passed}/{len(items)}"
            ratio = palette.good(ratio_text) if failed == 0 else palette.bad(ratio_text)
            print(f"  {label}  {ratio}", file=stream)
            print(f"    fail {failed} {dot} skip {skipped} {dot} {elapsed:.2f}s", file=stream)
        else:
            name_width = min(22, max(10, width - 47))
            label = category[:name_width]
            ratio_text = f"{passed}/{len(items)}"
            padded = f"{ratio_text:>7}"
            ratio = palette.good(padded) if failed == 0 else palette.bad(padded)
            print(f"  {label:<{name_width}} {ratio}  {dot} fail {failed:<2}  {dot} skip {skipped:<2}  {dot} {elapsed:6.2f}s",
                  file=stream)


def _render_ub_summary(results: list[TestResult], palette: Palette, dot: str,
                       stream: TextIO) -> None:
    for result in results:
        label = palette.status(result.status)
        observation = (f"libc {result.reference_observation} {dot} "
                       f"student {result.student_observation}")
        if shutil.get_terminal_size((80, 24)).columns < 72:
            print(f"  {result.case_id}  {label}", file=stream)
            print(f"    {observation}", file=stream)
        else:
            print(f"  {result.case_id:<24} {label} {dot} {observation}", file=stream)


def _render_result(result: TestResult, palette: Palette, stream: TextIO,
                   verbose: bool) -> None:
    print(f"\n  {palette.status(result.status)}  {result.case_id}", file=stream)
    print(f"    {result.description}", file=stream)
    if result.format_display:
        print(f"    format     {result.format_display}", file=stream)
    if result.arguments:
        print(f"    arguments  {result.arguments}", file=stream)
    if result.expected is not None:
        print(f"    expected   {len(result.expected)} bytes, return {result.expected_return}", file=stream)
    if result.actual is not None:
        print(f"    actual     {len(result.actual)} bytes, return {result.actual_return}", file=stream)
    if result.expected_call_returns or result.actual_call_returns:
        print(f"    call returns  expected {result.expected_call_returns}, actual {result.actual_call_returns}", file=stream)
    if result.expected is not None and result.actual is not None:
        difference = first_difference(result.expected, result.actual)
        if difference is not None:
            exp = "EOF" if difference >= len(result.expected) else f"0x{result.expected[difference]:02x}"
            act = "EOF" if difference >= len(result.actual) else f"0x{result.actual[difference]:02x}"
            print(f"    first diff byte {difference}: expected {palette.bad(exp)}, actual {palette.bad(act)}", file=stream)
            print(f"    expected   {_diff_excerpt(result.expected, difference)}", file=stream)
            print(f"    actual     {_diff_excerpt(result.actual, difference)}", file=stream)
        elif verbose:
            print(f"    bytes      {escape_bytes(result.actual)}", file=stream)
    if result.signal is not None:
        try:
            name = signal.Signals(result.signal).name
        except ValueError:
            name = "unknown"
        print(f"    signal     {result.signal} ({name})", file=stream)
    if result.stderr:
        print(f"    stderr     {escape_bytes(result.stderr, 320)}", file=stream)
    if result.sanitizer_findings:
        print(f"    sanitizer  {', '.join(result.sanitizer_findings)}", file=stream)
    if result.diagnostic:
        print(f"    diagnostic {_safe_text(result.diagnostic)}", file=stream)
    if result.reproduction:
        print(f"    rerun      {_safe_text(result.reproduction)}", file=stream)
    if result.artifact_path:
        print(f"    artifacts  {_safe_text(result.artifact_path)}", file=stream)


def _diff_excerpt(value: bytes, index: int, radius: int = 32) -> str:
    columns = shutil.get_terminal_size((80, 24)).columns
    radius = max(8, min(radius, max(8, (columns - 30) // 2)))
    start = max(0, index - radius)
    end = min(len(value), index + radius + 1)
    body = escape_bytes(value[start:end], limit=radius * 2 + 1)
    prefix = f"... [{start}:]" if start else ""
    suffix = "..." if end < len(value) else ""
    return f"{prefix}{body}{suffix}  << byte {index} >>"


def _safe_text(value: str) -> str:
    """Escape control and non-ASCII bytes deterministically."""
    data = value.encode("utf-8", "backslashreplace")
    return escape_bytes(data, max(320, len(data) + 1))[1:-1]


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[:max(1, width - 3)] + "..."


def write_json(report: RunReport, target: Path | str, stream: TextIO | None = None) -> None:
    payload = json.dumps(report.json_dict(), indent=2) + "\n"
    if str(target) == "-":
        (stream or sys.stdout).write(payload)
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
