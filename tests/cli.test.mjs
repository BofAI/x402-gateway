import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const cli = path.join(root, "dist", "cli.js");

function run(args, options = {}) {
  return spawnSync(process.execPath, [cli, ...args], {
    cwd: options.cwd ?? root,
    env: { ...process.env, ...(options.env ?? {}) },
    encoding: "utf8",
  });
}

function runAsync(args, options = {}) {
  return new Promise(resolve => {
    const child = spawn(process.execPath, [cli, ...args], {
      cwd: options.cwd ?? root,
      env: { ...process.env, ...(options.env ?? {}) },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.on("close", status => resolve({ status, stdout, stderr }));
  });
}

function providerFixture() {
  const dir = mkdtempSync(path.join(os.tmpdir(), "x402-gateway-cli-"));
  const providerDir = path.join(dir, "demo");
  mkdirSync(providerDir, { recursive: true });
  writeFileSync(path.join(providerDir, "provider.yml"), `name: demo-provider
forward_url: http://127.0.0.1:65535
operator:
  network: tron-nile
  recipient: TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i
  currencies:
    usd: ["USDT"]
  protocol: exact
  asset_transfer_method: permit2
endpoints:
  - method: GET
    path: /v1/ping
    metering:
      dimensions:
        - tiers:
            - price_usd: 0.000001
`);
  return dir;
}

test("help and version are available", () => {
  const help = run(["--help"]);
  assert.equal(help.status, 0);
  assert.match(help.stdout, /x402-gateway/);
  assert.match(help.stdout, /check --providers/);

  const version = run(["--version"]);
  assert.equal(version.status, 0);
  assert.match(version.stdout.trim(), /^\d+\.\d+\.\d+/);
});

test("unknown options, missing values, and invalid ports fail clearly", () => {
  const unknown = run(["--provders", "providers"]);
  assert.equal(unknown.status, 2);
  assert.match(unknown.stderr, /Unknown option/);

  const missing = run(["--providers"]);
  assert.equal(missing.status, 2);
  assert.match(missing.stderr, /requires a value/);

  const invalidPort = run(["--providers", "providers", "--port", "99999"]);
  assert.equal(invalidPort.status, 2);
  assert.match(invalidPort.stderr, /Invalid --port/);
});

test("check validates providers without starting a server", () => {
  const dir = providerFixture();
  try {
    const human = run(["check", "--providers", dir]);
    assert.equal(human.status, 0, human.stderr);
    assert.match(human.stdout, /demo-provider/);

    const json = run(["check", "--providers", dir, "--json"]);
    assert.equal(json.status, 0, json.stderr);
    assert.equal(JSON.parse(json.stdout).count, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("start prints human-friendly local URLs by default", async () => {
  const dir = providerFixture();
  const blocker = http.createServer((_request, response) => response.end("busy"));
  await new Promise(resolve => blocker.listen(0, "127.0.0.1", resolve));
  const port = blocker.address().port + 1;
  await new Promise(resolve => blocker.close(resolve));

  const child = spawn(process.execPath, [cli, "--providers", dir, "--port", String(port)], {
    cwd: root,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", chunk => { stdout += chunk; });

  try {
    for (let i = 0; i < 20; i += 1) {
      if (stdout.includes("/__402/health")) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.match(stdout, new RegExp(`http://127.0.0.1:${port}`));
    assert.match(stdout, /providers: 1 loaded/);
  } finally {
    child.kill("SIGTERM");
    await new Promise(resolve => child.on("close", resolve));
    rmSync(dir, { recursive: true, force: true });
  }
});
