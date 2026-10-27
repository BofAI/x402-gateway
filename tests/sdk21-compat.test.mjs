import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, test } from "node:test";
import { decodePaymentRequiredHeader, decodePaymentResponseHeader, encodePaymentSignatureHeader } from "@x402/core/http";
import { x402Client } from "@x402/core/client";
import { x402Facilitator } from "@x402/core/facilitator";
import { ExactEvmScheme } from "@x402/evm/exact/client";
import { ExactEvmScheme as ExactEvmFacilitator } from "@x402/evm/exact/facilitator";
import { verifyTypedData } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { ExactGasFreeTronScheme, GasFreeAPIClient } from "@bankofai/x402-tron/gasfree";
import { ExactGasFreeTronScheme as GasFreeFacilitator } from "@bankofai/x402-tron/gasfree/facilitator";
import { createTrc20ApprovalResourceSponsoringExtension } from "@bankofai/x402-tron-extensions/trc20-approval-resource-sponsoring";
import { ExactTronScheme } from "@bankofai/x402-tron/exact/client";
import { ExactTronScheme as ExactTronFacilitator } from "@bankofai/x402-tron/exact/facilitator";
import { createTrc20ApprovalResourceSponsoringClientExtension } from "@bankofai/x402-tron-extensions/trc20-approval-resource-sponsoring/client";
import { clientSigner, facilitatorSigner, testRecipient, testTransaction, testPrivateKey, testPayer, signedApproval } from "./helpers/offline-tron.mjs";
import { loadProvider } from "../dist/config.js";
import { createGatewayServer } from "../dist/server.js";


const servers = [];
const directories = [];
const recipient = testRecipient;

async function start(server) {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  return `http://127.0.0.1:${server.address().port}`;
}
function json(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}
async function body(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString());
}
function config(upstreamUrl, enabled, operator = {}) {
  return {
    name: "compat", forward_url: upstreamUrl,
    operator: { network: "tron:3448148188", recipient, currencies: { usd: ["USDT"] }, ...operator },
    endpoints: [{ method: "GET", path: "/paid", ...(enabled === undefined ? {} : { payment: { trc20_approval_resource_sponsoring: enabled } }), metering: { dimensions: [{ tiers: [{ price_usd: 0.001 }] }] } }],
  };
}
function parseProvider(value, facilitatorUrl = "http://127.0.0.1:1") {
  const directory = mkdtempSync(path.join(os.tmpdir(), "gateway-sdk21-"));
  directories.push(directory);
  const file = path.join(directory, "provider.yaml");
  writeFileSync(file, JSON.stringify({ ...value, operator: { ...value.operator, facilitator_url: facilitatorUrl } }));
  return loadProvider(file);
}
async function gateway(value, facilitatorUrl) {
  const entry = parseProvider(value, facilitatorUrl);
  return start(createGatewayServer(new Map([[entry.config.name, entry]])));
}
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise(resolve => server.close(resolve))));
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
});

for (const enabled of [undefined, false, true]) {
  test(`resource sponsoring declaration is ${enabled === true ? "advertised" : "absent"} when route config is ${enabled}`, async () => {
    const url = await gateway(config("http://127.0.0.1:1", enabled));
    const response = await fetch(`${url}/providers/compat/paid`);
    const required = await response.json();
    assert.equal(response.status, 402);
    assert.deepEqual(decodePaymentRequiredHeader(response.headers.get("PAYMENT-REQUIRED")), required);
    assert.equal(required.accepts[0].network, "tron:3448148188");
    assert.deepEqual(required.accepts[0].extra, { assetTransferMethod: "permit2" });
    if (enabled === true) {
      const declaration = required.extensions?.trc20ApprovalResourceSponsoring;
      assert.equal(declaration?.info.version, "1");
      assert.ok(declaration.schema.required.includes("signedTransaction"));
    } else {
      assert.equal(required.extensions, undefined);
    }
  });
}

for (const [name, enabled, operator, message] of [
  ["non-boolean declaration", "yes", {}, /must be a boolean/],
  ["EVM", true, { network: "eip155:56", recipient: "0x0000000000000000000000000000000000000001" }, /requires TRON exact Permit2/],
  ["GasFree-only", true, { scheme: "exact_gasfree" }, /requires TRON exact Permit2/],
]) {
  test(`provider parser rejects resource sponsoring for ${name}`, () => {
    assert.throws(() => parseProvider(config("http://127.0.0.1:1", enabled, operator)), message);
  });
}

test("resource sponsoring supports mixed TRON Exact and GasFree requirements", async () => {
  const url = await gateway(config("http://127.0.0.1:1", true, { schemes: ["exact", "exact_gasfree"] }));
  const response = await fetch(`${url}/providers/compat/paid`);
  const required = await response.json();
  assert.deepEqual(required.accepts.map(item => item.scheme), ["exact", "exact_gasfree"]);
  assert.equal(required.extensions?.trc20ApprovalResourceSponsoring?.info.version, "1");
  assert.deepEqual(required.accepts[1].extra, {});
});

async function exactFixture({ enabled, retryOnce = false, legacy = false } = {}) {
  const received = [];
  const writes = [];
  let deliveries = 0;
  const upstreamUrl = await start(http.createServer((request, response) => {
    deliveries += 1;
    assert.equal(request.headers["payment-signature"], undefined);
    assert.equal(request.headers["x-api-key"], "provider-secret");
    assert.equal(request.headers.authorization, undefined);
    json(response, 200, { delivered: true });
  }));
  const Facilitator = ExactTronFacilitator;
  const mechanism = new Facilitator(facilitatorSigner(args => writes.push(args)));
  const facilitatorUrl = await start(http.createServer(async (request, response) => {
    try {
      const input = await body(request);
      assert.equal(input.x402Version, 2);
      received.push({ path: request.url, input });
      if (retryOnce && received.length === 1) {
        response.setHeader("retry-after", "1");
        return json(response, 429, { error: { code: "RATE_LIMITED" } });
      }
      const result = await mechanism[request.url === "/verify" ? "verify" : "settle"](input.paymentPayload, input.paymentRequirements);
      json(response, 200, result);
    } catch (error) {
      json(response, 500, { error: error.message });
    }
  }));
  const provider = config(upstreamUrl, enabled);
  provider.routing = { auth: { method: "header", key: "x-api-key", value: "provider-secret" } };
  const url = await gateway(provider, facilitatorUrl);
  const required = await fetch(`${url}/providers/compat/paid`).then(response => response.json());
  let payload;
  if (legacy) {
    payload = JSON.parse(readFileSync(new URL("./fixtures/legacy-tron-v2.json", import.meta.url))).paymentPayload;
  } else {
    const signer = clientSigner();
    const client = new x402Client().register("tron:3448148188", new ExactTronScheme(signer));
    if (enabled) client.registerExtension(createTrc20ApprovalResourceSponsoringClientExtension(signer));
    payload = await client.createPaymentPayload(required);
  }
  return { url, payload, received, writes, deliveries: () => deliveries };
}

for (const enabled of [undefined, true]) {
  test(`SDK 2.1 signed Permit2 payment completes HTTP verification and settlement with resource declaration ${enabled}`, async () => {
    const fixture = await exactFixture({ enabled });
    fixture.payload.extensions = { ...fixture.payload.extensions, futureExtension: { info: { opaque: "preserve" } } };
    const response = await fetch(`${fixture.url}/providers/compat/paid`, {
      headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(fixture.payload), authorization: "Bearer client-secret", "x-api-key": "client-secret" },
    });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { delivered: true });
    assert.equal(fixture.deliveries(), 1);
    assert.equal(fixture.writes.length, 1);
    assert.deepEqual(fixture.received.map(item => item.path), ["/verify", "/settle"]);
    for (const { input } of fixture.received) {
      assert.deepEqual(input.paymentPayload, fixture.payload);
      assert.equal(input.paymentRequirements.amount, "1000");
    }
    const settled = decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE"));
    assert.equal(settled.success, true);
    assert.equal(settled.transaction, testTransaction);
    assert.equal(settled.network, "tron:3448148188");
  });
}

for (const enabled of [undefined, true]) {
  test(`registry SDK2 signed Permit2 fixture completes SDK 2.1 payments with resource declaration ${enabled}`, async t => {
    const fixtureData = JSON.parse(readFileSync(new URL("./fixtures/legacy-tron-v2.json", import.meta.url)));
    t.mock.method(Date, "now", () => fixtureData.provenance.signedAtMs);
    const fixture = await exactFixture({ legacy: true, enabled });
    const response = await fetch(`${fixture.url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(fixture.payload) } });
    assert.equal(response.status, 200);
    assert.equal(fixture.deliveries(), 1);
    assert.equal(fixture.writes.length, 1);
    assert.deepEqual(fixture.received.map(item => item.input.paymentPayload), [fixture.payload, fixture.payload].map(value => JSON.parse(JSON.stringify(value))));
  });
}

test("SDK 2.1 invalid signature never settles or delivers the upstream resource", async () => {
  const fixture = await exactFixture();
  fixture.payload.payload.signature = `0x${"0".repeat(130)}`;
  const response = await fetch(`${fixture.url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(fixture.payload) } });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "payment verification failed" });
  assert.equal(fixture.writes.length, 0);
  assert.equal(fixture.deliveries(), 0);
});

test("a client can retry an SDK 2.1 payment after facilitator 429 without an early settlement", async () => {
  const fixture = await exactFixture({ retryOnce: true });
  const options = { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(fixture.payload) } };
  const limited = await fetch(`${fixture.url}/providers/compat/paid`, options);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get("retry-after"), "1");
  assert.equal(fixture.received.length, 1);
  assert.equal(fixture.writes.length, 0);
  assert.equal(fixture.deliveries(), 0);
  const retried = await fetch(`${fixture.url}/providers/compat/paid`, options);
  assert.equal(retried.status, 200);
  assert.equal(fixture.writes.length, 1);
  assert.equal(fixture.deliveries(), 1);
});

async function mechanismHttp(mechanism) {
  const received = [];
  const results = [];
  const url = await start(http.createServer(async (request, response) => {
    try {
      const input = await body(request);
      received.push({ path: request.url, input });
      assert.ok(["/verify", "/settle"].includes(request.url));
      const result = await mechanism[request.url.slice(1)](input.paymentPayload, input.paymentRequirements);
      results.push({ path: request.url, result });
      json(response, 200, result);
    } catch (error) {
      json(response, 500, { error: error.message });
    }
  }));
  return { url, received, results };
}

async function upstreamFixture() {
  let deliveries = 0;
  const url = await start(http.createServer((_request, response) => {
    deliveries += 1;
    json(response, 200, { delivered: true });
  }));
  return { url, deliveries: () => deliveries };
}

test("SDK 2.1 signed Approval reaches official Core unchanged over Gateway HTTP", async () => {
    const Core = x402Facilitator;
    const Scheme = ExactTronFacilitator;
    const resourceExtension = createTrc20ApprovalResourceSponsoringExtension;
    let allowance = 0n;
    let sponsorships = 0;
    let settlements = 0;
    const core = new Core().register("tron:3448148188", new Scheme({
      ...facilitatorSigner(() => {
        assert.ok(allowance >= 1000n);
        settlements += 1;
      }),
      readContract: async ({ functionName }) => functionName === "allowance" ? allowance : 1_000_000n,
    })).registerExtension(resourceExtension({
      verify: async request => {
        assert.equal(request.requiredAllowance, "1000");
        assert.equal(request.approvalTxID.length, 64);
        return { isValid: true };
      },
      sponsor: async request => {
        sponsorships += 1;
        allowance = BigInt(request.amount);
        return { success: true, approvalTransaction: request.approvalTxID };
      },
    }));
    const facilitator = await mechanismHttp(core);
    const upstream = await upstreamFixture();
    const url = await gateway(config(upstream.url, true), facilitator.url);
    const required = await fetch(`${url}/providers/compat/paid`).then(response => response.json());
    const signer = clientSigner({
      readContract: async () => allowance,
      signPermit2Approval: signedApproval,
      ensureAllowance: async () => { throw new Error("client must not broadcast self-funded approval"); },
    });
    const client = new x402Client().register("tron:3448148188", new ExactTronScheme(signer))
      .registerExtension(createTrc20ApprovalResourceSponsoringClientExtension(signer));
    const payment = await client.createPaymentPayload(required);
    assert.match(payment.extensions.trc20ApprovalResourceSponsoring.info.signedTransaction, /^[a-f0-9]+$/);
    const response = await fetch(`${url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(payment) } });
    assert.equal(response.status, 200);
    assert.equal(sponsorships, 1);
    assert.equal(settlements, 1);
    assert.equal(upstream.deliveries(), 1);
    assert.deepEqual(facilitator.received.map(item => item.input.paymentPayload), [payment, payment]);
  });

test("official EVM 2.28 Base EIP-3009 signed payment preserves Gateway delivery", async () => {
  const account = privateKeyToAccount(`0x${testPrivateKey}`);
  const writes = [];
  const mechanism = new ExactEvmFacilitator({
    getAddresses: () => [account.address],
    verifyTypedData,
    getCode: async ({ address }) => address.toLowerCase() === account.address.toLowerCase() ? "0x" : "0x6001",
    readContract: async ({ functionName }) => {
      assert.equal(functionName, "transferWithAuthorization");
    },
    writeContract: async args => { writes.push(args); return `0x${testTransaction}`; },
    waitForTransactionReceipt: async () => ({ status: "success" }),
  });
  const facilitator = await mechanismHttp(mechanism);
  const upstream = await upstreamFixture();
  const url = await gateway(config(upstream.url, undefined, { network: "base-sepolia", currencies: { usd: ["USDC"] }, recipient: account.address }), facilitator.url);
  const required = await fetch(`${url}/providers/compat/paid`).then(response => response.json());
  assert.equal(required.accepts[0].network, "eip155:84532");
  assert.deepEqual(required.accepts[0].extra, { name: "USDC", version: "2" });
  const payment = await new x402Client().register("eip155:84532", new ExactEvmScheme(account)).createPaymentPayload(required);
  const response = await fetch(`${url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(payment) } });
  assert.equal(response.status, 200);
  assert.equal(upstream.deliveries(), 1);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].functionName, "transferWithAuthorization");
  assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).network, "eip155:84532");
});

test("SDK 2.1 GasFree signed payment preserves relayer HTTP and Gateway settlement headers", async () => {
  const submitted = [];
  const relayerUrl = await start(http.createServer(async (request, response) => {
    if (request.url === `/api/v1/address/${testPayer}`) {
      return json(response, 200, { code: 200, data: { accountAddress: testPayer, gasFreeAddress: testPayer, active: true, nonce: 0, allowSubmit: true, assets: [{ tokenAddress: "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", tokenSymbol: "USDT", transferFee: "1", activateFee: "0", decimal: 6, frozen: 0 }] } });
    }
    if (request.url === "/api/v1/config/provider/all") {
      return json(response, 200, { code: 200, data: { providers: [{ address: recipient, name: "offline", icon: "", website: "", config: { maxPendingTransfer: 1, minDeadlineDuration: 55, maxDeadlineDuration: 3595, defaultDeadlineDuration: 300 } }] } });
    }
    if (request.url === "/api/v1/gasfree/submit") {
      submitted.push(await body(request));
      return json(response, 200, { code: 200, data: { id: "trace-1" } });
    }
    if (request.url === "/api/v1/gasfree/trace-1") {
      return json(response, 200, { code: 200, data: { id: "trace-1", state: "SUCCEED", txnHash: testTransaction, txnState: "SOLIDITY" } });
    }
    json(response, 404, { error: "unexpected relayer endpoint" });
  }));
  const apiClients = { "tron:3448148188": new GasFreeAPIClient(relayerUrl) };
  const facilitator = await mechanismHttp(new GasFreeFacilitator(facilitatorSigner(), apiClients));
  const upstream = await upstreamFixture();
  const url = await gateway(config(upstream.url, false, { scheme: "exact_gasfree" }), facilitator.url);
  const required = await fetch(`${url}/providers/compat/paid`).then(response => response.json());
  const client = new x402Client().register("tron:3448148188", new ExactGasFreeTronScheme(clientSigner(), { apiClients }));
  const payment = await client.createPaymentPayload(required);
  const response = await fetch(`${url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(payment) } });
  assert.equal(response.status, 200);
  assert.equal(upstream.deliveries(), 1);
  assert.equal(submitted.length, 1);
  assert.equal(submitted[0].value, "1000");
  assert.equal(submitted[0].receiver, recipient);
  assert.equal(submitted[0].sig, payment.payload.signature.slice(2));
  assert.equal(decodePaymentResponseHeader(response.headers.get("PAYMENT-RESPONSE")).transaction, testTransaction);
});

test("SDK 2.1 sponsored Approval is rejected when the facilitator has no resource runtime", async () => {
  let settlements = 0;
  const legacy = new ExactTronFacilitator({
    ...facilitatorSigner(() => { settlements += 1; }),
    readContract: async ({ functionName }) => functionName === "allowance" ? 0n : 1_000_000n,
  });
  const facilitator = await mechanismHttp(legacy);
  const upstream = await upstreamFixture();
  const url = await gateway(config(upstream.url, true), facilitator.url);
  const required = await fetch(`${url}/providers/compat/paid`).then(response => response.json());
  const signer = clientSigner({ readContract: async () => 0n, signPermit2Approval: signedApproval });
  const client = new x402Client().register("tron:3448148188", new ExactTronScheme(signer))
    .registerExtension(createTrc20ApprovalResourceSponsoringClientExtension(signer));
  const payment = await client.createPaymentPayload(required);
  assert.ok(payment.extensions.trc20ApprovalResourceSponsoring.info.signedTransaction);
  const response = await fetch(`${url}/providers/compat/paid`, { headers: { "PAYMENT-SIGNATURE": encodePaymentSignatureHeader(payment) } });
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { error: "payment verification failed" });
  assert.equal(facilitator.results[0].result.invalidReason, "sponsor_runtime_unavailable");
  assert.equal(settlements, 0);
  assert.equal(upstream.deliveries(), 0);
});
