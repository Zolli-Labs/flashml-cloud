"""Static preflight over a fetched repo — the reason Plan 4 exists.

Its whole job is to turn *"your job failed on a stranger's laptop forty
minutes in"* into *"line 3 of your config is wrong"*, **before** anything is
queued. A volunteer node runs the job with ``--network none``, from a
pinned image nobody can add to, and commits only what lands in
``/work/out``. Every one of those constraints is invisible at submit time
unless something looks for it, and every one of them, when violated,
produces a failure on someone else's machine that reads like a node fault.

**Nothing here ever imports or executes the user's code.** Every check is a
static ``ast`` walk over bytes read off disk. That is not a stylistic
preference: preflight runs inside the API process, holding the operator
credential and a live database connection, on a tarball any signed-in user
can point at us. A sandbox escape at submit time would be far worse than
every failure preflight prevents put together. So there is no ``import``,
no ``importlib``, no ``exec``, no ``compile(..., "exec")``, and no
``sys.path`` manipulation in this module — ``ast.parse`` builds a tree and
that is all. A ``.pth`` file, a ``__init__.py``, a crafted filename, or a
symlink in the repo are all just bytes we never open or names we never
resolve.

**Scope: the entrypoint file only.** Not the whole repo. A repo-wide scan
sounds more thorough and is in practice a false-positive machine — a
``tests/`` directory that uses ``requests``, a ``docs/`` example importing a
package the chosen image lacks, or a vendored dependency would each block a
legitimate submission with an error the user cannot act on. Preflight is a
*helpfulness* control, not a security one: the sandbox and ``--network
none`` are what actually contain a job. When in doubt this module stays
quiet, and where the code visibly anticipates absence (a guarded ``try:
import torch``) it downgrades to a warning rather than refusing.

Every message that interpolates repo content runs through ``_safe`` first:
findings are returned in an HTTP response and written to logs, and a
newline or control character out of a user's source file has no business in
either.
"""
from __future__ import annotations

import ast
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from flashml_cloud_api.flashml_yaml import FlashmlConfig
from flashml_cloud_api.images import CURATED, CuratedImage

ERROR = "error"
WARNING = "warning"

#: Modules whose presence means the job wants the network. Volunteer nodes
#: run with ``--network none``, so this is an **error**, not a style note:
#: the code cannot work there, and the failure would otherwise surface as a
#: mid-run traceback on a stranger's machine.
#:
#: Deliberately short. Every name added here is a name that can block a
#: legitimate submission, so the list is the plan's five plus the two
#: unambiguous HTTP clients, and nothing speculative.
NETWORK_MODULES = frozenset(
    {
        "requests",
        "urllib",
        "urllib3",
        "httpx",
        "socket",
        "huggingface_hub",
        "aiohttp",
    }
)

#: Command-line tools that only exist to fetch something. Detected only as
#: the *program* of a recognised subprocess call — never from a bare string
#: anywhere in the file, which would fire on any docstring that mentions
#: curl.
NETWORK_TOOLS = frozenset({"curl", "wget", "aria2c", "scp", "rsync"})

#: Calls whose first argument (or first list element) is a program to run.
_SUBPROCESS_FUNCS = frozenset(
    {"run", "call", "check_call", "check_output", "Popen", "system", "popen"}
)
_SUBPROCESS_MODULES = frozenset({"subprocess", "os"})

#: Exception types that make an ``ImportError`` visibly anticipated.
_IMPORT_GUARDS = frozenset(
    {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
)

#: Method calls that take a filesystem path as a positional argument. Used
#: only for the ``writes-outside-out`` *warning*, so a miss here costs a
#: hint, never a blocked submission.
_WRITE_METHODS = frozenset(
    {
        "write_text",
        "write_bytes",
        "savefig",
        "to_csv",
        "to_json",
        "to_parquet",
        "to_pickle",
        "savez",
        "savez_compressed",
        "imwrite",
    }
)

#: The only directory a task's output is collected from.
OUT_DIR = "/work/out"

#: Largest entrypoint this module will read and parse. A single Python
#: source file larger than this is pathological; refusing to analyze it
#: beats reading an unbounded amount of a stranger's tarball into the API
#: process to hand to the parser.
MAX_SOURCE_BYTES = 4 * 1024 * 1024

#: Ceiling on how many findings come back. A pathological file can produce
#: thousands of near-identical warnings; past a few dozen the response has
#: stopped being useful and started being a payload.
MAX_FINDINGS = 50

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Finding:
    """One thing preflight noticed.

    ``level`` is ``"error"`` or ``"warning"``. An error refuses the
    submission (the caller returns *every* finding at once so the user fixes
    everything in one pass); a warning is advice that does not block.
    """

    level: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message}


def _safe(value: object, limit: int = 120) -> str:
    """Render repo-controlled text fit for an HTTP response and a log line.

    Control characters — above all CR and LF — become ``?``, and the result
    is truncated. Findings quote things a user chose: an entrypoint path, a
    string literal from their source, a parser message. None of that should
    be able to inject a line into the API's JSON-per-line logs or arrive in
    a response as anything but inert text.
    """
    text = _CONTROL_CHARS.sub("?", str(value))
    if len(text) > limit:
        text = text[:limit] + "..."
    return text


# ---------------------------------------------------------------------------
# entrypoint resolution
# ---------------------------------------------------------------------------


def _resolve_entrypoint(repo_root: Path, entrypoint: str) -> Path | None:
    """The entrypoint's real path inside the repo, or None if there isn't one.

    ``entrypoint`` comes straight out of a user's ``flashml.yaml``, so
    ``../../../../etc/passwd`` and ``/etc/passwd`` are both things it can
    say. Resolving first and then checking containment covers the symlink
    case too: a link inside the repo pointing at ``/etc/shadow`` resolves
    outside ``repo_root`` and is refused here, which is the only reason this
    module never reads a byte it was not pointed at.
    """
    if not entrypoint or "\x00" in entrypoint:
        return None
    root = Path(repo_root).resolve()
    candidate = (root / entrypoint).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


# ---------------------------------------------------------------------------
# import collection
# ---------------------------------------------------------------------------


def _try_nodes(tree: ast.AST) -> list[ast.AST]:
    kinds: list[type] = [ast.Try]
    try_star = getattr(ast, "TryStar", None)  # 3.11+
    if try_star is not None:
        kinds.append(try_star)
    return [n for n in ast.walk(tree) if isinstance(n, tuple(kinds))]


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True  # bare `except:` — catches ImportError along with all else
    candidates = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    for node in candidates:
        if isinstance(node, ast.Name) and node.id in _IMPORT_GUARDS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _IMPORT_GUARDS:
            return True
    return False


def _guarded_import_ids(tree: ast.AST) -> set[int]:
    """Ids of import nodes that sit in a ``try:`` whose ``except`` catches an
    ImportError.

    Such code visibly anticipates the package being absent — it has a
    fallback, or it degrades on purpose. Refusing it would block a
    submission that was written to survive exactly the condition we are
    refusing it for, so those become warnings instead (Plan 4's "prefer
    warning wherever the code visibly anticipates the absence")."""
    guarded: set[int] = set()
    for try_node in _try_nodes(tree):
        handlers = getattr(try_node, "handlers", [])
        if not any(_handler_catches_import_error(h) for h in handlers):
            continue
        for stmt in getattr(try_node, "body", []):
            for inner in ast.walk(stmt):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    guarded.add(id(inner))
    return guarded


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _type_checking_import_ids(tree: ast.AST) -> set[int]:
    """Ids of imports under ``if TYPE_CHECKING:``. They never execute at run
    time, so flagging one would refuse a job over an import that is not
    there when the job runs."""
    ignored: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for stmt in node.body:
                for inner in ast.walk(stmt):
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        ignored.add(id(inner))
    return ignored


def _imported_top_level_modules(tree: ast.AST) -> list[tuple[str, int, bool]]:
    """Every top-level module name the file imports, as
    ``(name, lineno, guarded)``.

    Two rules worth stating out loud:

    - ``import os.path`` yields ``os``. The package that has to exist is the
      first component; reporting ``os.path`` as a missing package would be
      both wrong and unactionable.
    - ``from . import utils`` and ``from ..pkg import x`` yield nothing.
      A relative import names something inside the repo, which no image
      manifest could ever contain — reporting it would be a guaranteed
      false positive on any repo laid out as a package.

    Imports anywhere in the file count, including inside a function body or
    a class: a deferred ``import torch`` fails just as hard, just later.
    """
    guarded = _guarded_import_ids(tree)
    ignored = _type_checking_import_ids(tree)
    found: list[tuple[str, int, bool]] = []
    for node in ast.walk(tree):
        if id(node) in ignored:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top:
                    found.append((top, node.lineno, id(node) in guarded))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative: resolves inside the repo, not the image
            if not node.module:
                continue
            top = node.module.split(".")[0]
            if top:
                found.append((top, node.lineno, id(node) in guarded))
    return found


def _is_repo_local_module(repo_root: Path, entry_dir: Path, name: str) -> bool:
    """True when ``name`` is a module the repo itself ships.

    ``import utils`` next to ``train.py`` is an absolute import of a
    first-party file. No image manifest lists it and it works fine, so
    without this check the single most common repo layout would be an
    ``unknown-import`` error on every submission.
    """
    if not name.isidentifier():
        return False
    for base in {entry_dir, Path(repo_root).resolve()}:
        if (base / f"{name}.py").is_file():
            return True
        if (base / name / "__init__.py").is_file():
            return True
    return False


def _providing_images(name: str, exclude: str) -> list[str]:
    return sorted(
        alias
        for alias, curated in CURATED.items()
        if alias != exclude and name in curated.packages
    )


# ---------------------------------------------------------------------------
# shelling out
# ---------------------------------------------------------------------------


def _call_func_name(call: ast.Call) -> tuple[str | None, str | None]:
    """``(module, attribute)`` for ``subprocess.run(...)``; ``(None, name)``
    for a bare ``run(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        module = owner.id if isinstance(owner, ast.Name) else None
        return module, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _program_strings(call: ast.Call) -> list[str]:
    """The literal program(s) a subprocess-ish call would execute.

    Only the first positional argument is considered, in its two legal
    shapes: a string (``os.system("curl ...")``) or a list whose first
    element is the program (``subprocess.run(["wget", url])``). Anything
    computed at run time is invisible to a static check and is left alone
    rather than guessed at.
    """
    if not call.args:
        return []
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [first.value]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return [head.value]
    return []


def _network_tool_in(command: str) -> str | None:
    """The fetch tool a command string starts with, if any.

    Looks only at the first whitespace-separated token, and only at its
    basename, so ``/usr/bin/curl -sSL ...`` counts and ``echo 'see curl
    docs'`` does not.
    """
    token = command.strip().split()[0] if command.strip() else ""
    if not token:
        return None
    program = posixpath.basename(token)
    return program if program in NETWORK_TOOLS else None


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def _is_write_mode(mode: str) -> bool:
    return any(ch in mode for ch in "wax+")


def _receiver_literal(node: ast.expr) -> str | None:
    """The literal path a bound call was made on: the ``"/tmp/x"`` in
    ``Path("/tmp/x").write_text(...)``. A receiver held in a variable is
    invisible to a static check and yields None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
    return None


def _open_write_paths(call: ast.Call) -> list[str]:
    """Literal paths an ``open(...)`` / ``.open(...)`` call writes to.

    The two spellings put their arguments in different places —
    ``open(path, mode)`` versus ``Path(path).open(mode)`` — so the argument
    index is chosen from which one this is rather than guessed by looking
    for a string that resembles a mode.
    """
    func = call.func
    builtin_open = isinstance(func, ast.Name) and func.id == "open"
    method_open = isinstance(func, ast.Attribute) and func.attr == "open"
    if not (builtin_open or method_open):
        return []

    mode: str | None = None
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                mode = keyword.value.value
    if mode is None:
        index = 1 if builtin_open else 0
        if len(call.args) > index:
            arg = call.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                mode = arg.value
    if mode is None or not _is_write_mode(mode):
        return []

    if builtin_open:
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str):
                return [value]
        return []
    receiver = _receiver_literal(func.value)  # type: ignore[union-attr]
    return [receiver] if receiver is not None else []


def _method_write_paths(call: ast.Call) -> list[str]:
    """Literal paths a known path-taking write method targets — either as an
    argument (``df.to_csv("/tmp/x.csv")``) or as the receiver
    (``Path("/tmp/x").write_text(...)``)."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _WRITE_METHODS:
        return []
    out: list[str] = []
    receiver = _receiver_literal(func.value)
    if receiver is not None:
        out.append(receiver)
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            candidate = arg.value
            if "/" in candidate or "." in candidate:
                out.append(candidate)
    return out


def _writes_outside_out(path: str) -> bool:
    """True when a literal path is not under ``/work/out``.

    Absolute paths elsewhere are the obvious case. A *relative* path counts
    too, and that is not pedantry: the container is started with the repo's
    working directory bound at ``/work`` but no ``-w``, so a relative write
    lands in whatever the image's own WORKDIR is and is never collected —
    the job appears to succeed and produces nothing.
    """
    if not path or "\x00" in path:
        return True
    if path.startswith("/"):
        normalised = posixpath.normpath(path)
        return not (normalised == OUT_DIR or normalised.startswith(OUT_DIR + "/"))
    normalised = posixpath.normpath(path)
    return not (normalised == "out" or normalised.startswith("out/"))


# ---------------------------------------------------------------------------
# the entry point
# ---------------------------------------------------------------------------


def preflight(
    config: FlashmlConfig, repo_root: Path, image: CuratedImage
) -> list[Finding]:
    """Every static objection to running ``config`` from ``repo_root`` on
    ``image``, worst first. Never raises for anything about the user's repo
    — a syntax error in their file is a ``unparseable`` finding, not an
    exception — and never imports or runs a line of it.
    """
    findings: list[Finding] = []
    entry_label = _safe(config.entrypoint, 200)

    path = _resolve_entrypoint(Path(repo_root), config.entrypoint)
    if path is None:
        findings.append(
            Finding(
                ERROR,
                "entrypoint-missing",
                f"entrypoint {entry_label!r} does not exist in the repo",
            )
        )
        return findings

    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        return [
            Finding(
                ERROR,
                "unparseable",
                f"{entry_label} is {size} bytes, too large to analyze statically "
                f"(cap {MAX_SOURCE_BYTES} bytes)",
            )
        ]

    source_bytes = path.read_bytes()
    try:
        # Bytes, not text, on purpose: ast.parse handles the BOM and any
        # PEP 263 coding cookie itself, so a file that declares latin-1 is
        # parsed as latin-1 rather than mangled by a guessed decode.
        tree = ast.parse(source_bytes, filename="<entrypoint>")
    except SyntaxError as exc:
        where = f" (line {exc.lineno})" if exc.lineno else ""
        return [
            Finding(
                ERROR,
                "unparseable",
                f"{entry_label} is not valid Python: {_safe(exc.msg)}{where}",
            )
        ]
    except (ValueError, RecursionError, MemoryError) as exc:
        # ValueError: embedded null byte. RecursionError/MemoryError: the
        # parser's own guards against a pathologically nested file. All
        # three are the user's file being unanalysable, never a crash of
        # ours — and never a hang, because every guard fires in the parser
        # rather than in a walk of ours (ast.walk is iterative).
        return [
            Finding(
                ERROR,
                "unparseable",
                f"{entry_label} could not be parsed as Python: "
                f"{_safe(type(exc).__name__)}",
            )
        ]

    source_text = source_bytes.decode("utf-8", errors="replace")

    findings.extend(_import_findings(tree, Path(repo_root), path, image))
    findings.extend(_shell_findings(tree))
    findings.extend(_metrics_findings(source_text, entry_label))
    findings.extend(_write_findings(tree))

    return _dedupe(findings)


def _import_findings(
    tree: ast.AST, repo_root: Path, entry_path: Path, image: CuratedImage
) -> list[Finding]:
    findings: list[Finding] = []
    entry_dir = entry_path.parent
    seen: set[str] = set()
    for name, lineno, guarded in _imported_top_level_modules(tree):
        if name in seen:
            continue
        seen.add(name)

        if name in NETWORK_MODULES:
            # Reported here rather than as an unknown import even when the
            # image also lacks it: "there is no network" is the actionable
            # fact, and two findings for one import is noise.
            findings.append(
                Finding(
                    ERROR,
                    "network-use",
                    f"line {lineno}: imports {name!r}, but volunteer nodes run "
                    f"with --network none — the job cannot reach the network at "
                    f"run time. Stage what you need as an input artifact instead.",
                )
            )
            continue

        if name in image.packages:
            continue
        if _is_repo_local_module(repo_root, entry_dir, name):
            continue

        providers = _providing_images(name, exclude=image.alias)
        if providers:
            tail = (
                f"the {providers[0]!r} image does provide it"
                if len(providers) == 1
                else f"these images do provide it: {', '.join(repr(p) for p in providers)}"
            )
        else:
            tail = (
                "no curated image provides it (available: "
                + ", ".join(repr(a) for a in sorted(CURATED))
                + ")"
            )
        message = (
            f"line {lineno}: imports {name!r}, which the {image.alias!r} image "
            f"does not provide; {tail}"
        )
        if guarded:
            findings.append(
                Finding(
                    WARNING,
                    "unknown-import",
                    message + " — the import is guarded, so this is a warning only",
                )
            )
        else:
            findings.append(Finding(ERROR, "unknown-import", message))
    return findings


def _shell_findings(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        module, attr = _call_func_name(node)
        if attr not in _SUBPROCESS_FUNCS:
            continue
        if module is not None and module not in _SUBPROCESS_MODULES:
            continue
        for command in _program_strings(node):
            tool = _network_tool_in(command)
            if tool is None:
                continue
            findings.append(
                Finding(
                    ERROR,
                    "network-use",
                    f"line {node.lineno}: shells out to {tool!r}, but volunteer "
                    f"nodes run with --network none — the fetch cannot succeed. "
                    f"Stage what you need as an input artifact instead.",
                )
            )
    return findings


def _metrics_findings(source_text: str, entry_label: str) -> list[Finding]:
    if "metrics.json" in source_text:
        return []
    return [
        Finding(
            WARNING,
            "no-metrics-json",
            f"{entry_label} never mentions 'metrics.json'; a task that does not "
            f"write {OUT_DIR}/metrics.json cannot commit its result",
        )
    ]


def _write_findings(tree: ast.AST) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for target in _open_write_paths(node) + _method_write_paths(node):
            if not _writes_outside_out(target):
                continue
            findings.append(
                Finding(
                    WARNING,
                    "writes-outside-out",
                    f"line {node.lineno}: writes to {_safe(target)!r}, which is "
                    f"outside {OUT_DIR} — only files under {OUT_DIR} are "
                    f"collected as artifacts",
                )
            )
    return findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Errors first, then warnings; duplicates dropped, total capped.

    Order matters for a human reading the response: the things that
    actually blocked the submission belong at the top.
    """
    seen: set[tuple[str, str, str]] = set()
    ordered: list[Finding] = []
    for level in (ERROR, WARNING):
        for finding in findings:
            if finding.level != level:
                continue
            key = (finding.level, finding.code, finding.message)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(finding)
    return ordered[:MAX_FINDINGS]
