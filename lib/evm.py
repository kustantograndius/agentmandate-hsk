"""Shared EVM helpers for the AgentMandate x402-HSK layer.

Python stdlib + pycryptodome (keccak-256) + the Foundry `cast` binary for
EIP-712 signing and transaction submission. No web3 dependency, mirroring
the hermetic, dependency-free style of the contracts in this repo.

Used by server/seller.py (receipt verification, read-only) and
agent/buyer.py (mandate creation, signing, settlement).
"""
import http.client
import json
import os
import subprocess
import time
import urllib.request
import urllib.error

from Crypto.Hash import keccak

CAST = os.path.expanduser("~/.foundry/bin/cast")
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}


def keccak256(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def selector(signature: str) -> bytes:
    return keccak256(signature.encode())[:4]


def cast(*args, timeout=120):
    """Run cast, return stdout stripped. Raises RuntimeError on failure."""
    proc = subprocess.run([CAST, *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"cast {' '.join(args[:2])} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def wallet_address(private_key: str) -> str:
    return cast("wallet", "address", "--private-key", private_key)


def sign_typed_data(private_key: str, typed_data: dict) -> str:
    """EIP-712 sign a typed-data dict via `cast wallet sign --data`."""
    return cast("wallet", "sign", "--data", json.dumps(typed_data),
                "--private-key", private_key)


def rpc(rpc_url, method, params, timeout=30, retries=5):
    """JSON-RPC call with transport retries.

    Public RPC endpoints intermittently drop connections (observed on
    HashKey testnet: RemoteDisconnected / IncompleteRead / timeouts).
    Retrying the identical request is safe: reads are idempotent, and a
    re-sent raw transaction carries the same bytes, hence the same hash.
    JSON-RPC error responses are deterministic and are NOT retried.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    delay = 1.0
    last_exc = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(rpc_url, data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            if "error" in out:
                raise RuntimeError(f"RPC {method} error: {out['error']}")
            return out["result"]
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"RPC {method} failed after {retries} attempts: {last_exc}")


def chain_id(rpc_url) -> int:
    return int(rpc(rpc_url, "eth_chainId", []), 16)


def _word(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _addr_word(address: str) -> bytes:
    return b"\x00" * 12 + bytes.fromhex(address[2:])


def eth_call(rpc_url, to, data: bytes) -> bytes:
    raw = rpc(rpc_url, "eth_call", [{"to": to, "data": "0x" + data.hex()}, "latest"])
    return bytes.fromhex(raw[2:])


def call_uint(rpc_url, to, signature, *uint_args) -> int:
    data = selector(signature) + b"".join(_word(a) for a in uint_args)
    return int.from_bytes(eth_call(rpc_url, to, data), "big")


def balance_of(rpc_url, token, account) -> int:
    data = selector("balanceOf(address)") + _addr_word(account)
    return int.from_bytes(eth_call(rpc_url, token, data), "big")


def native_balance(rpc_url, account) -> int:
    return int(rpc(rpc_url, "eth_getBalance", [account, "latest"]), 16)


def tx_count(rpc_url, account) -> int:
    return int(rpc(rpc_url, "eth_getTransactionCount", [account, "latest"]), 16)


# --- A2ASettlement views -------------------------------------------------

def read_receipt(rpc_url, settlement, receipt_id: int) -> dict:
    """receipts(uint256) -> (mandateId, payer, payee, token, amount, taskRef, settledAt)."""
    data = selector("receipts(uint256)") + _word(receipt_id)
    raw = eth_call(rpc_url, settlement, data)
    if len(raw) < 7 * 32:
        raise RuntimeError("short receipts() response")
    w = [raw[i * 32:(i + 1) * 32] for i in range(7)]
    return {
        "mandateId": int.from_bytes(w[0], "big"),
        "payer": "0x" + w[1][12:].hex(),
        "payee": "0x" + w[2][12:].hex(),
        "token": "0x" + w[3][12:].hex(),
        "amount": int.from_bytes(w[4], "big"),
        "taskRef": "0x" + w[5].hex(),
        "settledAt": int.from_bytes(w[6], "big"),
    }


def read_mandate(rpc_url, settlement, mandate_id: int) -> dict:
    """mandates(uint256) -> (principal, agent, token, perTxCap, totalCap, spent, expiry, revoked)."""
    data = selector("mandates(uint256)") + _word(mandate_id)
    raw = eth_call(rpc_url, settlement, data)
    if len(raw) < 8 * 32:
        raise RuntimeError("short mandates() response")
    w = [raw[i * 32:(i + 1) * 32] for i in range(8)]
    return {
        "principal": "0x" + w[0][12:].hex(),
        "agent": "0x" + w[1][12:].hex(),
        "token": "0x" + w[2][12:].hex(),
        "perTxCap": int.from_bytes(w[3], "big"),
        "totalCap": int.from_bytes(w[4], "big"),
        "spent": int.from_bytes(w[5], "big"),
        "expiry": int.from_bytes(w[6], "big"),
        "revoked": int.from_bytes(w[7], "big") != 0,
    }


# --- Transactions (via cast send) ----------------------------------------

def send_tx(rpc_url, private_key, to, signature, *args) -> dict:
    """Send a contract call with cast; return the parsed tx receipt."""
    out = cast("send", to, signature, *[str(a) for a in args],
               "--private-key", private_key, "--rpc-url", rpc_url, "--json",
               "--legacy")
    tx = json.loads(out)
    tx_hash = tx["transactionHash"]
    return wait_receipt(rpc_url, tx_hash)


def wait_receipt(rpc_url, tx_hash, timeout=120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        receipt = rpc(rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if receipt:
            if receipt.get("status") != "0x1":
                raise RuntimeError(f"tx {tx_hash} reverted")
            receipt["_hash"] = tx_hash
            return receipt
        time.sleep(1.0)
    raise RuntimeError(f"tx {tx_hash} not mined within {timeout}s")


def event_topic(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()


def find_event(receipt, signature):
    """Return the log matching an event signature, or None."""
    topic0 = event_topic(signature)
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if topics and topics[0].lower() == topic0.lower():
            return log
    return None
