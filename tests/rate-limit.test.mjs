import assert from "node:assert/strict";
import { test } from "node:test";
import { FixedWindowRateLimiter } from "../dist/rate-limit.js";

test("fixed-window limiter preserves limits and retry-after", () => {
  const limiter = new FixedWindowRateLimiter(2, 60_000, 60_000);

  assert.deepEqual(limiter.consume("client", 1_000), { allowed: true, retryAfter: 60 });
  assert.deepEqual(limiter.consume("client", 2_000), { allowed: true, retryAfter: 59 });
  assert.deepEqual(limiter.consume("client", 3_000), { allowed: false, retryAfter: 58 });
});

test("fixed-window limiter removes expired one-time client entries", () => {
  const limiter = new FixedWindowRateLimiter(10, 60_000, 60_000);

  limiter.consume("one-time-a", 1_000);
  limiter.consume("one-time-b", 2_000);
  assert.equal(limiter.size, 2);

  limiter.consume("current", 61_001);
  assert.equal(limiter.size, 2);

  limiter.consume("cleanup-trigger", 121_001);
  assert.equal(limiter.size, 1);
});
