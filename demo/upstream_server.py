"""Demo upstream API used by the local gateway integration stack."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Query

app = FastAPI(title="x402 Gateway Demo Upstream", version="0.1.0")


def _expected_token() -> str:
    return os.environ.get("DEMO_UPSTREAM_TOKEN", "demo-upstream-token")


def _require_auth(authorization: str | None) -> None:
    expected = f"Bearer {_expected_token()}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid upstream authorization")


@app.get("/health")
async def health() -> dict[str, object]:
    return {"ok": True, "service": "demo-upstream"}


@app.get("/v1/current")
async def current_weather(
    city: str = Query(default="Singapore"),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_auth(authorization)
    normalized = city.strip() or "Singapore"
    return {
        "city": normalized,
        "condition": "sunny",
        "temperatureC": 29,
        "humidityPct": 72,
        "source": "demo-upstream",
        "servedAt": datetime.now(timezone.utc).isoformat(),
    }
