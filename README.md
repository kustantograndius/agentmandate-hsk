# AgentMandate — A2A Settlement on HSK Chain

Entry for the **HSK Chain Horizon Hackathon · Seoul** (DoraHacks), **Track 2 —
Agent Payments & A2A Settlement**. Built by Cindy Livia (@cindylivia).

Agents hire and pay other agents on HashKey Chain (HSK Chain, chainId 177)
without a human approving every payment — safely. A principal issues a capped,
expiring, revocable **payment mandate** to its agent; the agent authorizes each
payment with an **EIP-712 wallet signature**; settlement moves USDC
principal → payee on-chain and writes an **auditable receipt** for every paid
task. This is the payment shape published for HSP (HashKey Settlement
Protocol): mandates for agent payment authorization + on-chain hashes turned
into auditable financial credentials, x402-compatible at the HTTP layer.

## Live on HSK Chain mainnet (chainId 177) — deployed 2026-11-25

| Contract | Address | Source verified |
| --- | --- | --- |
| `AgentServiceRegistry` | `0x7a1A0421d87f5429Fc1b9cC7a55598fE717AD7E5` | yes |
| `A2ASettlement` | `0xed36006100B45040154333f464Ed55Bc7F2e942c` | yes |
| Demo token (`MockUSDC`) | `0x61850A5112e11Fb9a1442f6F3f1a1246e95bD9F0` | yes |

Explorer: https://hsk.blockscout.com — a full paid-service flow was settled
live on mainnet (mandate → EIP-712 authorization → settlement → on-chain
receipt → deliverable redeemed against the receipt). Evidence and
reproduction: [`docs/MAINNET.md`](docs/MAINNET.md).

## Contracts

| Contract | Role |
| --- | --- |
| `src/AgentServiceRegistry.sol` | Discovery: agents list payable services (name, endpoint, token, price). |
| `src/A2ASettlement.sol` | Mandates + EIP-712-authorized settlement + on-chain receipts. Fail-closed caps, expiry, revocation, replay protection. |
| `src/mocks/MockUSDC.sol` | Plain ERC-20 (6 decimals) used for local tests, the testnet rehearsal, and — deployed on mainnet as a clearly-labelled demo token — the live mainnet demo settlement (see `docs/MAINNET.md` for why). The production configuration settles in real USDC. |

## Build & test

Zero external dependencies (no forge-std needed):

```bash
forge build
forge test   # 12 tests: mandate terms, happy-path settle + receipt,
             # per-tx/total cap, replay, bad signer, revoke, expiry, registry ACL
```

## x402 service layer (seller + buyer)

| Component | Role |
| --- | --- |
| `server/seller.py` | Paid endpoint `GET /x402/hsk-wallet-report?address=0x...`. Unpaid → HTTP 402 challenge (scheme `agent-mandate`); paid → verifies the on-chain receipt (payee, token, amount, taskRef, freshness, one-time redemption) and serves a wallet snapshot report. Python stdlib only. |
| `agent/buyer.py` | Buyer agent: reads the 402 challenge, (principal) creates a mandate + approval, signs the EIP-712 `PaymentAuthorization`, relays `settle()`, verifies the receipt, redeems it for the deliverable. |
| `lib/evm.py` | Shared JSON-RPC / EIP-712 helpers (stdlib + `cast` for signing/sending). |

Adapted from a production x402 service on Base (direct-transfer proof
swapped for mandate settlement + receipt verification).

## End-to-end test (local, no funds)

```bash
python3 e2e/run_e2e.py   # anvil + MockUSDC: deploy -> register -> 402 ->
                         # mandate -> sign -> settle -> receipt -> deliverable,
                         # plus replay/bogus/unpaid rejection checks
```

Latest run: **PASS** — evidence in [`docs/E2E.md`](docs/E2E.md) and
[`docs/e2e-local-20261122.log`](docs/e2e-local-20261122.log).

## Deploy & demo (HSK Chain)

All three runs below were executed on 2026-11-25; evidence in `docs/`.

```bash
# Testnet rehearsal (chainId 133): full deploy + live payment flow.
# DONE — settlement tx 0x64dcdba2...da37, log in docs/.
DEPLOYER_PK=... AGENT_PK=... SELLER_PK=... scripts/rehearsal-testnet.sh

# Mainnet (chainId 177): guarded script (chain-id check + explicit
# CONFIRM_MAINNET_DEPLOY env). Deploys the two contracts only; the
# production settlement token is real USDC
# 0x054ed45810DbBAb8B27668922D110669c9D88D0a. DONE — see docs/MAINNET.md.
scripts/deploy-mainnet.sh

# Mainnet demo settlement against the deployed contracts: deploys the
# demo token, registers the service in the live registry, and runs one
# full paid flow. DONE — settle tx 0x82582218...c1bf, receipt #1 on-chain.
DEPLOYER_PK=... AGENT_PK=... SELLER_PK=... scripts/demo-mainnet.sh
```

Contract sources are verified on the explorer
(https://hsk.blockscout.com) via the Blockscout v2 flattened-code API.

## Payment flow

1. Seller agent registers a service in `AgentServiceRegistry`.
2. Buyer principal creates a mandate in `A2ASettlement` (per-tx cap, total cap,
   expiry) naming its buyer agent, and approves the contract for the token.
3. Buyer agent calls the seller's x402 endpoint → HTTP 402 challenge priced
   from the registry entry.
4. Buyer agent signs a `PaymentAuthorization` (EIP-712) for the exact amount,
   payee, and task reference.
5. Anyone relays `settle(...)`; the contract verifies mandate + signature +
   caps, pulls USDC principal → seller, and stores a receipt.
6. Seller verifies the receipt on-chain and delivers the work.
