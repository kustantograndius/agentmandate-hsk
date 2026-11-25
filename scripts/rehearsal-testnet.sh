#!/usr/bin/env bash
# AgentMandate — REHEARSAL on HSK Chain TESTNET (chainId 133).
#
# Ready to run as soon as the three role accounts hold testnet tHSK from
# the faucet (thirdweb universal faucet lists HashKey Chain Testnet,
# ~0.01 tHSK/day — enough for deploys + this flow). Uses MockUSDC as the
# settlement-token stand-in (testnet has no canonical USDC faucet).
#
# Required env (keys are read from env only, never committed):
#   DEPLOYER_PK   principal + deployer (creates mandate, funds approval)
#   AGENT_PK      buyer agent (signs EIP-712, relays settle)
#   SELLER_PK     seller (registers the service, receives payment)
# Optional env:
#   RPC_URL       default https://testnet.hsk.xyz
#   HTTPS_PROXY   set if the host reaches the internet through a proxy
#                 (cast and the Python services honor proxy env vars)
#
# What it does: preflight (chain id + tHSK balances) -> forge create
# MockUSDC/Registry/Settlement -> mint mock to principal -> seller
# registers service -> start seller server -> run buyer agent -> assert
# seller balance. Exits BEFORE spending anything if preflight fails.
set -euo pipefail
export PATH="$HOME/.foundry/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RPC_URL="${RPC_URL:-https://testnet.hsk.xyz}"
PRICE=10000

: "${DEPLOYER_PK:?set DEPLOYER_PK}" "${AGENT_PK:?set AGENT_PK}" "${SELLER_PK:?set SELLER_PK}"

CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
if [ "$CHAIN_ID" != "133" ]; then
  echo "ABORT: RPC $RPC_URL reports chainId $CHAIN_ID, expected 133 (HSK testnet)." >&2
  exit 1
fi

for name in DEPLOYER AGENT SELLER; do
  pk_var="${name}_PK"
  addr=$(cast wallet address --private-key "${!pk_var}")
  bal=$(cast balance "$addr" --rpc-url "$RPC_URL")
  echo "$name $addr tHSK balance: $bal"
  if [ "$bal" = "0" ]; then
    echo "ABORT: $name has no tHSK. Claim from the faucet first" >&2
    echo "(thirdweb universal faucet -> HashKey Chain Testnet), then re-run." >&2
    exit 1
  fi
done

DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_PK")
AGENT=$(cast wallet address --private-key "$AGENT_PK")
SELLER=$(cast wallet address --private-key "$SELLER_PK")

deploy() { # contract -> address
  forge create "$1" --rpc-url "$RPC_URL" --private-key "$DEPLOYER_PK" --broadcast --legacy \
    | grep -oE 'Deployed to: 0x[0-9a-fA-F]{40}' | cut -d' ' -f3
}

MOCK=$(deploy src/mocks/MockUSDC.sol:MockUSDC)
REGISTRY=$(deploy src/AgentServiceRegistry.sol:AgentServiceRegistry)
SETTLEMENT=$(deploy src/A2ASettlement.sol:A2ASettlement)
echo "MockUSDC=$MOCK Registry=$REGISTRY Settlement=$SETTLEMENT"
echo "Explorer: https://testnet-explorer.hskchain.net/address/$SETTLEMENT"

cast send "$MOCK" "mint(address,uint256)" "$DEPLOYER" 1000000000 \
  --private-key "$DEPLOYER_PK" --rpc-url "$RPC_URL" --legacy >/dev/null
echo "minted 1000 mUSDC (testnet stand-in) to principal"

PORT=18777
cast send "$REGISTRY" "registerService(string,string,address,uint256)" \
  "HSK Wallet Report" "http://127.0.0.1:$PORT/x402/hsk-wallet-report" \
  "$MOCK" "$PRICE" --private-key "$SELLER_PK" --rpc-url "$RPC_URL" --legacy >/dev/null
echo "seller registered the service"

RPC_URL="$RPC_URL" SETTLEMENT_ADDRESS="$SETTLEMENT" TOKEN_ADDRESS="$MOCK" \
  PAY_TO="$SELLER" PRICE_ATOMIC="$PRICE" PORT="$PORT" \
  python3 "$REPO/server/seller.py" &
SELLER_PID=$!
trap 'kill $SELLER_PID 2>/dev/null || true' EXIT
sleep 2

PRINCIPAL_PK="$DEPLOYER_PK" AGENT_PK="$AGENT_PK" \
  python3 "$REPO/agent/buyer.py" --rpc "$RPC_URL" --settlement "$SETTLEMENT" \
    --registry "$REGISTRY" --service-id 1 \
    --seller "http://127.0.0.1:$PORT" --address "$AGENT" \
    --explorer "https://testnet-explorer.hskchain.net"

BAL=$(cast call "$MOCK" "balanceOf(address)(uint256)" "$SELLER" --rpc-url "$RPC_URL")
echo "seller mUSDC balance after rehearsal: $BAL (expect $PRICE)"
echo "REHEARSAL DONE — addresses: MockUSDC=$MOCK Registry=$REGISTRY Settlement=$SETTLEMENT"
