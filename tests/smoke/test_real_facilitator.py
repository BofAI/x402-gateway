"""End-to-end smoke against the **real** demo facilitator.

This test stands up:
  - the demo facilitator process from `x402-demo/facilitator/main.py`
    (subprocess; loads its own `.env`, registers real TRON + BSC mechanisms,
    real facilitator signer)
  - a mock upstream (records every header)
  - our gateway, pointing at the real facilitator

And drives a real `bankofai-x402` client signature through the full pipeline.

It is **opt-in**: skipped automatically unless

  X402_LIVE_FACILITATOR=1

is set. Set it together with a populated `x402-demo/.env`. On BSC testnet the
settle path needs a funded `BSC_FACILITATOR_PRIVATE_KEY` wallet (test BNB) and
a funded `BSC_CLIENT_PRIVATE_KEY` wallet with USDT.

Running:

    cd python/x402-gateway
    X402_LIVE_FACILITATOR=1 .venv/bin/python -m pytest \\
        tests/smoke/test_real_facilitator.py -v -s

The test asserts the gateway → facilitator → on-chain settle round trip
returns a real transaction hash that is NOT the deterministic mock value
(`0x11...11`), proving the call went all the way through.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator

import httpx
import pytest
from bankofai.x402.clients import X402Client, X402HttpClient
from bankofai.x402.mechanisms.evm.exact_permit import ExactPermitEvmClientMechanism
from bankofai.x402.signers.client import EvmClientSigner
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

DEMO_DIR = Path("/Users/bobo/code/x402/x402-demo")
DEMO_ENV = DEMO_DIR / ".env"
DEMO_FACILITATOR_MAIN = DEMO_DIR / "facilitator" / "main.py"
MOCK_TX_HASH = "0x" + "11" * 32

# Skip the whole module when not explicitly enabled.
pytestmark = pytest.mark.skipif(
    os.environ.get("X402_LIVE_FACILITATOR") != "1",
    reason="set X402_LIVE_FACILITATOR=1 to enable the live-facilitator smoke",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_demo_env() -> dict[str, str]:
    """Read x402-demo/.env into a dict without exposing values to the test process
    until the subprocess explicitly inherits them."""
    if not DEMO_ENV.exists():
        pytest.skip(f"missing {DEMO_ENV}; copy x402-demo/.env.sample and fill it")

    out: dict[str, str] = {}
    for line in DEMO_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _wait_health(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {url}")


@pytest.fixture
def real_facilitator() -> Generator[dict[str, Any], None, None]:
    """Spawn the demo facilitator as a subprocess; yield {url, port, proc}."""
    demo_env = _load_demo_env()
    if not demo_env.get("BSC_FACILITATOR_PRIVATE_KEY"):
        pytest.skip("BSC_FACILITATOR_PRIVATE_KEY missing in x402-demo/.env")
    if not demo_env.get("BSC_CLIENT_PRIVATE_KEY"):
        pytest.skip("BSC_CLIENT_PRIVATE_KEY missing in x402-demo/.env")

    port = _free_port()
    env = {
        **os.environ,
        **demo_env,
        # The demo facilitator's EVM signer goes through agent_wallet, which
        # reads AGENT_WALLET_PRIVATE_KEY. Map the BSC facilitator key onto it.
        "AGENT_WALLET_PRIVATE_KEY": demo_env["BSC_FACILITATOR_PRIVATE_KEY"],
        "FACILITATOR_PORT": str(port),
        "FACILITATOR_HOST": "127.0.0.1",
    }

    # The demo facilitator imports modules relative to its own directory; cd in.
    proc = subprocess.Popen(
        [sys.executable, str(DEMO_FACILITATOR_MAIN)],
        cwd=str(DEMO_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_health(f"http://127.0.0.1:{port}/supported", timeout=45.0)
    except Exception:
        proc.kill()
        out = (proc.stdout.read() if proc.stdout else b"").decode("utf-8", "replace")
        pytest.fail(f"facilitator failed to start:\n{out[-2000:]}")

    try:
        yield {
            "url": f"http://127.0.0.1:{port}",
            "port": port,
            "proc": proc,
            "demo_env": demo_env,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def gateway_with_real_facilitator(real_facilitator, tmp_path, monkeypatch):
    """Build the gateway pointed at the real facilitator + a mock upstream."""
    from tests.smoke.conftest import ServerHandle, make_upstream_app

    monkeypatch.setenv("UPSTREAM_TOKEN", "real-facilitator-smoke-token")

    upstream_port = _free_port()
    gateway_port = _free_port()

    upstream_app, upstream_captures = make_upstream_app()
    upstream_handle = ServerHandle(upstream_app, upstream_port)
    upstream_handle.start(ready_path="/__ready")

    pay_to = real_facilitator["demo_env"].get(
        "PAY_TO_ADDRESS"
    ) or real_facilitator["demo_env"].get("BSC_PAY_TO_ADDRESS")
    # For BSC we need an EVM address. PAY_TO_ADDRESS is TRON base58; use the
    # facilitator-derived EVM address by exposing it through env / fall back to
    # whatever the demo uses for `pay_to` on its server.
    if not pay_to or not pay_to.startswith("0x"):
        # The demo's BSC server uses BSC_PAY_TO_ADDRESS for receiver
        pay_to = real_facilitator["demo_env"].get("BSC_PAY_TO_ADDRESS")
    if not pay_to:
        pytest.skip("no PAY_TO address configured for BSC in x402-demo/.env")

    provider_yml = tmp_path / "provider.yml"
    provider_yml.write_text(
        f"""\
name: live-smoke
title: "Live Smoke API"
description: "Real facilitator end-to-end smoke target"
category: data
version: v1

forward_url: http://127.0.0.1:{upstream_port}

routing:
  type: proxy
  auth:
    method: header
    key: Authorization
    prefix: "Bearer "
    value_from_env: UPSTREAM_TOKEN

operator:
  network: bsc-testnet
  currencies:
    usd: ["USDT"]
  recipient: "{pay_to}"
  scheme: exact_permit
  facilitator_url: {real_facilitator['url']}

endpoints:
  - method: GET
    path: /v1/data
    description: "Live smoke endpoint"
    metering:
      dimensions:
        - direction: usage
          unit: requests
          scale: 1
          tiers:
            - price_usd: 0.0001
"""
    )

    from bankofai.x402_gateway.server.startup import build_app

    gateway_app = build_app(provider_yml, sandbox=False, print_banners=False)
    gateway_handle = ServerHandle(gateway_app, gateway_port)
    gateway_handle.start(ready_path="/__402/health")

    try:
        yield {
            "gateway_url": f"http://127.0.0.1:{gateway_port}",
            "upstream_url": f"http://127.0.0.1:{upstream_port}",
            "upstream_captures": upstream_captures,
            "facilitator_url": real_facilitator["url"],
            "demo_env": real_facilitator["demo_env"],
            "pay_to": pay_to,
        }
    finally:
        gateway_handle.stop()
        upstream_handle.stop()


class _LocalEvmWallet:
    def __init__(self, private_key: str) -> None:
        key = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._account = Account.from_key(key)

    async def get_address(self) -> str:
        return self._account.address

    async def sign_message(self, message: bytes) -> str:
        signed = self._account.sign_message(encode_defunct(primitive=message))
        return "0x" + signed.signature.hex()

    async def sign_typed_data(self, full_data: dict) -> str:
        signed = Account.sign_message(
            encode_typed_data(full_message=full_data),
            private_key=self._account.key,
        )
        return "0x" + signed.signature.hex()

    async def sign_transaction(self, tx: dict) -> str:  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_real_facilitator_round_trip_bsc_testnet(gateway_with_real_facilitator) -> None:
    """Drive the full pipeline through the real demo facilitator.

    Assertions:
      1. Unsigned GET returns 402 + PAYMENT-REQUIRED header.
      2. Signed GET returns 200 + body + PAYMENT-RESPONSE header.
      3. The settle tx hash is a real on-chain hash (not the mock placeholder).
      4. The upstream got the injected `Authorization: Bearer ...` and was
         stripped of every client-side payment/auth header.
    """
    ctx = gateway_with_real_facilitator

    # A. Unsigned 402
    async with httpx.AsyncClient(timeout=15.0) as http:
        unsigned = await http.get(f"{ctx['gateway_url']}/providers/live-smoke/v1/data")
    assert unsigned.status_code == 402
    header = unsigned.headers.get("PAYMENT-REQUIRED") or unsigned.headers.get(
        "payment-required"
    )
    assert header
    challenge = json.loads(base64.b64decode(header).decode())
    assert challenge["x402Version"] == 2
    assert challenge["accepts"][0]["network"] == "eip155:97"
    assert challenge["accepts"][0]["scheme"] == "exact_permit"

    # B. Signed retry
    client_key = ctx["demo_env"]["BSC_CLIENT_PRIVATE_KEY"]
    wallet = _LocalEvmWallet(client_key)
    signer = EvmClientSigner(wallet)
    signer.set_address(await wallet.get_address())

    x402 = X402Client()
    x402.register("eip155:97", ExactPermitEvmClientMechanism(signer))

    async with httpx.AsyncClient(timeout=120.0) as http:
        client = X402HttpClient(http, x402)
        response = await client.get(f"{ctx['gateway_url']}/providers/live-smoke/v1/data")

    assert response.status_code == 200, response.text
    pay_response_header = response.headers.get(
        "PAYMENT-RESPONSE"
    ) or response.headers.get("payment-response")
    assert pay_response_header, "200 must carry PAYMENT-RESPONSE"
    pay_response = json.loads(base64.b64decode(pay_response_header).decode())
    assert pay_response["success"] is True

    tx_hash = pay_response.get("transaction")
    assert tx_hash and isinstance(tx_hash, str) and tx_hash.startswith("0x")
    # crucially: NOT the mock facilitator's deterministic placeholder
    assert tx_hash != MOCK_TX_HASH, (
        "tx hash matches the mock placeholder — real settle did not happen"
    )
    assert len(tx_hash) == 66, f"BSC tx hash must be 32-byte hex, got {tx_hash}"

    # D. Upstream auth injection survived through the real settle path
    captures = ctx["upstream_captures"]
    assert len(captures) == 1, f"expected 1 upstream call, got {len(captures)}"
    cap = captures[0]
    assert cap["method"] == "GET"
    assert cap["path"] == "/v1/data"
    assert cap["headers"]["authorization"] == "Bearer real-facilitator-smoke-token"
    assert "payment-signature" not in cap["headers"]
    assert "x-payment" not in cap["headers"]
    assert "payment-required" not in cap["headers"]

    # Helpful echo so the user can verify on bscscan
    print(f"\n[live-smoke] BSC testnet tx: https://testnet.bscscan.com/tx/{tx_hash}")
