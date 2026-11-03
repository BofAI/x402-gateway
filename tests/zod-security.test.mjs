import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { z, PaymentPayloadV2Schema, ResourceInfoSchema } from "@x402/core/schemas";

for (const bound of ["max", "length"]) {
  for (const asyncParse of [false, true]) {
    test(`rejects oversized ${bound} arrays before visiting elements (async=${asyncParse})`, async () => {
      let visits = 0;
      const schema = z.array(z.string().superRefine(() => { visits += 1; }))[bound](2);
      const oversized = Array.from({ length: 1_000 }, () => "valid");
      const rejected = asyncParse
        ? await schema.safeParseAsync(oversized)
        : schema.safeParse(oversized);
      assert.equal(rejected.success, false);
      assert.equal(visits, 0);

      const accepted = asyncParse
        ? await schema.safeParseAsync(["one", "two"])
        : schema.safeParse(["one", "two"]);
      assert.equal(accepted.success, true);
      assert.equal(visits, 2);
    });
  }
}

test("bounds issue allocation in official Core resource tags", () => {
  const result = ResourceInfoSchema.safeParse({ url: "https://example.test", tags: Array(1_000).fill(0) });
  assert.equal(result.success, false);
  assert.equal(result.error.issues.length, 1);
  assert.equal(result.error.issues[0].code, "too_big");
  assert.deepEqual(result.error.issues[0].path, ["tags"]);
});

test("preserves SDK 2.0 payment records and strips unknown envelope fields", () => {
  const fixture = JSON.parse(readFileSync(new URL("./fixtures/legacy-tron-v2.json", import.meta.url), "utf8"));
  const payload = fixture.paymentPayload;
  const result = PaymentPayloadV2Schema.parse({ ...payload, unknownEnvelopeField: "strip" });
  assert.deepEqual(result.payload, payload.payload);
  assert.deepEqual(result.accepted, payload.accepted);
  assert.equal(Object.hasOwn(result, "unknownEnvelopeField"), false);
});
