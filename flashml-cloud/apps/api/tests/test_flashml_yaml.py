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


def test_version_other_than_1_is_refused():
    text = MINIMAL.replace("version: 1", "version: 2")
    with pytest.raises(ConfigError, match="version"):
        parse_flashml_yaml(text)


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
    text = (MINIMAL + "mode: federated\nrounds: 2\nmin_participants: 2\n"
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
    """A federated round's shards are the round's participants, decided by
    `shards:`. A partition would be a second, conflicting fan-out."""
    text = (MINIMAL + "\nmode: federated\nrounds: 2\nmin_participants: 2\n"
            "shards: 2\npartition:\n  range: [0, 10]\n  shards: 2\n")
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
