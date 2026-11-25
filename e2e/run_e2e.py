#!/usr/bin/env python3
"""AgentMandate — full local end-to-end test.

Chain: local anvil (chainId 31337) + MockUSDC. No external network, no
real funds. Orchestrates the complete HSP payment shape:

  forge build + forge test
  -> deploy MockUSDC, AgentServiceRegistry, A2ASettlement
  -> seller registers its service in the registry
  -> seller x402 server starts
  -> buyer agent: 402 challenge -> principal creates mandate + approves
     -> agent signs EIP-712 authorization -> settle() on-chain -> receipt
     -> redeem receipt -> wallet report delivered
  -> negative checks: replayed receipt rejected, bogus receipt rejected,
     unpaid request still 402
  -> balance assertions: seller received exactly the price; principal's
     spent total matches the mandate accounting

Roles use anvil's well-known PUBLIC dev accounts (insecure by design,
local only): principal = account 0, agent = account 1, seller = account 2
(address only; its one registration tx goes through anvil's unlocked
account).

Usage: python3 e2e/run_e2e.py   (writes docs/e2e-local-20261122.log and
docs/E2E.md in the repo)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib import evm  # noqa: E402

FOUNDRY = os.path.expanduser("~/.foundry/bin")
ANVIL_PORT = 8546
RPC = f"http://127.0.0.1:{ANVIL_PORT}"
SELLER_PORT = 18777
SELLER_URL = f"http://127.0.0.1:{SELLER_PORT}"
PRICE = 10000  # 0.01 USDC

# anvil/hardhat well-known public dev keys (local testing only).
PRINCIPAL_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
AGENT_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

LOG = []


def say(msg):
    print(msg, flush=True)
    LOG.append(msg)


def run(cmd, env=None, timeout=300, cwd=REPO):
    full_env = dict(os.environ)
    full_env["PATH"] = FOUNDRY + ":" + full_env["PATH"]
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=full_env)
    return proc


def forge_create(contract, pk, rpc):
    proc = run(["forge", "create", contract, "--rpc-url", rpc,
                "--private-key", pk, "--broadcast"])
    out = proc.stdout + proc.stderr
    m = re.search(r"Deployed to: (0x[0-9a-fA-F]{40})", out)
    if proc.returncode != 0 or not m:
        raise RuntimeError(f"forge create {contract} failed:\n{out[-1500:]}")
    tx = re.search(r"Transaction hash: (0x[0-9a-fA-F]{64})", out)
    return m.group(1), (tx.group(1) if tx else "")


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_for(fn, what, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if fn():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {what}")


def main():
    procs = []
    try:
        say("=" * 72)
        say("AgentMandate E2E — local anvil + MockUSDC + x402-HSK seller/buyer")
        say("=" * 72)

        # 1. Build + unit tests.
        proc = run(["forge", "build"])
        if proc.returncode != 0:
            raise RuntimeError("forge build failed:\n" + proc.stdout[-1000:] + proc.stderr[-1000:])
        say("[1] forge build: OK")
        proc = run(["forge", "test"])
        suites = re.findall(r"(\d+) passed; (\d+) failed", proc.stdout)
        passed = sum(int(a) for a, _ in suites)
        failed = sum(int(b) for _, b in suites)
        say(f"[1] forge test: {passed} passed; {failed} failed (all suites)")
        if proc.returncode != 0 or failed != 0:
            raise RuntimeError("forge test failed")

        # 2. Anvil.
        anvil_log = open(os.path.join(REPO, "e2e", "anvil.log"), "w")
        anvil = subprocess.Popen(
            [os.path.join(FOUNDRY, "anvil"), "--port", str(ANVIL_PORT),
             "--chain-id", "31337", "--silent"],
            stdout=anvil_log, stderr=anvil_log)
        procs.append(anvil)
        wait_for(lambda: evm.chain_id(RPC) == 31337, "anvil RPC")
        accounts = evm.rpc(RPC, "eth_accounts", [])
        principal = evm.wallet_address(PRINCIPAL_PK)
        agent = evm.wallet_address(AGENT_PK)
        seller = accounts[2]
        say(f"[2] anvil up (chainId 31337): principal={principal} agent={agent} seller={seller}")
        assert principal.lower() == accounts[0].lower() and agent.lower() == accounts[1].lower()

        # 3. Deploy.
        mock, mock_tx = forge_create("src/mocks/MockUSDC.sol:MockUSDC", PRINCIPAL_PK, RPC)
        registry, reg_tx = forge_create("src/AgentServiceRegistry.sol:AgentServiceRegistry", PRINCIPAL_PK, RPC)
        settlement, set_tx = forge_create("src/A2ASettlement.sol:A2ASettlement", PRINCIPAL_PK, RPC)
        say(f"[3] deployed: MockUSDC={mock} (tx {mock_tx})")
        say(f"    Registry={registry} (tx {reg_tx})")
        say(f"    Settlement={settlement} (tx {set_tx})")

        # 4. Fund principal with mock USDC; seller registers the service.
        evm.send_tx(RPC, PRINCIPAL_PK, mock, "mint(address,uint256)", principal, 1_000_000_000)
        say(f"[4] minted 1000 mUSDC to principal; balance={evm.balance_of(RPC, mock, principal)}")
        out = evm.cast("send", registry,
                       "registerService(string,string,address,uint256)",
                       "HSK Wallet Report", f"{SELLER_URL}/x402/hsk-wallet-report",
                       mock, str(PRICE),
                       "--unlocked", "--from", seller, "--rpc-url", RPC, "--json")
        reg_receipt = evm.wait_receipt(RPC, json.loads(out)["transactionHash"])
        svc_log = evm.find_event(reg_receipt, "ServiceRegistered(uint256,address,address,uint256)")
        service_id = int(svc_log["topics"][1], 16)
        say(f"[4] seller registered service #{service_id} at price {PRICE} (tx {reg_receipt['_hash']})")

        # 5. Seller server.
        seller_env = {"RPC_URL": RPC, "SETTLEMENT_ADDRESS": settlement,
                      "TOKEN_ADDRESS": mock, "PAY_TO": seller,
                      "PRICE_ATOMIC": str(PRICE), "PORT": str(SELLER_PORT)}
        redeemed = os.path.join(REPO, "server", "redeemed-hsk.json")
        if os.path.exists(redeemed):
            os.remove(redeemed)
        seller_log = open(os.path.join(REPO, "e2e", "seller.log"), "w")
        seller_proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "server", "seller.py")],
            env={**os.environ, **seller_env}, stdout=seller_log, stderr=seller_log)
        procs.append(seller_proc)
        wait_for(lambda: http_get(SELLER_URL + "/")[0] == 200, "seller server")
        say("[5] seller x402 server up")

        # 6. Buyer agent runs the whole payment flow.
        buyer_env = {"PRINCIPAL_PK": PRINCIPAL_PK, "AGENT_PK": AGENT_PK}
        proc = run([sys.executable, os.path.join(REPO, "agent", "buyer.py"),
                    "--rpc", RPC, "--settlement", settlement,
                    "--registry", registry, "--service-id", str(service_id),
                    "--seller", SELLER_URL, "--address", agent],
                   env=buyer_env, timeout=240)
        say("[6] buyer agent output:")
        for line in (proc.stdout + proc.stderr).splitlines():
            say("    " + line)
        if proc.returncode != 0:
            raise RuntimeError("buyer agent failed")

        # 7. Assertions on-chain + negative HTTP checks.
        seller_bal = evm.balance_of(RPC, mock, seller)
        receipt = evm.read_receipt(RPC, settlement, 1)
        mandate = evm.read_mandate(RPC, settlement, 1)
        say(f"[7] seller mUSDC balance = {seller_bal} (expect {PRICE})")
        say(f"    receipt #1: {json.dumps(receipt)}")
        say(f"    mandate #1 spent = {mandate['spent']} (expect {PRICE})")
        assert seller_bal == PRICE, "seller balance mismatch"
        assert receipt["payee"].lower() == seller.lower() and receipt["amount"] == PRICE
        assert mandate["spent"] == PRICE

        url = f"{SELLER_URL}/x402/hsk-wallet-report?address={agent}"
        st, _ = http_get(url, {"X-Payment-Receipt": "1"})
        say(f"    replay of receipt #1 -> HTTP {st} (expect 402)")
        assert st == 402
        st, _ = http_get(url, {"X-Payment-Receipt": "999"})
        say(f"    bogus receipt #999 -> HTTP {st} (expect 402)")
        assert st == 402
        st, _ = http_get(url)
        say(f"    unpaid request -> HTTP {st} (expect 402)")
        assert st == 402

        say("")
        say("E2E RESULT: PASS — mandate created, EIP-712 authorization signed, "
            "settlement on-chain, receipt verified, deliverable served, "
            "replay/bogus/unpaid all rejected.")
        result = {"settlement": settlement, "registry": registry, "mock": mock,
                  "serviceId": service_id, "receipt": receipt,
                  "sellerBalance": seller_bal}
        return result
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


if __name__ == "__main__":
    res = main()
    log_path = os.path.join(REPO, "docs", "e2e-local-20261122.log")
    with open(log_path, "w") as f:
        f.write("\n".join(LOG) + "\n")
    print(f"\nlog written: {log_path}")
