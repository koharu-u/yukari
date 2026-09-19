from __future__ import annotations

from dataclasses import dataclass
import random
import shlex
import struct
import time
from pathlib import Path

from ..build import BuildWorkspace, compile_harness, prepare_project
from ..models import Invocation, RunReport, Status, TestResult
from ..process import run_process


GENERATOR_VERSION = "2"
REFERENCE_MAGIC = 0x424F3432
STUDENT_MAGIC = 0x53543432


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
    kind: str = "required"
    stress_only: bool = False


def _call(case_id: str, category: str, description: str, c_format: str,
          arguments: str = "none", c_arguments: str = "", smoke: bool = False,
          *, kind: str = "required", stress_only: bool = False) -> Case:
    comma_args = f", {c_arguments}" if c_arguments else ""
    return Case(case_id, category, description, c_format, arguments,
                f"return CALL({c_format}{comma_args});", smoke,
                kind == "required", kind, stress_only)


def _tracked(case_id: str, category: str, description: str, format_display: str,
             arguments: str, body: str, smoke: bool = False,
             stress_only: bool = False) -> Case:
    return Case(case_id, category, description, format_display, arguments, body,
                smoke, True, "required", stress_only)


def _literal_bytes(value: bytes) -> str:
    return '"' + "".join(
        chr(byte) if 32 <= byte < 127 and byte not in (34, 92)
        else f"\\{byte:03o}" for byte in value
    ) + '"'


def deterministic_cases() -> list[Case]:
    cases = [
        _call("text.empty", "text", "empty format", '""', smoke=True),
        _call("text.plain", "text", "ordinary text", '"hello, 42"', smoke=True),
        _call("text.whitespace", "text", "spaces, newline, tab, and literal backslash",
              '" a b\\n\\t\\\\end"'),
        _call("percent.single", "percent", "literal percent", '"%%"', smoke=True),
        _call("percent.adjacent", "percent", "percent literals adjacent to conversions",
              '"%%%d%%%s%%"', "int 42, char *\"x\"", '42, "x"', True),
        _call("percent.repeated", "percent", "long run of percent literals",
              '"%%%%%%%%-%%%%-%%%%%%%%"'),
        _call("char.ascii", "character", "ordinary character", '"[%c]"',
              "int 'Q'", "'Q'", True),
        _call("char.nul.begin", "character", "NUL at beginning", '"%cAB"',
              "int 0", "0", True),
        _call("char.nul", "character", "NUL between visible bytes", '"A%cB"',
              "int 0", "0", True),
        _call("char.nul.end", "character", "NUL at end", '"AB%c"', "int 0", "0"),
        _call("char.nul.repeated", "character", "repeated and interleaved NUL bytes",
              '"%cA%c%cB%c"', "four promoted int zeros", "0, 0, 0, 0"),
        _call("char.promoted", "character", "unsigned-char maximum through integer promotion",
              '"%c:%c"', "int UCHAR_MAX, int 'z'", "UCHAR_MAX, 'z'"),
        _tracked(
            "char.every_byte", "character",
            "every unsigned-char value with integer promotion",
            '256 calls to "%c"', "int values 0 through UCHAR_MAX",
            'int total = 0; for (int value = 0; value <= UCHAR_MAX; ++value) '
            '{ int r = CALL("%c", value); TRACK(r); total += r; } return total;'),
        _call("string.empty", "string", "empty string", '"<%s>"',
              'char *""', '""', True),
        _call("string.one", "string", "one-character string", '"%s"',
              'char *"x"', '"x"', True),
        _call("string.short", "string", "short string", '"value=%s!"',
              'char *"forty-two"', '"forty-two"', True),
        _call("string.percent", "string", "format-looking text remains string data", '"%s"',
              'char *"100% ready %d"', '"100% ready %d"'),
        _call("string.content", "string", "percent-like text, whitespace, and backslashes",
              '"%s"', "percent text, backslash, tab, and newline",
              _literal_bytes(b"%d %% \\ tab\t line\n")),
        _call("string.utf8", "string", "UTF-8 byte sequence", '"utf8:%s"',
              "UTF-8 bytes for snowman and pi", '"\\342\\230\\203 \\317\\200"'),
        _call("string.embedded_nul", "string", "input stops at its first embedded NUL",
              '"<%s>"', 'char buffer "abc\\0hidden"', '"abc\\0hidden"', True),
        _call("string.substring", "string", "valid substring inside an allocation", '"%s"',
              "char *ctx->substring + 7", "ctx->substring + 7"),
        _call("string.repeated", "string", "same string reused several times", '"%s|%s|%s"',
              "same char pointer three times", "ctx->substring, ctx->substring, ctx->substring"),
        _call("string.page_edge", "string", "terminator is final byte before a protected page",
              '"%s"', "valid guarded char pointer", "ctx->guarded", True),
        _call("string.long", "string", "bounded 4097-byte string", '"%s"',
              "4097-byte valid string", "ctx->boundary_string"),
        _call("signed.zero", "signed", "zero through both signed conversions", '"%d/%i"',
              "int 0, int 0", "0, 0", True),
        _call("signed.values", "signed", "representative positive and negative integers", '"%d %i %d"',
              "int 42, int -42, int 7", "42, -42, 7", True),
        _call("signed.bounds", "signed", "INT_MIN and INT_MAX", '"%d|%i"',
              "int INT_MIN, int INT_MAX", "INT_MIN, INT_MAX"),
        _call("signed.core", "signed", "INT limits and -1, 0, 1 through d and i",
              '"%d|%d|%d|%d|%d;%i|%i|%i|%i|%i"',
              "INT_MIN, INT_MIN+1, -1, 0, INT_MAX and corresponding %i values",
              "INT_MIN, INT_MIN + 1, -1, 0, INT_MAX, INT_MIN, INT_MIN + 1, 1, INT_MAX - 1, INT_MAX", True),
        _tracked(
            "signed.powers10", "signed", "neighbors of every representable power of ten",
            'repeated "%d,%d,%d;"', "int p-1, p, p+1 and negative counterparts",
            'int total = 0; for (int p = 1; ; ) { int r; '
            'r = CALL("%d,%d,%d;", p - 1, p, p == INT_MAX ? p : p + 1); TRACK(r); total += r; '
            'r = CALL("%d,%d,%d;", -(p - 1), -p, p == INT_MAX ? -p : -(p + 1)); TRACK(r); total += r; '
            'if (p > INT_MAX / 10) break; p *= 10; } return total;'),
        _call("unsigned.core", "unsigned", "unsigned limits and central values",
              '"%u|%u|%u|%u|%u"',
              "unsigned 0, 1, high bit, UINT_MAX-1, UINT_MAX",
              "0U, 1U, (UINT_MAX ^ (UINT_MAX >> 1)), UINT_MAX - 1U, UINT_MAX", True),
        _call("unsigned.zero", "unsigned", "unsigned zero", '"%u"',
              "unsigned int 0", "0U", True),
        _call("unsigned.values", "unsigned", "representative unsigned values", '"%u/%u"',
              "unsigned int 42, unsigned int UINT_MAX/2", "42U, UINT_MAX / 2U"),
        _call("unsigned.max", "unsigned", "UINT_MAX", '"%u"',
              "unsigned int UINT_MAX", "UINT_MAX"),
        _tracked(
            "unsigned.powers", "unsigned", "neighbors of powers of 2, 10, and 16",
            'repeated "%u,%u,%u;"', "correctly typed unsigned neighbors",
            'unsigned int bases[3] = {2U, 10U, 16U}; int total = 0; '
            'for (int b = 0; b < 3; ++b) { for (unsigned int p = 1U; ; ) { '
            'unsigned int hi = p == UINT_MAX ? p : p + 1U; '
            'int r = CALL("%u,%u,%u;", p - 1U, p, hi); TRACK(r); total += r; '
            'if (p > UINT_MAX / bases[b]) break; p *= bases[b]; } } return total;'),
        _call("unsigned.patterns", "unsigned", "alternating-bit patterns",
              '"%u|%u"', "unsigned alternating masks",
              "(UINT_MAX / 3U), (UINT_MAX ^ (UINT_MAX / 3U))"),
        _call("hex.core", "hex", "hex limits in lowercase and uppercase",
              '"%x|%X;%x|%X;%x|%X"', "unsigned 0, high bit, UINT_MAX",
              "0U, 0U, (UINT_MAX ^ (UINT_MAX >> 1)), (UINT_MAX ^ (UINT_MAX >> 1)), UINT_MAX, UINT_MAX", True),
        _call("hex.zero", "hex", "hexadecimal zero", '"%x/%X"',
              "unsigned int zero twice", "0U, 0U", True),
        _call("hex.values", "hex", "lowercase and uppercase hexadecimal", '"%x %X"',
              "unsigned int 0xdeadbeef, unsigned int 0xabcdef",
              "(unsigned int)0xdeadbeefU, (unsigned int)0xabcdefU", True),
        _call("hex.max", "hex", "UINT_MAX in both hexadecimal cases", '"%x|%X"',
              "unsigned int UINT_MAX twice", "UINT_MAX, UINT_MAX"),
        _tracked(
            "hex.powers16", "hex", "hexadecimal neighbors of powers of sixteen",
            'repeated "%x/%X,%x/%X,%x/%X;"', "unsigned p-1, p, p+1",
            'int total = 0; for (unsigned int p = 1U; ; ) { unsigned int hi = p == UINT_MAX ? p : p + 1U; '
            'int r = CALL("%x/%X,%x/%X,%x/%X;", p - 1U, p - 1U, p, p, hi, hi); '
            'TRACK(r); total += r; if (p > UINT_MAX / 16U) break; p *= 16U; } return total;'),
        _call("pointer.object", "pointer", "live stack object cast to void pointer", '"%p"',
              "(void *)ctx->stack_ptr", "(void *)ctx->stack_ptr", True),
        _call("pointer.heap", "pointer", "live heap object cast to void pointer", '"%p"',
              "(void *)ctx->heap_ptr", "(void *)ctx->heap_ptr", True),
        _call("pointer.static", "pointer", "live static object cast to void pointer", '"%p"',
              "(void *)ctx->static_ptr", "(void *)ctx->static_ptr"),
        _call("pointer.repeated", "pointer", "identical pointer repeated", '"%p/%p/%p"',
              "same heap pointer three times", "(void *)ctx->heap_ptr, (void *)ctx->heap_ptr, (void *)ctx->heap_ptr"),
        _call("pointer.distinct", "pointer", "distinct stack, heap, and static objects", '"%p|%p|%p"',
              "three live void pointers", "(void *)ctx->stack_ptr, (void *)ctx->heap_ptr, (void *)ctx->static_ptr"),
        _call("pointer.interior", "pointer", "valid interior and one-past pointers", '"%p|%p"',
              "void *heap+3 and void *heap+32", "(void *)(ctx->heap_ptr + 3), (void *)(ctx->heap_ptr + 32)"),
        _call("pointer.null", "pointer", "null void pointer using host spelling", '"%p"',
              "(void *)NULL", "(void *)0", True),
        _call("mixed.dense", "mixed", "dense conversions and a long typed argument list",
              '"%c%s%d%i%u%x%X%%%c%s%d%i%u%x%X"',
              "14 correctly typed mixed arguments",
              "'A', \"b\", -2, 3, 4U, 5U, 6U, 'Z', \"tail\", INT_MIN, INT_MAX, UINT_MAX, 0xabU, 0xcdU", True),
        _call("mixed.consecutive", "mixed", "consecutive mandatory conversions", '"%c%s%d%i%u%x%X%%"',
              "char, string, two ints, three unsigned values",
              "'A', \"b\", -2, 3, 4U, 5U, 5U", True),
        _call("mixed.separated", "mixed", "mixed types with separators", '"[%s] %d %p %c %X"',
              "string, int, live pointer, promoted char, unsigned",
              '"mix", -123, (void *)ctx->heap_ptr, \'!\', 0x42U'),
        _call("mixed.edges", "mixed", "leading and trailing conversions with literal text",
              '"%d-leading-%s-trailing-%X"', "int -7, string, unsigned 0xbeef",
              "-7, \"middle\", 0xbeefU"),
        _tracked(
            "state.multiple_calls", "state", "nonempty, empty, and nonempty calls with individual returns",
            '"first:%d", "", "|second:%s"', "int 7; no arguments; char *\"ok\"",
            'int total = 0; int r = CALL("first:%d", 7); TRACK(r); total += r; '
            'r = CALL(""); TRACK(r); total += r; r = CALL("|second:%s", "ok"); TRACK(r); total += r; return total;', True),
        _call("long.pipe_capacity", "long-output", "output larger than typical pipe capacity",
              '"%s"', "131072-byte valid string", "ctx->large_output", True),
        _call("long.multiple", "long-output", "multiple long strings in one call", '"%s|%s|%s"',
              "same 4097-byte string three times", "ctx->boundary_string, ctx->boundary_string, ctx->boundary_string"),
        _tracked(
            "stress.repeated_calls", "stress", "1024 bounded calls with individual return checks",
            '1024 calls to "[%u]"', "unsigned values 0 through 1023",
            'int total = 0; for (unsigned int i = 0; i < 1024U; ++i) '
            '{ int r = CALL("[%u]", i); TRACK(r); total += r; } return total;', stress_only=True),
        _call("string.null.optional", "compatibility", "null %s host compatibility observation",
              '"%s"', "char *NULL", "(char *)0", kind="compatibility"),
    ]
    for size in (15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129,
                 255, 256, 257, 511, 512, 513, 1023, 1024, 1025,
                 2047, 2048, 2049, 4095, 4096, 4097):
        cases.append(_call(
            f"string.length.{size}", "string-boundaries",
            f"string length {size} near a common buffer boundary", '"%s"',
            f"char * with {size} bytes", _literal_bytes(b"S" * size),
        ))
    return cases


def generated_cases(seed: int, count: int) -> list[Case]:
    rng = random.Random(seed)
    signed = ["INT_MIN", "INT_MIN + 1", "-1001", "-1000", "-999", "-1", "0", "1",
              "9", "10", "11", "99", "100", "101", "INT_MAX - 1", "INT_MAX"]
    unsigned = ["0U", "1U", "2U", "15U", "16U", "17U", "255U", "256U", "257U",
                "(UINT_MAX ^ (UINT_MAX >> 1))", "UINT_MAX - 1U", "UINT_MAX"]
    strings = ['""', '"g"', '"with%percent"', '"%d is text"', '"spaces\\tand\\nlines"']
    specs = ("%c", "%s", "%d", "%i", "%u", "%x", "%X")
    result: list[Case] = []
    for index in range(1, count + 1):
        pieces = [f"g{index}:"]
        c_args: list[str] = []
        displays: list[str] = []
        for _ in range(rng.randint(3, 9)):
            if rng.random() < 0.18:
                pieces.append("%%")
            else:
                spec = rng.choice(specs)
                pieces.append(spec)
                if spec == "%c":
                    value = rng.choice(["0", "'A'", "'z'", "UCHAR_MAX"])
                    kind = "promoted int"
                elif spec == "%s":
                    value, kind = rng.choice(strings), "char *"
                elif spec in ("%d", "%i"):
                    value, kind = rng.choice(signed), "int"
                else:
                    value, kind = rng.choice(unsigned), "unsigned int"
                c_args.append(value)
                displays.append(f"{kind} {value}")
            pieces.append(rng.choice(("|", ":", " ", "-")))
        fmt = "".join(pieces)
        result.append(_call(
            f"generated.{index:03d}", "generated",
            f"boundary-biased generated mix (generator v{GENERATOR_VERSION})",
            _literal_bytes(fmt.encode("ascii")), ", ".join(displays), ", ".join(c_args),
        ))
    return result


def ub_cases() -> list[Case]:
    return [
        Case("ub.null_format", "ub", "null format pointer", "NULL", "none",
             "const char *fmt = NULL; return CALL(fmt);", kind="ub", required=False),
        Case("ub.trailing_percent", "ub", "trailing percent", 'runtime "%"', "none",
             "char fmt[] = {'%', 0}; return CALL(fmt);", kind="ub", required=False),
        Case("ub.unsupported_conversion", "ub", "unsupported conversion", 'runtime "%q"', "none",
             "char fmt[] = {'%', 'q', 0}; return CALL(fmt);", kind="ub", required=False),
        Case("ub.missing_argument", "ub", "missing variadic integer argument", 'runtime "%d"', "missing",
             "char fmt[] = {'%', 'd', 0}; return CALL(fmt);", kind="ub", required=False),
        Case("ub.mismatched_argument", "ub", "integer supplied for %s", 'runtime "%s"', "int 42",
             "char fmt[] = {'%', 's', 0}; return CALL(fmt, 42);", kind="ub", required=False),
        Case("ub.nonterminated_string", "ub", "nonterminated bytes before protected page", '"%s"',
             "nonterminated char *", 'return CALL("%s", ctx->nonterminated);', kind="ub", required=False),
        Case("ub.inaccessible_string", "ub", "string pointer into inaccessible mapping", '"%s"',
             "inaccessible char *", 'return CALL("%s", ctx->inaccessible);', kind="ub", required=False),
    ]


def _branches(cases: list[Case]) -> str:
    return "".join(f"case {index}: {{ {case.c_body} }}" for index, case in enumerate(cases))


def defined_harness_source(cases: list[Case]) -> str:
    branches = _branches(cases)
    return f'''#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int ft_printf(const char *format, ...);
typedef int (*printf_function)(const char *, ...);
static printf_function host_printf;
static int call_returns[2048];
static unsigned int call_count;
#define TRACK(value) do {{ if (call_count < 2048U) call_returns[call_count++] = (value); }} while (0)

struct context {{
    int *stack_ptr;
    unsigned char *heap_ptr;
    int *static_ptr;
    char substring[32];
    char *guarded;
    char *large_output;
    char *boundary_string;
    void *mapping;
    size_t page_size;
}};
static int static_object = 42;

static int setup_context(struct context *ctx, int *stack_object) {{
    memset(ctx, 0, sizeof(*ctx));
    ctx->stack_ptr = stack_object;
    ctx->static_ptr = &static_object;
    ctx->heap_ptr = malloc(32);
    ctx->large_output = malloc(131073);
    ctx->boundary_string = malloc(4098);
    if (!ctx->heap_ptr || !ctx->large_output || !ctx->boundary_string) return -1;
    memset(ctx->heap_ptr, 0x42, 32);
    memset(ctx->large_output, 'L', 131072); ctx->large_output[131072] = 0;
    memset(ctx->boundary_string, 'B', 4097); ctx->boundary_string[4097] = 0;
    memcpy(ctx->substring, "prefix-substring-value", 23);
    ctx->page_size = (size_t)sysconf(_SC_PAGESIZE);
    ctx->mapping = mmap(NULL, ctx->page_size * 2U, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (ctx->mapping == MAP_FAILED) return -1;
    if (mprotect((char *)ctx->mapping + ctx->page_size, ctx->page_size, PROT_NONE) != 0) return -1;
    ctx->guarded = (char *)ctx->mapping + ctx->page_size - 6;
    memcpy(ctx->guarded, "guard", 5); ctx->guarded[5] = 0;
    return 0;
}}

static int invoke_reference(int id, struct context *ctx) {{
#define CALL(...) host_printf(__VA_ARGS__)
    (void)ctx;
    switch (id) {{ {branches} default: return INT_MIN; }}
#undef CALL
}}
static int invoke_student(int id, struct context *ctx) {{
#define CALL(...) ft_printf(__VA_ARGS__)
    (void)ctx;
    switch (id) {{ {branches} default: return INT_MIN; }}
#undef CALL
}}

static int write_all(int fd, const void *data, size_t length) {{
    const unsigned char *cursor = data;
    while (length) {{
        ssize_t written = write(fd, cursor, length);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return -1;
        cursor += written; length -= (size_t)written;
    }}
    return 0;
}}

static int capture_reference(int id, int metadata_fd, struct context *ctx) {{
    FILE *capture = tmpfile();
    if (!capture) return -1;
    if (fflush(stdout) != 0) return -1;
    int saved = dup(STDOUT_FILENO);
    if (saved < 0 || dup2(fileno(capture), STDOUT_FILENO) < 0) return -1;
    call_count = 0;
    int result = invoke_reference(id, ctx);
    if (fflush(stdout) != 0 || dup2(saved, STDOUT_FILENO) < 0) return -1;
    close(saved);
    if (fseek(capture, 0, SEEK_END) != 0) return -1;
    long end = ftell(capture);
    if (end < 0 || fseek(capture, 0, SEEK_SET) != 0) return -1;
    uint32_t magic = {REFERENCE_MAGIC}U, count = call_count;
    uint64_t length = (uint64_t)end;
    if (write_all(metadata_fd, &magic, sizeof(magic)) ||
        write_all(metadata_fd, &result, sizeof(result)) ||
        write_all(metadata_fd, &count, sizeof(count)) ||
        write_all(metadata_fd, call_returns, sizeof(int) * count) ||
        write_all(metadata_fd, &length, sizeof(length))) return -1;
    unsigned char buffer[8192];
    size_t got;
    while ((got = fread(buffer, 1, sizeof(buffer), capture)) != 0)
        if (write_all(metadata_fd, buffer, got)) return -1;
    fclose(capture);
    return 0;
}}

int main(int argc, char **argv) {{
    if (argc != 3) return 119;
    char *end = NULL; long id = strtol(argv[1], &end, 10);
    if (!end || *end || id < 0 || id >= {len(cases)}) return 119;
    int metadata_fd = atoi(argv[2]);
    void *printf_symbol = dlsym(RTLD_NEXT, "printf");
    if (!printf_symbol || sizeof(printf_symbol) != sizeof(host_printf)) return 124;
    memcpy(&host_printf, &printf_symbol, sizeof(host_printf));
    int stack_object = 7; struct context ctx;
    if (setup_context(&ctx, &stack_object) != 0) return 123;
    if (capture_reference((int)id, metadata_fd, &ctx) != 0) return 125;
    call_count = 0;
    int result = invoke_student((int)id, &ctx);
    if (fflush(stdout) != 0) return 122;
    uint32_t magic = {STUDENT_MAGIC}U, count = call_count;
    if (write_all(metadata_fd, &magic, sizeof(magic)) ||
        write_all(metadata_fd, &result, sizeof(result)) ||
        write_all(metadata_fd, &count, sizeof(count)) ||
        write_all(metadata_fd, call_returns, sizeof(int) * count)) return 120;
    return 0;
}}
'''


def ub_harness_source(cases: list[Case]) -> str:
    branches = _branches(cases)
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
#define TRACK(value) ((void)(value))
struct context {{ char *nonterminated; char *inaccessible; void *mapping; size_t page_size; }};
static int setup(struct context *ctx) {{
    ctx->page_size = (size_t)sysconf(_SC_PAGESIZE);
    ctx->mapping = mmap(NULL, ctx->page_size * 2U, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (ctx->mapping == MAP_FAILED) return -1;
    ctx->nonterminated = (char *)ctx->mapping + ctx->page_size - 4;
    memcpy(ctx->nonterminated, "ABCD", 4);
    ctx->inaccessible = (char *)ctx->mapping + ctx->page_size;
    return mprotect(ctx->inaccessible, ctx->page_size, PROT_NONE);
}}
static int invoke(int id, struct context *ctx) {{
    switch (id) {{ {branches} default: return INT_MIN; }}
}}
static int write_all(int fd, const void *data, size_t length) {{
    const unsigned char *cursor = data;
    while (length) {{ ssize_t n = write(fd, cursor, length); if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1; cursor += n; length -= (size_t)n; }} return 0;
}}
int main(int argc, char **argv) {{
    if (argc != 3) return 119;
    char *end = NULL; long id = strtol(argv[1], &end, 10);
    if (!end || *end || id < 0 || id >= {len(cases)}) return 119;
    struct context ctx; if (setup(&ctx) != 0) return 123;
    int result = invoke((int)id, &ctx);
    if (fflush(stdout) != 0) return 122;
    return write_all(atoi(argv[2]), &result, sizeof(result)) == 0 ? 0 : 120;
}}
'''


class FtPrintfSuite:
    name = "ft_printf"

    def cases(self, profile: str, seed: int, include_ub: bool = False) -> list[Case]:
        base = deterministic_cases()
        if profile == "smoke":
            selected = [case for case in base if case.smoke or case.kind == "compatibility"]
        elif profile == "stress":
            selected = base + generated_cases(seed, 80)
        else:
            selected = [case for case in base if not case.stress_only] + generated_cases(seed, 24)
        if include_ub:
            selected += ub_cases()
        return selected

    def list_cases(self, profile: str, seed: int, pattern: str | None,
                   include_ub: bool = False) -> list[Case]:
        cases = self.cases(profile, seed, include_ub)
        if pattern:
            needle = pattern.casefold()
            cases = [case for case in cases if needle in " ".join(
                (case.case_id, case.category, case.description, case.kind)).casefold()]
        return cases

    def run(self, args) -> tuple[RunReport, int]:
        started = time.monotonic()
        project = args.project.resolve()
        report = RunReport(self.name, str(project), args.profile, args.seed, GENERATOR_VERSION)
        selected = self.list_cases(args.profile, args.seed, args.filter, args.ub)
        if args.case:
            selected = [case for case in self.cases(args.profile, args.seed, args.ub)
                        if case.case_id == args.case]
            if not selected:
                report.results.append(TestResult(
                    args.case, "configuration", "requested case", Status.HARNESS_ERROR,
                    diagnostic=f"unknown case ID for this mode: {args.case}",
                ))
                report.build_status = "NOT RUN"
                report.elapsed = time.monotonic() - started
                return report, 2
        if not selected:
            report.results.append(TestResult(
                "selection.empty", "configuration", "test selection", Status.HARNESS_ERROR,
                diagnostic="no cases matched the selection",
            ))
            report.build_status = "NOT RUN"
            report.elapsed = time.monotonic() - started
            return report, 2

        args.progress.start(len(selected))
        with BuildWorkspace(args.keep_artifacts) as workspace:
            assert workspace.root is not None
            args.progress.phase("building project")
            build = prepare_project(project, workspace.root, args.archive, args.build_timeout, args.max_output)
            if build.process:
                report.build_stdout = build.process.stdout.decode("utf-8", "replace")
                report.build_stderr = build.process.stderr.decode("utf-8", "replace")
            if build.error or build.archive is None:
                report.build_status = "ERROR"
                report.results.append(TestResult(
                    "build.project", "build", "build student archive", Status.BUILD_ERROR,
                    diagnostic=build.error or "archive unavailable",
                    reproduction=self._base_reproduction(args),
                    stderr=((build.process.stdout + build.process.stderr) if build.process else b""),
                ))
                if args.keep_artifacts:
                    report.artifacts = str(workspace.root)
                report.elapsed = time.monotonic() - started
                return report, 2
            report.build_status = "PASS"

            defined = [case for case in selected if case.kind != "ub"]
            ub = [case for case in selected if case.kind == "ub"]
            if defined:
                source = workspace.root / "ft_printf_defined.c"
                executable = workspace.root / "ft_printf_defined"
                source.write_text(defined_harness_source(defined), encoding="utf-8")
                args.progress.phase("compiling test harness")
                error = self._compile_one(source, executable, build.archive, workspace.root, args,
                                          "defined")
                if error:
                    report.results.append(error)
                else:
                    for index, case in enumerate(defined):
                        report.results.append(self._run_defined(case, index, executable, args))
                        args.progress.update(case.case_id)

            if ub and not any(r.status == Status.BUILD_ERROR for r in report.results):
                source = workspace.root / "ft_printf_ub.c"
                source.write_text(ub_harness_source(ub), encoding="utf-8")
                executables: dict[str, Path] = {}
                for label, archive in (("reference", None), ("student", build.archive)):
                    args.progress.phase(f"compiling UB {label} harness")
                    executable = workspace.root / f"ft_printf_ub_{label}"
                    error = self._compile_one(source, executable, archive, workspace.root, args,
                                              f"ub-{label}")
                    if error:
                        report.results.append(error)
                        break
                    executables[label] = executable
                if len(executables) == 2:
                    for index, case in enumerate(ub):
                        report.results.append(self._run_ub(
                            case, index, executables["reference"], executables["student"], args,
                        ))
                        args.progress.update(case.case_id)

            preserve = args.keep_artifacts or any(
                r.status in FAILURE_STATUSES and (
                    (r.expected is not None and len(r.expected) > 240)
                    or (r.actual is not None and len(r.actual) > 240)
                    or r.status == Status.OUTPUT_LIMIT
                ) for r in report.results
            )
            if preserve:
                workspace.preserve()
                self._save_failure_artifacts(workspace.root, report.results)
                report.artifacts = str(workspace.root)

        report.elapsed = time.monotonic() - started
        infrastructure = any(r.status in (Status.BUILD_ERROR, Status.HARNESS_ERROR) for r in report.results)
        failures = any(r.kind == "required" and r.status != Status.PASS for r in report.results)
        return report, 2 if infrastructure else (1 if failures else 0)

    def _compile_one(self, source: Path, executable: Path, archive: Path | None,
                     root: Path, args, label: str) -> TestResult | None:
        linked = compile_harness(source, executable, archive, root,
                                 args.build_timeout, args.max_output)
        if not (linked.error or linked.returncode != 0 or linked.timed_out or linked.output_limited):
            return None
        detail = linked.error or f"{label} harness compile/link failed"
        if linked.timed_out:
            detail = f"{label} harness compile/link timed out"
        elif linked.output_limited:
            detail = f"{label} harness compiler output exceeded capture limit"
        kind = "ub" if label.startswith("ub-") else "required"
        return TestResult(
            f"build.harness.{label}", "build", f"link {label} test harness",
            Status.BUILD_ERROR, required=kind == "required", diagnostic=detail,
            stderr=linked.stdout + linked.stderr,
            reproduction=self._base_reproduction(args), kind=kind,
        )

    def _run_defined(self, case: Case, index: int, executable: Path, args) -> TestResult:
        process = run_process(Invocation(
            [str(executable), str(index)], executable.parent, args.timeout,
            args.max_output,
        ), metadata=True)
        reproduction = self._reproduction(args, case)
        parsed = self._parse_defined_metadata(process.metadata)
        if parsed is None:
            return TestResult(
                case.case_id, case.category, case.description, Status.HARNESS_ERROR,
                case.required, case.format_display, case.arguments,
                actual=process.stdout, stderr=process.stderr, signal=process.signal,
                elapsed=process.elapsed, reproduction=reproduction,
                diagnostic="reference capture failed before producing valid metadata",
                kind=case.kind,
            )
        expected_return, expected_calls, expected, actual_return, actual_calls = parsed
        status, diagnostic = self._defined_process_status(process, args)
        if status == Status.PASS and actual_return is None:
            status, diagnostic = Status.HARNESS_ERROR, "student call returned no valid metadata"
        if status == Status.PASS and (
            process.stdout != expected or actual_return != expected_return
            or actual_calls != expected_calls
        ):
            status = Status.FAIL
        if case.kind == "compatibility" and status != Status.PASS:
            diagnostic = "optional host compatibility differs: " + (diagnostic or "bytes or returns differ")
            status = Status.SKIP
        assertions = 2 + len(expected_calls)
        return TestResult(
            case.case_id, case.category, case.description, status, case.required,
            case.format_display, case.arguments, expected, process.stdout,
            expected_return, actual_return, process.stderr, process.signal,
            process.elapsed, reproduction, diagnostic, case.kind, assertions,
            expected_calls, actual_calls,
        )

    @staticmethod
    def _parse_defined_metadata(data: bytes) -> tuple[int, list[int], bytes, int | None, list[int]] | None:
        header_size = struct.calcsize("=IiI")
        if len(data) < header_size:
            return None
        magic, expected_return, count = struct.unpack_from("=IiI", data, 0)
        if magic != REFERENCE_MAGIC or count > 2048:
            return None
        offset = header_size
        returns_size = count * struct.calcsize("=i")
        if len(data) < offset + returns_size + 8:
            return None
        expected_calls = list(struct.unpack_from(f"={count}i", data, offset)) if count else []
        offset += returns_size
        length = struct.unpack_from("=Q", data, offset)[0]
        offset += 8
        if len(data) < offset + length:
            return None
        expected = data[offset:offset + length]
        offset += length
        if len(data) < offset + header_size:
            return expected_return, expected_calls, expected, None, []
        student_magic, actual_return, student_count = struct.unpack_from("=IiI", data, offset)
        if student_magic != STUDENT_MAGIC or student_count > 2048:
            return expected_return, expected_calls, expected, None, []
        offset += header_size
        student_size = student_count * 4
        if len(data) < offset + student_size:
            return expected_return, expected_calls, expected, None, []
        actual_calls = list(struct.unpack_from(f"={student_count}i", data, offset)) if student_count else []
        return expected_return, expected_calls, expected, actual_return, actual_calls

    @staticmethod
    def _defined_process_status(process, args) -> tuple[Status, str]:
        if process.timed_out:
            return Status.TIMEOUT, f"student process exceeded {args.timeout:g}s"
        if process.output_limited:
            return Status.OUTPUT_LIMIT, f"captured output exceeded {args.max_output} bytes"
        if process.signal is not None:
            return Status.CRASH, "student process terminated by a signal"
        if process.error:
            return Status.HARNESS_ERROR, process.error
        if process.returncode != 0:
            if process.returncode in (119, 120, 122, 123, 124, 125):
                return Status.HARNESS_ERROR, f"harness exited with {process.returncode}"
            return Status.CRASH, f"student process exited early with {process.returncode}"
        return Status.PASS, ""

    def _run_ub(self, case: Case, index: int, reference_executable: Path,
                student_executable: Path, args) -> TestResult:
        invocation = dict(cwd=reference_executable.parent, timeout=args.timeout,
                          max_output=args.max_output)
        reference = run_process(Invocation([str(reference_executable), str(index)], **invocation), metadata=True)
        student = run_process(Invocation([str(student_executable), str(index)], **invocation), metadata=True)
        ref_label, ref_return, ref_infra = self._observation(reference)
        stu_label, stu_return, stu_infra = self._observation(student)
        reproduction = self._reproduction(args, case)
        if ref_infra or stu_infra:
            return TestResult(
                case.case_id, case.category, case.description, Status.HARNESS_ERROR,
                False, case.format_display, case.arguments, reference.stdout, student.stdout,
                ref_return, stu_return, student.stderr, student.signal,
                reference.elapsed + student.elapsed, reproduction,
                ref_infra or stu_infra or "UB harness error", "ub", 0,
                reference_observation=ref_label, student_observation=stu_label,
            )
        same = (
            ref_label == stu_label and reference.stdout == student.stdout
            and reference.stderr == student.stderr and ref_return == stu_return
        )
        findings = self._sanitizer_findings(reference.stderr + student.stderr)
        return TestResult(
            case.case_id, case.category, case.description,
            Status.SAME_OBSERVATION if same else Status.DIFFERENT_OBSERVATION,
            False, case.format_display, case.arguments, reference.stdout, student.stdout,
            ref_return, stu_return, student.stderr, student.signal,
            reference.elapsed + student.elapsed, reproduction,
            "Undefined behavior has no portable expected result.", "ub", 0,
            reference_observation=ref_label, student_observation=stu_label,
            sanitizer_findings=findings,
        )

    @staticmethod
    def _observation(process) -> tuple[str, int | None, str | None]:
        if process.error:
            return "HARNESS_ERROR", None, process.error
        if process.timed_out:
            return "TIMEOUT", None, None
        if process.output_limited:
            return "OUTPUT_LIMIT", None, None
        if process.signal is not None:
            return f"SIGNAL({process.signal})", None, None
        if process.returncode in (119, 120, 122, 123):
            return "HARNESS_ERROR", None, f"UB harness exited with {process.returncode}"
        if process.returncode != 0:
            return f"EXITED({process.returncode})", None, None
        if len(process.metadata) != 4:
            return "HARNESS_ERROR", None, "UB harness returned invalid metadata"
        value = struct.unpack("=i", process.metadata)[0]
        return f"RETURNED({value})", value, None

    @staticmethod
    def _sanitizer_findings(stderr: bytes) -> list[str]:
        findings = []
        for marker, label in (
            (b"AddressSanitizer", "AddressSanitizer"),
            (b"UndefinedBehaviorSanitizer", "UndefinedBehaviorSanitizer"),
            (b"runtime error:", "UBSan runtime error"),
        ):
            if marker in stderr:
                findings.append(label)
        return findings

    @staticmethod
    def _save_failure_artifacts(root: Path, results: list[TestResult]) -> None:
        failures = root / "failures"
        for result in results:
            if result.status not in FAILURE_STATUSES:
                continue
            destination = failures / result.case_id
            destination.mkdir(parents=True, exist_ok=True)
            if result.expected is not None:
                (destination / "expected.bin").write_bytes(result.expected)
            if result.actual is not None:
                (destination / "actual.bin").write_bytes(result.actual)
            if result.stderr:
                (destination / "stderr.bin").write_bytes(result.stderr)
            result.artifact_path = str(destination)
            (destination / "details.txt").write_text(
                f"status={result.status.value}\nexpected_return={result.expected_return}\n"
                f"actual_return={result.actual_return}\nsignal={result.signal}\n"
                f"diagnostic={result.diagnostic}\nrerun={result.reproduction}\n",
                encoding="utf-8",
            )

    @staticmethod
    def _base_reproduction(args) -> str:
        command_name = args.command_name
        if "/" in command_name:
            command_name = str(Path(command_name).resolve())
        command = [command_name, "ft_printf", str(args.project.resolve()),
                   "--profile", args.profile, "--seed", str(args.seed),
                   "--timeout", str(args.timeout), "--build-timeout", str(args.build_timeout),
                   "--max-output", str(args.max_output)]
        if args.ub:
            command.append("--ub")
        if args.archive:
            command.extend(("--archive", str(args.archive.resolve())))
        return shlex.join(command)

    @staticmethod
    def _reproduction(args, case: Case) -> str:
        command_name = args.command_name
        if "/" in command_name:
            command_name = str(Path(command_name).resolve())
        command = [command_name, "ft_printf", str(args.project.resolve()),
                   "--profile", args.profile, "--case", case.case_id,
                   "--seed", str(args.seed), "--timeout", str(args.timeout),
                   "--build-timeout", str(args.build_timeout),
                   "--max-output", str(args.max_output)]
        if case.kind == "ub":
            command.append("--ub")
        if args.archive:
            command.extend(("--archive", str(args.archive.resolve())))
        if args.keep_artifacts:
            command.append("--keep-artifacts")
        return shlex.join(command)


FAILURE_STATUSES = {
    Status.FAIL, Status.CRASH, Status.TIMEOUT, Status.OUTPUT_LIMIT,
    Status.BUILD_ERROR, Status.HARNESS_ERROR,
}
