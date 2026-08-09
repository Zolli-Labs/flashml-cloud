"""What this repo is allowed to import from its public sibling.

The workspace rule (`CLAUDE.md`) is that flashml-cloud imports
``flashruntime``'s versioned **protocol** package and nothing else crosses
repo boundaries. That rule is load-bearing: the public repos must stay
usable and releasable without knowing this one exists, and every extra
surface this repo reaches into is a thing that cannot be changed upstream
without breaking a private consumer nobody upstream can see.

Federated averaging added exactly one exception, and this file is where it
is written down and confined. ``flashml_cloud_api/fedavg.py`` imports
``flashml_workloads.fedavg_driver`` because the driver has to run inside
this API (design spec §5.4.5 — round aggregation must stay on trusted
infrastructure while a node's reported results are still believed) and the
driver itself belongs upstream, where a self-hosted user gets it too.

Dependency declaration (2026-08-09) added a second. ``compile.py`` imports
``flashruntime.images.manifest_for`` because the curated images' dependency
manifests ship as package data inside ``flashruntime`` (one file, two
readers: the image's own Dockerfile and this compiler), and the compiler is
what resolves ``image:`` to that manifest at submit time — see
``docs/superpowers/specs/2026-08-09-dependency-provisioning-design.md`` §4.

The exception is a **single module, listed by name**. A grep-based rule
("don't import flashml_workloads") would have been satisfied by a lazy
import inside a function; enumerating the allowed importers instead means a
second module reaching across trips this test and forces the conversation
rather than the drift.
"""
from __future__ import annotations

import ast
import pathlib

PACKAGE = pathlib.Path(__file__).parent.parent / "flashml_cloud_api"

#: The always-allowed surface: the versioned wire protocol.
PROTOCOL_ROOT = "flashruntime.protocol"

#: module (relative to the package) → the cross-repo modules it may import
#: beyond ``PROTOCOL_ROOT``. Adding an entry here is a deliberate widening
#: of the repo boundary and belongs in a commit that says so.
SANCTIONED_EXCEPTIONS = {
    "fedavg.py": {"flashml_workloads.fedavg_driver"},
    "compile.py": {"flashruntime.images"},
}

#: Top-level packages that live in the sibling public repo.
CROSS_REPO_ROOTS = ("flashruntime", "flashml_workloads", "flashnode")


def _imported_modules(path: pathlib.Path) -> set[str]:
    """Every module name imported anywhere in the file, including inside a
    function body — a deferred import crosses the boundary just as much, it
    just does it later."""
    tree = ast.parse(path.read_bytes(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module)
    return found


def _cross_repo(names: set[str]) -> set[str]:
    return {
        name for name in names
        if name.split(".")[0] in CROSS_REPO_ROOTS
    }


def test_flashnode_is_never_imported():
    """The host agent and this repo meet over the protocol or not at all."""
    for path in sorted(PACKAGE.rglob("*.py")):
        assert not any(
            name.split(".")[0] == "flashnode"
            for name in _imported_modules(path)
        ), path.name


def test_only_the_protocol_and_the_sanctioned_exceptions_cross_the_boundary():
    offenders: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        allowed = SANCTIONED_EXCEPTIONS.get(path.name, set())
        extra = {
            name
            for name in _cross_repo(_imported_modules(path))
            if not (name == PROTOCOL_ROOT or name.startswith(PROTOCOL_ROOT + "."))
            and name not in allowed
        }
        if extra:
            offenders[path.name] = extra
    assert offenders == {}, (
        f"these modules import across the repo boundary without being listed "
        f"in SANCTIONED_EXCEPTIONS: {offenders}. The workspace rule is "
        f"protocol-only; widening it is a decision to record, not to make "
        f"in passing."
    )


def test_the_driver_import_lives_in_exactly_one_module():
    """If this ever fails because a *second* module imports the driver, the
    fix is not to add it to the exception list — it is to route through
    ``fedavg.py``, so the boundary stays one door wide."""
    importers = [
        path.name
        for path in sorted(PACKAGE.rglob("*.py"))
        if any(name.startswith("flashml_workloads")
               for name in _imported_modules(path))
    ]
    assert importers == ["fedavg.py"]
