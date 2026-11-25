# AgentMandate — live on HSK Chain mainnet (chainId 177)

Deployed and demonstrated on mainnet on **2026-11-25**. Explorer:
https://hsk.blockscout.com (hashkey.blockscout.com redirects there).

## Contracts (source verified on the explorer)

| Contract | Address | Verified |
| --- | --- | --- |
| `AgentServiceRegistry` | `0x7a1A0421d87f5429Fc1b9cC7a55598fE717AD7E5` | yes |
| `A2ASettlement` | `0xed36006100B45040154333f464Ed55Bc7F2e942c` | yes |
| Demo token (`MockUSDC`, 6 decimals) | `0x61850A5112e11Fb9a1442f6F3f1a1246e95bD9F0` | yes |

Deploy driver: `scripts/deploy-mainnet.sh` (guarded by a chain-id check and
an explicit `CONFIRM_MAINNET_DEPLOY` env flag).

## Live demo settlement (one full paid-service flow, on mainnet)

Driver: `scripts/demo-mainnet.sh` — raw log:
[`docs/mainnet-demo-20261125.log`](mainnet-demo-20261125.log).

Roles (demo accounts, funded with a small amount of HSK gas):

- Principal / deployer: `0x2DfA330D38450b7755e38475aEE5426465E5b832`
- Buyer agent: `0xff4A720D0AD91700fa6E3560ECebCab141A9776d`
- Seller: `0xDa56D12A9717407148043b2dFD994CB3D058A9d3`

What happened, in order:

1. Seller registered the service **“HSK Wallet Report”** in the live
   mainnet registry (service id 1, price 10000 atomic = 0.01 demo-token).
2. Principal created **mandate #1** (per-tx cap 10000, total cap 100000,
   expiry +1 h) and approved the settlement contract:
   tx `0x936474eb3f71ee8dc213b7bbe30dbe5d4311ecf1d9deeebc99380ec75bedfab1`
3. Buyer agent hit the seller's x402 endpoint, received the HTTP 402
   challenge, signed the EIP-712 `PaymentAuthorization`, and relayed
   `settle(...)`:
   **tx `0x825822188a921f57666f2fa2925592d2364701cd534bdbfdeb30e63381d5c1bf`**
4. Receipt #1 was written on-chain and read back independently
   (`receipts(1)`): payer = principal, payee = seller, token = demo token,
   amount = 10000, settledAt = 1795616693.
5. The seller verified the receipt on-chain (payee, token, amount,
   taskRef, freshness, one-time redemption) and delivered the wallet
   report with the `paidVia` block referencing receipt #1. Seller's demo
   token balance after the flow: exactly 10000.

## Why a demo token on mainnet?

The settlement mechanics are token-agnostic: `A2ASettlement` works with
any ERC-20, and the production configuration points at the canonical
bridged USDC on chain 177,
`0x054ed45810DbBAb8B27668922D110669c9D88D0a` (which supports EIP-3009).
This wallet holds no USDC on chain 177 and acquiring some requires a
CEX/bridge hop, so the live demonstration uses the repo's own
clearly-labelled demo token — the same approach other Track 2 entries
use for their mainnet demos. Nothing in the contracts, the mandate flow,
or the receipt verification changes when the token address is swapped
for real USDC.

## Testnet rehearsal

The identical flow was rehearsed end-to-end on HSK testnet (chainId 133)
earlier the same day, including a failure-driven hardening pass (legacy
transaction mode, transport retries, receipt-redemption scoping):
[`docs/testnet-rehearsal-20261125.log`](testnet-rehearsal-20261125.log),
settlement tx
`0x64dcdba2011ddccfa09ffe5f9f4518cde302a9450f4ac30e0d15eb6f6593da37`.
