import assert from "node:assert/strict";
import http from "node:http";
import { afterEach, beforeEach, test } from "node:test";
import { encodePaymentSignatureHeader } from "@bankofai/x402-core/http";
import { createGatewayServer } from "../dist/server.js";
import { toSmallestUnit } from "../dist/tokens.js";

let servers = [];
let oldAdminToken;

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

async function startGateway({ facilitatorUrl, upstreamUrl }) {
  const entry = {
    facilitatorUrl,
    config: {
      name: "paid-provider",
      forward_url: upstreamUrl,
      operator: {
        network: "eip155:56",
        recipient: "0x7bac3352Bc5F342DcaFA573749aA4502CB12dA86",
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
  process.env.X402_GATEWAY_ADMIN_TOKEN = "test-admin";
});

afterEach(async () => {
  if (oldAdminToken === undefined) delete process.env.X402_GATEWAY_ADMIN_TOKEN;
  else process.env.X402_GATEWAY_ADMIN_TOKEN = oldAdminToken;
  await Promise.all(servers.map(server => new Promise(resolve => server.close(resolve))));
  servers = [];
});

test("amount conversion handles tiny decimal prices without producing zero", () => {
  assert.equal(toSmallestUnit(0.000001, 18), "1000000000000");
  assert.equal(toSmallestUnit(1e-7, 6), "1");
  assert.equal(toSmallestUnit("0.000000000000000001", 18), "1");
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
