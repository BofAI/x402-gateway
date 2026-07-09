import {
  decodePaymentSignatureHeader,
  encodePaymentRequiredHeader,
  encodePaymentResponseHeader,
} from "@bankofai/x402-core/http";

export const headers = {
  required: "PAYMENT-REQUIRED",
  signature: "PAYMENT-SIGNATURE",
  response: "PAYMENT-RESPONSE",
};

export type PaymentRequirement = {
  scheme: "exact";
  network: string;
  amount: string;
  asset: string;
  payTo: string;
  maxTimeoutSeconds: number;
  extra: Record<string, unknown>;
};

export function encodeRequired(value: unknown): string {
  return encodePaymentRequiredHeader(value as never);
}

export function encodeResponse(value: unknown): string {
  return encodePaymentResponseHeader(value as never);
}

export function decodeSignature(value: string): unknown {
  return decodePaymentSignatureHeader(value);
}

export function matchRequirement(payload: any, requirement: PaymentRequirement): boolean {
  const accepted = payload?.accepted ?? payload?.payment?.accepted ?? payload;
  if (!accepted || typeof accepted !== "object") return false;
  return (
    accepted.scheme === requirement.scheme &&
    accepted.network === requirement.network &&
    String(accepted.amount) === requirement.amount &&
    String(accepted.asset).toLowerCase() === requirement.asset.toLowerCase() &&
    String(accepted.payTo) === requirement.payTo
  );
}
