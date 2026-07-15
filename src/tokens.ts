export type TokenInfo = {
  address: string;
  decimals: number;
  name: string;
  symbol: string;
  assetTransferMethod?: "permit2";
};

export const TOKENS: Record<string, Record<string, TokenInfo>> = {
  "tron:0x2b6653dc": {
    USDT: { address: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", decimals: 6, name: "Tether USD", symbol: "USDT", assetTransferMethod: "permit2" },
    USDD: { address: "TXDk8mbtRbXeYuMNS83CfKPaYYT8XWv9Hz", decimals: 18, name: "Decentralized USD", symbol: "USDD", assetTransferMethod: "permit2" },
  },
  "tron:0xcd8690dc": {
    USDT: { address: "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf", decimals: 6, name: "Tether USD", symbol: "USDT", assetTransferMethod: "permit2" },
    USDD: { address: "TGjgvdTWWrybVLaVeFqSyVqJQWjxqRYbaK", decimals: 18, name: "Decentralized USD", symbol: "USDD", assetTransferMethod: "permit2" },
  },
  "eip155:56": {
    USDT: { address: "0x55d398326f99059fF775485246999027B3197955", decimals: 18, name: "Tether USD", symbol: "USDT", assetTransferMethod: "permit2" },
  },
  "eip155:97": {
    USDT: { address: "0x337610d27c682E347C9cD60BD4b3b107C9d34dDd", decimals: 18, name: "Tether USD", symbol: "USDT", assetTransferMethod: "permit2" },
    USDC: { address: "0x64544969ed7EBf5f083679233325356EbE738930", decimals: 18, name: "USD Coin", symbol: "USDC", assetTransferMethod: "permit2" },
  },
};

export function normalizeNetwork(network: string): string {
  return (
    {
      "tron-mainnet": "tron:0x2b6653dc",
      "tron:mainnet": "tron:0x2b6653dc",
      "mainnet": "tron:0x2b6653dc",
      "tron-shasta": "tron:0x94a9059e",
      "tron:shasta": "tron:0x94a9059e",
      "shasta": "tron:0x94a9059e",
      "tron-nile": "tron:0xcd8690dc",
      "tron:nile": "tron:0xcd8690dc",
      "nile": "tron:0xcd8690dc",
      "bsc-mainnet": "eip155:56",
      "bsc-testnet": "eip155:97",
    }[network] ?? network
  );
}

export function getToken(network: string, symbol: string): TokenInfo {
  const normalized = normalizeNetwork(network);
  const token = TOKENS[normalized]?.[symbol.toUpperCase()];
  if (!token) throw new Error(`unknown token ${symbol} on ${network}`);
  return token;
}

function numberToDecimalString(amount: number): string {
  const value = amount.toString();
  const match = value.match(/^(\d+(?:\.\d+)?)[eE]([+-]?\d+)$/);
  if (!match) return value;

  const [, coefficient, exponentText] = match;
  const exponent = Number(exponentText);
  const [whole, fraction = ""] = coefficient.split(".");
  const digits = whole + fraction;
  const decimalIndex = whole.length + exponent;

  if (decimalIndex <= 0) return `0.${"0".repeat(Math.abs(decimalIndex))}${digits}`.replace(/0+$/, "");
  if (decimalIndex >= digits.length) return `${digits}${"0".repeat(decimalIndex - digits.length)}`;
  return `${digits.slice(0, decimalIndex)}.${digits.slice(decimalIndex)}`.replace(/0+$/, "").replace(/\.$/, "");
}

export function toSmallestUnit(amount: number | string, decimals: number): string {
  if (!Number.isInteger(decimals) || decimals < 0) throw new Error(`invalid token decimals ${decimals}`);
  const numeric = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(numeric) || numeric < 0) throw new Error(`invalid amount ${amount}`);
  const value = typeof amount === "number" ? numberToDecimalString(amount) : String(amount);
  if (!/^\d+(\.\d+)?$/.test(value)) throw new Error(`invalid decimal amount ${amount}`);
  const [whole, fraction = ""] = value.split(".");
  const padded = (fraction + "0".repeat(decimals)).slice(0, decimals);
  let units = BigInt(whole || "0") * 10n ** BigInt(decimals) + BigInt(padded || "0");
  if (fraction.length > decimals && /[1-9]/.test(fraction.slice(decimals))) units += 1n;
  if (numeric > 0 && units === 0n) units = 1n;
  return units.toString();
}
