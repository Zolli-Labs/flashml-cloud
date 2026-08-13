"""Stand up an Alibaba FC Agent Sandbox as a FlashML worker, from your laptop.

The FC sandbox has NO user-facing shell — everything happens over the E2B
protocol — so "manually starting a worker on Alibaba" means running THIS
script, which performs the sequence proven live on 2026-08-13:

  1. create a sandbox (or reconnect to one you name)
  2. install uv, provision a private CPython 3.11 (the image ships 3.13,
     whose wheels don't match the curated pins; the host python is never
     touched — see findings §6.1)
  3. install the flashnode agent into that venv
  4. run `flashnode login` inside the sandbox and PRINT THE CODE for you
     to approve in the console (use the ?pool= link so the machine lands
     in your workspace)
  5. start the worker on the trusted tier

Usage, from the flashml-cloud repo root:

    set -a; source .env.dev; set +a        # E2B_API_KEY, E2B_REGION
    python scripts/fc_worker.py                          # new sandbox, dev
    python scripts/fc_worker.py --sandbox sbx-...        # reuse one
    python scripts/fc_worker.py --coordinator https://flashml-api.onrender.com

Needs `e2b-code-interpreter` (the api venv has it:
flashml-cloud/apps/api/.venv/bin/python).

The sandbox expires at --timeout (default 2h) and takes its enrolment with
it; re-running the script makes a NEW machine that needs a new approval.
That is by design — a sandbox is disposable capacity, not a pet.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

AGENT_SPEC = (
    "git+https://github.com/Zolli-Labs/flashml.git"
    "@fix/trusted-tier-execution#subdirectory=flashnode"
)
# Real PyPI, always: the FC image's /etc/pip.conf pins a lagging regional
# mirror (findings §0.1 / §2.4 of the 2026-08-13 register).
INDEX = "https://pypi.org/simple/"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sandbox", help="existing sandbox id to reuse")
    p.add_argument(
        "--coordinator",
        default="https://flashml-dev-api.onrender.com",
        help="FlashML API base URL (default: dev)",
    )
    p.add_argument("--timeout", type=int, default=7200, help="sandbox TTL seconds")
    p.add_argument("--python", default="3.11", help="interpreter to provision")
    args = p.parse_args()

    try:
        from e2b_code_interpreter import Sandbox
    except ImportError:
        print(
            "e2b-code-interpreter not installed — run me with the api venv:\n"
            "  flashml-cloud/apps/api/.venv/bin/python scripts/fc_worker.py",
            file=sys.stderr,
        )
        return 1

    key = os.environ.get("E2B_API_KEY")
    if not key:
        print("E2B_API_KEY not set — `set -a; source .env.dev; set +a` first.",
              file=sys.stderr)
        return 1
    region = os.environ.get("E2B_REGION", "ap-southeast-1")
    kw = dict(
        api_key=key,
        api_url=f"https://api.{region}.e2b.fc.aliyuncs.com",
        domain=f"{region}.e2b.fc.aliyuncs.com",
    )

    if args.sandbox:
        sbx = Sandbox.connect(args.sandbox, **kw)
        sbx.set_timeout(args.timeout)
        print(f"reconnected: {sbx.sandbox_id} (ttl reset to {args.timeout}s)")
    else:
        sbx = Sandbox.create(template="code-interpreter-v1", timeout=args.timeout, **kw)
        print(f"sandbox: {sbx.sandbox_id}")

    print("installing uv + provisioning python", args.python, "(1-2 min)...")
    sbx.commands.run(
        "python3 -m pip install --no-input --disable-pip-version-check "
        f"-i {INDEX} uv", timeout=180)
    sbx.commands.run(
        f"python3 -m uv venv $HOME/agent --python {args.python}", timeout=300)
    print("installing the agent (branch build, until 0.4.1 ships)...")
    sbx.commands.run(
        "python3 -m uv pip install --python $HOME/agent/bin/python "
        f"--index-url {INDEX} '{AGENT_SPEC}'", timeout=420)

    # Already enrolled from a previous run of this same sandbox? Then skip
    # straight to the worker — a second login would rotate the machine token
    # out from under nothing, but it needs a pointless browser round-trip.
    have = sbx.commands.run(
        "grep -q . $HOME/.flashnode/credentials.json 2>/dev/null && echo yes || echo no",
        timeout=30).stdout.strip()
    if have != "yes":
        sbx.commands.run(
            f"nohup $HOME/agent/bin/flashnode login --coordinator {args.coordinator} "
            "> /tmp/login.log 2>&1 & true", timeout=30)
        code_seen = False
        for _ in range(30):
            time.sleep(4)
            log = sbx.commands.run("cat /tmp/login.log 2>/dev/null || true",
                                   timeout=30).stdout or ""
            if not code_seen and "Your code" in log:
                # Print the code block verbatim — it carries the approve URL.
                for line in log.splitlines():
                    if line.strip():
                        print("  " + line)
                print("\n  >>> approve it now (add ?pool=<workspace-pool-id> to land it in your workspace)")
                code_seen = True
            if "enrolled" in log:
                print("enrolled.")
                break
        else:
            print("gave up waiting for approval; sandbox left running:",
                  sbx.sandbox_id, file=sys.stderr)
            return 1

    sbx.commands.run(
        f"nohup $HOME/agent/bin/flashnode work --coordinator {args.coordinator} "
        "--runner trusted --log-json > /tmp/work.log 2>&1 & true", timeout=30)
    time.sleep(8)
    alive = sbx.commands.run(
        "for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); "
        "case \"$c\" in *flashnode*work*) echo RUNNING; break;; esac; done",
        timeout=30).stdout.strip()
    print("worker:", alive or "NOT RUNNING — check /tmp/work.log in the sandbox")
    print(f"\nsandbox {sbx.sandbox_id} · expires in {args.timeout}s · "
          f"rerun with --sandbox {sbx.sandbox_id} to extend/reuse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
