"""Shared bearer-token auth for the edge, behind AUTH_MODE.

AUTH_MODE=off (default): every endpoint stays open — current dev/test
behavior, unchanged.
AUTH_MODE=token: a request must carry `Authorization: Bearer
<INTELLIOPS_AUTH_TOKEN>` to reach a protected endpoint; a missing or
mismatched token gets 401.

/health is exempt in every service, in every mode (see services/base.py),
so container healthchecks, k8s liveness/readiness probes, and the CI
compose-smoke job keep working without a token. demo-app's /metrics
(scraped by Prometheus, unauthenticated) and /work (simulated app
traffic, not a control) are exempt the same way — see services/demo_app/app.py.
Only the endpoints named in WORKPLAN.md (read, governance, and the
/break /fix /reset /reset-baseline simulation controls) are gated.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from common.config import Settings, get_settings


def _token_from_header(request: Request) -> str:
    header = request.headers.get("authorization", "")
    prefix = "Bearer "
    return header[len(prefix) :].strip() if header.startswith(prefix) else ""


def is_authorized(request: Request, settings: Settings) -> bool:
    """True if AUTH_MODE is off, or the request carries a valid token."""
    if settings.auth_mode != "token":
        return True
    token = _token_from_header(request)
    return bool(settings.auth_token) and hmac.compare_digest(token, settings.auth_token)


def require_token(request: Request) -> None:
    """FastAPI dependency: gate a single route regardless of its path.

    Use this (rather than the app-wide middleware in services/base.py) for
    services that mix protected and open routes on one app, e.g. demo-app's
    /break and /fix.
    """
    settings = get_settings()
    if not is_authorized(request, settings):
        raise HTTPException(status_code=401, detail="Unauthorized")
