#!/usr/bin/env python3
"""AgentMandate seller — an x402 paid endpoint settled through A2ASettlement
mandates on HSK Chain (the HSP payment shape: mandate + x402 + auditable
on-chain receipt).

Adapted from our live Base Wallet Report x402 service (~/workspace/
x402-service), with the payment rail swapped: instead of a direct USDC
transfer + tx-hash proof, the buyer settles through the A2ASettlement
contract under a principal's capped mandate, and this server verifies the
resulting on-chain RECEIPT before serving the deliverable.

Flow:
  GET /x402/hsk-wallet-report?address=0x...
    - no payment header  -> HTTP 402, x402-style challenge (body JSON +
      PAYMENT-REQUIRED header): scheme "agent-mandate", asset = USDC,
      payTo = seller, settlementContract, taskRef for this exact request.
    - header `X-Payment-Receipt: <receiptId>` -> the server reads
      receipts(receiptId) on-chain and checks: payee == seller, token ==
      USDC, amount >= price, taskRef == this request's taskRef, receipt
      not older than MAX_RECEIPT_AGE_SEC, receipt not redeemed before.
      All pass -> HTTP 200 with the wallet snapshot report.

Deliverable: HSK wallet snapshot (native balance, USDC balance, tx count)
read live from the configured chain RPC — the same product as the Base
service, on HSK rails.

Config (env): RPC_URL, SETTLEMENT_ADDRESS, TOKEN_ADDRESS, PAY_TO,
PRICE_ATOMIC (default 10000 = 0.01 USDC), PORT (default 18777),
MAX_RECEIPT_AGE_SEC (default 3600), EXPLORER_BASE (optional).
Stdlib only (plus repo lib/evm.py for chain reads).
"""
import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lib import evm  # noqa: E402

RPC_URL = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
SETTLEMENT = os.environ.get("SETTLEMENT_ADDRESS", "")
TOKEN = os.environ.get("TOKEN_ADDRESS", "")
PAY_TO = os.environ.get("PAY_TO", "")
PRICE = int(os.environ.get("PRICE_ATOMIC", "10000"))
PORT = int(os.environ.get("PORT", "18777"))
MAX_RECEIPT_AGE = int(os.environ.get("MAX_RECEIPT_AGE_SEC", "3600"))
EXPLORER = os.environ.get("EXPLORER_BASE", "").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
REDEEMED_FILE = os.path.join(HERE, "redeemed-hsk.json")
_lock = threading.Lock()

DESCRIPTION = (
    "HSK wallet snapshot report: live native + USDC balances and transaction "
    "count for any address, settled through an AgentMandate payment mandate "
    "(HSP shape: x402 challenge -> EIP-712 authorization -> on-chain "
    "settlement -> auditable receipt). Method: GET, query ?address=0x... "
    "Pay via A2ASettlement.settle under a mandate naming you payee, then "
    "retry with header X-Payment-Receipt: <receiptId>."
)


def task_ref_for(address: str) -> str:
    return "0x" + evm.keccak256(f"hsk-wallet-report:{address.lower()}".encode()).hex()


def load_redeemed():
    try:
        with open(REDEEMED_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_redeemed(s):
    tmp = REDEEMED_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(s), f)
    os.replace(tmp, REDEEMED_FILE)


_CHAIN_ID = None


def _scope(receipt_id: int) -> str:
    """Redemption keys are scoped to (chain, settlement contract).

    Receipt ids are per-contract counters: receipt #1 on one deployment
    is a different payment from receipt #1 on another. An unscoped store
    wrongly rejects fresh receipts after any redeploy.
    """
    global _CHAIN_ID
    if _CHAIN_ID is None:
        _CHAIN_ID = evm.chain_id(RPC_URL)
    return f"{_CHAIN_ID}:{SETTLEMENT.lower()}:{receipt_id}"


def verify_receipt(receipt_id: int, want_task_ref: str):
    """Return (ok, reason, receipt). All checks run against the chain.

    Checks only — marking happens in mark_redeemed(), called by the
    handler AFTER the deliverable has been produced, so a transient
    failure while building the report never burns a valid receipt.
    """
    with _lock:
        if _scope(receipt_id) in load_redeemed():
            return False, "receipt already redeemed", None
    try:
        r = evm.read_receipt(RPC_URL, SETTLEMENT, receipt_id)
    except Exception as e:
        return False, f"receipt lookup failed: {e}", None
    if r["payee"] == "0x" + "0" * 40:
        return False, "receipt does not exist", None
    if r["payee"].lower() != PAY_TO.lower():
        return False, "receipt payee is not this seller", None
    if r["token"].lower() != TOKEN.lower():
        return False, "receipt token mismatch", None
    if r["amount"] < PRICE:
        return False, "receipt amount below price", None
    if r["taskRef"].lower() != want_task_ref.lower():
        return False, "receipt taskRef does not match this request", None
    if int(time.time()) - r["settledAt"] > MAX_RECEIPT_AGE:
        return False, "receipt too old", None
    return True, "", r


def mark_redeemed(receipt_id: int):
    with _lock:
        redeemed = load_redeemed()
        redeemed.add(_scope(receipt_id))
        save_redeemed(redeemed)


def wallet_report(address: str) -> dict:
    native = evm.native_balance(RPC_URL, address)
    token_bal = evm.balance_of(RPC_URL, TOKEN, address)
    return {
        "address": address,
        "chainId": evm.chain_id(RPC_URL),
        "nativeBalanceWei": str(native),
        "nativeBalance": native / 1e18,
        "token": {
            "address": TOKEN,
            "balanceAtomic": str(token_bal),
            "balance": token_bal / 1e6,
            "decimals": 6,
        },
        "txCount": evm.tx_count(RPC_URL, address),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[seller] " + fmt % args + "\n")

    def _json(self, code, obj, extra_headers=None):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _challenge(self, address, reason="payment required"):
        cid = evm.chain_id(RPC_URL)
        challenge = {
            "x402Version": 1,
            "error": reason,
            "accepts": [{
                "scheme": "agent-mandate",
                "network": f"eip155:{cid}",
                "asset": TOKEN,
                "payTo": PAY_TO,
                "amount": str(PRICE),
                "settlementContract": SETTLEMENT,
                "taskRef": task_ref_for(address),
                "resource": f"/x402/hsk-wallet-report?address={address}",
                "description": DESCRIPTION,
                "mimeType": "application/json",
            }],
        }
        header = base64.b64encode(json.dumps(challenge).encode()).decode()
        return self._json(402, challenge, {"PAYMENT-REQUIRED": header})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._json(200, {
                "service": "AgentMandate — HSK Wallet Report (x402 / HSP shape)",
                "paidEndpoint": "/x402/hsk-wallet-report?address=0x...",
                "priceAtomic": PRICE,
                "asset": TOKEN,
                "payTo": PAY_TO,
                "settlementContract": SETTLEMENT,
                "rpc": RPC_URL,
            })
        if parsed.path != "/x402/hsk-wallet-report":
            return self._json(404, {"error": "not found"})
        address = (parse_qs(parsed.query).get("address") or [""])[0]
        if not (address.startswith("0x") and len(address) == 42):
            return self._json(400, {"error": "query ?address=0x<40 hex> required"})
        receipt_header = self.headers.get("X-Payment-Receipt")
        if receipt_header is None:
            return self._challenge(address)
        try:
            receipt_id = int(receipt_header.strip())
        except ValueError:
            return self._challenge(address, "malformed X-Payment-Receipt header")
        ok, reason, receipt = verify_receipt(receipt_id, task_ref_for(address))
        if not ok:
            return self._challenge(address, f"payment not accepted: {reason}")
        report = wallet_report(address)
        mark_redeemed(receipt_id)
        report["paidVia"] = {
            "scheme": "agent-mandate",
            "receiptId": receipt_id,
            "mandateId": receipt["mandateId"],
            "payer": receipt["payer"],
            "payee": receipt["payee"],
            "amount": str(receipt["amount"]),
            "taskRef": receipt["taskRef"],
            "settledAt": receipt["settledAt"],
            "settlementContract": SETTLEMENT,
        }
        if EXPLORER:
            report["paidVia"]["explorer"] = f"{EXPLORER}/address/{SETTLEMENT}"
        return self._json(200, report)


def main():
    missing = [k for k, v in {"SETTLEMENT_ADDRESS": SETTLEMENT,
                              "TOKEN_ADDRESS": TOKEN, "PAY_TO": PAY_TO}.items() if not v]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)}")
    print(f"[seller] chain={evm.chain_id(RPC_URL)} rpc={RPC_URL} "
          f"settlement={SETTLEMENT} token={TOKEN} payTo={PAY_TO} price={PRICE}",
          flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
