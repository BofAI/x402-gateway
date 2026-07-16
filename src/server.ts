import http, { IncomingMessage, ServerResponse } from "node:http";
import { URL } from "node:url";
import type { ProviderEntry } from "./config.js";
import { endpointFor, paymentRequirements, priceUsd } from "./config.js";
import { decodeSignature, encodeRequired, encodeResponse, headers, matchRequirement, type PaymentRequirement } from "./x402.js";

class HttpError extends Error {
  constructor(
    public status: number,
    public publicMessage: string,
    message = publicMessage,
    public responseHeaders: Record<string, string> = {},
  ) {
    super(message);
  }
}

class RequestTooLargeError extends HttpError {
  constructor() {
    super(413, "request body too large");
  }
}

function positiveIntegerEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  if (!/^\d+$/.test(raw)) throw new Error(`${name} must be a positive integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${name} must be a positive integer`);
  return value;
}

const MAX_BODY_BYTES = positiveIntegerEnv("X402_GATEWAY_MAX_BODY_BYTES", 1_000_000);
const FACILITATOR_TIMEOUT_MS = positiveIntegerEnv("X402_GATEWAY_FACILITATOR_TIMEOUT_MS", 10_000);
const UPSTREAM_TIMEOUT_MS = positiveIntegerEnv("X402_GATEWAY_UPSTREAM_TIMEOUT_MS", 30_000);
const STRIP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "transfer-encoding",
  "content-length",
  "authorization",
  "proxy-authorization",
  "cookie",
  "x-api-key",
  "api-key",
  "apikey",
  "x-auth-token",
  "x-access-token",
  "x-payment",
  "payment-signature",
  "payment-required",
  "x-payment-required",
  "accept-encoding",
]);
const STRIP_RESPONSE_HEADERS = new Set([
  "connection",
  "transfer-encoding",
  "content-encoding",
  "content-length",
  "authorization",
  "proxy-authorization",
  "set-cookie",
  "payment-required",
  "x-payment-required",
  "payment-signature",
  "x-payment",
  "payment-response",
  "x-payment-response",
]);

type GatewayMetrics = {
  requests: number;
  paidRequests: number;
  verifyFailures: number;
  settleFailures: number;
  upstreamFailures: number;
};

function createMetrics(): GatewayMetrics {
  return {
    requests: 0,
    paidRequests: 0,
    verifyFailures: 0,
    settleFailures: 0,
    upstreamFailures: 0,
  };
}

async function readBody(request: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    total += buffer.length;
    if (total > MAX_BODY_BYTES) throw new RequestTooLargeError();
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

function json(response: ServerResponse, status: number, body: unknown, extraHeaders: Record<string, string> = {}): void {
  response.writeHead(status, { "content-type": "application/json", ...extraHeaders });
  response.end(JSON.stringify(body));
}

async function fetchWithTimeout(url: URL, init: RequestInit, timeoutMs: number, label = "upstream"): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw new HttpError(504, `${label} request timed out`);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function facilitatorPost(entry: ProviderEntry, path: string, body: unknown): Promise<any> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (entry.facilitatorApiKey) {
    headers["x-api-key"] = entry.facilitatorApiKey;
  }
  const response = await fetchWithTimeout(
    new URL(path, entry.facilitatorUrl),
    { method: "POST", headers, body: JSON.stringify(body) },
    FACILITATOR_TIMEOUT_MS,
    "facilitator",
  );
  const text = await response.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    logFacilitatorFailure(entry, path, response, body, { code: "invalid_json" });
    throw new HttpError(502, "facilitator returned invalid response");
  }
  if (!response.ok) {
    logFacilitatorFailure(entry, path, response, body, data);
    if (response.status === 429) {
      const retryAfter = response.headers.get("retry-after");
      throw new HttpError(
        429,
        "facilitator rate limited",
        `facilitator ${path} rate limited`,
        retryAfter ? { "retry-after": retryAfter } : {},
      );
    }
    throw new HttpError(502, "facilitator request failed", `facilitator ${path} failed: ${response.status}`);
  }
  return data;
}

function configuredPublicBaseUrl(): string | undefined {
  const value = process.env.X402_GATEWAY_PUBLIC_BASE_URL?.trim();
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("unsupported protocol");
    return `${value.replace(/\/+$/, "")}/`;
  } catch {
    throw new Error("X402_GATEWAY_PUBLIC_BASE_URL must be a valid http(s) URL");
  }
}

function resourceUrl(url: URL, publicBaseUrl?: string): string {
  const path = `${url.pathname}${url.search}`;
  if (!publicBaseUrl) return path;
  return new URL(path, publicBaseUrl).toString();
}

function logFacilitatorFailure(
  entry: ProviderEntry,
  path: string,
  response: Response,
  body: unknown,
  data: any,
): void {
  const requirement = (body as any)?.paymentRequirements;
  const nestedError = data?.error && typeof data.error === "object" ? data.error : undefined;
  const message = nestedError?.message ?? data?.message ?? data?.detail ??
    (typeof data?.error === "string" ? data.error : undefined);
  console.error(JSON.stringify({
    event: "facilitator_request_failed",
    provider: entry.config.name,
    endpoint: path,
    status: response.status,
    scheme: requirement?.scheme,
    network: requirement?.network,
    errorCode: nestedError?.code ?? data?.code,
    errorMessage: typeof message === "string" ? message.slice(0, 200) : undefined,
    retryAfter: response.headers.get("retry-after") ?? undefined,
    cfRay: response.headers.get("cf-ray") ?? undefined,
  }));
}

function isVerifySuccess(verify: any): boolean {
  return verify?.valid === true || verify?.isValid === true;
}

function isSettleSuccess(settle: any): boolean {
  return (
    settle?.success === true ||
    settle?.settled === true ||
    (typeof settle?.transaction === "string" && settle.transaction.length > 0) ||
    (typeof settle?.txHash === "string" && settle.txHash.length > 0)
  );
}

function isAdminAllowed(request: IncomingMessage): boolean {
  const token = process.env.X402_GATEWAY_ADMIN_TOKEN;
  if (!token) return process.env.X402_GATEWAY_ADMIN_ALLOW_PUBLIC === "true";
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
    if (STRIP_REQUEST_HEADERS.has(lower)) continue;
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

async function forward(metrics: GatewayMetrics, entry: ProviderEntry, request: IncomingMessage, response: ServerResponse, routePath: string, body: Buffer, paymentResponse?: unknown): Promise<void> {
  const upstream = upstreamUrl(entry, request, routePath);
  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetchWithTimeout(upstream, {
      method: request.method,
      headers: upstreamHeaders(request, entry),
      body: ["GET", "HEAD"].includes(request.method ?? "GET") ? undefined : new Uint8Array(body),
    }, UPSTREAM_TIMEOUT_MS);
  } catch (error) {
    metrics.upstreamFailures += 1;
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, "upstream request failed");
  }
  const responseHeaders: Record<string, string> = {};
  upstreamResponse.headers.forEach((value, key) => {
    if (!STRIP_RESPONSE_HEADERS.has(key.toLowerCase())) {
      responseHeaders[key] = value;
    }
  });
  if (paymentResponse) responseHeaders[headers.response] = encodeResponse(paymentResponse);
  const responseBody = Buffer.from(await upstreamResponse.arrayBuffer());
  response.writeHead(upstreamResponse.status, responseHeaders);
  response.end(responseBody);
}

export function createGatewayServer(providers: Map<string, ProviderEntry>): http.Server {
  const publicBaseUrl = configuredPublicBaseUrl();
  const metrics = createMetrics();
  return http.createServer(async (request, response) => {
    try {
      metrics.requests += 1;
      const url = new URL(request.url ?? "/", "http://local");
      if (url.pathname === "/__402/health") {
        json(response, 200, { ok: true, providers: providers.size });
        return;
      }
      if (url.pathname === "/__402/ready") {
        json(response, providers.size ? 200 : 503, { ok: providers.size > 0, providers: providers.size });
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
        if (!isAdminAllowed(request)) return json(response, 401, { error: "unauthorized" });
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
        await forward(metrics, entry, request, response, routePath, body);
        return;
      }
      const paymentHeader = request.headers[headers.signature.toLowerCase()];
      if (!paymentHeader || Array.isArray(paymentHeader)) {
        const accepts = requirements;
        const challenge = {
          x402Version: 2,
          error: "Payment required",
          resource: { url: resourceUrl(url, publicBaseUrl) },
          accepts,
        };
        json(response, 402, challenge, { [headers.required]: encodeRequired(challenge) });
        return;
      }
      let payload;
      try {
        payload = decodeSignature(paymentHeader);
      } catch {
        json(response, 400, { error: "invalid payment signature" });
        return;
      }
      const requirement = requirements.find(item => matchRequirement(payload, item));
      if (!requirement) {
        json(response, 400, { error: "payment does not match any requirement" });
        return;
      }
      const verify = await facilitatorPost(entry, "/verify", {
        paymentPayload: payload,
        paymentRequirements: requirement,
      });
      if (!isVerifySuccess(verify)) {
        metrics.verifyFailures += 1;
        json(response, 400, { error: "payment verification failed" });
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
      if (!isSettleSuccess(settle)) {
        metrics.settleFailures += 1;
        json(response, 502, { error: "settlement failed" });
        return;
      }
      metrics.paidRequests += 1;
      try {
        await forward(metrics, entry, request, response, routePath, body, settle);
      } catch (error) {
        const status = error instanceof HttpError ? error.status : 502;
        const extraHeaders = error instanceof HttpError ? error.responseHeaders : {};
        json(response, status, {
          error: "upstream failed after payment settlement",
          settled: true,
        }, {
          ...extraHeaders,
          [headers.response]: encodeResponse(settle),
        });
      }
    } catch (error) {
      if (error instanceof HttpError) {
        json(response, error.status, { error: error.publicMessage }, error.responseHeaders);
        return;
      }
      console.error(error);
      json(response, 500, { error: "internal server error" });
    }
  });
}
