import assert from "node:assert/strict";
import http from "node:http";
import { afterEach, test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { encodePaymentSignatureHeader, decodePaymentResponseHeader } from "@x402/core/http";

const servers = [];
let moduleId = 0;
const ordinaryTimeout = 40;
const responseDelay = 160;
const resourceTimeout = 800;

function json(response, status, value) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}
async function start(server) {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  return `http://127.0.0.1:${server.address().port}`;
}
async function serverModule(environment = {}) {
  const names = ["X402_GATEWAY_FACILITATOR_TIMEOUT_MS", "X402_GATEWAY_RESOURCE_SPONSORING_TIMEOUT_MS",
    "X402_GATEWAY_GASFREE_TIMEOUT_MS"];
  const old = Object.fromEntries(names.map(name => [name, process.env[name]]));
  try {
    for (const name of names) {
      if (environment[name] === undefined) delete process.env[name];
      else process.env[name] = String(environment[name]);
    }
    return await import(`../dist/server.js?resource-timeout-${moduleId++}`);
  } finally {
    for (const name of names) {
      if (old[name] === undefined) delete process.env[name];
      else process.env[name] = old[name];
    }
  }
}

// The facilitator is an actual delayed HTTP peer. Payment validation is its
// responsibility; these tests isolate Gateway's timeout and routing contract.
async function fixture({ enabled = true, extension = true, network = "tron:3448148188", scheme = "exact",
  delayMs = responseDelay, delayBody = false, operator = {},
  delayStage = "/settle", environment = { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout,
    X402_GATEWAY_RESOURCE_SPONSORING_TIMEOUT_MS: resourceTimeout } } = {}) {
  const { createGatewayServer } = await serverModule(environment);
  const calls = [];
  let upstreamHits = 0;
  const facilitatorUrl = await start(http.createServer(async (request, response) => {
    calls.push(request.url);
    const result = request.url === "/verify" ? { isValid: true }
      : { success: true, transaction: "offline-settlement", network };
    if (delayBody && request.url === delayStage) {
      response.writeHead(200, { "content-type": "application/json" });
      response.flushHeaders();
      await delay(delayMs);
      response.end(JSON.stringify(result));
    } else {
      if (request.url === delayStage) await delay(delayMs);
      json(response, 200, result);
    }
  }));
  const upstreamUrl = await start(http.createServer((_request, response) => {
    upstreamHits++;
    json(response, 200, { delivered: true });
  }));
  const tron = network.startsWith("tron:");
  const accepted = { scheme, network, amount: tron ? "1000" : "1000000000000000",
    asset: tron ? "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf" : "0x55d398326f99059fF775485246999027B3197955",
    payTo: tron ? "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i" : "0x0000000000000000000000000000000000000001",
    maxTimeoutSeconds: 300, extra: scheme === "exact" ? { assetTransferMethod: "permit2" } : {} };
  const entry = { facilitatorUrl, config: { name: "timeout", forward_url: upstreamUrl,
    operator: { network, recipient: accepted.payTo, scheme, currencies: { usd: ["USDT"] }, ...operator },
    endpoints: [{ method: "GET", path: "/paid", payment: { trc20_approval_resource_sponsoring: enabled },
      metering: { dimensions: [{ tiers: [{ price_usd: 0.001 }] }] } }] } };
  const server = createGatewayServer(new Map([[entry.config.name, entry]]));
  const gatewayUrl = await start(server);
  const payload = { x402Version: 2, accepted, payload: { signature: "delegated-validation" },
    ...(extension ? { extensions: { trc20ApprovalResourceSponsoring: { info: { signedTransaction: "abcd", version: "1" } } } } : {}) };
  const request = () => fetch(`${gatewayUrl}/providers/timeout/paid`, {
    headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(payload) },
  });
  return { server, calls, request, upstreamHits: () => upstreamHits };
}

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise(resolve => server.close(resolve))));
});

for (const delayStage of ["/verify", "/settle"]) {
  test(`enabled TRON Permit2 resource payments survive delayed ${delayStage} and deliver PAYMENT-RESPONSE`, async () => {
    const current = await fixture({ delayStage });
    const response = await current.request();
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { delivered: true });
    assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).transaction, "offline-settlement");
    assert.deepEqual(current.calls, ["/verify", "/settle"]);
    assert.equal(current.upstreamHits(), 1);
  });
}

for (const [name, options] of [
  ["ordinary route", { enabled: false, extension: false }],
  ["disabled resource route carrying an extension", { enabled: false }],
  ["enabled route with pre-approved payment and no extension", { extension: false }],
  ["non-TRON requirement", { network: "eip155:56" }],
  ["non-TRON GasFree requirement", { network: "eip155:56", scheme: "exact_gasfree" }],
]) {
  test(`${name} retains the ordinary facilitator timeout`, async () => {
    const current = await fixture(options);
    const response = await current.request();
    assert.equal(response.status, 504);
    assert.deepEqual(await response.json(), { error: "facilitator request timed out" });
    assert.equal(current.upstreamHits(), 0);
  });
}

test("resource default survives a short explicit global timeout", async () => {
  const current = await fixture({ environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout } });
  assert.equal((await current.request()).status, 200);
  assert.equal(current.upstreamHits(), 1);
});

test("an explicit resource timeout overrides a larger global timeout", async () => {
  const current = await fixture({ environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: resourceTimeout,
    X402_GATEWAY_RESOURCE_SPONSORING_TIMEOUT_MS: ordinaryTimeout } });
  assert.equal((await current.request()).status, 504);
  assert.equal(current.upstreamHits(), 0);
});

test("resource waiting does not enlarge the incoming body or header timeout windows", async () => {
  const ordinary = await fixture({ enabled: false, extension: false });
  const resource = await fixture();
  const gasfree = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree" });
  assert.equal(resource.server.requestTimeout, ordinary.server.requestTimeout);
  assert.equal(resource.server.headersTimeout, ordinary.server.headersTimeout);
  assert.equal(resource.server.timeout, 0);
  assert.equal(gasfree.server.requestTimeout, ordinary.server.requestTimeout);
  assert.equal(gasfree.server.headersTimeout, ordinary.server.headersTimeout);
  assert.equal(gasfree.server.timeout, 0);
});

for (const delayStage of ["/verify", "/settle"]) {
  test(`GasFree survives delayed ${delayStage} with a short global timeout and delivers once`, async () => {
    const current = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree", delayStage,
      environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout } });
    const response = await current.request();
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { delivered: true });
    assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).transaction, "offline-settlement");
    assert.deepEqual(current.calls, ["/verify", "/settle"]);
    assert.equal(current.upstreamHits(), 1);
  });
}

test("default GasFree delivers a settlement response taking longer than the old ten-second budget", async () => {
  const current = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree",
    delayMs: 10_200, environment: {} });
  const response = await current.request();
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { delivered: true });
  assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).transaction, "offline-settlement");
  assert.equal(current.upstreamHits(), 1);
  assert.deepEqual(current.calls, ["/verify", "/settle"]);
});

test("mixed Exact and GasFree provider uses the selected GasFree payment's waiting budget", async () => {
  const current = await fixture({ enabled: true, extension: false, scheme: "exact_gasfree",
    operator: { scheme: "exact", schemes: ["exact", "exact_gasfree"] },
    environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout } });
  assert.equal((await current.request()).status, 200);
  assert.equal(current.upstreamHits(), 1);
});

for (const delayStage of ["/verify", "/settle"]) {
  test(`explicit GasFree timeout overrides a larger global budget at ${delayStage}`, async () => {
    const current = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree", delayStage,
      environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: resourceTimeout,
        X402_GATEWAY_GASFREE_TIMEOUT_MS: ordinaryTimeout } });
    const response = await current.request();
    assert.equal(response.status, 504);
    assert.deepEqual(await response.json(), { error: "facilitator request timed out" });
    assert.equal(current.upstreamHits(), 0);
    assert.deepEqual(current.calls, delayStage === "/verify" ? ["/verify"] : ["/verify", "/settle"]);
  });
}

test("GasFree's explicit budget is independent of the resource-only override", async () => {
  const current = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree",
    environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout,
      X402_GATEWAY_RESOURCE_SPONSORING_TIMEOUT_MS: ordinaryTimeout,
      X402_GATEWAY_GASFREE_TIMEOUT_MS: resourceTimeout } });
  assert.equal((await current.request()).status, 200);
  assert.equal(current.upstreamHits(), 1);
});

test("a large GasFree override does not enlarge ordinary Exact waiting", async () => {
  const current = await fixture({ enabled: false, extension: false,
    environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: ordinaryTimeout,
      X402_GATEWAY_GASFREE_TIMEOUT_MS: resourceTimeout } });
  assert.equal((await current.request()).status, 504);
  assert.equal(current.upstreamHits(), 0);
});

test("GasFree's configured deadline covers a response body delayed after headers", async () => {
  const current = await fixture({ enabled: false, extension: false, scheme: "exact_gasfree", delayBody: true,
    environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: resourceTimeout,
      X402_GATEWAY_GASFREE_TIMEOUT_MS: ordinaryTimeout } });
  const response = await current.request();
  assert.equal(response.status, 504);
  assert.equal(current.upstreamHits(), 0);
  assert.equal(response.headers.get("PAYMENT-RESPONSE"), null);
});

for (const invalid of [0, "invalid", 2_147_483_648]) {
  test(`invalid GasFree timeout ${invalid} fails at module initialization`, async () => {
    await assert.rejects(serverModule({ X402_GATEWAY_GASFREE_TIMEOUT_MS: invalid }), /X402_GATEWAY_GASFREE_TIMEOUT_MS must/);
  });
}

test("invalid resource timeout configuration fails at module initialization", async () => {
  await assert.rejects(serverModule({ X402_GATEWAY_RESOURCE_SPONSORING_TIMEOUT_MS: 0 }), /must be an integer between 1/);
});

// Inspect real abort timer budgets while using fast HTTP responses, so defaults
// of 10 seconds / 4 minutes do not make the test suite wait for those durations.
async function captureBudgets(t, options) {
  const timeouts = [];
  const original = globalThis.setTimeout;
  t.mock.method(globalThis, "setTimeout", (callback, milliseconds, ...args) => {
    timeouts.push(milliseconds);
    return original(callback, milliseconds, ...args);
  });
  const current = await fixture({ ...options, delayStage: "/never-delay" });
  assert.equal((await current.request()).status, 200);
  return timeouts;
}

test("ordinary facilitator calls still default to 10000 milliseconds", async t => {
  const budgets = await captureBudgets(t, { enabled: false, extension: false, environment: {} });
  assert.equal(budgets.filter(value => value === 10_000).length, 2);
});

test("resource facilitator calls default to 240000 milliseconds", async t => {
  const budgets = await captureBudgets(t, { environment: {} });
  assert.equal(budgets.filter(value => value === 240_000).length, 2);
});

test("resource defaults preserve a global timeout larger than four minutes", async t => {
  const budgets = await captureBudgets(t, { environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: 300_001 } });
  assert.equal(budgets.filter(value => value === 300_001).length, 2);
});

test("GasFree defaults preserve a global timeout larger than four minutes", async t => {
  const budgets = await captureBudgets(t, { enabled: false, extension: false, scheme: "exact_gasfree",
    environment: { X402_GATEWAY_FACILITATOR_TIMEOUT_MS: 300_001 } });
  assert.equal(budgets.filter(value => value === 300_001).length, 2);
});

test("facilitator timeout covers a body delayed after headers", async () => {
  const { createGatewayServer } = await serverModule({ X402_GATEWAY_FACILITATOR_TIMEOUT_MS: 40 });
  const facilitatorUrl = await start(http.createServer(async (request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    if (request.url === "/settle") { response.flushHeaders(); await delay(160); }
    response.end(JSON.stringify(request.url === "/verify" ? { isValid: true }
      : { success: true, transaction: "offline", network: "tron:3448148188" }));
  }));
  const accepted = { scheme: "exact", network: "tron:3448148188", amount: "1000",
    asset: "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", payTo: "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i",
    maxTimeoutSeconds: 300, extra: { assetTransferMethod: "permit2" } };
  const config = { name: "body", forward_url: facilitatorUrl,
    operator: { network: accepted.network, recipient: accepted.payTo, scheme: "exact", currencies: { usd: ["USDT"] } },
    endpoints: [{ method: "GET", path: "/paid", metering: { dimensions: [{ tiers: [{ price_usd: 0.001 }] }] } }] };
  const gatewayUrl = await start(createGatewayServer(new Map([["body", { config, facilitatorUrl }]])));
  const response = await fetch(`${gatewayUrl}/providers/body/paid`, { headers: {
    "PAYMENT-SIGNATURE": encodePaymentSignatureHeader({ x402Version: 2, accepted, payload: { signature: "offline" } }),
  } });
  assert.equal(response.status, 504);
  assert.deepEqual(await response.json(), { error: "facilitator request timed out" });
});

test("upstream body timeout preserves the successful settlement response", async () => {
  const original = process.env.X402_GATEWAY_UPSTREAM_TIMEOUT_MS;
  process.env.X402_GATEWAY_UPSTREAM_TIMEOUT_MS = "40";
  let createGatewayServer;
  try { ({ createGatewayServer } = await serverModule()); }
  finally {
    if (original === undefined) delete process.env.X402_GATEWAY_UPSTREAM_TIMEOUT_MS;
    else process.env.X402_GATEWAY_UPSTREAM_TIMEOUT_MS = original;
  }
  const network = "tron:3448148188";
  const facilitatorUrl = await start(http.createServer((request, response) => json(response, 200,
    request.url === "/verify" ? { isValid: true } : { success: true, transaction: "settled-before-timeout", network })));
  const upstreamUrl = await start(http.createServer(async (_request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.flushHeaders(); await delay(160); response.end('{"delivered":true}');
  }));
  const accepted = { scheme: "exact", network, amount: "1000", asset: "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf",
    payTo: "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i", maxTimeoutSeconds: 300, extra: { assetTransferMethod: "permit2" } };
  const config = { name: "body", forward_url: upstreamUrl,
    operator: { network, recipient: accepted.payTo, scheme: "exact", currencies: { usd: ["USDT"] } },
    endpoints: [{ method: "GET", path: "/paid", metering: { dimensions: [{ tiers: [{ price_usd: 0.001 }] }] } }] };
  const gatewayUrl = await start(createGatewayServer(new Map([["body", { config, facilitatorUrl }]])));
  const response = await fetch(`${gatewayUrl}/providers/body/paid`, { headers: {
    "PAYMENT-SIGNATURE": encodePaymentSignatureHeader({ x402Version: 2, accepted, payload: { signature: "offline" } }),
  } });
  assert.equal(response.status, 504);
  assert.equal((await response.json()).settled, true);
  assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).transaction, "settled-before-timeout");
});
