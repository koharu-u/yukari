from __future__ import annotations

from dataclasses import dataclass
import random
import shlex
import struct
from pathlib import Path

from ..build import BuildWorkspace, compile_harness, prepare_project
from ..models import Invocation, RunReport, Status, TestResult
from ..process import run_process


GENERATOR_VERSION = "1"


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    description: str
    format_display: str
    arguments: str
    c_body: str
    smoke: bool = False
    required: bool = True


def _call(case_id: str, category: str, description: str, c_format: str,
          arguments: str = "none", c_arguments: str = "", smoke: bool = False,
          required: bool = True) -> Case:
    comma_args = f", {c_arguments}" if c_arguments else ""
    return Case(case_id, category, description, c_format, arguments,
                f"return CALL({c_format}{comma_args});", smoke, required)


def deterministic_cases() -> list[Case]:
    long_text = "L" * 4096
    cases = [
        _call("text.empty", "text", "empty format", '""', smoke=True),
        _call("text.plain", "text", "ordinary text", '"hello, 42"', smoke=True),
        _call("text.whitespace", "text", "spaces, newline, tab, and backslash",
              '" a b\\n\\t\\\\end"'),
        _call("percent.single", "percent", "literal percent", '"%%"', smoke=True),
        _call("percent.repeated", "percent", "repeated percent signs", '"%%%%-%%-%%%%%%"'),
        _call("char.ascii", "char", "ordinary character", '"[%c]"', "int 'Q'", "'Q'", True),
        _call("char.nul", "char", "embedded NUL between visible bytes", '"A%cB"',
              "int 0", "0", True),
        _call("char.promoted", "char", "promoted unsigned-char value", '"%c:%c"',
              "int 255, int 'z'", "255, 'z'"),
        _call("string.empty", "string", "empty string", '"<%s>"',
              'char *""', '""', True),
        _call("string.short", "string", "short string", '"value=%s!"',
              'char *"forty-two"', '"forty-two"', True),
        _call("string.percent", "string", "percent characters inside string", '"%s"',
              'char *"100% ready %d"', '"100% ready %d"'),
        _call("string.long", "string", "bounded long string", '"prefix:%s:suffix"',
              "4096-byte string", f'"{long_text}"'),
        _call("string.null.optional", "compatibility", "null string compatibility (undefined by C)",
              '"%s"', "char *NULL", "(char *)0", required=False),
        _call("signed.zero", "signed", "signed zero", '"%d/%i"',
              "int 0, int 0", "0, 0", True),
        _call("signed.values", "signed", "positive and negative integers", '"%d %i %d"',
              "int 42, int -42, int 7", "42, -42, 7", True),
        _call("signed.bounds", "signed", "INT_MIN and INT_MAX", '"%d|%i"',
              "int INT_MIN, int INT_MAX", "INT_MIN, INT_MAX"),
        _call("unsigned.zero", "unsigned", "unsigned zero", '"%u"',
              "unsigned int 0", "(unsigned int)0", True),
        _call("unsigned.values", "unsigned", "representative unsigned values", '"%u/%u"',
              "unsigned int 42, unsigned int 4000000000", "(unsigned int)42, (unsigned int)4000000000U"),
        _call("unsigned.max", "unsigned", "UINT_MAX", '"%u"',
              "unsigned int UINT_MAX", "UINT_MAX"),
        _call("hex.zero", "hex", "hexadecimal zero", '"%x/%X"',
              "unsigned int 0, unsigned int 0", "(unsigned int)0, (unsigned int)0", True),
        _call("hex.values", "hex", "lowercase and uppercase hexadecimal", '"%x %X"',
              "unsigned int 0xdeadbeef, unsigned int 0xabcdef", "0xdeadbeefU, 0xabcdefU", True),
        _call("hex.max", "hex", "hexadecimal UINT_MAX", '"%x|%X"',
              "unsigned int UINT_MAX twice", "UINT_MAX, UINT_MAX"),
        _call("pointer.object", "pointer", "valid mapped object pointer", '"%p"',
              "void *ptr", "ptr", True),
        _call("pointer.repeated", "pointer", "same valid pointer repeated", '"%p/%p"',
              "void *ptr twice", "ptr, ptr"),
        _call("pointer.null", "pointer", "null pointer using host spelling", '"%p"',
              "void *NULL", "(void *)0", True),
        _call("mixed.consecutive", "mixed", "consecutive conversions", '"%c%s%d%i%u%x%X%%"',
              "int 'A', char *\"b\", int -2, int 3, unsigned 4, unsigned 5 twice",
              "'A', \"b\", -2, 3, 4U, 5U, 5U", True),
        _call("mixed.separated", "mixed", "mixed types with separators", '"[%s] %d %p %c %X"',
              "string, int, pointer, char, unsigned", '"mix", -123, ptr, \'!\', 0x42U'),
        Case("state.multiple_calls", "state", "multiple calls do not retain state", '"first:%d" then "|second:%s"',
             "int 7; char *\"ok\"",
             'int a = CALL("first:%d", 7); int b = CALL("|second:%s", "ok"); return a + b;', True),
        _call("long.multiple", "long-output", "multiple bounded long arguments", '"%s%s%s"',
              "three 4096-byte strings", f'"{long_text}", "{long_text}", "{long_text}"'),
    ]
    return cases


def generated_cases(seed: int, count: int = 12) -> list[Case]:
    rng = random.Random(seed)
    pool = [
        ("%c", lambda: (str(rng.choice([32, 33, 65, 90, 97, 126])), "int")),
        ("%s", lambda: (rng.choice(['"gen"', '"with%percent"', '""', '"spaces here"']), "char *")),
        ("%d", lambda: (str(rng.randint(-100000, 100000)), "int")),
        ("%i", lambda: (str(rng.randint(-100000, 100000)), "int")),
        ("%u", lambda: (f"{rng.randrange(0, 2**32)}U", "unsigned int")),
        ("%x", lambda: (f"{rng.randrange(0, 2**32)}U", "unsigned int")),
        ("%X", lambda: (f"{rng.randrange(0, 2**32)}U", "unsigned int")),
    ]
    result: list[Case] = []
    for index in range(1, count + 1):
        pieces: list[str] = [f"g{index}:"]
        c_args: list[str] = []
        displays: list[str] = []
        for _ in range(rng.randint(2, 6)):
            if rng.random() < 0.2:
                pieces.append("%%")
                continue
            spec, make_arg = rng.choice(pool)
            value, kind = make_arg()
            pieces.append(spec)
            c_args.append(value)
            displays.append(f"{kind} {value}")
            pieces.append(rng.choice(["|", ":", " "]))
        fmt = "".join(pieces)
        c_format = '"' + fmt.replace("\\", "\\\\").replace('"', '\\"') + '"'
        result.append(_call(
            f"generated.{index:03d}", "generated", f"valid generated mix (generator v{GENERATOR_VERSION})",
            c_format, ", ".join(displays) or "none", ", ".join(c_args),
        ))
    return result


def harness_source(cases: list[Case]) -> str:
    branches = []
    for index, case in enumerate(cases):
        branches.append(f"case {index}: {{ {case.c_body} }}")
    return f'''#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#ifdef STUDENT_HARNESS
int ft_printf(const char *format, ...);
#define CALL(...) ft_printf(__VA_ARGS__)
#else
#define CALL(...) printf(__VA_ARGS__)
#endif

static int invoke(int id, void *ptr) {{
    switch (id) {{
        {''.join(branches)}
        default: return INT_MIN;
    }}
}}

static int write_all(int fd, const void *data, size_t length) {{
    const unsigned char *cursor = data;
    while (length) {{
        ssize_t written = write(fd, cursor, length);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return -1;
        cursor += written;
        length -= (size_t)written;
    }}
    return 0;
}}

int main(int argc, char **argv) {{
    if (argc != 3) return 119;
    char *end = NULL;
    long id = strtol(argv[1], &end, 10);
    if (!end || *end || id < 0 || id >= {len(cases)}) return 119;
    int metadata_fd = atoi(argv[2]);
    void *wanted = (void *)(uintptr_t)0x6f420000UL;
    void *ptr = mmap(wanted, 4096, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (ptr == MAP_FAILED || ptr != wanted) return 121;
    memcpy(ptr, "42check", 8);
    int result = invoke((int)id, ptr);
    if (fflush(stdout) != 0) return 122;
    if (write_all(metadata_fd, &result, sizeof(result)) != 0) return 120;
    return 0;
}}
'''


class FtPrintfSuite:
    name = "ft_printf"

    def cases(self, profile: str, seed: int) -> list[Case]:
        base = deterministic_cases()
        if profile == "smoke":
            return [case for case in base if case.smoke]
        return base + generated_cases(seed)

    def list_cases(self, profile: str, seed: int, pattern: str | None) -> list[Case]:
        cases = self.cases(profile, seed)
        if pattern:
            needle = pattern.casefold()
            cases = [case for case in cases if needle in " ".join(
                (case.case_id, case.category, case.description)).casefold()]
        return cases

    def run(self, args) -> tuple[RunReport, int]:
        project = args.project.resolve()
        report = RunReport(self.name, str(project), args.profile, args.seed, GENERATOR_VERSION)
        selected = self.list_cases(args.profile, args.seed, args.filter)
        if args.case:
            selected = [case for case in self.cases("full", args.seed) if case.case_id == args.case]
            if not selected:
                report.results.append(TestResult(
                    args.case, "configuration", "requested case", Status.HARNESS_ERROR,
                    diagnostic=f"unknown case ID: {args.case}",
                ))
                return report, 2
        if not selected:
            report.results.append(TestResult(
                "selection.empty", "configuration", "test selection", Status.HARNESS_ERROR,
                diagnostic="no cases matched the selection",
            ))
            return report, 2

        with BuildWorkspace(args.keep_artifacts) as workspace:
            assert workspace.root is not None
            build = prepare_project(project, workspace.root, args.archive, args.build_timeout, args.max_output)
            if build.process:
                report.build_stdout = build.process.stdout.decode("utf-8", "replace")
                report.build_stderr = build.process.stderr.decode("utf-8", "replace")
            if build.error or build.archive is None:
                report.results.append(TestResult(
                    "build.project", "build", "build student archive", Status.BUILD_ERROR,
                    diagnostic=build.error or "archive unavailable",
                    stderr=((build.process.stdout + build.process.stderr) if build.process else b""),
                ))
                if args.keep_artifacts:
                    report.artifacts = str(workspace.root)
                return report, 2

            all_cases = self.cases("full", args.seed)
            source = workspace.root / "ft_printf_harness.c"
            reference_executable = workspace.root / "ft_printf_reference"
            student_executable = workspace.root / "ft_printf_student"
            source.write_text(harness_source(all_cases), encoding="utf-8")
            for label, executable, archive in (
                ("reference", reference_executable, None),
                ("student", student_executable, build.archive),
            ):
                linked = compile_harness(source, executable, archive, workspace.root,
                                         args.build_timeout, args.max_output)
                if linked.error or linked.returncode != 0 or linked.timed_out or linked.output_limited:
                    detail = linked.error or f"{label} harness compile/link failed"
                    if linked.timed_out:
                        detail = f"{label} harness compile/link timed out"
                    elif linked.output_limited:
                        detail = f"{label} harness compiler output exceeded capture limit"
                    report.results.append(TestResult(
                        f"build.harness.{label}", "build", f"link {label} test harness",
                        Status.BUILD_ERROR, diagnostic=detail,
                        stderr=linked.stdout + linked.stderr,
                    ))
                    if args.keep_artifacts:
                        report.artifacts = str(workspace.root)
                    return report, 2

            indices = {case.case_id: index for index, case in enumerate(all_cases)}
            for case in selected:
                report.results.append(self._run_case(
                    case, indices[case.case_id], reference_executable, student_executable, args,
                ))
            if args.keep_artifacts:
                self._save_failure_artifacts(workspace.root, report.results)
                report.artifacts = str(workspace.root)

        infrastructure = any(r.status in (Status.BUILD_ERROR, Status.HARNESS_ERROR) for r in report.results)
        failures = any(r.required and r.status not in (Status.PASS, Status.SKIP) for r in report.results)
        return report, 2 if infrastructure else (1 if failures else 0)

    def _run_case(self, case: Case, index: int, reference_executable: Path,
                  student_executable: Path, args) -> TestResult:
        common = dict(cwd=reference_executable.parent, timeout=args.timeout, max_output=args.max_output)
        reference = run_process(Invocation([str(reference_executable), str(index)], **common), metadata=True)
        reproduction = self._reproduction(args, case.case_id)
        oracle_problem = self._process_problem(reference, oracle=True)
        if oracle_problem:
            return TestResult(
                case.case_id, case.category, case.description, Status.HARNESS_ERROR, case.required,
                case.format_display, case.arguments, stderr=reference.stderr,
                signal=reference.signal, elapsed=reference.elapsed,
                reproduction=reproduction, diagnostic=oracle_problem,
            )
        expected_return = self._metadata_return(reference)
        if expected_return is None:
            return TestResult(
                case.case_id, case.category, case.description, Status.HARNESS_ERROR, case.required,
                case.format_display, case.arguments, expected=reference.stdout,
                stderr=reference.stderr, elapsed=reference.elapsed, reproduction=reproduction,
                diagnostic="reference harness returned invalid metadata",
            )

        student = run_process(Invocation([str(student_executable), str(index)], **common), metadata=True)
        status = Status.PASS
        diagnostic = ""
        if student.timed_out:
            status, diagnostic = Status.TIMEOUT, f"student process exceeded {args.timeout:g}s"
        elif student.output_limited:
            status, diagnostic = Status.OUTPUT_LIMIT, f"captured output exceeded {args.max_output} bytes"
        elif student.signal is not None:
            status, diagnostic = Status.CRASH, "student process terminated by a signal"
        elif student.error:
            status, diagnostic = Status.HARNESS_ERROR, student.error
        elif student.returncode != 0:
            if student.returncode in (119, 120, 121, 122):
                status, diagnostic = Status.HARNESS_ERROR, f"harness exited with {student.returncode}"
            else:
                status, diagnostic = Status.CRASH, f"student process exited early with {student.returncode}"
        actual_return = self._metadata_return(student)
        if status == Status.PASS and actual_return is None:
            status, diagnostic = Status.HARNESS_ERROR, "student harness returned invalid metadata"
        if status == Status.PASS and (student.stdout != reference.stdout or actual_return != expected_return):
            status = Status.FAIL
        if not case.required and status != Status.PASS:
            diagnostic = "optional non-portable compatibility check: " + (diagnostic or "output differs from host")
            status = Status.SKIP
        return TestResult(
            case.case_id, case.category, case.description, status, case.required,
            case.format_display, case.arguments, reference.stdout, student.stdout,
            expected_return, actual_return, student.stderr, student.signal,
            student.elapsed, reproduction, diagnostic,
        )

    @staticmethod
    def _process_problem(result, oracle: bool) -> str | None:
        side = "reference" if oracle else "student"
        if result.error:
            return f"could not start {side} harness: {result.error}"
        if result.timed_out:
            return f"{side} harness timed out"
        if result.output_limited:
            return f"{side} harness exceeded output limit"
        if result.signal is not None:
            return f"{side} harness terminated by signal {result.signal}"
        if result.returncode != 0:
            return f"{side} harness exited with {result.returncode}"
        return None

    @staticmethod
    def _metadata_return(result) -> int | None:
        if len(result.metadata) != struct.calcsize("=i"):
            return None
        return struct.unpack("=i", result.metadata)[0]

    @staticmethod
    def _save_failure_artifacts(root: Path, results: list[TestResult]) -> None:
        failures = root / "failures"
        for result in results:
            if result.status in (Status.PASS, Status.SKIP):
                continue
            destination = failures / result.case_id
            destination.mkdir(parents=True, exist_ok=True)
            if result.expected is not None:
                (destination / "expected.bin").write_bytes(result.expected)
            if result.actual is not None:
                (destination / "actual.bin").write_bytes(result.actual)
            if result.stderr:
                (destination / "stderr.bin").write_bytes(result.stderr)
            (destination / "details.txt").write_text(
                f"status={result.status.value}\n"
                f"expected_return={result.expected_return}\n"
                f"actual_return={result.actual_return}\n"
                f"signal={result.signal}\n"
                f"diagnostic={result.diagnostic}\n"
                f"reproduce={result.reproduction}\n",
                encoding="utf-8",
            )

    @staticmethod
    def _reproduction(args, case_id: str) -> str:
        command_name = args.command_name
        if "/" in command_name:
            command_name = str(Path(command_name).resolve())
        command = [command_name, "ft_printf", str(args.project.resolve()), "--case", case_id,
                   "--seed", str(args.seed), "--timeout", str(args.timeout),
                   "--build-timeout", str(args.build_timeout),
                   "--max-output", str(args.max_output)]
        if args.archive:
            command.extend(("--archive", str(args.archive.resolve())))
        if args.keep_artifacts:
            command.append("--keep-artifacts")
        return shlex.join(command)
