from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    CRASH = "CRASH"
    TIMEOUT = "TIMEOUT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    SKIP = "SKIP"
    BUILD_ERROR = "BUILD_ERROR"
    HARNESS_ERROR = "HARNESS_ERROR"
    SAME_OBSERVATION = "SAME_OBSERVATION"
    DIFFERENT_OBSERVATION = "DIFFERENT_OBSERVATION"


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    cwd: Path
    timeout: float
    max_output: int
    env: dict[str, str] | None = None


@dataclass
class ProcessResult:
    argv: list[str]
    stdout: bytes = b""
    stderr: bytes = b""
    metadata: bytes = b""
    returncode: int | None = None
    elapsed: float = 0.0
    timed_out: bool = False
    output_limited: bool = False
    error: str | None = None

    @property
    def signal(self) -> int | None:
        if self.returncode is not None and self.returncode < 0:
            return -self.returncode
        return None


@dataclass
class TestResult:
    case_id: str
    category: str
    description: str
    status: Status
    required: bool = True
    format_display: str = ""
    arguments: str = ""
    expected: bytes | None = None
    actual: bytes | None = None
    expected_return: int | None = None
    actual_return: int | None = None
    stderr: bytes = b""
    signal: int | None = None
    elapsed: float = 0.0
    reproduction: str = ""
    diagnostic: str = ""
    kind: str = "required"
    assertions: int = 2
    expected_call_returns: list[int] = field(default_factory=list)
    actual_call_returns: list[int] = field(default_factory=list)
    reference_observation: str = ""
    student_observation: str = ""
    sanitizer_findings: list[str] = field(default_factory=list)
    artifact_path: str = ""

    def json_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        for key in ("expected", "actual", "stderr"):
            value = result[key]
            result[key] = None if value is None else {
                "length": len(value),
                "hex": value.hex(),
                "escaped": escape_bytes(value),
            }
        return result


@dataclass
class RunReport:
    suite: str
    project: str
    profile: str
    seed: int
    generator_version: str
    results: list[TestResult] = field(default_factory=list)
    build_stdout: str = ""
    build_stderr: str = ""
    artifacts: str | None = None
    build_status: str = "not run"
    elapsed: float = 0.0

    def json_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        section_counts: dict[str, dict[str, int]] = {}
        for result in self.results:
            counts[result.status.value] = counts.get(result.status.value, 0) + 1
            section = section_counts.setdefault(result.kind, {})
            section[result.status.value] = section.get(result.status.value, 0) + 1
        section_assertions: dict[str, int] = {}
        for result in self.results:
            section_assertions[result.kind] = section_assertions.get(result.kind, 0) + result.assertions
        return {
            "schema_version": 2,
            "suite": self.suite,
            "project": self.project,
            "profile": self.profile,
            "seed": self.seed,
            "generator_version": self.generator_version,
            "counts": counts,
            "section_counts": section_counts,
            "assertions": sum(result.assertions for result in self.results),
            "section_assertions": section_assertions,
            "build_status": self.build_status,
            "elapsed_seconds": self.elapsed,
            "artifacts": self.artifacts,
            "results": [r.json_dict() for r in self.results],
        }


def escape_bytes(value: bytes, limit: int = 240) -> str:
    clipped = value[:limit]
    out = []
    for byte in clipped:
        if byte == 10:
            out.append(r"\n")
        elif byte == 13:
            out.append(r"\r")
        elif byte == 9:
            out.append(r"\t")
        elif byte == 92:
            out.append(r"\\")
        elif byte == 34:
            out.append(r'\"')
        elif 32 <= byte < 127:
            out.append(chr(byte))
        else:
            out.append(f"\\x{byte:02x}")
    if len(value) > limit:
        out.append(f"...(+{len(value) - limit} bytes)")
    return '"' + "".join(out) + '"'
