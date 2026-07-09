#!/usr/bin/env node
import fs from "node:fs";
import { loadProviders } from "./config.js";
import { createGatewayServer } from "./server.js";

type Options = Record<string, string | boolean>;
type ParsedArgs = { command: string; options: Options };

class CliError extends Error {
  constructor(message: string, public exitCode = 2) {
    super(message);
  }
}

const VALUE_OPTIONS = new Set(["provider", "providers", "host", "port"]);
const BOOLEAN_OPTIONS = new Set(["debug", "help", "json", "quiet", "version"]);
const ALL_OPTIONS = new Set([...VALUE_OPTIONS, ...BOOLEAN_OPTIONS]);

function parseArgs(argv: string[]): ParsedArgs {
  const explicitCommand = Boolean(argv[0] && !argv[0].startsWith("-"));
  const command = explicitCommand ? argv[0] : "start";
  const rest = explicitCommand ? argv.slice(1) : argv;
  const options: Options = {};
  if (command !== "start" && command !== "check") {
    throw new CliError(`Unknown command: ${command}`);
  }
  for (let i = 0; i < rest.length; i += 1) {
    const item = rest[i];
    if (item === "-h") {
      options.help = true;
      continue;
    }
    if (item === "-v" || item === "-V") {
      options.version = true;
      continue;
    }
    if (!item.startsWith("--")) throw new CliError(`Unexpected argument: ${item}`);
    const eq = item.indexOf("=");
    const key = eq > 2 ? item.slice(2, eq) : item.slice(2);
    if (!ALL_OPTIONS.has(key)) throw new CliError(`Unknown option: --${key}`);
    if (BOOLEAN_OPTIONS.has(key)) {
      if (eq > 2) throw new CliError(`Option --${key} does not take a value`);
      options[key] = true;
      continue;
    }
    const inline = eq > 2 ? item.slice(eq + 1) : undefined;
    const next = rest[i + 1];
    if (inline !== undefined) {
      if (!inline) throw new CliError(`Option --${key} requires a value`);
      options[key] = inline;
    } else {
      if (!next || next.startsWith("--")) throw new CliError(`Option --${key} requires a value`);
      options[key] = next;
      i += 1;
    }
  }
  return { command, options };
}

function opt(options: Options, key: string, fallback?: string): string | undefined {
  const value = options[key];
  return typeof value === "string" ? value : fallback;
}

function flag(options: Options, key: string): boolean {
  return options[key] === true;
}

function version(): string {
  try {
    const url = new URL("../package.json", import.meta.url);
    return String(JSON.parse(fs.readFileSync(url, "utf8")).version ?? "0.0.0");
  } catch {
    return "0.0.0";
  }
}

function help(): string {
  return `x402-gateway ${version()}

Usage:
  x402-gateway start --providers <dir> [options]
  x402-gateway --providers <dir> [options]
  x402-gateway check --providers <dir>
  x402-gateway check --provider <file>

Commands:
  start                  Start the x402 gateway server (default)
  check                  Validate provider YAML and print a summary

Options:
  --provider <file>      Load one provider YAML file
  --providers <dir>      Load provider.yml/provider.yaml files from a directory
  --host <host>          Bind host (default: 127.0.0.1)
  --port <port>          Bind port (default: 8080)
  --json                 Print machine-readable JSON
  --quiet                Suppress startup/shutdown messages
  --debug                Print stack traces for startup errors
  -h, --help             Show help
  -v, -V, --version      Show version

Examples:
  x402-gateway --provider examples/provider.yml --host 127.0.0.1 --port 4020
  x402-gateway start --providers providers --port 4020
  x402-gateway check --providers providers --json
`;
}

function providerPath(options: Options): string {
  const source = opt(options, "providers", opt(options, "provider", process.env.X402_GATEWAY_PROVIDERS_DIR));
  if (!source) throw new CliError("No provider source configured. Use --provider <file> or --providers <dir>.");
  return source;
}

function parsePort(value: string | undefined): number {
  const raw = value ?? process.env.PORT ?? "8080";
  if (!/^\d+$/.test(raw)) throw new CliError(`Invalid --port ${raw}; expected an integer between 1 and 65535`);
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new CliError(`Invalid --port ${raw}; expected an integer between 1 and 65535`);
  }
  return port;
}

function publicHost(host: string): string {
  return host === "0.0.0.0" || host === "::" ? "127.0.0.1" : host;
}

function printStartup(options: Options, host: string, port: number, providers: string[]): void {
  if (flag(options, "quiet")) return;
  if (flag(options, "json")) {
    process.stdout.write(JSON.stringify({ ok: true, host, port, providers }, null, 2) + "\n");
    return;
  }
  const base = `http://${publicHost(host)}:${port}`;
  process.stdout.write(`x402-gateway listening on ${base}\n`);
  process.stdout.write(`providers: ${providers.length} loaded\n`);
  process.stdout.write(`health: ${base}/__402/health\n`);
  process.stdout.write(`ready: ${base}/__402/ready\n`);
}

function check(options: Options): void {
  const source = providerPath(options);
  const providers = loadProviders(source);
  const summary = [...providers.values()].map(entry => ({
    name: entry.config.name,
    network: entry.config.operator.network,
    endpoints: entry.config.endpoints?.length ?? 0,
  }));
  if (flag(options, "json")) {
    process.stdout.write(JSON.stringify({ ok: true, source, count: summary.length, providers: summary }, null, 2) + "\n");
    return;
  }
  process.stdout.write(`ok: ${summary.length} provider${summary.length === 1 ? "" : "s"} loaded from ${source}\n`);
  for (const item of summary) {
    process.stdout.write(`  ${item.name} (${item.network}) endpoints=${item.endpoints}\n`);
  }
}

async function start(options: Options): Promise<void> {
  const source = providerPath(options);
  const host = opt(options, "host", process.env.X402_GATEWAY_HOST || "127.0.0.1")!;
  const port = parsePort(opt(options, "port"));
  const providers = loadProviders(source);
  const server = createGatewayServer(providers);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => {
      server.off("error", reject);
      printStartup(options, host, port, [...providers.keys()]);
      resolve();
    });
  });
  const shutdown = (signal: NodeJS.Signals) => {
    if (!flag(options, "quiet") && !flag(options, "json")) process.stderr.write(`received ${signal}, shutting down...\n`);
    server.close(() => {
      if (!flag(options, "quiet") && !flag(options, "json")) process.stderr.write("server closed\n");
      process.exit(0);
    });
  };
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

async function main(): Promise<void> {
  const parsed = parseArgs(process.argv.slice(2));
  if (flag(parsed.options, "help")) {
    process.stdout.write(help());
    return;
  }
  if (flag(parsed.options, "version")) {
    process.stdout.write(`${version()}\n`);
    return;
  }
  if (parsed.command === "check") check(parsed.options);
  else await start(parsed.options);
}

main().catch(error => {
  const parsed = (() => {
    try {
      return parseArgs(process.argv.slice(2));
    } catch {
      return { options: {} as Options };
    }
  })();
  const message = error instanceof Error ? error.message : String(error);
  if (flag(parsed.options, "json")) {
    process.stdout.write(JSON.stringify({ ok: false, error: { message } }, null, 2) + "\n");
  } else {
    process.stderr.write(`x402-gateway: ${message}\n`);
    process.stderr.write("Run `x402-gateway --help` for usage.\n");
    if (flag(parsed.options, "debug") && error instanceof Error && error.stack) {
      process.stderr.write(`${error.stack}\n`);
    }
  }
  process.exit(error instanceof CliError ? error.exitCode : 1);
});
