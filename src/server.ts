import http, { IncomingMessage, ServerResponse } from "node:http";
import { URL } from "node:url";
import type { ProviderEntry } from "./config.js";
import { endpointFor, paymentRequirements, priceUsd } from "./config.js";
import { decodeSignature, encodeRequired, encodeResponse, headers, matchRequirement, type PaymentRequirement } from "./x402.js";

const metrics = {
  requests: 0,
  paidRequests: 0,
  verifyFailures: 0,
  settleFailures: 0,
  upstreamFailures: 0,
};

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

function json(response: ServerResponse, status: number, body: unknown, extraHeaders: Record<string, string> = {}): void {
  response.writeHead(status, { "content-type": "application/json", ...extraHeaders });
  response.end(JSON.stringify(body));
}

async function facilitatorPost(entry: ProviderEntry, path: string, body: unknown): Promise<any> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (entry.facilitatorApiKey) {
    headers.authorization = `Bearer ${entry.facilitatorApiKey}`;
  }
  const response = await fetch(new URL(path, entry.facilitatorUrl), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`facilitator ${path} failed: ${response.status} ${text}`);
  return data;
}

async function attachFeeQuotes(entry: ProviderEntry, requirements: PaymentRequirement[], context?: unknown): Promise<PaymentRequirement[]> {
  try {
    const quotes = await facilitatorPost(entry, "/fee_quote", {
      paymentRequirements: requirements,
      context,
    });
    const list = Array.isArray(quotes) ? quotes : quotes.quotes ?? quotes.fees ?? [];
    return requirements.map(requirement => {
      const quote = list.find((item: any) =>
        item.scheme === requirement.scheme &&
        item.network === requirement.network &&
        String(item.asset).toLowerCase() === requirement.asset.toLowerCase(),
      );
      return quote?.fee ? { ...requirement, extra: { ...requirement.extra, fee: quote.fee } } : requirement;
    });
  } catch {
    return requirements;
  }
}

function isAdminAllowed(request: IncomingMessage): boolean {
  const token = process.env.X402_GATEWAY_ADMIN_TOKEN;
  if (!token) return true;
  const auth = request.headers.authorization ?? "";
  return auth === `Bearer ${token}`;
}

function requestParams(url: URL, body: Buffer, request: IncomingMessage): Record<string, string> {
  const params: Record<string, string> = {};
  url.searchParams.forEach((value, key) => { params[key] = value; });
  const contentType = String(request.headers["content-type"] ?? "").split(";", 1)[0].toLowerCase();
  try {
    if (body.length && contentType === "application/json") {
      const parsed = JSON.parse(body.toString("utf8"));
      if (parsed && typeof parsed === "object") {
        for (const [key, value] of Object.entries(parsed)) {
          if (["string", "number", "boolean"].includes(typeof value)) params[key] = String(value);
        }
      }
    } else if (body.length && contentType === "application/x-www-form-urlencoded") {
      new URLSearchParams(body.toString("utf8")).forEach((value, key) => { params[key] = value; });
    }
  } catch {
    // Metering variants are advisory; malformed bodies just do not add params.
  }
  return params;
}

function upstreamHeaders(request: IncomingMessage, entry: ProviderEntry): Headers {
  const headersOut = new Headers();
  for (const [key, value] of Object.entries(request.headers)) {
    if (!value) continue;
    const lower = key.toLowerCase();
    if (["host", "connection", "content-length", "authorization", "payment-signature"].includes(lower)) continue;
    headersOut.set(key, Array.isArray(value) ? value.join(",") : value);
  }
  const auth = entry.config.routing?.auth;
  if (auth) {
    const value = auth.value ?? (auth.value_from_env ? process.env[auth.value_from_env] : undefined);
    if (value && ["header", "access_token", "oauth2", undefined].includes(auth.method)) {
      headersOut.set(auth.key ?? "Authorization", `${auth.prefix ?? ""}${value}`);
    }
  }
  return headersOut;
}

function upstreamUrl(entry: ProviderEntry, request: IncomingMessage, routePath: string): URL {
  const sourceUrl = new URL(request.url ?? "/", "http://local");
  const upstream = new URL(routePath + (sourceUrl.search || ""), entry.config.forward_url);
  const auth = entry.config.routing?.auth;
  const value = auth?.value ?? (auth?.value_from_env ? process.env[auth.value_from_env] : undefined);
  if (auth?.method === "query_param" && value) {
    upstream.searchParams.set(auth.param ?? auth.key ?? "api_key", value);
  }
  return upstream;
}

async function forward(entry: ProviderEntry, request: IncomingMessage, response: ServerResponse, routePath: string, body: Buffer, paymentResponse?: unknown): Promise<void> {
  const upstream = upstreamUrl(entry, request, routePath);
  const upstreamResponse = await fetch(upstream, {
    method: request.method,
    headers: upstreamHeaders(request, entry),
    body: ["GET", "HEAD"].includes(request.method ?? "GET") ? undefined : new Uint8Array(body),
  });
  const responseHeaders: Record<string, string> = {};
  upstreamResponse.headers.forEach((value, key) => {
    if (!["connection", "transfer-encoding", "content-encoding", "content-length"].includes(key.toLowerCase())) {
      responseHeaders[key] = value;
    }
  });
  if (paymentResponse) responseHeaders[headers.response] = encodeResponse(paymentResponse);
  response.writeHead(upstreamResponse.status, responseHeaders);
  response.end(Buffer.from(await upstreamResponse.arrayBuffer()));
}

export function createGatewayServer(providers: Map<string, ProviderEntry>): http.Server {
  return http.createServer(async (request, response) => {
    try {
      metrics.requests += 1;
      const url = new URL(request.url ?? "/", "http://local");
      if (url.pathname === "/__402/health") {
        json(response, 200, { ok: true, providers: providers.size });
        return;
      }
      if (url.pathname === "/__402/ready") {
        json(response, 200, { ok: true, providers: providers.size });
        return;
      }
      if (url.pathname === "/__402/providers") {
        if (!isAdminAllowed(request)) return json(response, 401, { error: "unauthorized" });
        json(response, 200, { providers: [...providers.values()].map(entry => ({
          name: entry.config.name,
          title: entry.config.title,
          network: entry.config.operator.network,
          facilitatorUrl: entry.facilitatorUrl,
          endpoints: entry.config.endpoints?.length ?? 0,
        })) });
        return;
      }
      if (url.pathname === "/__402/endpoints") {
        if (!isAdminAllowed(request)) return json(response, 401, { error: "unauthorized" });
        json(response, 200, { endpoints: [...providers.values()].flatMap(entry =>
          (entry.config.endpoints ?? []).map(endpoint => ({
            provider: entry.config.name,
            method: endpoint.method,
            path: endpoint.path,
            priceUsd: priceUsd(endpoint),
            network: entry.config.operator.network,
          })),
        ) });
        return;
      }
      if (url.pathname === "/metrics") {
        response.writeHead(200, { "content-type": "text/plain; version=0.0.4" });
        response.end([
          `x402_gateway_requests_total ${metrics.requests}`,
          `x402_gateway_paid_requests_total ${metrics.paidRequests}`,
          `x402_gateway_verify_failures_total ${metrics.verifyFailures}`,
          `x402_gateway_settle_failures_total ${metrics.settleFailures}`,
          `x402_gateway_upstream_failures_total ${metrics.upstreamFailures}`,
          "",
        ].join("\n"));
        return;
      }
      const match = url.pathname.match(/^\/providers\/([^/]+)(\/.*)$/);
      if (!match) {
        json(response, 404, { error: "not found" });
        return;
      }
      const entry = providers.get(match[1]);
      if (!entry) {
        json(response, 404, { error: "provider not found" });
        return;
      }
      const routePath = match[2];
      const endpoint = endpointFor(entry.config, request.method ?? "GET", routePath);
      if (!endpoint) {
        json(response, 404, { error: "endpoint not found" });
        return;
      }
      const body = await readBody(request);
      const requirements = paymentRequirements(entry.config, priceUsd(endpoint, requestParams(url, body, request)));
      if (!requirements.length) {
        await forward(entry, request, response, routePath, body);
        return;
      }
      const paymentHeader = request.headers[headers.signature.toLowerCase()];
      if (!paymentHeader || Array.isArray(paymentHeader)) {
        const accepts = await attachFeeQuotes(entry, requirements);
        const challenge = {
          x402Version: 2,
          error: "Payment required",
          resource: { url: url.pathname },
          accepts,
        };
        json(response, 402, challenge, { [headers.required]: encodeRequired(challenge) });
        return;
      }
      const payload = decodeSignature(paymentHeader);
      const requirement = requirements.find(item => matchRequirement(payload, item));
      if (!requirement) {
        json(response, 400, { error: "payment does not match any requirement" });
        return;
      }
      const verify = await facilitatorPost(entry, "/verify", {
        paymentPayload: payload,
        paymentRequirements: requirement,
      });
      if (verify?.valid === false || verify?.isValid === false) {
        metrics.verifyFailures += 1;
        json(response, 400, { error: "payment verification failed", verify });
        return;
      }
      let settle;
      try {
        settle = await facilitatorPost(entry, "/settle", {
          paymentPayload: payload,
          paymentRequirements: requirement,
        });
      } catch (error) {
        metrics.settleFailures += 1;
        throw error;
      }
      metrics.paidRequests += 1;
      await forward(entry, request, response, routePath, body, settle);
    } catch (error) {
      json(response, 500, { error: error instanceof Error ? error.message : String(error) });
    }
  });
}
