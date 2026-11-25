#!/usr/bin/env bash
# AgentMandate — MAINNET deploy on HSK Chain (chainId 177).
#
# *** READY TO RUN, DELIBERATELY NOT EXECUTED ***
# Mainnet gas (HSK) is not yet held; acquiring it is the owner's decision
# (buy a small amount on an exchange listing HSK and withdraw to the
# deployer address on chain 177, or bridge). Estimated need: far below
# 1 HSK for both deploys (initcode measured 3,813 B + 2,712 B; re-check
# `cast gas-price --rpc-url $RPC_URL` before running).
#
# Safety guards — the script refuses to send anything unless ALL hold:
#   CONFIRM_MAINNET_DEPLOY=YES_I_UNDERSTAND is set in the environment
#   the RPC reports chainId exactly 177
#   DEPLOYER_PK is set (env only, never committed)
#
# Deploys ONLY AgentServiceRegistry + A2ASettlement. No MockUSDC on
# mainnet: the settlement token is the real bridged USDC
# 0x054ed45810DbBAb8B27668922D110669c9D88D0a (6 decimals, EIP-3009).
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
RPC_URL="${RPC_URL:-https://mainnet.hsk.xyz}"
USDC="0x054ed45810DbBAb8B27668922D110669c9D88D0a"

if [ "${CONFIRM_MAINNET_DEPLOY:-}" != "YES_I_UNDERSTAND" ]; then
  echo "REFUSED: set CONFIRM_MAINNET_DEPLOY=YES_I_UNDERSTAND to deploy to mainnet." >&2
  exit 1
fi
: "${DEPLOYER_PK:?set DEPLOYER_PK}"

CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
if [ "$CHAIN_ID" != "177" ]; then
  echo "ABORT: RPC $RPC_URL reports chainId $CHAIN_ID, expected 177 (HSK mainnet)." >&2
  exit 1
fi
DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_PK")
echo "deployer: $DEPLOYER"
echo "HSK balance: $(cast balance "$DEPLOYER" --rpc-url "$RPC_URL")"
echo "gas price:   $(cast gas-price --rpc-url "$RPC_URL")"

deploy() {
  forge create "$1" --rpc-url "$RPC_URL" --private-key "$DEPLOYER_PK" --broadcast --legacy \
    | grep -oE 'Deployed to: 0x[0-9a-fA-F]{40}' | cut -d' ' -f3
}

REGISTRY=$(deploy src/AgentServiceRegistry.sol:AgentServiceRegistry)
SETTLEMENT=$(deploy src/A2ASettlement.sol:A2ASettlement)
echo "AgentServiceRegistry: $REGISTRY"
echo "A2ASettlement:        $SETTLEMENT"
echo "Settlement token (real USDC): $USDC"
echo "Explorer: https://hashkey.blockscout.com/address/$SETTLEMENT"
echo
echo "Next steps (separate, deliberate):"
echo "  1. Verify both sources on hashkey.blockscout.com."
echo "  2. Seller registers the demo service in the registry."
echo "  3. Principal creates a small demo mandate; run one real settlement"
echo "     with agent/buyer.py --explorer https://hashkey.blockscout.com"
echo "     and record the receipt link for the submission."
