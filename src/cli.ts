#!/usr/bin/env node
import { loadProviders } from "./config.js";
import { createGatewayServer } from "./server.js";

type Options = Record<string, string | boolean>;

function parseArgs(argv: string[]): Options {
  const options: Options = {};
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) options[key] = true;
    else {
      options[key] = next;
      i += 1;
    }
  }
  return options;
}

function opt(options: Options, key: string, fallback: string): string {
  const value = options[key];
  return typeof value === "string" ? value : fallback;
}

const options = parseArgs(process.argv.slice(2));
const providersPath = opt(
  options,
  "providers",
  opt(options, "provider", process.env.X402_GATEWAY_PROVIDERS_DIR || "/app/providers"),
);
const host = opt(options, "host", process.env.X402_GATEWAY_HOST || "0.0.0.0");
const port = Number(opt(options, "port", process.env.PORT || "8080"));
const providers = loadProviders(providersPath);
const server = createGatewayServer(providers);

server.listen(port, host, () => {
  process.stdout.write(JSON.stringify({ ok: true, host, port, providers: [...providers.keys()] }, null, 2) + "\n");
});
