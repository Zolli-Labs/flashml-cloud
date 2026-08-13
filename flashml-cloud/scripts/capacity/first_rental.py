#!/usr/bin/env python3
"""The first real GPU rental, driven by hand, with teardown as the feature.

    PY=flashml-cloud/apps/api/.venv/bin/python
    S=flashml-cloud/scripts/capacity/first_rental.py

    # Stage 0 only. Reads the configuration and the database. Spends nothing,
    # touches no venue.
    $PY $S --owner you@example.com --pool <pool-uuid> --preflight-only

    # Stage 0 + Stage 1. Prints the exact RunInstances parameters and issues
    # them with DryRun=true. Creates nothing. THIS IS THE DEFAULT.
    $PY $S --owner you@example.com --pool <pool-uuid>

    # Stage 0 + 1 + 2 + 3. SPENDS REAL MONEY.
    $PY $S --owner you@example.com --pool <pool-uuid> --rent-for-real

`capacity/ecs.py` was written, reviewed and merged without a single instance
ever being created — deliberately, and under instruction. This script is what
turns it into evidence, and it is written on the assumption that the expensive
failure is not "the rental did not work" but **"the rental worked and nothing
destroyed it"**. A leaked `ecs.gn6i-c4g1.xlarge` bills $1.279/hr against a $10
total ceiling: about eight hours to spend the whole budget, one overnight to
spend it twice.

So: teardown is the primary feature and the rental is secondary.

WHAT IS IN HERE THAT IS NOT A CONVENIENCE
-----------------------------------------
* **It refuses before it spends.** Stage 0 asks nine questions of the
  configuration and the database and answers every one before a venue is
  contacted. Each refusal names the variable, the id or the row that is wrong.
* **The default is free.** Stage 2 needs `--rent-for-real`. An accidental run,
  a shell-history recall, a copy-pasted command without the flag: all cost
  nothing.
* **The instance id is printed the moment anything knows it, and by a watcher
  rather than by the acquisition.** `EcsGpuProvider.acquire` holds the handle
  inside the call until it returns — that is the documented orphan window, and
  the module says so — so a `Ctrl-C` fifteen minutes into a boot can leave a
  machine nothing has ever named. The watcher here polls
  `public.rented_capacity` for the row `acquire_for_job` opened *before* the
  venue was asked, derives the node id from it (`rented-<rid[:12]}`, which is
  `acquire.py`'s own construction) and asks ECS for anything tagged with it.
  From that moment the terminal carries the instance id whatever happens next.
* **Teardown runs in `finally`, and on SIGINT/SIGTERM, and again at exit.**
  `release_capacity` is what does it — not a raw `DeleteInstance`, because the
  row and the credential are two thirds of teardown and only the sweep's own
  function does all three. A tag-discovered instance the row never learned
  about is destroyed directly afterwards, because `release_capacity` cannot
  address a machine the row cannot name (its NO_HANDLE branch, correctly,
  refuses to guess).
* **The last word belongs to the venue, not to us.** The run ends with a
  `DescribeInstances` listing everything in the region tagged `flashml-node-id`
  and prints it. A clean run is one where that list is empty — not one where
  our rows say RELEASED.

THE CHECK THAT OUTRANKS EVERYTHING ELSE
----------------------------------------
`public.attempts` is read by every teardown guard in `capacity/reconcile.py`:
`WORK_IN_FLIGHT_SQL`, `has_ever_claimed`, `claimed_recently`. If a rented host
enrols somewhere that is not this API — the D9 failure, one line applied
identically to every rental — the machine works perfectly and writes **no
attempt row**, and then every guard reads "nothing in flight, nothing ever
claimed" and an armed sweep destroys the fleet mid-task.

So the loudest thing this script prints is whether a row exists in
`public.attempts` naming the machine it rented, and it reports three outcomes
rather than two: LEDGER SEES IT, LEDGER IS BLIND, or NOT ANSWERED (no work was
ever offered to the machine, so the question did not get asked). The third is
not a pass. It is the answer you get if you rent a machine and never submit a
job into its pool while it is up.

WHAT THIS SCRIPT DELIBERATELY DOES NOT HAVE
--------------------------------------------
* **No `--keep` flag.** There is no supported way to finish a run with the
  instance still alive. If you want to poke at a booted host, raise `--hold-s`
  — the money is bounded by `--max-spend-usd` either way.
* **No arming of the sweep.** `RENTED_CAPACITY_DESTROY` must be *false* for
  this run and Stage 0 refuses if it is not. The background sweep stays
  disarmed; this script is the teardown, deliberately and visibly.
* **No acquisition of its own.** Everything from the budget gate to the
  credential mint to the failure paths is `capacity/acquire.acquire_for_job`,
  which was built and reviewed for exactly this. A second acquisition path in
  an operator script is a second set of failure paths nobody reviewed.

WHAT A HUMAN MUST HAVE SET UP FIRST
------------------------------------
1. A RAM user with `ecs:RunInstances`, `ecs:DeleteInstance`,
   `ecs:DescribeInstances`, `ecs:DescribePrice`, one region, and a GPU quota
   above zero for the instance type.
2. `ECS_ACCESS_KEY_ID`, `ECS_ACCESS_KEY_SECRET`, `ECS_IMAGE_ID`,
   `ECS_INSTANCE_TYPE`, `ECS_SECURITY_GROUP_ID`, `ECS_VSWITCH_ID` in the
   environment — all six or none.
3. `FLASHML_PUBLIC_API_URL` pointing at an API **the rented host can reach from
   the public internet**, serving the same `DATABASE_URL` this script writes
   to. On a laptop that means a tunnel:

       cloudflared tunnel --url http://127.0.0.1:8000
       export FLASHML_PUBLIC_API_URL=https://<whatever>.trycloudflare.com

   Stage 0 refuses `localhost`, a private address and an unreachable host,
   because a machine that cannot phone home never appears in `public.attempts`
   and every guard downstream reads it as idle.
4. `DATABASE_URL`, and `COORDINATOR_URL` + `COORDINATOR_OPERATOR_TOKEN` (or
   `FLASHML_REQUIRE_AUTH=false`) so `Settings.from_env()` will load. Nothing
   here talks to the coordinator.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: The measured `DescribePrice` figure for `ecs.gn6i-c4g1.xlarge` (1x Tesla T4,
#: 16 GB) pay-as-you-go in `ap-southeast-1`, 2026-08-12. It is the DEFAULT
#: QUOTE, not a fact about the instance you are about to rent: change the
#: instance type and this number is wrong, which is why `--usd-per-hour`
#: exists. The budget gate refuses an unpriced acquisition outright, so
#: something has to be passed here.
MEASURED_T4_USD_PER_HOUR = 1.279

#: How long to keep the machine after it has enrolled, so there is time for a
#: task to be claimed and for the ledger check to mean something. Ten minutes
#: of a T4 is about 21 cents.
DEFAULT_HOLD_S = 600.0

#: The most this ONE run may commit, computed as rate x (registration window +
#: hold + slack). Not a cap on the account and not a cap on the invoice — a
#: refusal to *start* a run whose own arithmetic exceeds it. The standing $10
#: total ceiling is an agreement between people, watched on Alibaba's billing
#: page; this is the part a script can enforce.
DEFAULT_MAX_SPEND_USD = 2.0

#: Slack added to the projected spend: the destroy is not instantaneous and
#: ECS bills a started instance from creation.
SPEND_SLACK_S = 180.0

#: How often the watcher asks the database, and the venue, what this
#: acquisition has created so far.
WATCH_INTERVAL_S = 8.0

#: How often the hold phase reports.
HOLD_POLL_S = 15.0

#: The tag `run_params` puts on every instance. Read here, never written here.
NODE_TAG_KEY = "flashml-node-id"

#: What ECS answers to a `DryRun=true` request that WOULD have succeeded.
#: Alibaba's own products are not consistent about which of these they use, so
#: all three are accepted and anything else is reported verbatim rather than
#: guessed at. A `DryRun` that comes back as a plain success payload (no
#: `Code`) is also treated as a pass.
DRY_RUN_OK_CODES = frozenset({
    "DryRunOperation", "DRYRUN.SUCCESS", "DryRunSuccess", "DRYRUN_SUCCESS",
})

#: Host names that are never reachable from a rented machine in Singapore.
LOCAL_HOST_NAMES = frozenset({
    "", "localhost", "localhost.localdomain", "ip6-localhost", "0.0.0.0", "::",
})

#: Suffixes that mean "this name only resolves on your own network".
LOCAL_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home")

EXIT_OK = 0
EXIT_REFUSED = 1
#: The one that matters: something may still be running at the venue.
EXIT_MAYBE_LEAKED = 2
EXIT_FAILED = 3


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def say(text: str = "") -> None:
    """One place, unbuffered. The terminal is the durable record of a run that
    dies badly, so nothing here may sit in a buffer."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def banner(title: str, lines: Sequence[str] = (), *, char: str = "=") -> None:
    say("")
    say(char * 78)
    say(f" {title}")
    for line in lines:
        say(f"   {line}")
    say(char * 78)


#: A base64 run long enough to be a secret rather than an id. `UserData`
#: carries the machine token, and an ECS error body echoes parameters it did
#: not like.
_BLOB = re.compile(r"[A-Za-z0-9+/=]{200,}")


def scrub(text: object) -> str:
    """Remove anything long enough to be a seeded credential.

    The launch script — with the machine token in it — is base64 in
    `UserData`, and this script prints exception text from a venue that echoes
    the parameters it rejected. `AliyunEcsClient` already redacts the access
    key and its secret with the account's own values; it cannot redact a token
    it did not mint. So: any base64 run of 200 characters or more never
    reaches the terminal.
    """
    return _BLOB.sub("<redacted-blob>", str(text))


# --------------------------------------------------------------------------
# the ledger: what a human needs in order to destroy this by hand
# --------------------------------------------------------------------------


@dataclass
class Ledger:
    """Every id this run has learned, printed at every stage and on every path.

    This exists because a process that is killed uncleanly leaves nothing
    behind but its terminal. Whatever else fails, the two lines below must be
    on the screen: the instance id, and the machine id.
    """

    region: str = ""
    instance_type: str = ""
    job_id: str = ""
    #: `rented_capacity.id`, opened by `acquire_for_job` BEFORE the venue is
    #: asked anything. The first thing that exists.
    rented_id: str = ""
    #: `rented-<rented_id[:12]>` — `acquire.py`'s own construction, derived
    #: here rather than read, so it is known before the mint has committed.
    node_id: str = ""
    machine_id: str = ""
    #: The ECS instance id. Learned either from the row or from the venue by
    #: tag, whichever gets there first.
    instance_id: str = ""
    #: Set only on evidence — `release_capacity` returned True, or the venue
    #: says the instance is gone.
    destroyed: bool = False
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"instance={self.instance_id or '(not yet known)'} "
            f"machine={self.machine_id or '(not yet minted)'} "
            f"node={self.node_id or '(not yet known)'} "
            f"rental={self.rented_id or '(not yet opened)'} "
            f"region={self.region or '?'}"
        )

    def manual_destroy(self) -> list[str]:
        """What to type if this process is gone and the machine is not."""
        target = self.instance_id or f"<find by tag {NODE_TAG_KEY}={self.node_id}>"
        return [
            f"aliyun ecs DeleteInstance --RegionId {self.region} "
            f"--InstanceId {target} --Force true",
            f"console: https://ecs.console.aliyun.com/server/region/{self.region}",
            f"the instance is tagged {NODE_TAG_KEY}={self.node_id or '?'}",
        ]

    def stage(self, where: str) -> None:
        say(f"[ids] {where}: {self.line()}")


def _exit_banner(ledger: Ledger) -> None:
    """Last thing this process does, on every exit that is not a SIGKILL.

    Registered with `atexit`, so it survives an unhandled exception, a
    `sys.exit` from anywhere, and a teardown that itself blew up. It is not a
    substitute for the destroy; it is the note the destroy leaves when it
    could not happen.
    """
    if ledger.instance_id and not ledger.destroyed:
        banner(
            "!!! A MACHINE MAY STILL BE RUNNING AND BILLING !!!",
            [ledger.line(), ""] + ledger.manual_destroy(),
            char="!",
        )
    elif ledger.instance_id:
        say(f"[ids] final: {ledger.line()} destroyed=True")


# --------------------------------------------------------------------------
# pure helpers (the parts worth testing)
# --------------------------------------------------------------------------


def public_url_problem(url: str) -> str | None:
    """Why this URL cannot be a rented host's enrolment target, or None.

    **This check earns its place more than any other in Stage 0.** A rented
    machine that cannot reach this API boots, installs the agent, fails to
    enrol, and is destroyed an hour later by `boot_grace_s` — that is the
    cheap outcome. The expensive one is subtler: the machine never appears in
    `public.attempts`, so if it *does* somehow work, every teardown guard
    reads it as idle. `capacity/ecs.py`'s D9 section argues it at length: one
    line, applied identically to every rental, does not lose one GPU, it loses
    all of them.

    Refused: no value, a scheme that is not http(s), a loopback or private or
    link-local literal address, and the names that only resolve on your own
    machine or your own LAN. A public *name* is accepted here and proven by
    the reachability probe, which is a separate check because a name that does
    not resolve and a name that resolves to your router are different repairs.
    """
    value = (url or "").strip()
    if not value:
        return (
            "FLASHML_PUBLIC_API_URL is unset. A rented host enrols against "
            "THIS API's public URL and nothing else; unset, there is nothing "
            "to point it at. On a laptop, run a tunnel "
            "(cloudflared tunnel --url http://127.0.0.1:8000) and export its "
            "https URL."
        )
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https"):
        return (
            f"FLASHML_PUBLIC_API_URL={value!r} has scheme "
            f"{parts.scheme or '(none)'!r}; it must be http or https."
        )
    host = (parts.hostname or "").lower()
    if host in LOCAL_HOST_NAMES or host.endswith(LOCAL_HOST_SUFFIXES):
        return (
            f"FLASHML_PUBLIC_API_URL={value!r} names {host or '(nothing)'!r}, "
            "which resolves only on this machine or this LAN. A rented "
            "instance in an Alibaba region cannot reach it, will never enrol, "
            "and will never write a row to public.attempts."
        )
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None  # a name; the reachability probe is what proves it
    # `is_global` is the whole test and the named properties are only there to
    # make the message specific: documentation ranges (192.0.2/24,
    # 203.0.113/24) are none of loopback/private/link-local and are just as
    # unroutable from Singapore.
    if not address.is_global:
        return (
            f"FLASHML_PUBLIC_API_URL={value!r} is a "
            f"{'loopback' if address.is_loopback else 'private' if address.is_private else 'non-routable'}"
            " address. A rented instance cannot route to it. Use a tunnel or "
            "a deployed API."
        )
    return None


def projected_spend_usd(
    usd_per_hour: float, *, registration_timeout_s: float, hold_s: float
) -> float:
    """The most this run can commit if every window runs to its end.

    Registration window plus hold plus slack, because ECS bills a
    pay-as-you-go instance from creation and the destroy is not instant. It is
    an upper bound on ONE run, not on the account: `budget.window_spend_usd`
    is the ceiling that bounds a loop, and Stage 0 consults that too.
    """
    seconds = float(registration_timeout_s) + float(hold_s) + SPEND_SLACK_S
    return float(usd_per_hour) * seconds / 3600.0


def dry_run_passed(code: str) -> bool:
    """Does this ECS error code mean "the request was valid"?

    `DryRun=true` reports success as an *error* code, which is Alibaba's
    convention and reads as a failure to anything that does not know it. Three
    spellings are accepted because Alibaba's own products differ; anything
    else is a genuine refusal — a wrong image id, a vSwitch in another zone, a
    RAM policy that does not allow `RunInstances`, a GPU quota still at zero —
    and is printed verbatim rather than interpreted.
    """
    return str(code or "").strip() in DRY_RUN_OK_CODES


def format_run_params(params: dict[str, str]) -> str:
    """The launch, legibly, with the one secret in it withheld.

    `UserData` is base64 of the cloud-init script, and that script contains
    the machine token — `capacity/ecs.py` says in as many words that the text
    is a secret and goes into `UserData` and nowhere else. So it is summarised
    by length and digest, and everything else is printed in full, because the
    whole point of Stage 1 is that a human can read what is about to be sent.
    """
    lines = []
    for key in sorted(params):
        value = str(params[key])
        if key == "UserData":
            digest = hashlib.sha256(value.encode()).hexdigest()[:12]
            try:
                decoded = f"{len(base64.b64decode(value))} bytes decoded"
            except Exception:  # noqa: BLE001 - reported, not raised
                decoded = "NOT VALID BASE64"
            value = (
                f"<base64, {len(value)} chars, {decoded}, "
                f"sha256:{digest} — NOT PRINTED: it carries the machine token>"
            )
        lines.append(f"    {key:<26} = {value}")
    return "\n".join(lines)


def launch_self_checks(params: dict[str, str], enrolment_url: str) -> list[tuple[bool, str]]:
    """Read back the properties the adapter's docstrings claim, from the bytes
    it is actually about to send.

    Not a re-test of `test_capacity_ecs.py` — the suite asserts these against a
    fake. This asserts them against the parameters built from THIS
    deployment's settings, which is where a value from the environment can
    still make a reviewed module send something else.
    """
    try:
        script = base64.b64decode(params.get("UserData", "")).decode()
    except Exception:  # noqa: BLE001 - reported as a failed check
        return [(False, "UserData is not decodable base64")]
    return [
        (
            params.get("HttpEndpoint") == "disabled",
            "HttpEndpoint=disabled (D3.3: no metadata endpoint, so the job's "
            "own code cannot read the token we seeded through UserData)",
        ),
        (
            "RamRoleName" not in params,
            "no RamRoleName (D3.3: nothing behind the metadata endpoint even "
            "if it were reachable)",
        ),
        (
            params.get("Amount") == "1" and params.get("MinAmount") == "1",
            "Amount=MinAmount=1 (D1: one job, one instance)",
        ),
        (
            params.get("InstanceChargeType") == "PostPaid",
            "InstanceChargeType=PostPaid (a subscription instance cannot be "
            "destroyed on the same terms)",
        ),
        (
            enrolment_url.rstrip("/") in script,
            f"the launch points the host at {enrolment_url} (D9: this API's "
            "public URL, never the coordinator's)",
        ),
        (
            "FLASHNODE_SANDBOX_CAPABLE" not in script,
            "FLASHNODE_SANDBOX_CAPABLE appears nowhere in the launch (an "
            "unsandboxed rented box must never claim it)",
        ),
        (
            "--runner trusted" in script,
            "--runner trusted (D2: a rented host has no Docker daemon)",
        ),
        (
            int(params.get("InternetMaxBandwidthOut", "0") or 0) > 0,
            "InternetMaxBandwidthOut > 0 (zero means no public IP, which "
            "means a host that can never enrol)",
        ),
    ]


def attempts_verdict(
    attempts: Sequence[dict], job_row: dict | None, *, expect_claim: bool
) -> tuple[str, list[str]]:
    """The loudest thing this script says. Three outcomes, not two.

    A row in `public.attempts` naming the rented machine is the whole
    question: it is what `reconcile.WORK_IN_FLIGHT_SQL`, `has_ever_claimed`
    and `claimed_recently` read, and it is written by this API when the
    machine claims a lease *through this API*. A machine talking past us
    writes nothing here while working perfectly.

    The third outcome exists because "no row" has an innocent explanation that
    must not be dressed up as a pass or as a failure: if no job ever offered
    this machine any work, the check did not run. Saying "LEDGER IS BLIND"
    there would cry wolf; saying "PASS" would be a lie. `--expect-claim` turns
    it into a failure for a run where work definitely was offered.
    """
    if attempts:
        return (
            "LEDGER SEES IT",
            [
                f"{len(attempts)} row(s) in public.attempts name this machine.",
                "Every teardown guard in capacity/reconcile.py can therefore "
                "see rented machines: WORK_IN_FLIGHT_SQL, has_ever_claimed "
                "and claimed_recently all key on this table.",
                "This is the result that makes the rented-capacity guards real.",
            ],
        )
    offered = bool(job_row) or expect_claim
    if offered:
        return (
            "LEDGER IS BLIND",
            [
                "NO row in public.attempts names this machine, and work WAS "
                "offered to its pool.",
                "The host is talking past this API (D9) or never claimed at "
                "all. Either way every teardown guard reads this machine as "
                "idle: WORK_IN_FLIGHT_SQL is false, has_ever_claimed is "
                "false, and an armed sweep would destroy a busy machine.",
                "DO NOT set RENTED_CAPACITY_DESTROY=true. Fix the enrolment "
                "URL first and re-run this script.",
            ],
        )
    return (
        "NOT ANSWERED",
        [
            "NO row in public.attempts names this machine — and no job was "
            "offered to it during this run, so the question was never asked.",
            "This is NOT a pass. Re-run with a job submitted into this pool "
            "while the machine is up (and --expect-claim), or the guards stay "
            "unproven.",
        ],
    )


def stage_plan(args: argparse.Namespace) -> tuple[str, ...]:
    """Which stages this invocation will run. One place, so the answer is
    printable before anything happens and testable without a venue.

    The default carries no `rent`. That is the whole safety property of the
    command line: renting is opt-in, spelled `--rent-for-real`, and every
    other invocation is free.
    """
    if getattr(args, "preflight_only", False):
        return ("preflight",)
    if getattr(args, "rent_for_real", False):
        return ("preflight", "dryrun", "rent", "teardown")
    return ("preflight", "dryrun")


def redacted_dsn(dsn: str) -> str:
    """Enough of DATABASE_URL to compare against the API's, and no password."""
    if "://" not in (dsn or ""):
        return "(non-URL DSN; not printed)"
    parts = urllib.parse.urlsplit(dsn)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.username or '?'}@{parts.hostname or '?'}{port}/{(parts.path or '').lstrip('/') or '?'}"


# --------------------------------------------------------------------------
# stage 0 — preflight
# --------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    #: A check that could not run (no credentials, probe skipped). Neither a
    #: pass nor a refusal, and never counted as one.
    skipped: bool = False


def guarded(name: str, body: Any) -> Check:
    """Run one check, and turn anything it raises into a refusal.

    **A preflight question that cannot be answered is a refusal, not a
    traceback.** Every check below this line reads a database that may be a
    migration behind — the first real run of this script found exactly that
    (`relation "public.rented_capacity" does not exist`, on a dev project
    where migration 0022 had not been applied) and a stack trace is a worse
    way to learn it than a line naming the check. It also means one broken
    question does not hide the answers to the others.
    """
    try:
        return body()
    except Exception as exc:  # noqa: BLE001 - reported as a refusal
        return Check(
            name, False,
            f"the check itself failed: {type(exc).__name__}: {scrub(exc)}\n"
            "Unanswerable is refused. A missing table usually means this "
            "database has not had the capacity migrations applied.",
        )


def report(checks: Iterable[Check]) -> bool:
    ok = True
    for check in checks:
        mark = "SKIP" if check.skipped else ("PASS" if check.ok else "REFUSE")
        say(f"  [{mark:>6}] {check.name}")
        if check.detail:
            for line in check.detail.splitlines():
                say(f"           {line}")
        if not check.ok and not check.skipped:
            ok = False
    return ok


def probe_public_api(url: str, *, timeout_s: float = 10.0) -> Check:
    """Can anything reach this API from here, and does it answer healthy?

    A weaker statement than "a machine in Singapore can reach it" — nothing
    runnable from a laptop proves that — and still the check that catches the
    common failure: a tunnel that has expired, a URL typed from memory, a
    deployed API that is down. The DNS half is the part that matters most for
    a laptop rehearsal, because a name that resolves to nothing is exactly
    what a dead `cloudflared` leaves behind.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as exc:
        return Check(
            "the public API URL resolves", False,
            f"{host}: {exc}. A rented host will fail the same lookup.",
        )
    addresses = sorted({info[4][0] for info in infos})
    private = [
        a for a in addresses
        if _is_private_literal(a)
    ]
    if private and len(private) == len(addresses):
        return Check(
            "the public API URL resolves to a public address", False,
            f"{host} resolves only to {', '.join(addresses)}, which is not "
            "routable from an Alibaba region.",
        )
    health = urllib.parse.urljoin(url.rstrip("/") + "/", "healthz")
    try:
        with urllib.request.urlopen(health, timeout=timeout_s) as response:
            body = response.read(2000).decode("utf-8", "replace")
            status = response.status
    except Exception as exc:  # noqa: BLE001 - every failure is the same refusal
        return Check(
            "the public API URL answers /healthz", False,
            f"GET {health} failed: {type(exc).__name__}: {scrub(exc)}\n"
            "A rented host reaches this API or it never enrols. Start the "
            "API, start the tunnel, then re-run.",
        )
    if status != 200:
        return Check(
            "the public API URL answers /healthz", False,
            f"GET {health} -> HTTP {status}: {scrub(body)[:200]}",
        )
    return Check(
        "the public API URL answers /healthz", True,
        f"GET {health} -> 200 {scrub(body)[:120]} (resolved: {', '.join(addresses)})",
    )


def _is_private_literal(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return bool(
        parsed.is_loopback or parsed.is_private or parsed.is_link_local
        or parsed.is_reserved or parsed.is_unspecified
    )


def preflight(settings: Any, db: Any, args: argparse.Namespace) -> list[Check]:
    """Everything that can be refused before a venue is contacted.

    Ordered so the cheapest and most common failures come first, and so that
    nothing that touches the network runs before the configuration has been
    read. Every check returns a `Check` rather than raising, so one run
    reports every problem instead of making the operator discover them one
    redeploy at a time.
    """
    from flashml_cloud_api import db as dbmod
    from flashml_cloud_api.capacity.budget import (
        BudgetRefused, assert_within_budget, window_spend_usd,
    )
    from flashml_cloud_api.capacity.registry import providers_for
    from flashml_cloud_api.router.venues import VENUE_ECS_GPU

    checks: list[Check] = []

    # 1. The venue, all six or none.
    required = {
        "ECS_ACCESS_KEY_ID": settings.ecs_access_key_id,
        "ECS_ACCESS_KEY_SECRET": settings.ecs_access_key_secret,
        "ECS_IMAGE_ID": settings.ecs_image_id,
        "ECS_INSTANCE_TYPE": settings.ecs_instance_type,
        "ECS_SECURITY_GROUP_ID": settings.ecs_security_group_id,
        "ECS_VSWITCH_ID": settings.ecs_vswitch_id,
    }
    missing = [name for name, value in required.items() if not value]
    checks.append(Check(
        "Alibaba ECS is fully configured",
        not missing,
        "" if not missing else (
            "missing: " + ", ".join(missing) + "\n"
            "settings.ecs_configured is all-or-nothing on purpose: a "
            "half-configured venue reads as OFF, so nothing can be rented AND "
            "nothing can be destroyed — the sweep has no adapter to ask."
        ),
    ))

    # 2. The sweep stays disarmed. This script is the teardown.
    checks.append(Check(
        "RENTED_CAPACITY_DESTROY is false",
        not bool(getattr(settings, "rented_capacity_destroy", False)),
        "" if not getattr(settings, "rented_capacity_destroy", False) else (
            "The background sweep is ARMED. For a first rental the teardown "
            "must be deliberate and visible, and an armed sweep can destroy "
            "this machine on a window you are not watching. Unset it."
        ),
    ))

    # 3. THE ONE THAT MATTERS MORE THAN IT LOOKS.
    problem = public_url_problem(getattr(settings, "public_api_url", ""))
    checks.append(Check(
        "FLASHML_PUBLIC_API_URL is a public address", problem is None,
        problem or (
            f"{settings.public_api_url} — this is the only URL the launch can "
            "carry (capacity/ecs.py has no access to a coordinator URL at all)."
        ),
    ))

    # 4. ...and something answers on it.
    if problem is not None:
        checks.append(Check(
            "the public API URL answers /healthz", True, skipped=True,
            detail="not probed: the URL was refused above.",
        ))
    elif args.skip_api_probe:
        checks.append(Check(
            "the public API URL answers /healthz", True, skipped=True,
            detail="--skip-api-probe given. Nothing has confirmed a rented "
                   "host could ever reach this API.",
        ))
    else:
        checks.append(probe_public_api(settings.public_api_url))

    # 5. The database this script writes, which must be the one the API at
    #    that URL serves. Nothing here can prove it; printing both is what
    #    lets a human see a mismatch before paying for a boot.
    checks.append(Check(
        "DATABASE_URL is set", bool(settings.database_url),
        redacted_dsn(settings.database_url) + "\n"
        "The API behind FLASHML_PUBLIC_API_URL must serve THIS database. A "
        "mismatch shows up as a rental that boots, is refused 401 on "
        "enrolment, never registers and is destroyed on timeout."
        if settings.database_url else
        "DATABASE_URL is unset; there is nowhere to open a rented_capacity row.",
    ))

    # 6/7. The owner and the pool, resolved the way `provision_rented_machine`
    #      will resolve them: it calls `lock_pool_for_owner`, which requires
    #      OWNERSHIP and MEMBERSHIP both, and answers an indistinguishable
    #      "not found" to everything else.
    owner_id = args.resolved_owner_id
    checks.append(Check(
        "the owner resolves to a profile", bool(owner_id),
        f"owner_id={owner_id}" if owner_id else
        f"{args.owner!r} matched no row in auth.users / public.profiles.",
    ))
    if owner_id and settings.database_url:
        def _pool_check() -> Check:
            with db.cursor() as cur:
                cur.execute(
                    "select id, name, owner_id from public.pools where id = %s",
                    (args.pool,),
                )
                pool = cur.fetchone()
            owns = bool(pool) and str(pool["owner_id"]) == str(owner_id)
            member = bool(pool) and dbmod.is_pool_member(
                db, str(args.pool), str(owner_id)
            )
            return Check(
                "the pool resolves, and the owner owns AND is in it",
                bool(pool) and owns and member,
                (f"pool={args.pool} name={pool['name']!r} owner={pool['owner_id']}"
                 if pool else f"no public.pools row {args.pool!r}") + "\n"
                + ("" if owns else "the owner does not OWN this pool; "
                   "lock_pool_for_owner refuses, 404-shaped, before the venue "
                   "is asked anything. ")
                + ("" if member else "the owner is not a MEMBER of this pool; "
                   "a machine minted into a pool its owner does not belong to "
                   "is stamped with nothing and can never claim the job it was "
                   "rented for (pool_ids_for_machine intersects with "
                   "pool_members)."),
            )

        checks.append(guarded(
            "the pool resolves, and the owner owns AND is in it", _pool_check
        ))
    else:
        checks.append(Check(
            "the pool resolves, and the owner owns AND is in it", True,
            skipped=True, detail="not asked: no owner or no database.",
        ))

    # 8. An adapter exists for this venue. Without one, nothing in this
    #    process — or in the sweep — can destroy what it rents.
    providers = providers_for(settings)
    checks.append(Check(
        f"a provider is registered for {VENUE_ECS_GPU}",
        VENUE_ECS_GPU in providers,
        "" if VENUE_ECS_GPU in providers else
        "capacity/registry.py built no ECS adapter. Nothing in this process "
        "can create OR destroy a rental. Check the startup log for the "
        "half-configuration warning.",
    ))

    # 9. The budget gate, run for real against the real ceilings. Not a
    #    restatement of them: `assert_within_budget` itself, so a quote this
    #    deployment would refuse is refused here rather than after the row is
    #    open and the credential minted.
    if settings.database_url:
        def _budget_check() -> Check:
            try:
                assert_within_budget(
                    db, venue_id=VENUE_ECS_GPU,
                    usd_per_hour=float(args.usd_per_hour), settings=settings,
                )
            except BudgetRefused as exc:
                return Check(
                    "the budget gate accepts this quote", False, str(exc)
                )
            committed = window_spend_usd(
                db, hours=float(settings.rented_usd_window_hours)
            )
            return Check(
                "the budget gate accepts this quote", True,
                f"quote ${args.usd_per_hour:.4f}/hr, per-acquisition ceiling "
                f"${settings.rented_usd_per_acquisition_max}/hr, "
                f"{settings.rented_usd_window_hours}h window already commits "
                f"${committed:.4f}/hr of ${settings.rented_usd_window_max}/hr.",
            )

        checks.append(guarded("the budget gate accepts this quote", _budget_check))
    else:
        checks.append(Check(
            "the budget gate accepts this quote", True, skipped=True,
            detail="not asked: no database.",
        ))

    # 10. This run's own arithmetic.
    projected = projected_spend_usd(
        float(args.usd_per_hour),
        registration_timeout_s=float(args.registration_timeout_s),
        hold_s=float(args.hold_s),
    )
    checks.append(Check(
        "this run's projected spend is under the ceiling",
        projected <= float(args.max_spend_usd),
        f"${projected:.2f} = ${args.usd_per_hour}/hr x "
        f"({args.registration_timeout_s:.0f}s registration + "
        f"{args.hold_s:.0f}s hold + {SPEND_SLACK_S:.0f}s slack), "
        f"ceiling ${args.max_spend_usd:.2f}. "
        + ("" if projected <= float(args.max_spend_usd)
           else "Lower --hold-s or --registration-timeout-s, or raise "
                "--max-spend-usd deliberately."),
    ))
    return checks


def resolve_owner(db: Any, value: str) -> str:
    """A uuid, or an email address resolved through `auth.users`.

    An email is what an operator actually has to hand, and typing the wrong
    uuid is a refusal three checks later that reads like a broken database.

    Never raises: `--owner not-a-uuid` is a Postgres `invalid input syntax`,
    and an unresolved owner is already a refusal with a better message.
    """
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return _resolve_owner(db, text)
    except Exception:  # noqa: BLE001 - "did not resolve" is the answer
        return ""


def _resolve_owner(db: Any, text: str) -> str:
    with db.cursor() as cur:
        if "@" in text:
            cur.execute(
                "select p.id from public.profiles p join auth.users u "
                "on u.id = p.id where lower(u.email) = lower(%s)",
                (text,),
            )
        else:
            cur.execute("select id from public.profiles where id = %s::uuid", (text,))
        row = cur.fetchone()
    return str(row["id"]) if row else ""


# --------------------------------------------------------------------------
# building the adapter (public constructors only)
# --------------------------------------------------------------------------


def build_client(settings: Any) -> Any:
    """A second `EcsClient` for this script's own venue reads.

    Separate from the provider's, and only ever used for questions the
    provider does not answer: "what does the venue hold right now" and "which
    instance carries this node tag". Nothing here destroys anything —
    destroying goes through `release_capacity` and the provider, which is the
    path that also moves the row and ends the lease.
    """
    from flashml_cloud_api.capacity.ecs import AliyunEcsClient

    return AliyunEcsClient(
        access_key_id=settings.ecs_access_key_id,
        access_key_secret=settings.ecs_access_key_secret,
        region=settings.ecs_region,
    )


def build_provider(settings: Any, *, registration_timeout_s: float | None = None) -> Any:
    """The real `EcsGpuProvider`, optionally with a shorter enrolment window.

    `EcsGpuProvider.from_settings` is the production path and takes no
    window, so a first rental — which is watched, and whose operator wants to
    stop paying sooner than fifteen minutes if nothing enrols — is built from
    the same three public pieces `from_settings` uses. Nothing is
    reimplemented: `EcsLaunchConfig.from_settings` and `db_registration_probe`
    are the adapter's own, exported for exactly this.
    """
    from flashml_cloud_api.capacity.ecs import (
        EcsGpuProvider, EcsLaunchConfig, db_registration_probe,
    )

    if registration_timeout_s is None:
        return EcsGpuProvider.from_settings(settings)
    return EcsGpuProvider(
        client=build_client(settings),
        config=EcsLaunchConfig.from_settings(settings),
        registered=db_registration_probe(settings),
        registration_timeout_s=float(registration_timeout_s),
    )


# --------------------------------------------------------------------------
# venue reads (the last word belongs to ECS, not to our rows)
# --------------------------------------------------------------------------


async def venue_inventory(client: Any, region: str) -> tuple[list[dict], str]:
    """Everything at the venue tagged as ours. The VENUE's answer, not a row.

    Filtered by the tag KEY alone, so it also finds instances from an earlier
    run that nothing in our database names — which is the whole failure mode
    this listing exists to expose. If the tag filter is refused (an API that
    wants a value with the key, say), it falls back to listing the region and
    says so, because "we could not ask" must never be printed as "nothing is
    there".
    """
    from flashml_cloud_api.capacity.ecs import EcsApiError

    params = {"RegionId": region, "Tag.1.Key": NODE_TAG_KEY, "PageSize": "100"}
    note = f"DescribeInstances filtered on tag {NODE_TAG_KEY}"
    try:
        payload = await client.call("DescribeInstances", params)
    except EcsApiError as exc:
        note = (
            f"the tag filter was refused ({exc.code or exc.status}); listing "
            f"the whole of {region} instead"
        )
        payload = await client.call(
            "DescribeInstances", {"RegionId": region, "PageSize": "100"}
        )
    instances = ((payload.get("Instances") or {}).get("Instance") or [])
    return [dict(i) for i in instances], note


def describe(instance: dict) -> str:
    tags = {
        str(t.get("TagKey") or t.get("Key")): str(t.get("TagValue") or t.get("Value"))
        for t in ((instance.get("Tags") or {}).get("Tag") or [])
    }
    return (
        f"{instance.get('InstanceId')} status={instance.get('Status')} "
        f"type={instance.get('InstanceType')} "
        f"created={instance.get('CreationTime')} "
        f"{NODE_TAG_KEY}={tags.get(NODE_TAG_KEY, '-')}"
    )


async def find_instance_by_node_tag(client: Any, region: str, node_id: str) -> str:
    """The instance this rental created, asked of ECS by tag.

    **This is what closes the orphan window.** `EcsGpuProvider.acquire` holds
    the instance id inside the call until it returns: on a `Ctrl-C`, a crash
    or a registration timeout that fails to destroy, the row may never learn
    it and no sweep can name it. `run_params` tags every instance with
    `flashml-node-id`, and the node id is derivable from the rental row id, so
    the tag is a second, independent route back to the machine — available
    while `acquire` is still waiting.
    """
    payload = await client.call(
        "DescribeInstances",
        {
            "RegionId": region,
            "Tag.1.Key": NODE_TAG_KEY,
            "Tag.1.Value": node_id,
            "PageSize": "10",
        },
    )
    instances = ((payload.get("Instances") or {}).get("Instance") or [])
    return str(instances[0].get("InstanceId")) if instances else ""


# --------------------------------------------------------------------------
# database reads used during a rental
# --------------------------------------------------------------------------


def rental_row(db: Any, job_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "select id, state, provider_handle, machine_id, usd_per_hour, "
            "       acquired_at, released_at, failure_code, failure_detail, "
            "       created_at"
            "  from public.rented_capacity where job_id = %s"
            " order by created_at desc limit 1",
            (job_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def machine_row(db: Any, node_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "select id, node_id, status, lifecycle, created_at, last_seen_at, "
            "       revoked_at from public.machines where node_id = %s",
            (node_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def attempts_for(db: Any, machine_id: str) -> list[dict]:
    if not machine_id:
        return []
    with db.cursor() as cur:
        cur.execute(
            "select * from public.attempts where machine_id = %s::uuid "
            "order by claimed_at",
            (machine_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def job_row(db: Any, job_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "select id, status, created_at, finished_at from public.jobs "
            "where id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# stage 1 — dry run
# --------------------------------------------------------------------------


async def stage_dry_run(settings: Any, args: argparse.Namespace, ledger: Ledger) -> bool:
    """Print the exact launch, then ask ECS to validate it without creating it.

    Nothing here can produce an instance: `DryRun=true` is the whole point,
    and the identity in the launch is a placeholder rather than a minted
    credential, so this stage also cannot leave a lease behind. A bad image
    id, a vSwitch in the wrong zone, a RAM policy short of `RunInstances` and
    a GPU quota still at zero all surface here for free.
    """
    from flashml_cloud_api.capacity.ecs import EcsApiError
    from flashml_cloud_api.capacity.provider import CapacityRequest
    from flashml_cloud_api.router.venues import VENUE_ECS_GPU

    provider = build_provider(settings)
    # A placeholder identity, and it is marked as one. Minting a real
    # credential for a request that will never be sent would leave a `leased`
    # machine bound to the pool with no rental row naming it — the exact
    # orphan `reconcile.orphaned_leases` exists to clean up after.
    request = CapacityRequest(
        venue_id=VENUE_ECS_GPU,
        owner_id=args.resolved_owner_id or "00000000-0000-0000-0000-000000000000",
        pool_id=args.pool,
        job_id=args.job_id,
        gpu_count=1,
        min_vram_gb=float(args.min_vram_gb),
        enrolment_url=settings.public_api_url,
        quoted_usd_per_hour=float(args.usd_per_hour),
        node_id="dryrun-not-a-real-node",
        machine_token="dryrun-not-a-real-token",
    )
    params = provider.run_params(request)

    banner("STAGE 1 — DRY RUN (nothing is created)")
    say("The exact RunInstances parameters EcsGpuProvider.run_params() builds")
    say("from this deployment's settings:")
    say("")
    say(format_run_params(params))
    say("")
    say("Read back from those bytes:")
    ok = True
    for passed, what in launch_self_checks(params, settings.public_api_url):
        say(f"  [{'PASS' if passed else 'REFUSE':>6}] {what}")
        ok = ok and passed
    if not ok:
        say("")
        say("A launch self-check failed. That is a configuration problem in "
            "this deployment, not a venue problem; nothing was sent.")
        return False

    client = build_client(settings)

    say("")
    say(f"RunInstances with DryRun=true against {settings.ecs_region} ...")
    try:
        payload = await client.call("RunInstances", {**params, "DryRun": "true"})
    except EcsApiError as exc:
        if dry_run_passed(exc.code):
            say(f"  [  PASS] {exc.code}: the venue would accept this launch.")
        else:
            say(f"  [REFUSE] {exc.code or exc.status}: {scrub(exc)}")
            say("")
            say("Nothing was created. Fix what the venue named — image id, "
                "vSwitch/zone coherence, security group, RAM policy, or a GPU "
                "quota still at zero — and run this again.")
            return False
    except Exception as exc:  # noqa: BLE001 - transport, credentials, signature
        say(f"  [REFUSE] {type(exc).__name__}: {scrub(exc)}")
        say("A signature that is wrong is indistinguishable from a credential "
            "that is wrong; check both.")
        return False
    else:
        say(f"  [  PASS] the venue answered a plain success: "
            f"{scrub(json.dumps(payload))[:200]}")

    # Free, and it answers one of the adapter's own open questions: does
    # DescribePrice speak USD on this account? Reaching for the provider's
    # private helper deliberately — re-spelling its parameter list here would
    # be a second copy of the thing under test.
    try:
        rate = await provider._hourly_usd()  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - best effort, never fatal
        say(f"  [  SKIP] DescribePrice: {type(exc).__name__}: {scrub(exc)}")
    else:
        if rate is None:
            say("  [  SKIP] DescribePrice returned no USD rate (a non-USD "
                "settlement currency, or no answer). The quote stands, and "
                "acquire_for_job's re-gate step will never fire.")
        else:
            say(f"  [  PASS] DescribePrice says ${rate:.4f}/hr in USD "
                f"(quote for the gate: ${args.usd_per_hour}/hr)")

    inventory, note = await venue_inventory(client, settings.ecs_region)
    say("")
    say(f"What is at the venue right now ({note}):")
    if not inventory:
        say("  nothing tagged as ours in this region.")
    for instance in inventory:
        say(f"  {describe(instance)}")
    ledger.stage("after stage 1")
    return True


# --------------------------------------------------------------------------
# stage 2 + 3 — the rental, and the teardown that outranks it
# --------------------------------------------------------------------------


async def watch(
    settings: Any, ledger: Ledger, client: Any, stop: asyncio.Event
) -> None:
    """Learn the ids while the acquisition is still in flight, and print them.

    `acquire_for_job` opens the `rented_capacity` row BEFORE the venue is
    asked for anything, and mints the node id as `rented-<rid[:12]>`. Both
    facts are load-bearing here: from the moment the row exists this loop can
    name the machine at the venue by tag, which is the one route to an
    instance whose handle is still inside `provider.acquire`.

    Its own database connection, deliberately: it runs concurrently with the
    acquisition, and two coroutines sharing one psycopg connection is a way to
    make a rental fail for a reason that has nothing to do with renting.
    """
    from flashml_cloud_api import db as dbmod

    conn = dbmod.connect(settings)
    try:
        while not stop.is_set():
            try:
                row = await asyncio.to_thread(rental_row, conn, ledger.job_id)
                if row:
                    if not ledger.rented_id:
                        ledger.rented_id = str(row["id"])
                        ledger.node_id = f"rented-{ledger.rented_id[:12]}"
                        say(f"[ids] rental row opened: {ledger.line()}")
                    if row.get("machine_id") and not ledger.machine_id:
                        ledger.machine_id = str(row["machine_id"])
                        say(f"[ids] credential minted: {ledger.line()}")
                    if row.get("provider_handle") and not ledger.instance_id:
                        ledger.instance_id = str(row["provider_handle"])
                        say(f"[ids] the row learned the handle: {ledger.line()}")
                if ledger.node_id and not ledger.instance_id:
                    found = await find_instance_by_node_tag(
                        client, settings.ecs_region, ledger.node_id
                    )
                    if found:
                        ledger.instance_id = found
                        banner(
                            "AN INSTANCE EXISTS AND IS BILLING",
                            [ledger.line(), ""] + ledger.manual_destroy(),
                        )
            except Exception as exc:  # noqa: BLE001 - a watcher must not fail a run
                say(f"[watch] {type(exc).__name__}: {scrub(exc)}")
            try:
                await asyncio.wait_for(stop.wait(), timeout=WATCH_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
    finally:
        conn.close()


async def teardown(
    settings: Any, db: Any, provider: Any, client: Any, ledger: Ledger
) -> bool:
    """Destroy, then ask the venue whether it believes us. Never raises.

    Three separate things, in this order and each independently:

    1. `release_capacity` — the sweep's own function, not a raw
       `DeleteInstance`. It destroys the machine, revokes the lease and moves
       the row, and it is idempotent on every half. A raw call here would stop
       the money and leave a live machine token bound to a user's workspace
       for hardware somebody else will rent next week.
    2. A direct `provider.release` for an instance the ROW never learned. This
       is the orphan case: `release_capacity`'s no-handle branch deliberately
       refuses to guess, and the watcher above may hold an id the row does
       not.
    3. The venue's own listing. A run is clean when ECS says nothing of ours
       is left — never when our rows say RELEASED.
    """
    banner("STAGE 3 — TEARDOWN", [ledger.line()])
    from flashml_cloud_api.capacity.reconcile import release_capacity

    settled = False
    if ledger.rented_id:
        try:
            settled = await release_capacity(
                db, provider, rented_id=ledger.rented_id
            )
            say(f"  release_capacity({ledger.rented_id}) -> {settled}")
        except Exception as exc:  # noqa: BLE001 - teardown never raises
            say(f"  release_capacity RAISED: {type(exc).__name__}: {scrub(exc)}")
    else:
        say("  no rental row was ever opened; nothing to release through the row.")

    # The row may not name what the venue made. The watcher may.
    if ledger.instance_id and not settled:
        say(f"  the row did not settle; destroying {ledger.instance_id} "
            "directly, which is the only route to an instance the row never "
            "learned about.")
        try:
            outcome = await provider.release(handle=ledger.instance_id)
            say(f"  provider.release({ledger.instance_id}) -> "
                f"destroyed={outcome.destroyed} {outcome.detail}")
            settled = settled or bool(outcome.destroyed)
        except Exception as exc:  # noqa: BLE001
            say(f"  provider.release RAISED: {type(exc).__name__}: {scrub(exc)}")

    # 3. The venue's answer, which is the only one that counts.
    left: list[dict] = []
    try:
        left, note = await venue_inventory(client, settings.ecs_region)
        say("")
        say(f"  the venue's own listing ({note}):")
        if not left:
            say("    nothing tagged as ours remains in this region.")
        for instance in left:
            say(f"    {describe(instance)}")
    except Exception as exc:  # noqa: BLE001
        say(f"  could not ask the venue what is left: {type(exc).__name__}: "
            f"{scrub(exc)}")
        say("  TREAT THIS AS 'SOMETHING MAY STILL BE RUNNING'. Open the "
            "console and look.")
        return False

    ours = [
        i for i in left
        if str(i.get("InstanceId")) == ledger.instance_id
        or _tag_value(i, NODE_TAG_KEY) == ledger.node_id
    ]
    if ours:
        banner(
            "!!! NOT DESTROYED — THIS IS STILL BILLING !!!",
            [describe(i) for i in ours] + [""] + ledger.manual_destroy(),
            char="!",
        )
        return False
    if left:
        banner(
            "OTHER FLASHML-TAGGED INSTANCES EXIST AT THIS VENUE",
            [describe(i) for i in left]
            + ["", "None of them is this run's. They are somebody's leak — "
               "reconcile them against public.rented_capacity by hand."],
            char="!",
        )
    ledger.destroyed = True
    say("")
    say(f"  the venue says this run's instance is GONE: {ledger.line()}")
    return True


def _tag_value(instance: dict, key: str) -> str:
    for tag in ((instance.get("Tags") or {}).get("Tag") or []):
        if str(tag.get("TagKey") or tag.get("Key")) == key:
            return str(tag.get("TagValue") or tag.get("Value"))
    return ""


async def stage_rent(
    settings: Any, db: Any, args: argparse.Namespace, ledger: Ledger,
    provider: Any, client: Any,
) -> tuple[bool, dict]:
    """The rental itself. Everything money-related is `acquire_for_job`'s.

    What is here and not there: the wall-clock measurement nobody has (boot to
    registration — `DEFAULT_REGISTRATION_TIMEOUT_S` is 15 minutes because of
    an argument, not a measurement), the hold that gives a task time to be
    claimed, and the ledger reads that answer the D9 question.
    """
    from flashml_cloud_api.capacity.acquire import acquire_for_job
    from flashml_cloud_api.capacity.provider import CapacityRequest
    from flashml_cloud_api.router.venues import VENUE_ECS_GPU

    evidence: dict[str, Any] = {"job_id": args.job_id}

    request = CapacityRequest(
        venue_id=VENUE_ECS_GPU,
        owner_id=args.resolved_owner_id,
        pool_id=args.pool,
        job_id=args.job_id,
        gpu_count=1,
        min_vram_gb=float(args.min_vram_gb),
        # D9. The only URL that can reach the launch, and Stage 0 has already
        # refused every value a rented host could not phone home to.
        enrolment_url=settings.public_api_url,
        quoted_usd_per_hour=float(args.usd_per_hour),
    )

    banner(
        "STAGE 2 — RENTING A REAL MACHINE. THIS SPENDS MONEY.",
        [
            f"venue={VENUE_ECS_GPU} region={settings.ecs_region} "
            f"type={settings.ecs_instance_type}",
            f"quote=${args.usd_per_hour}/hr  projected max="
            f"${projected_spend_usd(float(args.usd_per_hour), registration_timeout_s=float(args.registration_timeout_s), hold_s=float(args.hold_s)):.2f}",
            f"enrolment_url={settings.public_api_url}",
            f"job_id={args.job_id} pool={args.pool}",
        ],
    )

    stop = asyncio.Event()
    watcher = asyncio.create_task(watch(settings, ledger, client, stop))
    started = time.monotonic()
    ok = False
    try:
        try:
            rented_id = await acquire_for_job(
                db, provider, settings, request=request
            )
        finally:
            elapsed = time.monotonic() - started
            evidence["acquire_wall_clock_s"] = round(elapsed, 1)
            say(f"[time] acquire_for_job returned/failed after {elapsed:.0f}s")
        ledger.rented_id = rented_id
        ledger.node_id = f"rented-{rented_id[:12]}"
        ok = True
    except BaseException as exc:  # noqa: BLE001 - reported; teardown follows
        evidence["acquire_error"] = f"{type(exc).__name__}: {scrub(exc)}"
        banner(
            "ACQUISITION FAILED",
            [f"{type(exc).__name__}: {scrub(exc)}", "", ledger.line()],
            char="!",
        )
        # Give the watcher one more pass: a machine may exist that the row
        # never named, and the tag is the only route to it.
        await asyncio.sleep(WATCH_INTERVAL_S + 1)
    finally:
        stop.set()
        await watcher

    if not ok:
        return False, evidence

    # What the row and the machine say, now that acquisition has returned.
    row = await asyncio.to_thread(rental_row, db, args.job_id)
    machine = await asyncio.to_thread(machine_row, db, ledger.node_id)
    if row:
        ledger.instance_id = str(row.get("provider_handle") or ledger.instance_id)
        ledger.machine_id = str(row.get("machine_id") or ledger.machine_id)
        evidence["rented_capacity"] = {
            k: str(v) for k, v in row.items() if k != "failure_detail"
        }
        say(f"  row state={row['state']} handle={row['provider_handle']} "
            f"usd_per_hour={row['usd_per_hour']}")
        if row.get("usd_per_hour") is not None:
            quoted = float(args.usd_per_hour)
            answered = float(row["usd_per_hour"])
            say(f"  the row carries ${answered}/hr against a ${quoted}/hr "
                + ("quote — the venue restated the price."
                   if abs(answered - quoted) > 1e-9
                   else "quote — DescribePrice did not restate it in USD."))
    if machine:
        created = machine.get("created_at")
        seen = machine.get("last_seen_at")
        evidence["machine"] = {k: str(v) for k, v in machine.items()}
        if created and seen:
            boot = (seen - created).total_seconds()
            evidence["boot_to_registration_s"] = round(boot, 1)
            banner(
                "MEASURED: BOOT TO REGISTRATION",
                [
                    f"{boot:.0f}s from machines.created_at to the first "
                    f"last_seen_at.",
                    "Nobody had this number before. "
                    "DEFAULT_REGISTRATION_TIMEOUT_S is 900s and "
                    "reconcile.DEFAULT_BOOT_GRACE_S is 3600s, both sized by "
                    "argument rather than measurement.",
                ],
            )
    ledger.stage("acquired")

    # The hold. Money is running; every line printed here is paid for.
    deadline = time.monotonic() + float(args.hold_s)
    seen_attempts = 0
    while time.monotonic() < deadline:
        machine = await asyncio.to_thread(machine_row, db, ledger.node_id)
        attempts = await asyncio.to_thread(attempts_for, db, ledger.machine_id)
        if len(attempts) != seen_attempts:
            seen_attempts = len(attempts)
            say(f"  [attempts] {seen_attempts} row(s) now name this machine: "
                + ", ".join(
                    f"{a.get('job_id')}/{a.get('task_id')}" for a in attempts[-3:]
                ))
        left = deadline - time.monotonic()
        say(f"  [hold {left:5.0f}s left] last_seen_at="
            f"{machine.get('last_seen_at') if machine else '(no machine row)'} "
            f"attempts={seen_attempts}  {ledger.line()}")
        await asyncio.sleep(min(HOLD_POLL_S, max(left, 0.0)))

    attempts = await asyncio.to_thread(attempts_for, db, ledger.machine_id)
    job = await asyncio.to_thread(job_row, db, args.job_id)
    evidence["attempts"] = [
        {k: str(v) for k, v in a.items()} for a in attempts
    ]
    verdict, why = attempts_verdict(attempts, job, expect_claim=args.expect_claim)
    evidence["ledger_verdict"] = verdict
    banner(
        f"THE LEDGER CHECK — {verdict}",
        [f"machine_id={ledger.machine_id or '(none)'} "
         f"node_id={ledger.node_id}", ""] + list(why),
        char="#",
    )
    return True, evidence


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--owner", required=True,
        help="the submitter: a profile uuid, or an email in auth.users.",
    )
    parser.add_argument(
        "--pool", required=True,
        help="the pool uuid the rented machine joins. The owner must OWN it "
             "and be a MEMBER of it (lock_pool_for_owner requires both).",
    )
    parser.add_argument(
        "--job-id", default="",
        help="rented_capacity.job_id. Free text (no FK). Defaults to "
             "first-rental-<utc>.",
    )
    parser.add_argument(
        "--usd-per-hour", type=float, default=MEASURED_T4_USD_PER_HOUR,
        help=f"the QUOTE the budget gate reads. Default {MEASURED_T4_USD_PER_HOUR} "
             "= the measured ecs.gn6i-c4g1.xlarge rate. Wrong for any other "
             "instance type.",
    )
    parser.add_argument("--min-vram-gb", type=float, default=16.0)
    parser.add_argument(
        "--registration-timeout-s", type=float, default=900.0,
        help="how long to wait for the host to enrol before destroying it. "
             "The adapter's own default is 900.",
    )
    parser.add_argument(
        "--hold-s", type=float, default=DEFAULT_HOLD_S,
        help="how long to keep the machine after it enrols, so a task has "
             "time to be claimed. There is no way to keep it longer than "
             "this run.",
    )
    parser.add_argument(
        "--max-spend-usd", type=float, default=DEFAULT_MAX_SPEND_USD,
        help="refuse to start if this run's own arithmetic exceeds it.",
    )
    parser.add_argument(
        "--expect-claim", action="store_true",
        help="you have submitted work into this pool: treat an empty "
             "public.attempts as a FAILURE rather than an unanswered question.",
    )
    parser.add_argument(
        "--skip-api-probe", action="store_true",
        help="do not GET the public API's /healthz. Only for an API that is "
             "deliberately unreachable from here but reachable from the venue.",
    )
    # Mutually exclusive so "stop before the venue" and "spend money" can
    # never be asked for in the same command line.
    how_far = parser.add_mutually_exclusive_group()
    how_far.add_argument(
        "--preflight-only", action="store_true",
        help="stage 0 alone. Touches no venue.",
    )
    how_far.add_argument(
        "--rent-for-real", action="store_true",
        help="ARM STAGE 2. Creates a real instance and spends real money.",
    )
    parser.add_argument(
        "--json", dest="json_path", default="",
        help="write the run's evidence here.",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    from flashml_cloud_api import db as dbmod
    from flashml_cloud_api.settings import Settings

    ledger = Ledger()
    atexit.register(_exit_banner, ledger)

    try:
        settings = Settings.from_env()
    except Exception as exc:  # noqa: BLE001 - a refusal, not a crash
        banner(
            "STAGE 0 — REFUSED: the settings would not load",
            [
                f"{type(exc).__name__}: {scrub(exc)}",
                "",
                "This script never talks to the coordinator, but "
                "Settings.from_env() refuses to build without "
                "COORDINATOR_URL and COORDINATOR_OPERATOR_TOKEN while "
                "FLASHML_REQUIRE_AUTH is on. Set them, or set "
                "FLASHML_REQUIRE_AUTH=false for this run.",
            ],
            char="!",
        )
        return EXIT_REFUSED

    ledger.region = settings.ecs_region
    ledger.instance_type = settings.ecs_instance_type
    ledger.job_id = args.job_id

    db = None
    if settings.database_url:
        try:
            db = dbmod.connect(settings)
        except Exception as exc:  # noqa: BLE001
            banner("STAGE 0 — REFUSED: the database would not open",
                   [f"{type(exc).__name__}: {scrub(exc)}"], char="!")
            return EXIT_REFUSED

    args.resolved_owner_id = resolve_owner(db, args.owner) if db else ""

    plan = stage_plan(args)
    banner(
        "FIRST RENTAL",
        [
            f"stages: {' -> '.join(plan)}",
            ("STAGE 2 IS ARMED: this run will create a real instance."
             if "rent" in plan else
             "Stage 2 is NOT armed. Nothing will be created. Pass "
             "--rent-for-real to rent."),
            f"job_id={args.job_id}",
        ],
    )

    banner("STAGE 0 — PREFLIGHT (no spending)")
    checks = preflight(settings, db, args)
    if not report(checks):
        say("")
        say("REFUSED. Nothing was contacted and nothing was created.")
        return EXIT_REFUSED
    say("")
    say("Preflight passed.")

    if "dryrun" not in plan:
        return EXIT_OK

    if not await stage_dry_run(settings, args, ledger):
        return EXIT_FAILED

    if "rent" not in plan:
        say("")
        say("Stage 1 passed and stage 2 is not armed. Nothing was created. "
            "Re-run with --rent-for-real when you are ready to spend.")
        return EXIT_OK

    # ---- stage 2 + 3 ----
    #
    # ONE provider and ONE client for the rental, the watcher and the
    # teardown. The teardown must be able to address exactly what the
    # acquisition created, and two adapters built from the same settings are
    # one refactor away from not being the same adapter.
    provider = build_provider(
        settings, registration_timeout_s=float(args.registration_timeout_s)
    )
    client = build_client(settings)

    loop = asyncio.get_running_loop()
    work = asyncio.create_task(
        stage_rent(settings, db, args, ledger, provider, client)
    )
    interrupts = {"n": 0}

    def on_signal(name: str) -> None:
        """Print first, then stop the work — never the other way round.

        The ids are the thing a human needs if this process is about to die
        badly, so they go to the terminal before anything is cancelled. The
        first signal cancels the rental and lets the `finally` below run the
        real teardown in a live event loop; the second says so; the third
        gives up and leaves the recipe on the screen.
        """
        interrupts["n"] += 1
        banner(
            f"{name} ({interrupts['n']})",
            [ledger.line(), ""] + ledger.manual_destroy(),
            char="!",
        )
        if interrupts["n"] == 1:
            say("stopping the rental; teardown runs next. Do not kill this "
                "process — it is what destroys the machine.")
            work.cancel()
        elif interrupts["n"] == 2:
            say("teardown is already running. Killing this process now leaves "
                "a machine billing.")
        else:
            say("giving up. DESTROY THE INSTANCE ABOVE BY HAND.")
            os._exit(EXIT_MAYBE_LEAKED)

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(
                getattr(signal, signame), on_signal, signame
            )
        except (NotImplementedError, AttributeError):  # pragma: no cover
            pass

    evidence: dict[str, Any] = {}
    rented_ok = False
    try:
        rented_ok, evidence = await work
    except asyncio.CancelledError:
        say("the rental was cancelled; teardown follows.")
    except Exception as exc:  # noqa: BLE001 - teardown follows regardless
        banner("STAGE 2 RAISED", [f"{type(exc).__name__}: {scrub(exc)}"], char="!")
    finally:
        # Not cancelled ourselves — only `work` was — so this runs in a live
        # loop with everything it needs.
        torn_down = await teardown(settings, db, provider, client, ledger)
        evidence["torn_down"] = torn_down
        evidence["ledger"] = {
            "instance_id": ledger.instance_id,
            "machine_id": ledger.machine_id,
            "node_id": ledger.node_id,
            "rented_id": ledger.rented_id,
            "region": ledger.region,
        }
        # The ledger check again, from the rows as they stand after teardown:
        # attempts survive a revoke, and this is the line the whole run is for.
        if db is not None:
            attempts = await asyncio.to_thread(attempts_for, db, ledger.machine_id)
            job = await asyncio.to_thread(job_row, db, args.job_id)
            verdict, why = attempts_verdict(
                attempts, job, expect_claim=args.expect_claim
            )
            evidence["ledger_verdict"] = verdict
            banner(
                f"THE LEDGER CHECK — {verdict}",
                [f"machine_id={ledger.machine_id or '(none)'}", ""] + list(why),
                char="#",
            )
        if args.json_path:
            with open(args.json_path, "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, indent=2, sort_keys=True, default=str)
            say(f"evidence written to {args.json_path}")

    if not evidence.get("torn_down"):
        return EXIT_MAYBE_LEAKED
    return EXIT_OK if rented_ok else EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.job_id:
        args.job_id = "first-rental-" + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
    args.resolved_owner_id = ""
    return asyncio.run(run(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
