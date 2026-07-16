import assert from "node:assert/strict";
import http from "node:http";
import { afterEach, beforeEach, test } from "node:test";
import { encodePaymentSignatureHeader } from "@bankofai/x402-core/http";
import { createGatewayServer } from "../dist/server.js";
import { paymentRequirements } from "../dist/config.js";
import { normalizeNetwork, toSmallestUnit } from "../dist/tokens.js";

let servers = [];
let oldAdminToken;
let oldPublicBaseUrl;

function listen(server) {
  return new Promise(resolve => {
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

async function startUpstream() {
  let hits = 0;
  const server = http.createServer((_request, response) => {
    hits += 1;
    json(response, 200, { ok: true });
  });
  const port = await listen(server);
  servers.push(server);
  return { url: `http://127.0.0.1:${port}`, hits: () => hits };
}

async function startFacilitator(handlers = {}) {
  const server = http.createServer((request, response) => {
    const handler = handlers[request.url];
    if (handler) return handler(request, response);
    json(response, 200, {});
  });
  const port = await listen(server);
  servers.push(server);
  return `http://127.0.0.1:${port}`;
}

async function startGateway({ facilitatorUrl, upstreamUrl, facilitatorApiKey, network = "eip155:56", recipient = "0x7bac3352Bc5F342DcaFA573749aA4502CB12dA86", scheme = "exact" }) {
  const entry = {
    facilitatorUrl,
    facilitatorApiKey,
    config: {
      name: "paid-provider",
      forward_url: upstreamUrl,
      operator: {
        network,
        recipient,
        scheme,
        valid_for_seconds: 300,
      },
      endpoints: [
        {
          method: "GET",
          path: "/price/{asset}",
          metering: {
            dimensions: [{ tiers: [{ price_usd: 0.000001 }] }],
          },
        },
      ],
    },
  };
  const server = createGatewayServer(new Map([[entry.config.name, entry]]));
  const port = await listen(server);
  servers.push(server);
  return `http://127.0.0.1:${port}`;
}

beforeEach(() => {
  oldAdminToken = process.env.X402_GATEWAY_ADMIN_TOKEN;
  oldPublicBaseUrl = process.env.X402_GATEWAY_PUBLIC_BASE_URL;
  process.env.X402_GATEWAY_ADMIN_TOKEN = "test-admin";
});

afterEach(async () => {
  if (oldAdminToken === undefined) delete process.env.X402_GATEWAY_ADMIN_TOKEN;
  else process.env.X402_GATEWAY_ADMIN_TOKEN = oldAdminToken;
  if (oldPublicBaseUrl === undefined) delete process.env.X402_GATEWAY_PUBLIC_BASE_URL;
  else process.env.X402_GATEWAY_PUBLIC_BASE_URL = oldPublicBaseUrl;
  await Promise.all(servers.map(server => new Promise(resolve => server.close(resolve))));
  servers = [];
});

test("amount conversion handles tiny decimal prices without producing zero", () => {
  assert.equal(toSmallestUnit(0.000001, 18), "1000000000000");
  assert.equal(toSmallestUnit(1e-7, 6), "1");
  assert.equal(toSmallestUnit("0.000000000000000001", 18), "1");
});

test("legacy TRON aliases are rejected in favor of canonical CAIP-2 IDs", () => {
  assert.throws(() => normalizeNetwork("tron:nile"), /use tron:0xcd8690dc/);
  assert.throws(() => normalizeNetwork("tron-nile"), /use tron:0xcd8690dc/);
  assert.throws(() => normalizeNetwork("tron:mainnet"), /use tron:0x2b6653dc/);
  assert.throws(() => normalizeNetwork("tron:shasta"), /use tron:0x94a9059e/);
});

test("TRON GasFree providers emit exact_gasfree requirements without Permit2 metadata", () => {
  const requirements = paymentRequirements({
    name: "gasfree-provider",
    forward_url: "https://example.com",
    operator: {
      network: "tron:0xcd8690dc",
      recipient: "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i",
      scheme: "exact_gasfree",
      currencies: { usd: ["USDT"] },
    },
    endpoints: [],
  }, 0.000001);

  assert.equal(requirements[0].scheme, "exact_gasfree");
  assert.equal(requirements[0].network, "tron:0xcd8690dc");
  assert.deepEqual(requirements[0].extra, {});
});

test("TRON providers can advertise Exact Permit2 and GasFree together", () => {
  const requirements = paymentRequirements({
    name: "dual-tron-provider",
    forward_url: "https://example.com",
    operator: {
      network: "tron:0x2b6653dc",
      recipient: "TLXPgJVJFgL97gc49j8w8kC22mDTpH9EGa",
      schemes: ["exact", "exact_gasfree"],
      currencies: { usd: ["USDT"] },
    },
    endpoints: [],
  }, 0.000001);

  assert.deepEqual(requirements.map(requirement => requirement.scheme), ["exact", "exact_gasfree"]);
  assert.deepEqual(requirements[0].extra, { assetTransferMethod: "permit2" });
  assert.deepEqual(requirements[1].extra, {});
});

test("admin endpoints and metrics require the admin token", async () => {
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator();
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });

  assert.equal((await fetch(`${gatewayUrl}/__402/providers`)).status, 401);
  assert.equal((await fetch(`${gatewayUrl}/metrics`)).status, 401);
  assert.equal((await fetch(`${gatewayUrl}/__402/providers`, {
    headers: { authorization: "Bearer test-admin" },
  })).status, 200);
});

test("unpaid requests return a payment challenge", async () => {
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator();
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`);
  const body = await response.json();

  assert.equal(response.status, 402);
  assert.equal(body.accepts[0].network, "eip155:56");
  assert.equal(body.accepts[0].extra.assetTransferMethod, "permit2");
  assert.equal(upstream.hits(), 0);
});

test("public base URL produces an absolute challenge resource URL", async () => {
  process.env.X402_GATEWAY_PUBLIC_BASE_URL = "https://tm-x402-gateway.bankofai.io/";
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator();
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt?source=qa`);
  const body = await response.json();

  assert.equal(response.status, 402);
  assert.equal(
    body.resource.url,
    "https://tm-x402-gateway.bankofai.io/providers/paid-provider/price/usdt?source=qa",
  );
});

test("GasFree challenges omit legacy facilitator fee quotes", async () => {
  const upstream = await startUpstream();
  let feeQuoteRequests = 0;
  const facilitatorUrl = await startFacilitator({
    "/fee_quote": (_request, response) => {
      feeQuoteRequests += 1;
      json(response, 500, { error: "legacy endpoint must not be called" });
    },
  });
  const gatewayUrl = await startGateway({
    facilitatorUrl,
    upstreamUrl: upstream.url,
    network: "tron:0xcd8690dc",
    recipient: "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i",
    scheme: "exact_gasfree",
  });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`);
  const body = await response.json();

  assert.equal(response.status, 402);
  assert.equal(body.accepts[0].scheme, "exact_gasfree");
  assert.equal(body.accepts[0].extra.fee, undefined);
  assert.equal(body.accepts[0].extra.assetTransferMethod, undefined);
  assert.equal(feeQuoteRequests, 0);
});

test("invalid payment signatures are rejected as client errors", async () => {
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator();
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`, {
    headers: { "PAYMENT-SIGNATURE": "not-base64-json" },
  });

  assert.equal(response.status, 400);
  assert.equal(upstream.hits(), 0);
});

test("facilitator verify must explicitly succeed before forwarding", async () => {
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator({
    "/verify": (_request, response) => json(response, 200, {}),
  });
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });
  const signature = encodePaymentSignatureHeader({
    accepted: {
      scheme: "exact",
      network: "eip155:56",
      amount: "1000000000000",
      asset: "0x55d398326f99059fF775485246999027B3197955",
      payTo: "0x7bac3352Bc5F342DcaFA573749aA4502CB12dA86",
    },
    signature: "test",
  });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`, {
    headers: { "PAYMENT-SIGNATURE": signature },
  });

  assert.equal(response.status, 400);
  assert.equal(upstream.hits(), 0);
});

test("facilitator failures log status and routing metadata without payment payloads", async () => {
  const upstream = await startUpstream();
  const facilitatorUrl = await startFacilitator({
    "/verify": (_request, response) => {
      response.writeHead(429, { "content-type": "application/json", "retry-after": "36" });
      response.end(JSON.stringify({
        error: { code: "RATE_LIMITED", message: "try again later" },
      }));
    },
  });
  const gatewayUrl = await startGateway({ facilitatorUrl, upstreamUrl: upstream.url });
  const signature = encodePaymentSignatureHeader({
    accepted: {
      scheme: "exact",
      network: "eip155:56",
      amount: "1000000000000",
      asset: "0x55d398326f99059fF775485246999027B3197955",
      payTo: "0x7bac3352Bc5F342DcaFA573749aA4502CB12dA86",
    },
    signature: "sensitive-test-signature",
  });
  const messages = [];
  const originalError = console.error;
  console.error = message => messages.push(String(message));
  try {
    const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`, {
      headers: { "PAYMENT-SIGNATURE": signature },
    });
    assert.equal(response.status, 429);
    assert.equal(response.headers.get("retry-after"), "36");
    assert.deepEqual(await response.json(), { error: "facilitator rate limited" });
  } finally {
    console.error = originalError;
  }

  assert.equal(messages.length, 1);
  const log = JSON.parse(messages[0]);
  assert.deepEqual({
    event: log.event,
    provider: log.provider,
    endpoint: log.endpoint,
    status: log.status,
    scheme: log.scheme,
    network: log.network,
    errorCode: log.errorCode,
  }, {
    event: "facilitator_request_failed",
    provider: "paid-provider",
    endpoint: "/verify",
    status: 429,
    scheme: "exact",
    network: "eip155:56",
    errorCode: "RATE_LIMITED",
  });
  assert.equal(messages[0].includes("sensitive-test-signature"), false);
  assert.equal(upstream.hits(), 0);
});

test("facilitator API keys use the X-API-KEY header", async () => {
  const upstream = await startUpstream();
  const receivedKeys = [];
  const facilitatorUrl = await startFacilitator({
    "/verify": (request, response) => {
      receivedKeys.push(request.headers["x-api-key"]);
      assert.equal(request.headers.authorization, undefined);
      json(response, 200, { valid: true });
    },
    "/settle": (request, response) => {
      receivedKeys.push(request.headers["x-api-key"]);
      assert.equal(request.headers.authorization, undefined);
      json(response, 200, { success: true, transaction: "test-transaction" });
    },
  });
  const gatewayUrl = await startGateway({
    facilitatorUrl,
    upstreamUrl: upstream.url,
    facilitatorApiKey: "secret-facilitator-key",
  });
  const signature = encodePaymentSignatureHeader({
    accepted: {
      scheme: "exact",
      network: "eip155:56",
      amount: "1000000000000",
      asset: "0x55d398326f99059fF775485246999027B3197955",
      payTo: "0x7bac3352Bc5F342DcaFA573749aA4502CB12dA86",
    },
    signature: "test",
  });

  const response = await fetch(`${gatewayUrl}/providers/paid-provider/price/usdt`, {
    headers: { "PAYMENT-SIGNATURE": signature },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(receivedKeys, ["secret-facilitator-key", "secret-facilitator-key"]);
});
