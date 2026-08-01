"""Environment configuration for the FlashML Cloud API.

Fails loudly at startup when auth is required and a secret is missing,
rather than silently running open and failing at the first request. This
mirrors the coordinator's ``FLASHML_REQUIRE_NODE_AUTH`` guard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    supabase_url: str
    supabase_jwt_secret: str
    supabase_service_key: str
    coordinator_url: str
    coordinator_operator_token: str
    require_auth: bool

    @classmethod
    def from_env(cls) -> "Settings":
        require_auth = os.environ.get("FLASHML_REQUIRE_AUTH", "true").strip().lower() not in (
            "0", "false", "no", "off", "",
        )

        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        coordinator_url = os.environ.get("COORDINATOR_URL", "")
        coordinator_operator_token = os.environ.get("COORDINATOR_OPERATOR_TOKEN", "")

        settings = cls(
            supabase_url=supabase_url,
            supabase_jwt_secret=supabase_jwt_secret,
            supabase_service_key=supabase_service_key,
            coordinator_url=coordinator_url,
            coordinator_operator_token=coordinator_operator_token,
            require_auth=require_auth,
        )

        if require_auth:
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", supabase_url),
                    ("SUPABASE_JWT_SECRET", supabase_jwt_secret),
                    ("SUPABASE_SERVICE_KEY", supabase_service_key),
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

        return settings
