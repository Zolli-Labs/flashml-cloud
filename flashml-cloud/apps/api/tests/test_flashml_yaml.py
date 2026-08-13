"""flashml.yaml parsing and validation.

No I/O here — parse_flashml_yaml takes text, not a path, so preflight and
the endpoint can feed it bytes pulled straight out of an extracted repo
tarball without this module ever touching a filesystem itself.

Every rule below is deliberate refusal, not lenient best-effort parsing:
a config that fails loudly at submit time is worth far more than one that
silently means something the user didn't intend.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.flashml_yaml import ConfigError, FlashmlConfig, parse_flashml_yaml

MINIMAL = """
version: 1
name: cifar-sweep
image: pytorch-cpu
entrypoint: train.py
"""


def test_minimal_valid_config_parses():
    config = parse_flashml_yaml(MINIMAL)
    assert isinstance(config, FlashmlConfig)
    assert config.version == 1
    assert config.name == "cifar-sweep"
    assert config.image == "pytorch-cpu"
    assert config.entrypoint == "train.py"
    assert config.args == []
    assert config.sweep == {}
    assert config.resources == {}
    assert config.timeout_seconds is None or isinstance(config.timeout_seconds, int)


def test_full_config_parses():
    text = """
version: 1
name: cifar-sweep
image: pytorch-cpu
entrypoint: train.py
args: ["--epochs", "20"]
sweep:
  lr: [0.001, 0.01, 0.1]
  batch_size: [32, 64]
resources: {cpus: 2, memory_gb: 4}
timeout_seconds: 1800
"""
    config = parse_flashml_yaml(text)
    assert config.args == ["--epochs", "20"]
    assert config.sweep == {"lr": [0.001, 0.01, 0.1], "batch_size": [32, 64]}
    assert config.resources == {"cpus": 2, "memory_gb": 4}
    assert config.timeout_seconds == 1800


def test_an_unsupported_version_is_refused():
    """Both 1 and 2 are read — 2 exists because ``mode: federated`` changed
    its authoring surface, and 1 is every sweep already in the wild."""
    for version in (0, 3, "one"):
        with pytest.raises(ConfigError, match="version"):
            parse_flashml_yaml(MINIMAL.replace("version: 1", f"version: {version}"))


def test_missing_entrypoint_is_refused():
    text = """
version: 1
name: x
image: pytorch-cpu
"""
    with pytest.raises(ConfigError, match="entrypoint"):
        parse_flashml_yaml(text)


def test_missing_image_is_refused():
    text = """
version: 1
name: x
entrypoint: train.py
"""
    with pytest.raises(ConfigError, match="image"):
        parse_flashml_yaml(text)


def test_args_must_be_a_list_not_a_shell_string():
    # A string would invite shell-injection thinking where none applies
    # (there is no shell) — and would be silently wrong: the executor
    # expects an argv list, not something to be split later.
    text = MINIMAL + "\nargs: \"--epochs 20\"\n"
    with pytest.raises(ConfigError, match="args"):
        parse_flashml_yaml(text)


def test_args_must_be_a_list_of_strings():
    text = MINIMAL + "\nargs: [1, 2]\n"
    with pytest.raises(ConfigError, match="args"):
        parse_flashml_yaml(text)


def test_sweep_values_must_be_non_empty_lists():
    text = MINIMAL + "\nsweep:\n  lr: []\n"
    with pytest.raises(ConfigError, match="sweep"):
        parse_flashml_yaml(text)


def test_sweep_value_must_be_a_list_not_scalar():
    text = MINIMAL + "\nsweep:\n  lr: 0.01\n"
    with pytest.raises(ConfigError, match="sweep"):
        parse_flashml_yaml(text)


def test_sweep_cartesian_product_is_capped_and_message_names_the_size():
    # 5 * 5 * 5 * 5 = 625, over the 100 cap, and far more likely a typo
    # than an intention (num_shards is bounded at 999 downstream).
    text = MINIMAL + """
sweep:
  a: [1, 2, 3, 4, 5]
  b: [1, 2, 3, 4, 5]
  c: [1, 2, 3, 4, 5]
  d: [1, 2, 3, 4, 5]
"""
    with pytest.raises(ConfigError, match="625"):
        parse_flashml_yaml(text)


def test_sweep_cartesian_product_at_the_cap_is_allowed():
    text = MINIMAL + """
sweep:
  a: [1, 2, 3, 4, 5]
  b: [1, 2, 3, 4, 5]
  c: [1, 2, 3, 4]
"""
    # 5 * 5 * 4 = 100, exactly at the cap.
    config = parse_flashml_yaml(text)
    assert config.sweep["a"] == [1, 2, 3, 4, 5]


def test_timeout_seconds_is_bounded():
    text = MINIMAL + "\ntimeout_seconds: 100000000\n"
    with pytest.raises(ConfigError, match="timeout_seconds"):
        parse_flashml_yaml(text)


def test_timeout_seconds_must_be_positive():
    text = MINIMAL + "\ntimeout_seconds: 0\n"
    with pytest.raises(ConfigError, match="timeout_seconds"):
        parse_flashml_yaml(text)


def test_unknown_top_level_key_is_refused():
    # A typo'd `entrypint` must fail loudly, not be silently ignored while
    # `entrypoint` falls back to some default meaning.
    text = MINIMAL + "\nentrypint: oops.py\n"
    with pytest.raises(ConfigError, match="entrypint"):
        parse_flashml_yaml(text)


# ---------------------------------------------------------------------------
# `checkpoint:` — refused with an explanation, never as a typo
#
# Checkpointing is unconditional: the compiler emits the workload parameter
# on every job (`compile.CHECKPOINT_PARAM`). Someone who reads that FlashML
# survives a machine dying will still reasonably try to switch it on, and
# "unknown key(s) ['checkpoint']" sends them looking for a spelling mistake
# in a feature that simply is not theirs to configure. So it joins the three
# removed federated keys in `REFUSED_KEYS`, checked BEFORE the generic
# unknown-key check — and it is NOT added to `ALLOWED_KEYS`, because
# accepting the key and ignoring its value is the surface this parser exists
# to refuse.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "{}", '{dir: out/ckpt}', "every: 10"])
def test_checkpoint_is_refused_whatever_it_is_set_to(value):
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(MINIMAL + f"\ncheckpoint: {value}\n")
    assert "checkpoint" in str(excinfo.value)


def test_checkpoint_is_refused_with_the_convention_not_as_a_typo():
    """The whole point of the entry: the message has to teach where to write
    resumable state and where to read it back, so the reader stops looking
    for a typo and goes and writes the two file paths instead."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(MINIMAL + "\ncheckpoint: true\n")
    message = str(excinfo.value)
    assert "unknown key" not in message
    assert "always on" in message
    assert "out/ckpt/step-<N>.json" in message
    assert "/work/inputs/resume.json" in message


def test_checkpoint_is_not_an_accepted_key():
    """It is refused, not honoured — `ALLOWED_KEYS` must not learn it, or
    the parser would accept a value nothing reads."""
    from flashml_cloud_api.flashml_yaml import ALLOWED_KEYS

    assert "checkpoint" not in ALLOWED_KEYS


def test_the_removed_federated_keys_are_still_refused_with_their_reasons():
    """`REFUSED_KEYS` extends the existing mechanism rather than replacing
    it: the federated migration messages must survive the extension."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(MINIMAL + "\nshards: 4\n")
    message = str(excinfo.value)
    assert "shards" in message
    assert "epochs" in message
    assert "unknown key" not in message


def test_a_typo_is_still_an_unknown_key():
    """The explained refusals must not swallow the generic check — an
    ordinary misspelling still gets the unknown-key message."""
    with pytest.raises(ConfigError) as excinfo:
        parse_flashml_yaml(MINIMAL + "\ncheckpointing: true\n")
    message = str(excinfo.value)
    assert "unknown key" in message
    assert "checkpointing" in message


def test_a_config_that_says_nothing_about_checkpoints_still_parses():
    """The overwhelmingly common case, unchanged: the key never appears in a
    real flashml.yaml because there is nothing to say."""
    assert parse_flashml_yaml(MINIMAL).name == "cifar-sweep"


def test_not_a_mapping_is_refused():
    with pytest.raises(ConfigError):
        parse_flashml_yaml("- just\n- a\n- list\n")


def test_empty_text_is_refused():
    with pytest.raises(ConfigError):
        parse_flashml_yaml("")


def test_invalid_yaml_is_refused_as_config_error_not_a_yaml_exception():
    with pytest.raises(ConfigError):
        parse_flashml_yaml("version: 1\n  bad indent: [unterminated\n")


def test_name_must_be_a_string():
    text = """
version: 1
name: 123
image: pytorch-cpu
entrypoint: train.py
"""
    with pytest.raises(ConfigError, match="name"):
        parse_flashml_yaml(text)


def test_missing_name_is_refused():
    text = """
version: 1
image: pytorch-cpu
entrypoint: train.py
"""
    with pytest.raises(ConfigError, match="name"):
        parse_flashml_yaml(text)


# ---------------------------------------------------------------------------
# local_inputs — data the host lends, that is never uploaded
# ---------------------------------------------------------------------------
#
# The label alphabet duplicated below is flashnode's
# `flashnode/config/local_data.py::LABEL_RE`. It is asserted here as well as
# there on purpose: flashnode is the last line of defence (it is the process
# holding the directory), but by the time it refuses a label the job has been
# submitted, compiled, leased and claimed, and the author sees a node-side
# failure instead of the config error it is. Submit time is the earliest point
# a job author can be told, so both ends check, and the alphabets must agree.


def test_local_inputs_parses_to_a_list_of_names():
    config = parse_flashml_yaml(MINIMAL + '\nlocal_inputs: ["patients"]\n')
    assert config.local_inputs == ["patients"]


def test_local_inputs_absent_is_an_empty_list():
    # Absent means "this job wants nothing from the host", which is every
    # job written before this feature existed.
    assert parse_flashml_yaml(MINIMAL).local_inputs == []


def test_local_inputs_empty_list_is_accepted():
    assert parse_flashml_yaml(MINIMAL + "\nlocal_inputs: []\n").local_inputs == []


@pytest.mark.parametrize(
    "value",
    [
        '"patients"',          # a bare string — the argv-style type confusion
        "{a: b}",              # a mapping (label=path, which a job may NEVER write)
        "3",
        "true",
        "null",
    ],
)
def test_local_inputs_must_be_a_list(value):
    with pytest.raises(ConfigError, match="local_inputs"):
        parse_flashml_yaml(MINIMAL + f"\nlocal_inputs: {value}\n")


@pytest.mark.parametrize("value", ['["patients", 3]', "[[nested]]", "[null]"])
def test_local_inputs_must_contain_only_strings(value):
    with pytest.raises(ConfigError, match="local_inputs"):
        parse_flashml_yaml(MINIMAL + f"\nlocal_inputs: {value}\n")


@pytest.mark.parametrize(
    "label",
    [
        "..",          # the traversal itself
        "/etc",        # an absolute path
        "a b",         # whitespace
        "a/b",         # a path fragment
        ".",
        ".hidden",     # legal on a filesystem, not a label: must start alnum
        "-leading",
        "",
        "pati&nts",
        "pa:tients",   # ':' is what splits a docker -v argument
    ],
)
def test_a_label_outside_the_shared_alphabet_is_refused_at_submit_time(label):
    # Refused HERE rather than on the volunteer's machine: the label is the
    # only thing a submitter controls about local data, and this is the
    # earliest place the author is standing in front of the error.
    with pytest.raises(ConfigError, match="local_inputs") as excinfo:
        parse_flashml_yaml(MINIMAL + f'\nlocal_inputs: ["{label}"]\n')
    # The message must name the offending label, not just the key — a config
    # with six labels and one typo is otherwise a guessing game.
    assert repr(label) in str(excinfo.value)


@pytest.mark.parametrize("label", ["patients", "labs-2026", "a.b_c", "X9", "0"])
def test_legal_labels_are_accepted(label):
    config = parse_flashml_yaml(MINIMAL + f'\nlocal_inputs: ["{label}"]\n')
    assert config.local_inputs == [label]


def test_local_inputs_did_not_widen_the_allowed_keys():
    # Adding one optional key must not turn the parser permissive: the
    # typo'd-key refusal above is the property most easily lost by accident.
    with pytest.raises(ConfigError, match="local_input"):
        parse_flashml_yaml(MINIMAL + '\nlocal_input: ["patients"]\n')


def test_local_inputs_works_under_federated_mode():
    # Federated learning over data that cannot be pooled is the use case this
    # exists for, so the two features have to compose.
    text = (MINIMAL.replace("version: 1", "version: 2")
            + "mode: federated\nepochs: 2\n"
            'local_inputs: ["patients"]\n')
    config = parse_flashml_yaml(text)
    assert config.is_federated and config.local_inputs == ["patients"]


# -- partition / validators / reduce -----------------------------------------
#
# The three keys that carry the §4–§6 surface. Validation here is deliberately
# STRUCTURAL only — is it the right shape — because the authoritative semantic
# rules (which reducers exist, which need a metric, how many shards a range
# may produce) live in flashruntime and must not be restated here where the
# two copies would drift. A wrong-but-well-formed declaration is answered by
# the coordinator's 422.


def test_partition_is_accepted_and_carried():
    text = MINIMAL + "\npartition:\n  range: [0, 1000]\n  shards: 8\n"
    config = parse_flashml_yaml(text)
    assert config.partition == {"range": [0, 1000], "shards": 8}


def test_partition_must_be_a_mapping():
    with pytest.raises(ConfigError, match="partition"):
        parse_flashml_yaml(MINIMAL + "\npartition: 8\n")


def test_partition_and_sweep_are_two_generators_for_one_job():
    """Same refusal `task_params` + `partition` gets upstream: silently
    preferring one would run a job the author did not describe."""
    text = MINIMAL + "\nsweep:\n  lr: [0.1]\npartition:\n  range: [0, 10]\n  shards: 2\n"
    with pytest.raises(ConfigError, match="sweep.*partition|partition.*sweep"):
        parse_flashml_yaml(text)


def test_partition_cannot_be_combined_with_federated_mode():
    """A federated round already cuts one pass over the data into chunks. A
    partition would be a second, conflicting split of the same data."""
    text = (MINIMAL.replace("version: 1", "version: 2")
            + "\nmode: federated\nepochs: 2\n"
            "partition:\n  range: [0, 10]\n  shards: 2\n")
    with pytest.raises(ConfigError, match="federated"):
        parse_flashml_yaml(text)


def test_validators_are_accepted_and_carried():
    text = MINIMAL + "\nvalidators:\n  keys: [accuracy]\n"
    assert parse_flashml_yaml(text).validators == {"keys": ["accuracy"]}


def test_validators_must_be_a_mapping():
    with pytest.raises(ConfigError, match="validators"):
        parse_flashml_yaml(MINIMAL + "\nvalidators: [keys]\n")


def test_reduce_is_accepted_and_carried():
    text = MINIMAL + "\nreduce:\n  kind: rank\n  metric: accuracy\n"
    assert parse_flashml_yaml(text).reduce == {"kind": "rank", "metric": "accuracy"}


def test_reduce_must_be_a_mapping():
    with pytest.raises(ConfigError, match="reduce"):
        parse_flashml_yaml(MINIMAL + "\nreduce: rank\n")


def test_reduce_needs_a_kind():
    """The one semantic check worth restating here: without `kind` there is
    nothing for the coordinator to look up, and 'reduce: {metric: acc}' is a
    plausible typo that would otherwise silently mean no reduction at all."""
    with pytest.raises(ConfigError, match="kind"):
        parse_flashml_yaml(MINIMAL + "\nreduce:\n  metric: accuracy\n")


def test_the_three_new_keys_default_to_empty():
    config = parse_flashml_yaml(MINIMAL)
    assert config.partition == {}
    assert config.validators == {}
    assert config.reduce == {}


def test_allow_partial_is_accepted_and_defaults_off():
    """Fail-closed, matching the runtime field it sets: a job that never
    mentions it keeps all-or-nothing semantics, where one exhausted task
    fails the whole run."""
    assert parse_flashml_yaml(MINIMAL).allow_partial is False
    assert parse_flashml_yaml(MINIMAL + "\nallow_partial: true\n").allow_partial is True


def test_allow_partial_must_be_a_boolean():
    with pytest.raises(ConfigError, match="allow_partial"):
        parse_flashml_yaml(MINIMAL + "\nallow_partial: yes-please\n")


# ---------------------------------------------------------------------------
# dependencies — extras on top of the curated image's own manifest,
# resolved by the compiler (Task 4 of the dependency-declaration plan)
# ---------------------------------------------------------------------------


def test_dependencies_defaults_to_empty():
    assert parse_flashml_yaml(MINIMAL).dependencies == []


def test_dependencies_parses_to_a_list_of_requirement_strings():
    text = MINIMAL + '\ndependencies: ["transformers==4.44.0", "datasets==2.19.0"]\n'
    config = parse_flashml_yaml(text)
    assert config.dependencies == ["transformers==4.44.0", "datasets==2.19.0"]


def test_dependencies_must_be_a_list_not_a_string():
    # 'dependencies: torch==2.3.1' is a plausible typo that would otherwise
    # silently parse as a list of characters.
    with pytest.raises(ConfigError, match="dependencies"):
        parse_flashml_yaml(MINIMAL + "\ndependencies: torch==2.3.1\n")


def test_dependencies_must_be_a_list_of_strings():
    with pytest.raises(ConfigError, match="dependencies"):
        parse_flashml_yaml(MINIMAL + "\ndependencies: [1, 2]\n")


# ---------------------------------------------------------------------------
# python — the interpreter those pins were resolved against
#
# The same class of key as `dependencies` above and the same doctrine: the
# image manifest is the base, the yaml refines it. Absent is the default and
# the common case (a curated image implies its own interpreter — see
# `compile.CURATED_IMAGE_PYTHON`); writing it is how a custom image becomes
# provisionable at all, and how a submitter overrules the default.
#
# Live evidence for why the key exists at all: on 2026-08-13 the Alibaba FC
# sandbox ran Python 3.13.13 against `numpy==1.26.4`, whose wheels stop at
# cp312. Every env build there fell into a source compile and one node burned
# a healthy task to TASK_EXHAUSTED.
# ---------------------------------------------------------------------------


def test_python_defaults_to_absent():
    assert parse_flashml_yaml(MINIMAL).python is None


def test_python_parses_to_a_major_minor_string():
    config = parse_flashml_yaml(MINIMAL + '\npython: "3.11"\n')
    assert config.python == "3.11"


def test_an_unquoted_python_is_refused_because_yaml_reads_it_as_a_number():
    """The silent one, and the reason a non-string is refused rather than
    coerced: YAML reads a bare `3.10` as the float 3.1, so coercing would
    hand a job that asked for 3.10 an interpreter that has been end-of-life
    since 2020."""
    with pytest.raises(ConfigError, match="python"):
        parse_flashml_yaml(MINIMAL + "\npython: 3.10\n")


@pytest.mark.parametrize("bad", ['"3"', '"3.11.4"', '"pypy3.11"', '"python3.11"',
                                 '"4.0"', "311", '"3.x"', '""'])
def test_python_refuses_anything_that_is_not_cpython_major_minor(bad):
    """Major.minor is the granularity a wheel tag has (`cp312`) and the
    granularity `uv python install` resolves. A patch level promises a
    precision nothing distinguishes; another implementation names something
    these pins were never resolved against."""
    with pytest.raises(ConfigError, match="python"):
        parse_flashml_yaml(MINIMAL + f"\npython: {bad}\n")


# ---------------------------------------------------------------------------
# datasets — a public origin the HOST fetches, never us
# ---------------------------------------------------------------------------


def test_datasets_defaults_to_empty():
    assert parse_flashml_yaml(MINIMAL).datasets == []


def test_a_minimal_dataset_needs_only_a_name_and_source():
    text = MINIMAL + (
        "\ndatasets:\n"
        "  - name: imdb\n"
        "    source: hf://stanfordnlp/imdb\n"
    )
    config = parse_flashml_yaml(text)
    assert config.datasets == [
        {"name": "imdb", "source": "hf://stanfordnlp/imdb",
         "select": None, "split": None}
    ]


def test_split_is_not_defaulted_here():
    """Inference needs `mode`, which is validated separately — a default
    baked in at parse time would silently win over the inference."""
    text = MINIMAL + "\ndatasets:\n  - name: d\n    source: hf://a/b\n"
    assert parse_flashml_yaml(text).datasets[0]["split"] is None


def test_an_explicit_split_is_kept():
    text = MINIMAL + (
        "\ndatasets:\n  - name: d\n    source: hf://a/b\n    split: replica\n"
    )
    assert parse_flashml_yaml(text).datasets[0]["split"] == "replica"


def test_an_unknown_split_is_refused():
    text = MINIMAL + (
        "\ndatasets:\n  - name: d\n    source: hf://a/b\n    split: sideways\n"
    )
    with pytest.raises(ConfigError, match="split"):
        parse_flashml_yaml(text)


def test_datasets_must_be_a_list_of_mappings():
    with pytest.raises(ConfigError, match="datasets"):
        parse_flashml_yaml(MINIMAL + "\ndatasets: hf://a/b\n")


def test_a_dataset_name_is_a_name_not_a_path():
    """It becomes a directory on a volunteer's disk — same rule as
    `local_inputs`, and for the same reason."""
    for bad in ("../evil", "/abs", "a/b", "."):
        text = MINIMAL + f'\ndatasets:\n  - name: "{bad}"\n    source: hf://a/b\n'
        with pytest.raises(ConfigError, match="name"):
            parse_flashml_yaml(text)


def test_an_unsupported_scheme_is_refused_with_the_supported_list():
    text = MINIMAL + "\ndatasets:\n  - name: d\n    source: ftp://a/b\n"
    with pytest.raises(ConfigError, match="hf://"):
        parse_flashml_yaml(text)


def test_a_dataset_needs_a_source():
    with pytest.raises(ConfigError, match="source"):
        parse_flashml_yaml(MINIMAL + "\ndatasets:\n  - name: d\n")


def test_two_datasets_may_not_share_a_name():
    """They would mount to the same /work/data/<name>/ and silently
    overwrite each other."""
    text = MINIMAL + (
        "\ndatasets:\n"
        "  - name: d\n    source: hf://a/b\n"
        "  - name: d\n    source: hf://c/e\n"
    )
    with pytest.raises(ConfigError, match="name"):
        parse_flashml_yaml(text)


def test_datasets_did_not_widen_the_allowed_keys():
    with pytest.raises(ConfigError, match="dataset"):
        parse_flashml_yaml(MINIMAL + "\ndataset:\n  - name: d\n")


def test_at_most_four_datasets_may_be_declared():
    """Each source is resolved serially against its origin inside the submit
    request, so the count bounds how long a live HTTP handler can block."""
    entries = "".join(
        f"  - name: d{i}\n    source: hf://a/b{i}\n" for i in range(5)
    )
    with pytest.raises(ConfigError, match="at most"):
        parse_flashml_yaml(MINIMAL + "\ndatasets:\n" + entries)


def test_four_datasets_are_fine():
    entries = "".join(
        f"  - name: d{i}\n    source: hf://a/b{i}\n" for i in range(4)
    )
    assert len(parse_flashml_yaml(MINIMAL + "\ndatasets:\n" + entries).datasets) == 4


def test_partition_and_datasets_cannot_be_combined():
    """Refused at parse, not left to blow up in the recipe.

    A partition job carries no `task_params` — the coordinator expands the
    range — so the compiler cuts ONE slice list while N tasks expand, and the
    mismatch surfaces as a ValueError inside CommandRecipe.expand, after the
    route already answered 201.
    """
    text = (
        MINIMAL
        + "\npartition:\n  range: [0, 100]\n  shards: 4\n"
        + "datasets:\n  - name: d\n    source: hf://a/b\n"
    )
    with pytest.raises(ConfigError, match="partition"):
        parse_flashml_yaml(text)


def test_a_sweep_with_datasets_is_still_allowed():
    """The legitimate combination: every task gets the whole dataset."""
    text = (
        MINIMAL
        + "\nsweep:\n  lr: [0.1, 0.2]\n"
        + "datasets:\n  - name: d\n    source: hf://a/b\n"
    )
    config = parse_flashml_yaml(text)
    assert config.sweep and config.datasets


def test_partition_without_datasets_is_untouched():
    text = MINIMAL + "\npartition:\n  range: [0, 100]\n  shards: 4\n"
    assert parse_flashml_yaml(text).partition["shards"] == 4
