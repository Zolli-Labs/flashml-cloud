"""Environment configuration for the FlashML Cloud API.

Fails loudly at startup when auth is required and a secret is missing,
rather than silently running open and failing at the first request. This
mirrors the coordinator's ``FLASHML_REQUIRE_NODE_AUTH`` guard.

Note that ``SUPABASE_JWT_SECRET`` is NOT among those required secrets:
modern Supabase projects sign with asymmetric ES256 keys and have no shared
secret to hand out. ``SUPABASE_URL`` is the mandatory input instead, because
the public keys are fetched from it (see ``auth.jwks_url``).
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass, field as dc_field


def _decode_pem(value: str) -> str:
    """Return an RSA private key PEM from either of the two forms it
    legitimately arrives in.

    The GitHub App private key is multi-line. Render's dashboard accepts
    newlines in an env var; a `.env` file does not, and the three-place
    map in `.env.dev.example` exists precisely because values drift when
    one place cannot hold what another can. Base64 is the one encoding
    that survives every place this has to live, so both are accepted and
    normalised here — the single point the value enters the process.

    A value that is neither is returned UNCHANGED rather than mangled.
    `github_app.py` then fails with "not a valid private key", which
    names the actual problem; a forced base64 decode of a typo produces
    binary noise and an error about padding.

    A PEM is returned byte-for-byte, trailing newline included. Stripping
    a credential to tidy it is how a valid value becomes an invalid one —
    the whitespace is only removed for the base64 *attempt*, where
    `validate=True` would reject it.
    """
    if "-----BEGIN" in value:
        return value
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value
    return decoded if "-----BEGIN" in decoded else value


def _int_env(name: str, default: int) -> int:
    """Read an integer env var, falling back on anything unparseable.

    A typo in a timeout must not take the API down at startup — but it must
    also not silently become zero, which for `FC_SANDBOX_TIMEOUT_MS` would
    mean a sandbox that expires the moment it is created.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r is not an integer; using %d", name, raw, default
        )
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float, *, allow_zero: bool = False) -> float:
    """Read a float env var, falling back on anything unparseable.

    A typo must not take the API down at startup. What happens to a value
    that parses but is not positive depends on what the number MEANS, so the
    caller says which:

    * ``allow_zero=False`` — the default, and ``_int_env``'s behaviour. Zero
      falls back. For a width, a timeout or an interval, zero is never what
      an operator wants and is usually a half-finished edit.
    * ``allow_zero=True`` — **zero is honoured exactly as typed.** For a
      spend ceiling, ``0`` is the emergency stop: the obvious way to say
      "rent nothing, right now". Coercing it to the default handed an
      operator who had just tried to halt all renting a $10/hr allowance
      instead, which is the opposite of what they asked for and the reason
      this flag exists. It is deliberately opt-in: ``_int_env``'s callers
      (``FC_SANDBOX_TIMEOUT_MS``) mean the other thing, where zero would be
      a sandbox that expires the moment it is created.

    Negatives fall back either way, and now say so in the log: a negative
    ceiling is a typo rather than a stricter zero, and silently refusing
    every acquisition on one is a failure nobody typed and nothing explains.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r is not a number; using %s", name, raw, default
        )
        return default
    if value > 0 or (allow_zero and value == 0):
        return value
    logging.getLogger(__name__).warning(
        "%s=%r is not a usable value here; using %s", name, raw, default
    )
    return default


def _with_default_scheme(url: str, scheme: str) -> str:
    """Prepend ``scheme://`` to `url` when it has none.

    Render's Blueprint ``fromService: {property: hostport}`` returns a bare
    ``host:port`` (e.g. ``flashml-coordinator:10000``) for COORDINATOR_URL —
    never a scheme, because a private-service hostport isn't a URL as far as
    Render is concerned. Passed straight to httpx that raises
    ``UnsupportedProtocol`` on every outbound call (register, claim,
    complete, artifact upload) even though ``/healthz`` looks fine, since it
    never touches the coordinator. This is the single place that value
    enters the process, so this is where it gets normalized rather than at
    each call site. An already-scheme'd value (``http://`` or ``https://``)
    and an empty value both pass through unchanged.
    """
    if not url or "://" in url:
        return url
    return f"{scheme}://{url}"


@dataclass
class Settings:
    supabase_url: str
    supabase_service_key: str
    coordinator_url: str
    coordinator_operator_token: str
    require_auth: bool
    #: LEGACY, and optional. Only projects still issuing HS256 tokens from a
    #: shared secret need this. Our project rotated to ECC (P-256) and every
    #: newly-issued token is ES256, verified against the JWKS public key —
    #: which is why this is not in the require_auth check below. Left set, it
    #: keeps not-yet-expired tokens signed by the PREVIOUS key working
    #: through a rotation.
    supabase_jwt_secret: str = ""
    database_url: str = ""
    #: Public base URL of the browser console, used to build the
    #: `verification_uri` a machine prints during device-code enrolment.
    #: Deliberately NOT in the require_auth missing-secret check: refusing to
    #: boot over a display string would take the whole API down for it.
    #:
    #: It is not, however, merely cosmetic when unset, which is what this
    #: comment used to claim. The old reasoning was that a relative
    #: "/activate" still lets "a human find the page from whatever host they
    #: are on" — but the consumer of this string is `flashnode login`,
    #: printing into a TERMINAL on a volunteer's own laptop. There is no
    #: current host there. Hands-on QA on 2026-08-04 hit exactly that: the
    #: agent printed "Approve at: /activate" and the operator had no way to
    #: know which host to put in front of it. Unset is a real, if
    #: non-fatal, defect — hence the startup warning in `from_env`.
    console_url: str = ""
    #: THIS API's own public base URL — the one a machine outside our network
    #: can reach. Distinct from `console_url` (the web app) and, critically,
    #: from `coordinator_url`.
    #:
    #: A FlashNode running inside an Alibaba FC Sandbox must enrol against
    #: *this* API, not the coordinator, for two independent reasons and either
    #: alone is fatal:
    #:
    #:   1. It authenticates with a MACHINE TOKEN, which is a cloud-API
    #:      concept. The coordinator has no notion of one and would refuse it.
    #:   2. On Render the coordinator is `type: pserv` — a PRIVATE service on
    #:      the internal network. A sandbox in Singapore cannot route to it at
    #:      all. The whole point of `pserv` is that nothing outside can.
    #:
    #: Unset, `sandbox_orchestrator.start_session` falls back to
    #: `coordinator_url`, which is correct only for a local dev loop where both
    #: happen to be reachable. In any deployed environment that fallback
    #: produces a sandbox whose worker can never enrol — and the failure looks
    #: like a bootstrap timeout, not a configuration error, which is why this
    #: field exists rather than a bare `os.environ` read at the call site.
    public_api_url: str = ""
    #: Resend API key. Optional: an unconfigured deploy must still boot and
    #: serve — the failure mode of a missing mail provider is a silent
    #: product, not a dead API, so this is deliberately NOT in the
    #: require_auth missing-secret check. Same reasoning as `console_url`,
    #: which warns rather than refusing.
    resend_api_key: str = ""
    #: The From address, e.g. "FlashML <no-reply@mail.zolliai.com>". Mail is
    #: sent only when this AND `resend_api_key` are both set.
    email_from: str = ""
    #: Reply-to. Falls back to `email_from`. The declined email invites a
    #: reply (re-applying is refused by design — POST /access-request 409s
    #: once decided), so this should be a monitored mailbox.
    email_reply_to: str = ""
    #: GitHub App credentials, for reading a submitter's PRIVATE repos. All
    #: three or none — see `github_app_configured`. Optional for the same
    #: reason as `resend_api_key` above: an unconfigured deploy must still
    #: boot and serve, because public-repo submission is the whole product
    #: for everyone who has not connected GitHub.
    github_app_id: str = ""
    #: The App's URL slug, used only to build the install redirect
    #: (`https://github.com/apps/<slug>/installations/new`).
    github_app_slug: str = ""
    #: The App's RSA private key, PEM. Accepted base64-encoded as well —
    #: see `_decode_pem`.
    github_app_private_key: str = ""

    # --- Alibaba FC Agent Sandbox --------------------------------------
    #
    # The hibernatable evaluation worker. Optional for the same reason as
    # `resend_api_key`: a deploy without it must still boot and serve, and
    # every existing pool keeps working — the sandbox is one more machine,
    # not a dependency of the product.
    #
    # REGION IS NOT A PREFERENCE. Deep hibernation (`sandbox.pause()`) is
    # enabled only in `ap-southeast-1`. Measured 2026-08-11: `us-west-1`
    # returns `403 PauseSessionForbidden — pauseSession is not enabled for
    # this function` while Singapore pauses in 2.7 s and wakes in 0.95 s.
    # Pointing this at any other region silently costs the entire
    # hibernation capability, so it is configuration, not a default.
    fc_sandbox_api_key: str = dc_field(default="", repr=False)
    #: `https://api.<region>.e2b.fc.aliyuncs.com`
    fc_sandbox_api_url: str = ""
    #: `<region>.e2b.fc.aliyuncs.com`
    fc_sandbox_domain: str = ""
    fc_sandbox_region: str = "ap-southeast-1"
    #: Alibaba's built-in template. A custom one needs an ACR tier that
    #: can process images — Economy cannot, and upgrading is four figures.
    fc_sandbox_template: str = "code-interpreter-v1"
    #: The pool that contains ONLY the managed sandbox machine.
    #:
    #: CORRECTED 2026-08-11 — the earlier comment here claimed this "stops a
    #: public job claiming the sandbox". It does not, and believing it would
    #: be a security hole. The pool gate (the seventh) only runs for tasks
    #: whose payload NAMES a pool. A public job compiles to
    #: `placement.pool = "any"`, which expands to a payload with no `pool`
    #: key at all, so the gate never fires for it.
    #:
    #: What this pool DOES guarantee, both of which matter:
    #:   - the evaluation task cannot be claimed by anything else;
    #:   - another team's pool-scoped job cannot claim this machine.
    #:
    #: What keeps PUBLIC work off the sandbox is a different gate entirely:
    #: a public job is `tier: sandboxed, allowFallback: false` and is
    #: placeable only on a node advertising `sandbox_capable`. An FC Agent
    #: Sandbox cannot nest a container runtime, so the worker must run a
    #: no-container tier and must not claim that capability. That is a
    #: property of HOW `flashnode` is launched (`sandbox_bootstrap.py`), not
    #: of this setting — the stamp the coordinator judges comes from the
    #: agent's own registration, and `machines.sandbox_capable` is a
    #: display-only snapshot with no authority over placement.
    fc_sandbox_pool_id: str = ""
    #: The sandbox's own lifetime.
    #:
    #: THERE ARE PROBABLY TWO CLOCKS HERE, and they disagree. Measured
    #: 2026-08-11 (n=1, 150 s pause): the E2B sandbox TTL **stops** while
    #: paused — a 90 s-TTL sandbox reconnected 64 s past its own expiry was
    #: intact, and `end_at` read while paused was byte-identical to `end_at`
    #: at create. But Alibaba doc 3028695 says the FC **Session** TTL keeps
    #: accruing from creation ("仍从原始创建时间累计"). Those describe
    #: different objects — the E2B-compatible sandbox lifetime and the
    #: underlying FC session — so both can be true, and only the first has
    #: been measured.
    #:
    #: Therefore: size this to exceed the WHOLE train→wait→evaluate span, as
    #: if nothing stopped. That advice is correct under either clock, and it
    #: is the only advice that is. Do not build anything that REQUIRES the
    #: clock to stop until a multi-hour hibernation has actually been
    #: observed to survive — the longest pause measured so far is 150 s.
    #:
    #: A sandbox that expires mid-hibernation is gone, and takes the prepared
    #: environment with it. Default 1 hour.
    fc_sandbox_timeout_ms: int = 3_600_000

    # --- Alibaba OSS (artifacts) ---------------------------------------
    #
    # Where committed artifacts live, replacing the coordinator's local
    # disk (`FLASHML_LOCAL_ARTIFACTS_DIR`) — 5 GB on prod, and nothing at
    # all on dev, where `/tmp` loses every artifact on redeploy.
    #
    # The sandbox never receives these credentials. It is handed presigned
    # URLs, which carry no identity and expire on their own. See
    # `alibaba_oss.sign_get`.
    oss_bucket: str = ""
    #: Host only, no scheme: `oss-ap-southeast-1.aliyuncs.com`.
    oss_endpoint: str = ""
    oss_region: str = "ap-southeast-1"
    oss_access_key_id: str = ""
    oss_access_key_secret: str = dc_field(default="", repr=False)

    # --- Rented capacity budget gate ------------------------------------
    #
    # The check that stands between a bug in automatic GPU renting and the
    # account. Two ceilings, because they answer different questions: the
    # per-acquisition cap bounds ONE mistake, the rolling-window cap bounds
    # a LOOP of correct-looking decisions, which is what actually empties
    # an account. See `capacity/budget.py`.
    #
    # **BOTH NUMBERS ARE RATES — DOLLARS PER HOUR — NOT TOTALS.** Neither
    # one is a cap on how many dollars may ever be spent, because this
    # system has no such cap and no column recording dollars actually
    # billed: `rented_capacity` stores an hourly rate per rental and
    # `budget.window_spend_usd` adds those rates up. Anyone bounding real
    # spend from this file is bounding a rate of commitment and must do the
    # multiplication themselves.
    #
    #: Per-acquisition ceiling, in $/hr: the most any ONE rental may be
    #: quoted — or answered — at. Bounds a single mistake, not a sequence of
    #: them. An explicit `0` is honoured and refuses every priced
    #: acquisition (see `_float_env`).
    rented_usd_per_acquisition_max: float = 2.0
    #: Rolling-window ceiling, in $/hr, across ALL acquisitions. This is the
    #: one that bounds a loop of correct-looking decisions.
    #:
    #: What it actually bounds: the SUM OF THE HOURLY RATES of every
    #: `rented_capacity` row created in the last `rented_usd_window_hours`,
    #: whatever state those rows are now in. At the default that reads "the
    #: rentals committed in the last 24 hours may commit at most $10 PER
    #: HOUR between them" — which, held at the ceiling and left running, is
    #: about $240 a day. It is NOT $10 of spend, and it does not stop
    #: falling once the money is gone, because nothing here counts money
    #: that has been spent.
    #:
    #: The standing $10 operational ceiling on TOTAL rented spend is an
    #: agreement between people, enforced by watching the venue's own
    #: billing page. To make this variable enforce something like it, size
    #: it against the window: e.g. `$0.40/hr` over a 24h window is ~$10 a
    #: day if every rental runs the whole day.
    #:
    #: An explicit `0` is honoured and is the emergency stop: no priced
    #: acquisition passes the gate at all.
    rented_usd_window_max: float = 10.0
    #: Width of the rolling window the ceiling above is measured over, in
    #: hours. A width, not a ceiling: `0` here would make the window count
    #: nothing and is therefore refused (it falls back to this default).
    rented_usd_window_hours: float = 24.0

    @property
    def fc_sandbox_configured(self) -> bool:
        """All four present. All-or-nothing, like the GitHub App.

        A half-configured sandbox cannot create anything, so reporting it
        as available would let the console offer an evaluation button that
        fails after the user has already submitted a training job.
        """
        return bool(
            self.fc_sandbox_api_key
            and self.fc_sandbox_api_url
            and self.fc_sandbox_domain
            and self.fc_sandbox_pool_id
        )

    @property
    def oss_configured(self) -> bool:
        """All four present, same reasoning.

        Unconfigured is not an error: artifacts fall back to the
        coordinator's local store and the whole loop still runs.
        """
        return bool(
            self.oss_bucket
            and self.oss_endpoint
            and self.oss_access_key_id
            and self.oss_access_key_secret
        )

    @property
    def fc_sandbox_hibernation_region(self) -> bool:
        """Whether the configured region can actually pause.

        Read this before promising hibernation in a UI or a claim. It is a
        allowlist of one today; when Alibaba enables another region, add it
        here and nowhere else.
        """
        return self.fc_sandbox_region == "ap-southeast-1"

    @property
    def github_app_configured(self) -> bool:
        """All three present. Deliberately all-or-nothing.

        A half-configured App can mint no token, so reporting it as
        configured would have the console render a Connect button that
        leads a person through granting us access to their code and then
        fails. Off is the safe direction to round to.
        """
        return bool(
            self.github_app_id
            and self.github_app_slug
            and self.github_app_private_key
        )

    @classmethod
    def from_env(cls) -> "Settings":
        require_auth = os.environ.get("FLASHML_REQUIRE_AUTH", "true").strip().lower() not in (
            "0", "false", "no", "off", "",
        )

        # Mandatory when auth is on: the JWKS the API verifies browser tokens
        # against is derived from it.
        supabase_url = os.environ.get("SUPABASE_URL", "")
        # Optional — see the field comment. Absent on any project migrated to
        # asymmetric signing keys.
        supabase_jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        # http, not https: Render private-service traffic (COORDINATOR_URL
        # above) is plain HTTP on the internal network, which does not
        # terminate TLS — forcing https would fail the handshake outright.
        coordinator_url = _with_default_scheme(
            os.environ.get("COORDINATOR_URL", ""), "http"
        )
        coordinator_operator_token = os.environ.get("COORDINATOR_OPERATOR_TOKEN", "")
        # Standard libpq connection string/URI for the Postgres database
        # (see flashml_cloud_api.db). Not included in the require_auth
        # missing-secret check below: browser/machine auth can be verified
        # without a DB connection (e.g. in unit tests), so a deploy that is
        # only missing this should not be treated the same as one missing a
        # signing secret. db.connect() raises its own clear error when this
        # is absent and a connection is actually requested.
        database_url = os.environ.get("DATABASE_URL", "")
        console_url = os.environ.get("FLASHML_CONSOLE_URL", "")
        # Normalised the same way COORDINATOR_URL is, and https by default:
        # this one is reached from OUTSIDE our network, so plain http would be
        # a downgrade rather than the internal-network shortcut that makes
        # http correct for the coordinator.
        public_api_url = _with_default_scheme(
            os.environ.get("FLASHML_PUBLIC_API_URL", "").strip().rstrip("/"), "https"
        )
        resend_api_key = os.environ.get("RESEND_API_KEY", "")
        email_from = os.environ.get("EMAIL_FROM", "")
        email_reply_to = os.environ.get("EMAIL_REPLY_TO", "")
        github_app_id = os.environ.get("GITHUB_APP_ID", "").strip()
        github_app_slug = os.environ.get("GITHUB_APP_SLUG", "").strip()
        # NOT stripped before the call: a PEM's trailing newline is part of
        # the credential, and `_decode_pem` strips only for its base64 probe.
        github_app_private_key = _decode_pem(
            os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
        )

        # Alibaba FC Agent Sandbox. The region defaults to the only one that
        # can hibernate; api_url/domain are DERIVED from it when unset so a
        # deploy cannot end up with a Singapore key pointed at a Virginia
        # endpoint — a mismatch Alibaba's own docs warn produces failures in
        # authentication, template lookup, creation and connection alike.
        fc_sandbox_region = (
            os.environ.get("E2B_REGION") or "ap-southeast-1"
        ).strip()
        fc_sandbox_api_key = os.environ.get("E2B_API_KEY", "").strip()
        fc_sandbox_api_url = (
            os.environ.get("E2B_API_URL", "").strip()
            or f"https://api.{fc_sandbox_region}.e2b.fc.aliyuncs.com"
        )
        fc_sandbox_domain = (
            os.environ.get("E2B_DOMAIN", "").strip()
            or f"{fc_sandbox_region}.e2b.fc.aliyuncs.com"
        )
        fc_sandbox_template = (
            os.environ.get("FC_SANDBOX_TEMPLATE", "").strip() or "code-interpreter-v1"
        )
        fc_sandbox_pool_id = os.environ.get("FC_SANDBOX_POOL_ID", "").strip()
        fc_sandbox_timeout_ms = _int_env("FC_SANDBOX_TIMEOUT_MS", 3_600_000)

        # Alibaba OSS.
        oss_bucket = os.environ.get("OSS_BUCKET", "").strip()
        oss_endpoint = os.environ.get("OSS_ENDPOINT", "").strip()
        oss_region = (os.environ.get("OSS_REGION") or fc_sandbox_region).strip()
        oss_access_key_id = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
        oss_access_key_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()

        # Rented capacity budget gate. `allow_zero=True` on the two CEILINGS,
        # because `0` there is the emergency stop and has to survive as `0`.
        #
        # Deliberately NOT on the window WIDTH: a zero-hour window makes
        # `window_spend_usd` count nothing, so the rolling ceiling stops
        # bounding the loop it exists to bound — an explicit `0` there would
        # switch a ceiling OFF, which is the same class of surprise this
        # whole flag exists to remove. It falls back to 24h and logs.
        rented_usd_per_acquisition_max = _float_env(
            "RENTED_USD_PER_ACQUISITION_MAX", 2.0, allow_zero=True
        )
        rented_usd_window_max = _float_env(
            "RENTED_USD_WINDOW_MAX", 10.0, allow_zero=True
        )
        rented_usd_window_hours = _float_env("RENTED_USD_WINDOW_HOURS", 24.0)

        settings = cls(
            supabase_url=supabase_url,
            supabase_jwt_secret=supabase_jwt_secret,
            supabase_service_key=supabase_service_key,
            coordinator_url=coordinator_url,
            coordinator_operator_token=coordinator_operator_token,
            require_auth=require_auth,
            database_url=database_url,
            console_url=console_url,
            public_api_url=public_api_url,
            resend_api_key=resend_api_key,
            email_from=email_from,
            email_reply_to=email_reply_to,
            github_app_id=github_app_id,
            github_app_slug=github_app_slug,
            github_app_private_key=github_app_private_key,
            fc_sandbox_api_key=fc_sandbox_api_key,
            fc_sandbox_api_url=fc_sandbox_api_url,
            fc_sandbox_domain=fc_sandbox_domain,
            fc_sandbox_region=fc_sandbox_region,
            fc_sandbox_template=fc_sandbox_template,
            fc_sandbox_pool_id=fc_sandbox_pool_id,
            fc_sandbox_timeout_ms=fc_sandbox_timeout_ms,
            oss_bucket=oss_bucket,
            oss_endpoint=oss_endpoint,
            oss_region=oss_region,
            oss_access_key_id=oss_access_key_id,
            oss_access_key_secret=oss_access_key_secret,
            rented_usd_per_acquisition_max=rented_usd_per_acquisition_max,
            rented_usd_window_max=rented_usd_window_max,
            rented_usd_window_hours=rented_usd_window_hours,
        )

        if require_auth:
            missing = [
                name
                for name, value in (
                    # SUPABASE_JWT_SECRET is deliberately absent from this
                    # list. It is legacy and optional; SUPABASE_URL is what
                    # verification actually needs now, and it must still be
                    # a hard startup failure.
                    #
                    # SUPABASE_SERVICE_KEY is deliberately absent too, for a
                    # different reason: NOTHING READS IT. The API reaches
                    # Postgres directly over libpq (db.connect, DATABASE_URL)
                    # and verifies browser tokens against the project JWKS —
                    # it never calls PostgREST or the Auth Admin API, which
                    # are the only things a service-role key is for. Making a
                    # never-used credential mandatory pressures whoever
                    # deploys this into copying the one key that bypasses
                    # every RLS policy and every ownership check in db.py
                    # into one more system, for nothing. The field stays on
                    # Settings as the seam for a future Storage/Admin-API
                    # caller; the day something actually reads it, add it
                    # back here.
                    ("SUPABASE_URL", supabase_url),
                    ("COORDINATOR_URL", coordinator_url),
                    ("COORDINATOR_OPERATOR_TOKEN", coordinator_operator_token),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "Missing required settings while FLASHML_REQUIRE_AUTH is on: "
                    + ", ".join(missing)
                    + ". Refusing to start with auth silently disabled."
                )

            # Warn, do not refuse: enrolment still works, but every machine
            # that runs `flashnode login` against this deploy is told to
            # "Approve at: /activate" — a path with no host, printed into a
            # terminal. See the field comment above.
            if not console_url:
                logging.getLogger("flashml-cloud-api").warning(
                    "FLASHML_CONSOLE_URL is unset: device-code enrolment will "
                    "print a relative /activate path that a volunteer cannot "
                    "resolve. Set it to the console's public base URL."
                )

            # Warn, do not refuse. Half-configured mail is the case worth
            # naming: a key with no From address (or the reverse) looks
            # configured in the dashboard and sends nothing, so approvals go
            # back to being silent with no signal anywhere.
            if bool(resend_api_key) != bool(email_from):
                logging.getLogger("flashml-cloud-api").warning(
                    "Mail is half-configured: RESEND_API_KEY and EMAIL_FROM "
                    "must both be set. No approval or decline email will be "
                    "sent until they are."
                )

            # Same shape, same signal. A partly-set App reads as OFF (see
            # `github_app_configured`), so without this it is off with no
            # explanation — and the person who set two of three variables
            # is precisely the one who believes it is on.
            github_app_values = (
                github_app_id,
                github_app_slug,
                github_app_private_key,
            )
            if any(github_app_values) and not all(github_app_values):
                logging.getLogger("flashml-cloud-api").warning(
                    "The GitHub App is half-configured: GITHUB_APP_ID, "
                    "GITHUB_APP_SLUG and GITHUB_APP_PRIVATE_KEY must all be "
                    "set. Private-repo submission stays off until they are."
                )

        return settings
