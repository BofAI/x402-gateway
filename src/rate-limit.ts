export type RateLimitResult = {
  allowed: boolean;
  retryAfter: number;
};

type RateEntry = { count: number; resetAt: number };

export class FixedWindowRateLimiter {
  private readonly entries = new Map<string, RateEntry>();
  private nextCleanupAt = 0;

  constructor(
    private readonly limit: number,
    private readonly windowMs = 60_000,
    private readonly cleanupIntervalMs = 60_000,
  ) {}

  consume(key: string, now = Date.now()): RateLimitResult {
    if (now >= this.nextCleanupAt) {
      for (const [entryKey, entry] of this.entries) {
        if (entry.resetAt <= now) this.entries.delete(entryKey);
      }
      this.nextCleanupAt = now + this.cleanupIntervalMs;
    }

    let entry = this.entries.get(key);
    if (!entry || entry.resetAt <= now) {
      entry = { count: 0, resetAt: now + this.windowMs };
      this.entries.set(key, entry);
    }
    entry.count += 1;

    return {
      allowed: entry.count <= this.limit,
      retryAfter: Math.max(1, Math.ceil((entry.resetAt - now) / 1000)),
    };
  }

  get size(): number {
    return this.entries.size;
  }
}
