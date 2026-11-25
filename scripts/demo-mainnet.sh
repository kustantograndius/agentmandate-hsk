#!/usr/bin/env bash
# AgentMandate — MAINNET demo settlement on HSK Chain (chainId 177).
#
# Runs ONE full paid-service flow against the already-deployed mainnet
# contracts (AgentServiceRegistry + A2ASettlement, deployed by
# scripts/deploy-mainnet.sh):
#
#   deploy demo token -> fund agent/seller gas -> seller registers the
#   service in the live registry -> seller server up -> buyer agent pays
#   through a mandate -> settlement receipt on mainnet -> deliverable
#   redeemed against the receipt -> assert seller token balance.
#
# The settlement token for this demo is the repo's MockUSDC deployed on
# mainnet as a clearly-labelled demo token: the canonical bridged USDC
# on chain 177 cannot be acquired from this wallet without a CEX/bridge
# hop, and the mandate/settlement mechanics being demonstrated are
# token-agnostic (the settlement contract works with any ERC-20; the
# production configuration points at real USDC
# 0x054ed45810DbBAb8B27668922D110669c9D88D0a).
#
# Required env: DEPLOYER_PK (principal, holds HSK + demo tokens),
#               AGENT_PK, SELLER_PK (fresh demo accounts).
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RPC_URL="https://mainnet.hsk.xyz"
REGISTRY="0x7a1A0421d87f5429Fc1b9cC7a55598fE717AD7E5"
SETTLEMENT="0xed36006100B45040154333f464Ed55Bc7F2e942c"
PRICE=10000
FUND=300000000000000000   # 0.3 HSK gas for each demo account

: "${DEPLOYER_PK:?set DEPLOYER_PK}" "${AGENT_PK:?set AGENT_PK}" "${SELLER_PK:?set SELLER_PK}"

CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
if [ "$CHAIN_ID" != "177" ]; then
  echo "ABORT: RPC reports chainId $CHAIN_ID, expected 177." >&2; exit 1
fi

DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_PK")
AGENT=$(cast wallet address --private-key "$AGENT_PK")
SELLER=$(cast wallet address --private-key "$SELLER_PK")
echo "principal=$DEPLOYER agent=$AGENT seller=$SELLER"
echo "principal HSK: $(cast balance "$DEPLOYER" --rpc-url "$RPC_URL")"

deploy() {
  forge create "$1" --rpc-url "$RPC_URL" --private-key "$DEPLOYER_PK" --broadcast --legacy \
    | grep -oE 'Deployed to: 0x[0-9a-fA-F]{40}' | cut -d' ' -f3
}
MOCK=$(deploy src/mocks/MockUSDC.sol:MockUSDC)
echo "DemoToken(MockUSDC)=$MOCK Registry=$REGISTRY Settlement=$SETTLEMENT"

cast send "$MOCK" "mint(address,uint256)" "$DEPLOYER" 1000000000 \
  --private-key "$DEPLOYER_PK" --rpc-url "$RPC_URL" --legacy >/dev/null
echo "minted 1000 demo tokens to principal"

for target in "$AGENT" "$SELLER"; do
  cast send "$target" --value "$FUND" \
    --private-key "$DEPLOYER_PK" --rpc-url "$RPC_URL" --legacy >/dev/null
done
echo "funded agent + seller with 0.3 HSK gas each"

PORT=18777
cast send "$REGISTRY" "registerService(string,string,address,uint256)" \
  "HSK Wallet Report" "http://127.0.0.1:$PORT/x402/hsk-wallet-report" \
  "$MOCK" "$PRICE" --private-key "$SELLER_PK" --rpc-url "$RPC_URL" --legacy >/dev/null
echo "seller registered the service in the mainnet registry"

RPC_URL="$RPC_URL" SETTLEMENT_ADDRESS="$SETTLEMENT" TOKEN_ADDRESS="$MOCK" \
  PAY_TO="$SELLER" PRICE_ATOMIC="$PRICE" PORT="$PORT" \
  EXPLORER_BASE="https://hashkey.blockscout.com" \
  python3 "$REPO/server/seller.py" &
SELLER_PID=$!
trap 'kill $SELLER_PID 2>/dev/null || true' EXIT
sleep 2

PRINCIPAL_PK="$DEPLOYER_PK" AGENT_PK="$AGENT_PK" \
  python3 "$REPO/agent/buyer.py" --rpc "$RPC_URL" --settlement "$SETTLEMENT" \
    --registry "$REGISTRY" --service-id 1 \
    --seller "http://127.0.0.1:$PORT" --address "$AGENT" \
    --explorer "https://hashkey.blockscout.com"

BAL=$(cast call "$MOCK" "balanceOf(address)(uint256)" "$SELLER" --rpc-url "$RPC_URL")
echo "seller demo-token balance after demo: $BAL (expect $PRICE)"
echo "MAINNET DEMO DONE — DemoToken=$MOCK Registry=$REGISTRY Settlement=$SETTLEMENT"
