"""The onboarding form: what it may contain, and nothing else.

`parse_submission` is a whitelist by construction — it reads the fields it
knows and has nowhere to put anything else. A body carrying `is_admin` or
`admitted_at` is not rejected with an error that tells an attacker the
field name exists; it is simply never read. Same discipline as
`PATCH /v1alpha1/me`, which has taken exactly one key since it shipped.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLES = frozenset({"researcher", "ml_engineer", "student", "founder", "other"})
TEAM_SIZES = frozenset({"solo", "2_5", "6_20", "20_plus"})
COMPUTE_SOURCES = frozenset({"own_machines", "colab", "runpod", "cloud", "none"})
HEARD_FROM = frozenset(
    {"github", "search", "twitter", "friend", "paper", "event", "other"}
)

NAME_MAX = 80
COMPANY_MAX = 160
USE_CASE_MAX = 2000


@dataclass(frozen=True)
class OnboardingSubmission:
    first_name: str
    last_name: str
    company_name: str
    role: str
    team_size: str
    use_case: str
    compute_sources: list[str]
    heard_from: str | None


def _required_text(payload: dict, field: str, cap: int) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise ValueError(f"{field} is required")
    value = raw.strip()
    if not value:
        raise ValueError(f"{field} is required")
    if len(value) > cap:
        raise ValueError(f"{field} is limited to {cap} characters")
    return value


def _one_of(payload: dict, field: str, allowed: frozenset[str]) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str) or raw not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return raw


def parse_submission(payload: dict) -> OnboardingSubmission:
    """Validate the form body. Raises ``ValueError`` with a message safe to
    show a user; the route turns that into a 400."""
    sources = payload.get("compute_sources", [])
    if not isinstance(sources, list):
        raise ValueError("compute_sources must be a list")
    seen: list[str] = []
    for item in sources:
        if not isinstance(item, str) or item not in COMPUTE_SOURCES:
            raise ValueError(
                "compute_sources must contain only: "
                + ", ".join(sorted(COMPUTE_SOURCES))
            )
        if item not in seen:
            seen.append(item)

    heard = payload.get("heard_from")
    if heard is not None:
        heard = _one_of(payload, "heard_from", HEARD_FROM)

    return OnboardingSubmission(
        first_name=_required_text(payload, "first_name", NAME_MAX),
        last_name=_required_text(payload, "last_name", NAME_MAX),
        company_name=_required_text(payload, "company_name", COMPANY_MAX),
        role=_one_of(payload, "role", ROLES),
        team_size=_one_of(payload, "team_size", TEAM_SIZES),
        use_case=_required_text(payload, "use_case", USE_CASE_MAX),
        compute_sources=seen,
        heard_from=heard,
    )
