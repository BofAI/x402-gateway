export type TokenInfo = {
  address: string;
  decimals: number;
  name: string;
  symbol: string;
  assetTransferMethod?: "permit2";
};

export const TOKENS: Record<string, Record<string, TokenInfo>> = {
  "tron:mainnet": {
    USDT: { address: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", decimals: 6, name: "Tether USD", symbol: "USDT", assetTransferMethod: "permit2" },
    USDD: { address: "TXDk8mbtRbXeYuMNS83CfKPaYYT8XWv9Hz", decimals: 18, name: "Decentralized USD", symbol: "USDD", assetTransferMethod: "permit2" },
  },
  "tron:nile": {
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
      "tron-mainnet": "tron:mainnet",
      "tron-shasta": "tron:shasta",
      "tron-nile": "tron:nile",
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

export function toSmallestUnit(amount: number | string, decimals: number): string {
  const [whole, fraction = ""] = String(amount).split(".");
  const padded = (fraction + "0".repeat(decimals)).slice(0, decimals);
  return (BigInt(whole || "0") * 10n ** BigInt(decimals) + BigInt(padded || "0")).toString();
}
