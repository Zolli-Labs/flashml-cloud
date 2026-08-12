"""Preflight: every check fires on a crafted repo and stays quiet on a clean one.

Both halves matter equally. A check that never fires is useless; a check
that *always* fires is worse, because it blocks legitimate submissions and
trains users to ignore the output. So almost every case here is paired: the
violation, and the near-identical clean repo that must produce nothing.

Nothing in this file executes the fixture code. Neither does preflight —
that is the property the last section of this file attacks directly.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image
from flashml_cloud_api.preflight import Finding, preflight

PYTHON_SLIM = resolve_image("python-slim")
PYTORCH = resolve_image("pytorch-cpu")


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))
    return root


def _config(entrypoint: str = "train.py", image: str = "python-slim"):
    return parse_flashml_yaml(
        f"version: 1\nname: demo\nimage: {image}\nentrypoint: {entrypoint}\n"
    )


def _codes(findings: list[Finding]) -> set[str]:
    return {f.code for f in findings}


def _levels(findings: list[Finding], code: str) -> set[str]:
    return {f.level for f in findings if f.code == code}


def _except_checkpoint(findings: list[Finding]) -> list[Finding]:
    """Everything except the ``no-checkpoint`` advisory.

    Checkpointing is on for every task, so an entrypoint that says nothing
    about it draws a warning — which is the entire point of that check, and
    which most fixtures in this file do. They are testing something else,
    and asserting "no findings at all" on them would be asserting that
    ``no-checkpoint`` does not work. The dedicated section near the bottom
    is where its firing and staying quiet are both pinned down.
    """
    return [f for f in findings if f.code != "no-checkpoint"]


CLEAN_SOURCE = """
    import json
    import os.path
    import argparse


    def main() -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--epochs", type=int, default=1)
        args = parser.parse_args()
        result = {"epochs": args.epochs, "accuracy": 0.9}
        with open("/work/out/metrics.json", "w") as handle:
            json.dump(result, handle)
        print(os.path.basename("/work/out/metrics.json"))


    if __name__ == "__main__":
        main()
"""


# ---------------------------------------------------------------------------
# the clean repo: the baseline every other case is measured against
# ---------------------------------------------------------------------------


def test_a_clean_repo_produces_no_findings_at_all(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    assert _except_checkpoint(preflight(_config(), root, PYTHON_SLIM)) == []


def test_the_only_finding_on_a_clean_repo_is_the_checkpoint_advisory(tmp_path):
    """The baseline moved, and it is worth spelling out where it moved to.

    A file this short has nothing to resume from, so `no-checkpoint` firing
    is the check working: checkpointing is on for every task and this one
    would lose everything to a closed laptop. It is a warning precisely so
    that saying so costs a line of advice and not a refused submission.
    """
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _codes(findings) == {"no-checkpoint"}
    assert _levels(findings, "no-checkpoint") == {"warning"}


def test_a_clean_pytorch_repo_produces_no_findings(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                import json
                import torch
                import numpy as np

                model = torch.nn.Linear(2, 2)
                with open("/work/out/metrics.json", "w") as fh:
                    json.dump({"loss": float(np.mean([1.0]))}, fh)
            """
        },
    )
    findings = preflight(_config(image="pytorch-cpu"), root, PYTORCH)
    assert _except_checkpoint(findings) == []


# ---------------------------------------------------------------------------
# entrypoint-missing
# ---------------------------------------------------------------------------


def test_missing_entrypoint_is_an_error(tmp_path):
    root = _repo(tmp_path, {"other.py": CLEAN_SOURCE})
    findings = preflight(_config("train.py"), root, PYTHON_SLIM)
    assert _codes(findings) == {"entrypoint-missing"}
    assert findings[0].level == "error"
    assert "train.py" in findings[0].message


def test_an_entrypoint_that_is_a_directory_is_missing_not_a_crash(tmp_path):
    root = _repo(tmp_path, {"train.py/inner.py": "x = 1\n"})
    findings = preflight(_config("train.py"), root, PYTHON_SLIM)
    assert _codes(findings) == {"entrypoint-missing"}


def test_no_other_check_runs_once_the_entrypoint_is_missing(tmp_path):
    """One actionable error, not five speculative ones about a file that
    isn't there."""
    root = _repo(tmp_path, {})
    findings = preflight(_config("nope.py"), root, PYTHON_SLIM)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# unknown-import
# ---------------------------------------------------------------------------


def test_unknown_import_is_an_error_naming_the_package(tmp_path):
    root = _repo(tmp_path, {"train.py": "import torch\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    unknown = [f for f in findings if f.code == "unknown-import"]
    assert len(unknown) == 1
    assert unknown[0].level == "error"
    assert "torch" in unknown[0].message


def test_unknown_import_names_the_curated_image_that_does_provide_it(tmp_path):
    root = _repo(tmp_path, {"train.py": "import torch\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    message = next(f.message for f in findings if f.code == "unknown-import")
    assert "pytorch-cpu" in message


def test_unknown_import_says_so_when_no_curated_image_provides_it(tmp_path):
    root = _repo(tmp_path, {"train.py": "import tensorflow\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    message = next(f.message for f in findings if f.code == "unknown-import")
    assert "no curated image provides it" in message
    assert "python-slim" in message  # the available set is still listed


def test_a_provided_package_is_not_reported(tmp_path):
    root = _repo(tmp_path, {"train.py": "import torch\nimport numpy\n"})
    findings = preflight(_config(image="pytorch-cpu"), root, PYTORCH)
    assert "unknown-import" not in _codes(findings)


def test_a_relative_import_is_never_reported_as_an_unknown_package(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                from . import utils
                from .helpers import thing
                from ..pkg import other

                print("metrics.json", utils, thing, other)
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" not in _codes(findings), findings


def test_import_os_path_resolves_against_os(tmp_path):
    root = _repo(
        tmp_path,
        {"train.py": "import os.path\nfrom os.path import join\nprint('metrics.json')\n"},
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" not in _codes(findings), findings


def test_a_repo_local_module_imported_absolutely_is_not_unknown(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": "import utils\nimport mypkg\nprint('metrics.json')\n",
            "utils.py": "VALUE = 1\n",
            "mypkg/__init__.py": "",
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" not in _codes(findings), findings


def test_an_import_inside_a_function_body_is_still_detected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                def train():
                    import torch
                    return torch

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" in _codes(findings)
    assert _levels(findings, "unknown-import") == {"error"}


def test_an_import_inside_a_class_body_is_still_detected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                class Trainer:
                    import torch

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" in _codes(findings)


def test_a_guarded_import_is_a_warning_not_an_error(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                try:
                    import torch
                except ImportError:
                    torch = None

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _levels(findings, "unknown-import") == {"warning"}
    assert all(f.level != "error" for f in findings), findings


def test_a_guard_that_catches_something_unrelated_is_still_an_error(tmp_path):
    """`except ValueError` around an import does not anticipate absence."""
    root = _repo(
        tmp_path,
        {
            "train.py": """
                try:
                    import torch
                except ValueError:
                    torch = None

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _levels(findings, "unknown-import") == {"error"}


def test_an_import_under_if_type_checking_is_not_reported(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    import torch

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" not in _codes(findings), findings


def test_the_same_missing_package_is_reported_once_not_per_import(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": """
                import torch
                import torch.nn
                from torch import optim

                print("metrics.json")
            """,
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert len([f for f in findings if f.code == "unknown-import"]) == 1


# ---------------------------------------------------------------------------
# network-use
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "import requests",
        "import urllib.request",
        "import httpx",
        "import socket",
        "import huggingface_hub",
        "from requests import get",
        "from huggingface_hub import snapshot_download",
    ],
)
def test_network_imports_are_errors(tmp_path, line):
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "network-use" in _codes(findings), findings
    assert _levels(findings, "network-use") == {"error"}


def test_network_use_is_an_error_not_a_warning_because_of_network_none(tmp_path):
    root = _repo(tmp_path, {"train.py": "import requests\nprint('metrics.json')\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    message = next(f.message for f in findings if f.code == "network-use")
    assert "--network none" in message


def test_a_clean_repo_does_not_trigger_network_use(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    assert "network-use" not in _codes(preflight(_config(), root, PYTHON_SLIM))


@pytest.mark.parametrize(
    "call",
    [
        'subprocess.run(["curl", "-sSL", "http://x/data.tar"])',
        'subprocess.check_call(["wget", "http://x/data.tar"])',
        'os.system("curl http://x/data.tar -o /tmp/d.tar")',
        'subprocess.Popen(["/usr/bin/curl", "http://x"])',
    ],
)
def test_shelling_out_to_a_fetch_tool_is_an_error(tmp_path, call):
    root = _repo(
        tmp_path,
        {"train.py": f"import subprocess\nimport os\n{call}\nprint('metrics.json')\n"},
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "network-use" in _codes(findings), findings
    assert _levels(findings, "network-use") == {"error"}


def test_a_docstring_mentioning_curl_does_not_fire(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": '''
                """Fetch the dataset yourself with curl before submitting.

                Do not use wget inside the job.
                """
                print("metrics.json")
            ''',
        },
    )
    assert _except_checkpoint(preflight(_config(), root, PYTHON_SLIM)) == []


def test_an_ordinary_subprocess_call_is_not_network_use(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": (
                "import subprocess\n"
                'subprocess.run(["python", "-c", "print(1)"])\n'
                "print('metrics.json')\n"
            )
        },
    )
    assert "network-use" not in _codes(preflight(_config(), root, PYTHON_SLIM))


# ---------------------------------------------------------------------------
# no-metrics-json
# ---------------------------------------------------------------------------


def test_never_mentioning_metrics_json_is_a_warning(tmp_path):
    root = _repo(tmp_path, {"train.py": 'print("done")\n'})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "no-metrics-json" in _codes(findings)
    assert _levels(findings, "no-metrics-json") == {"warning"}


def test_mentioning_metrics_json_silences_the_warning(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    assert "no-metrics-json" not in _codes(preflight(_config(), root, PYTHON_SLIM))


def test_no_metrics_json_never_blocks_a_submission(tmp_path):
    root = _repo(tmp_path, {"train.py": 'print("done")\n'})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert all(f.level == "warning" for f in findings)


# ---------------------------------------------------------------------------
# writes-outside-out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'open("/tmp/model.pt", "wb").close()',
        'open("results.json", "w").close()',
        'open("/work/output/metrics.json", "w").close()',
        'from pathlib import Path; Path("/tmp/x.txt").write_text("hi")',
        'df.to_csv("/data/out.csv")',
    ],
)
def test_writing_outside_work_out_is_a_warning(tmp_path, line):
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "writes-outside-out" in _codes(findings), findings
    assert _levels(findings, "writes-outside-out") == {"warning"}


@pytest.mark.parametrize(
    "line",
    [
        'open("/work/out/metrics.json", "w").close()',
        'open("/work/out/nested/model.pt", "wb").close()',
        'from pathlib import Path; Path("/work/out/m.json").write_text("{}")',
        'open("/work/out/metrics.json", mode="w").close()',
    ],
)
def test_writing_under_work_out_is_not_reported(tmp_path, line):
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "writes-outside-out" not in _codes(findings), findings


def test_reading_a_file_outside_work_out_is_not_a_write(tmp_path):
    root = _repo(
        tmp_path,
        {"train.py": 'open("/work/inputs/data.csv").close()\nprint("metrics.json")\n'},
    )
    assert _except_checkpoint(preflight(_config(), root, PYTHON_SLIM)) == []


def test_a_computed_path_is_not_guessed_at(tmp_path):
    """No literal, no finding — a static check that guesses is a check that
    cries wolf."""
    root = _repo(
        tmp_path,
        {
            "train.py": """
                import os
                out = os.environ.get("OUT", "/work/out")
                open(os.path.join(out, "metrics.json"), "w").close()
            """,
        },
    )
    assert "writes-outside-out" not in _codes(preflight(_config(), root, PYTHON_SLIM))


# ---------------------------------------------------------------------------
# no-checkpoint / checkpoint-path — the convention nothing else states
#
# Checkpointing is unconditional: the compiler emits `parameters["checkpoint"]
# = {}` for every job, so the relay is always running and there is no
# `checkpoint:` key for a user to find. The directory and the glob are
# hardcoded in flashnode. That makes these two findings the only place in the
# product where a user is told what their code has to do, which is why both
# halves of the contract — the write path and the resume path — are pinned
# here rather than left to the message text.
# ---------------------------------------------------------------------------

COMPETITION = Path(__file__).resolve().parents[4] / "e2e" / "competition"

#: The two tests below read real workloads out of `e2e/`, which is a sibling
#: of this app inside the repo and absent from an installed wheel. Skipped
#: rather than failed there: they are a cross-check on files this package
#: does not own, not a property of the package.
needs_competition = pytest.mark.skipif(
    not COMPETITION.is_dir(), reason="e2e/competition not present in this checkout"
)


def _competition_repo(tmp_path: Path, entry: str) -> Path:
    """A repo holding one real competition workload plus the helper it
    imports. These are the reference implementations of the convention (and
    of having nothing to checkpoint), so they are the strongest available
    check that these findings fire on the right file and only on it."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    for name in (entry, "workload_common.py"):
        (root / name).write_bytes((COMPETITION / name).read_bytes())
    return root


def test_an_entrypoint_that_never_mentions_checkpointing_is_warned(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "no-checkpoint" in _codes(findings), findings
    assert _levels(findings, "no-checkpoint") == {"warning"}


def test_no_checkpoint_never_blocks_a_submission(tmp_path):
    """A warning, not an error, and the reason is not squeamishness.

    `federated-contract` is an error because the author opted into a mode
    that cannot work without the contract. Nobody opts into checkpointing —
    it is on for everyone — and a forty-second job has nothing to resume
    from. Refusing those would block working code, which the module
    docstring rules out in as many words.
    """
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert [f for f in findings if f.level == "error"] == []


def test_no_checkpoint_states_both_halves_of_the_contract(tmp_path):
    """The finding is the documentation. A message that said "this job is
    not resumable" would be an observation with no way to act on it."""
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    (message,) = [
        f.message for f in preflight(_config(), root, PYTHON_SLIM)
        if f.code == "no-checkpoint"
    ]
    assert "/work/out/ckpt/step-<N>.json" in message      # where to write
    assert "/work/inputs/resume.json" in message          # where to read
    assert "atomic" in message                            # how to write it
    assert "start from scratch" in message                # absent on attempt 1


def test_no_checkpoint_warns_about_the_torch_helper_that_looks_right(tmp_path):
    """`flashruntime.torch.checkpoint()` defaults its root to
    `<output dir>/ckpt` — the exact directory the relay watches — and then
    writes `step-000100/` *directories* of `model.pt`, while the relay globs
    `step-*.json` *files*. Reaching for the runtime's own helper is the one
    wrong turn a careful user is most likely to take, and it is silent."""
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    (message,) = [
        f.message for f in preflight(_config(), root, PYTHON_SLIM)
        if f.code == "no-checkpoint"
    ]
    assert "flashruntime.torch.checkpoint()" in message
    assert "step-*.json" in message


@pytest.mark.parametrize(
    "line",
    [
        'open("/work/out/ckpt/step-100.json", "w").close()',
        'open("out/ckpt/step-0.json", "w").close()',
        'print("/work/inputs/resume.json")',
        'ckpt = f"step-{n}.json"',
        'CKPT_DIR = "/work/out/ckpt"',
    ],
)
def test_any_one_marker_silences_no_checkpoint(tmp_path, line):
    """Each marker is individually weak evidence, so any one of them is
    enough. Demanding all three would fire on code that builds its
    checkpoint directory out of an `--out` flag — which is most of it."""
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    assert "no-checkpoint" not in _codes(preflight(_config(), root, PYTHON_SLIM))


@needs_competition
def test_the_real_checkpointed_workload_is_not_warned(tmp_path):
    """`e2e/competition/train_checkpointed.py` is the convention done right,
    and it never spells `out/ckpt` — it joins `--out` with "ckpt" at run
    time. If this fires, the check is wrong about the reference file."""
    root = _competition_repo(tmp_path, "train_checkpointed.py")
    findings = preflight(_config("train_checkpointed.py"), root, PYTHON_SLIM)
    assert _codes(findings) == set(), findings


@needs_competition
def test_the_real_evaluate_workload_is_warned_and_still_allowed(tmp_path):
    """`evaluate_model.py` genuinely has nothing to checkpoint. It is still
    told — an eval that dies halfway still starts over — and it is still a
    warning, so nothing about this finding stands in its way."""
    root = _competition_repo(tmp_path, "evaluate_model.py")
    findings = preflight(_config("evaluate_model.py"), root, PYTHON_SLIM)
    assert "no-checkpoint" in _codes(findings), findings
    assert _levels(findings, "no-checkpoint") == {"warning"}
    assert "checkpoint-path" not in _codes(findings)


@pytest.mark.parametrize(
    "line",
    [
        # The trap in the brief: plausible, under `out/`, and silent until
        # now because `writes-outside-out` correctly does not fire on it.
        'open("/work/out/checkpoints/model.pt", "wb").close()',
        'open("/work/out/ckpt/model.pt", "wb").close()',
        'open("/work/out/ckpt/latest.json", "w").close()',
        # What `flashruntime.torch.checkpoint()` produces, spelled out.
        'open("/work/out/ckpt/step-000100/model.pt", "wb").close()',
        'open("out/ckpt/checkpoint.json", "w").close()',
        'from pathlib import Path; Path("/work/out/ckpt/final.json").write_text("{}")',
    ],
)
def test_a_checkpoint_shaped_write_in_the_wrong_shape_is_warned(tmp_path, line):
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "checkpoint-path" in _codes(findings), findings
    assert _levels(findings, "checkpoint-path") == {"warning"}
    # `writes-outside-out` is silent on all of these — the path IS under
    # `out/` — which is exactly the gap this finding exists to close.
    assert "writes-outside-out" not in _codes(findings)


def test_checkpoint_path_names_the_line_and_the_offending_path(tmp_path):
    root = _repo(
        tmp_path,
        {
            "train.py": 'print("metrics.json")\n'
            'open("/work/out/checkpoints/model.pt", "wb").close()\n'
        },
    )
    (message,) = [
        f.message for f in preflight(_config(), root, PYTHON_SLIM)
        if f.code == "checkpoint-path"
    ]
    assert message.startswith("line 2:")
    assert "'/work/out/checkpoints/model.pt'" in message
    assert "collected as an ordinary artifact" in message
    assert "lost with the machine" in message


@pytest.mark.parametrize(
    "line",
    [
        'open("/work/out/ckpt/step-100.json", "w").close()',
        'open("/work/out/ckpt/step-000100.json", "w").close()',
        'open("out/ckpt/step-7.json", "w").close()',
        'from pathlib import Path; Path("/work/out/ckpt/step-1.json").write_text("{}")',
    ],
)
def test_the_convention_itself_is_never_warned_about(tmp_path, line):
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    assert "checkpoint-path" not in _codes(preflight(_config(), root, PYTHON_SLIM))


@pytest.mark.parametrize(
    "line",
    [
        'open("/work/out/metrics.json", "w").close()',
        'open("/work/out/model.pt", "wb").close()',
        'open("/work/out/nested/results.json", "w").close()',
    ],
)
def test_an_ordinary_output_is_not_mistaken_for_a_checkpoint(tmp_path, line):
    """The name is the whole trigger. An output that never claims to be a
    checkpoint is just an output, and saying otherwise would put this
    warning on every job in the system."""
    root = _repo(tmp_path, {"train.py": f"{line}\nprint('metrics.json')\n"})
    assert "checkpoint-path" not in _codes(preflight(_config(), root, PYTHON_SLIM))


def test_a_checkpoint_written_outside_out_is_reported_once_as_the_worse_thing(
    tmp_path,
):
    """`/tmp/ckpt/step-1.json` is not collected at all, which subsumes not
    being relayed. One finding, and it is the one that matters."""
    root = _repo(
        tmp_path,
        {
            "train.py": 'open("/tmp/ckpt/step-1.json", "w").close()\n'
            'print("metrics.json")\n'
        },
    )
    codes = _codes(preflight(_config(), root, PYTHON_SLIM))
    assert "writes-outside-out" in codes
    assert "checkpoint-path" not in codes


def test_a_computed_checkpoint_path_is_not_guessed_at(tmp_path):
    """Same doctrine as `writes-outside-out`: no literal, no finding. The
    `no-checkpoint` text scan is what covers the computed case."""
    root = _repo(
        tmp_path,
        {
            "train.py": """
                from pathlib import Path
                ckpt_dir = Path("/work/out") / "ckpt"
                open(ckpt_dir / "step-1.json", "w").close()
                print("metrics.json")
            """,
        },
    )
    assert "checkpoint-path" not in _codes(preflight(_config(), root, PYTHON_SLIM))


# ---------------------------------------------------------------------------
# unparseable
# ---------------------------------------------------------------------------


def test_a_syntax_error_is_a_finding_not_a_crash(tmp_path):
    root = _repo(tmp_path, {"train.py": "def broken(:\n    pass\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _codes(findings) == {"unparseable"}
    assert findings[0].level == "error"


def test_unparseable_short_circuits_the_other_checks(tmp_path):
    root = _repo(tmp_path, {"train.py": "import torch\nthis is not python(\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert len(findings) == 1
    assert findings[0].code == "unparseable"


def test_a_null_byte_in_the_source_is_a_finding_not_a_crash(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "train.py").write_bytes(b"x = 1\n\x00\n")
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _codes(findings) == {"unparseable"}


def test_a_valid_python_file_is_never_unparseable(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    assert "unparseable" not in _codes(preflight(_config(), root, PYTHON_SLIM))


# ---------------------------------------------------------------------------
# adversarial: preflight must not execute, hang, blow up, or reflect
# ---------------------------------------------------------------------------


def test_preflight_never_executes_the_entrypoint(tmp_path):
    """The fixture would write a marker file if it were ever run. Import
    side effects, module-level statements, `if __name__` — none of it can
    fire, because nothing here ever imports or execs the file."""
    marker = tmp_path / "EXECUTED"
    root = _repo(
        tmp_path,
        {
            "train.py": f"""
                from pathlib import Path
                Path({str(marker)!r}).write_text("preflight executed user code")
                print("metrics.json")
            """,
        },
    )
    preflight(_config(), root, PYTHON_SLIM)
    assert not marker.exists(), "preflight executed the user's entrypoint"


def test_a_sitecustomize_or_pth_file_in_the_repo_is_never_loaded(tmp_path):
    """A `.pth` file and a `sitecustomize.py` are the classic ways repo
    content gets executed by anything that puts the repo on sys.path.
    Preflight never touches sys.path, so they are inert bytes."""
    marker = tmp_path / "PTH_EXECUTED"
    root = _repo(
        tmp_path,
        {
            "train.py": 'print("metrics.json")\n',
            "sitecustomize.py": f"from pathlib import Path; Path({str(marker)!r}).write_text('x')\n",
            "evil.pth": f"import os; os.system('touch {marker}')\n",
            "__init__.py": f"from pathlib import Path; Path({str(marker)!r}).write_text('x')\n",
        },
    )
    assert _except_checkpoint(preflight(_config(), root, PYTHON_SLIM)) == []
    assert not marker.exists()


def test_an_entrypoint_escaping_the_repo_is_reported_missing_not_read(tmp_path):
    secret = tmp_path / "secret.py"
    secret.write_text("import torch  # outside the repo\n")
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})

    findings = preflight(_config("../secret.py"), root, PYTHON_SLIM)
    assert _codes(findings) == {"entrypoint-missing"}
    assert "torch" not in findings[0].message


def test_an_absolute_entrypoint_is_reported_missing(tmp_path):
    root = _repo(tmp_path, {"train.py": CLEAN_SOURCE})
    findings = preflight(_config("/etc/passwd"), root, PYTHON_SLIM)
    assert _codes(findings) == {"entrypoint-missing"}


def test_a_symlinked_entrypoint_pointing_outside_the_repo_is_refused(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("import torch\n")
    root = _repo(tmp_path, {"placeholder.py": "x = 1\n"})
    (root / "train.py").symlink_to(outside)

    findings = preflight(_config("train.py"), root, PYTHON_SLIM)
    assert _codes(findings) == {"entrypoint-missing"}


def test_findings_never_contain_a_newline_from_repo_content(tmp_path):
    """A finding's message is returned in a response and written to a
    JSON-per-line log. Repo-controlled text in it must not be able to carry
    a line break."""
    root = _repo(
        tmp_path,
        {
            "train.py": 'open("/tmp/a\\nb\\rc", "w").close()\nprint("metrics.json")\n',
        },
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert findings
    for finding in findings:
        assert "\n" not in finding.message
        assert "\r" not in finding.message


def test_an_enormous_literal_path_is_truncated_in_the_message(tmp_path):
    root = _repo(
        tmp_path,
        {"train.py": 'open("/tmp/' + "A" * 50_000 + '", "w").close()\n'},
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert findings
    # What has to be bounded is the part the *repo* controls. A finding's
    # own fixed text is as long as the contract it has to state — both
    # `federated-contract` and `no-checkpoint` quote a full protocol on
    # purpose — so a flat cap on message length would be measuring the
    # wrong thing and would shorten the wrong text when it failed.
    for finding in findings:
        assert "A" * 200 not in finding.message
        assert len(finding.message) < 2000


def test_a_pathologically_nested_source_file_does_not_hang_or_crash(tmp_path):
    """Deep nesting is where a naive recursive walk blows the stack. The
    parser's own guard fires first, and the walk is iterative regardless —
    either way this returns a finding rather than a RecursionError."""
    root = _repo(tmp_path, {"train.py": "x = " + "(" * 5000 + "1" + ")" * 5000 + "\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert findings  # a finding, not an exception
    assert all(isinstance(f, Finding) for f in findings)


def test_a_very_long_but_valid_file_is_analyzed_without_incident(tmp_path):
    body = "\n".join(f"x{i} = {i}" for i in range(20_000))
    root = _repo(tmp_path, {"train.py": "import torch\n" + body + "\n"})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert "unknown-import" in _codes(findings)


def test_an_oversized_entrypoint_is_refused_rather_than_parsed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "train.py").write_bytes(b"# padding\n" * (500_000))  # ~5 MB
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert _codes(findings) == {"unparseable"}
    assert "too large" in findings[0].message


def test_findings_are_capped(tmp_path):
    source = "\n".join(f'open("/tmp/f{i}.txt", "w").close()' for i in range(500))
    root = _repo(tmp_path, {"train.py": source})
    findings = preflight(_config(), root, PYTHON_SLIM)
    assert len(findings) <= 50


def test_errors_are_ordered_before_warnings(tmp_path):
    root = _repo(
        tmp_path,
        {"train.py": 'import torch\nopen("/tmp/x", "w").close()\n'},
    )
    findings = preflight(_config(), root, PYTHON_SLIM)
    levels = [f.level for f in findings]
    assert "error" in levels and "warning" in levels
    assert levels == sorted(levels, key=lambda level: 0 if level == "error" else 1)
