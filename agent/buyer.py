#!/usr/bin/env python3
"""AgentMandate buyer agent — buys a paid x402-HSK service under a mandate.

Demonstrates the full HSP payment shape from the buyer's side:

  1. GET the seller's paid endpoint -> HTTP 402 challenge (scheme
     "agent-mandate": payTo, amount, USDC asset, settlement contract,
     taskRef for this exact request).
  2. Ensure a payment mandate exists on A2ASettlement: the PRINCIPAL
     creates it (capped per-tx + total, expiring, revocable) and approves
     the settlement contract to pull up to the total cap.
     (--mandate-id reuses an existing mandate instead.)
  3. The buyer AGENT signs an EIP-712 PaymentAuthorization for this one
     payment (mandateId, payee, amount, taskRef, nonce, deadline).
  4. Anyone may relay: the agent submits settle() on-chain. Funds move
     principal -> seller; the contract writes an auditable receipt.
  5. The agent reads the receipt back on-chain, verifies its fields, then
     retries the HTTP request with `X-Payment-Receipt: <id>` and prints
     the deliverable.

Keys come from env (PRINCIPAL_PK, AGENT_PK) and are never printed.
Config via flags or env: --rpc / RPC_URL, --settlement / SETTLEMENT_ADDRESS,
--seller / SELLER_URL, --address (wallet to report on; default: principal).
On a public chain, pass --explorer https://hashkey.blockscout.com for links.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import evm  # noqa: E402

AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "PaymentAuthorization": [
        {"name": "mandateId", "type": "uint256"},
        {"name": "payee", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "taskRef", "type": "bytes32"},
        {"name": "nonce", "type": "bytes32"},
        {"name": "deadline", "type": "uint64"},
    ],
}


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def step(msg):
    print(f"[buyer] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="AgentMandate buyer agent")
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    ap.add_argument("--settlement", default=os.environ.get("SETTLEMENT_ADDRESS", ""))
    ap.add_argument("--registry", default=os.environ.get("REGISTRY_ADDRESS", ""))
    ap.add_argument("--service-id", type=int, default=int(os.environ.get("SERVICE_ID", "0")))
    ap.add_argument("--seller", default=os.environ.get("SELLER_URL", "http://127.0.0.1:18777"))
    ap.add_argument("--address", default="", help="wallet to report on (default: principal)")
    ap.add_argument("--mandate-id", type=int, default=int(os.environ.get("MANDATE_ID", "0")))
    ap.add_argument("--total-cap-mult", type=int, default=10,
                    help="new mandate total cap = price x this (default 10)")
    ap.add_argument("--mandate-minutes", type=int, default=60)
    ap.add_argument("--deadline-sec", type=int, default=600)
    ap.add_argument("--explorer", default=os.environ.get("EXPLORER_BASE", "").rstrip("/"))
    args = ap.parse_args()

    principal_pk = os.environ.get("PRINCIPAL_PK", "")
    agent_pk = os.environ.get("AGENT_PK", "")
    if not args.settlement:
        raise SystemExit("missing --settlement / SETTLEMENT_ADDRESS")
    if not agent_pk or (not args.mandate_id and not principal_pk):
        raise SystemExit("need AGENT_PK (and PRINCIPAL_PK unless --mandate-id reuses one)")

    rpc_url = args.rpc
    settlement = args.settlement
    cid = evm.chain_id(rpc_url)
    agent = evm.wallet_address(agent_pk)
    principal = evm.wallet_address(principal_pk) if principal_pk else None
    target = args.address or principal
    step(f"chain={cid} agent={agent} principal={principal} settlement={settlement}")

    # Optional discovery cross-check against the on-chain registry.
    if args.registry and args.service_id:
        try:
            out = evm.cast("call", args.registry,
                           "getService(uint256)((address,string,string,address,uint256,bool))",
                           str(args.service_id), "--rpc-url", rpc_url)
            step(f"registry service #{args.service_id}: {out.splitlines()[0]} ...")
        except Exception as e:
            step(f"registry cross-check skipped: {e}")

    # 1. Ask for the service -> expect a 402 agent-mandate challenge.
    url = f"{args.seller}/x402/hsk-wallet-report?address={target}"
    status, body = http_get(url)
    if status != 402:
        raise SystemExit(f"expected HTTP 402 challenge, got {status}: {body}")
    offer = body["accepts"][0]
    assert offer["scheme"] == "agent-mandate", offer
    assert offer["settlementContract"].lower() == settlement.lower(), offer
    pay_to, amount, token, task_ref = (offer["payTo"], int(offer["amount"]),
                                       offer["asset"], offer["taskRef"])
    step(f"402 challenge: pay {amount} atomic of {token} to {pay_to}, taskRef={task_ref}")

    # 2. Ensure a mandate + principal allowance.
    mandate_id = args.mandate_id
    if mandate_id:
        m = evm.read_mandate(rpc_url, settlement, mandate_id)
        step(f"reusing mandate #{mandate_id}: agent={m['agent']} totalCap={m['totalCap']} "
             f"spent={m['spent']} revoked={m['revoked']}")
        if m["revoked"] or m["agent"].lower() != agent.lower():
            raise SystemExit("mandate revoked or agent mismatch")
        if amount > m["perTxCap"] or m["spent"] + amount > m["totalCap"]:
            raise SystemExit("payment exceeds mandate caps")
    else:
        total_cap = amount * args.total_cap_mult
        expiry = int(time.time()) + args.mandate_minutes * 60
        receipt = evm.send_tx(rpc_url, principal_pk, settlement,
                              "createMandate(address,address,uint256,uint256,uint64)",
                              agent, token, amount, total_cap, expiry)
        log = evm.find_event(receipt, "MandateCreated(uint256,address,address,address,uint256,uint256,uint64)")
        mandate_id = int(log["topics"][1], 16)
        step(f"mandate #{mandate_id} created by principal (perTxCap={amount}, "
             f"totalCap={total_cap}, expiry={expiry}) tx={receipt['_hash']}")
        evm.send_tx(rpc_url, principal_pk, token, "approve(address,uint256)",
                    settlement, total_cap)
        step(f"principal approved settlement to pull up to {total_cap}")

    # 3. Agent signs the EIP-712 PaymentAuthorization for this one payment.
    nonce = "0x" + os.urandom(32).hex()
    deadline = int(time.time()) + args.deadline_sec
    typed = {
        "types": AUTH_TYPES,
        "primaryType": "PaymentAuthorization",
        "domain": {"name": "A2ASettlement", "version": "1", "chainId": cid,
                   "verifyingContract": settlement},
        "message": {"mandateId": mandate_id, "payee": pay_to, "amount": amount,
                    "taskRef": task_ref, "nonce": nonce, "deadline": deadline},
    }
    signature = evm.sign_typed_data(agent_pk, typed)
    step(f"agent signed PaymentAuthorization (nonce={nonce[:18]}..., deadline={deadline})")

    # 4. Relay settle() on-chain; the contract enforces the mandate.
    receipt = evm.send_tx(rpc_url, agent_pk, settlement,
                          "settle(uint256,address,uint256,bytes32,bytes32,uint64,bytes)",
                          mandate_id, pay_to, amount, task_ref, nonce, deadline, signature)
    log = evm.find_event(receipt, "Settled(uint256,uint256,address,uint256,bytes32)")
    receipt_id = int(log["topics"][1], 16)
    step(f"settled on-chain: receipt #{receipt_id} tx={receipt['_hash']}")
    if args.explorer:
        step(f"explorer: {args.explorer}/tx/{receipt['_hash']}")

    # 5. Verify the receipt on-chain, then redeem it for the deliverable.
    r = evm.read_receipt(rpc_url, settlement, receipt_id)
    assert r["payee"].lower() == pay_to.lower() and r["amount"] == amount, r
    assert r["taskRef"].lower() == task_ref.lower(), r
    step(f"receipt verified on-chain: payer={r['payer']} amount={r['amount']} "
         f"settledAt={r['settledAt']}")
    status, report = http_get(url, {"X-Payment-Receipt": str(receipt_id)})
    if status != 200:
        raise SystemExit(f"redemption failed: HTTP {status}: {body}")
    step("deliverable received (HTTP 200):")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
