import assert from "node:assert/strict";
import { TronWeb, utils } from "tronweb";
import { normalizeAddressForSigning, PERMIT2_ADDRESSES } from "@bankofai/x402-tron";
import { serializeSignedTronTransaction } from "@bankofai/x402-tron/transactions";

export const testPrivateKey = "1".repeat(64); // Public, deterministic, never funded.
export const testPayer = TronWeb.address.fromPrivateKey(testPrivateKey);
export const testRecipient = "TTX1Us19zqsLXhY39PPR7KRUoMa93s3J3i";
export const testTransaction = "a".repeat(64);

export function clientSigner(overrides = {}) {
  return {
    address: testPayer,
    network: "tron:3448148188",
    signTypedData: async ({ domain, types, message }) => utils.typedData.signTypedData(domain, types, message, testPrivateKey),
    readContract: async ({ functionName }) => {
      assert.ok(["allowance", "balanceOf"].includes(functionName));
      return 1_000_000n;
    },
    ensureAllowance: async () => true,
    ...overrides,
  };
}

export function facilitatorSigner(onWrite = () => {}) {
  return {
    getAddresses: () => [testRecipient],
    readContract: async ({ functionName }) => {
      assert.ok(["allowance", "balanceOf"].includes(functionName));
      return 1_000_000n;
    },
    verifyTypedData: async ({ address, domain, types, message, signature }) =>
      normalizeAddressForSigning(utils.typedData.verifyTypedData(domain, types, message, signature)) === normalizeAddressForSigning(address),
    writeContract: async args => {
      assert.equal(args.functionName, "settle");
      onWrite(args);
      return testTransaction;
    },
    waitForTransactionReceipt: async ({ hash }) => {
      assert.equal(hash, testTransaction);
      return { status: "success" };
    },
  };
}

// Offline transaction fixture adapted from the SDK's sponsored-payment consumer test.
// Imports intentionally resolve only the published packages installed in this Gateway.
export async function signedApproval({ token, network }) {
  const tronWeb = new TronWeb({ fullHost: "http://127.0.0.1:1" });
  const transaction = {
    visible: false,
    raw_data: {
      contract: [{
        type: "TriggerSmartContract",
        parameter: {
          type_url: "type.googleapis.com/protocol.TriggerSmartContract",
          value: {
            owner_address: TronWeb.address.toHex(testPayer),
            contract_address: TronWeb.address.toHex(token),
            data: "095ea7b3" + normalizeAddressForSigning(PERMIT2_ADDRESSES[network]).slice(2).padStart(64, "0") + "f".repeat(64),
            call_value: 0,
          },
        },
      }],
      ref_block_bytes: "1234",
      ref_block_hash: "0102030405060708",
      timestamp: Date.now(),
      expiration: Date.now() + 600_000,
      fee_limit: 100_000_000,
    },
  };
  const protobuf = utils.transaction.txJsonToPb(transaction);
  transaction.raw_data_hex = utils.transaction.txPbToRawDataHex(protobuf);
  transaction.txID = utils.transaction.txPbToTxID(protobuf).replace(/^0x/, "");
  return serializeSignedTronTransaction(await tronWeb.trx.sign(transaction, testPrivateKey));
}
