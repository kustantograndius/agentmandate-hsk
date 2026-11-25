// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {TestBase} from "./TestBase.sol";
import {A2ASettlement} from "../src/A2ASettlement.sol";
import {MockUSDC} from "../src/mocks/MockUSDC.sol";

contract A2ASettlementTest is TestBase {
    uint256 internal constant PRINCIPAL_PK = 1;
    uint256 internal constant AGENT_PK = 2;
    uint256 internal constant STRANGER_PK = 3;

    A2ASettlement internal settlement;
    MockUSDC internal usdc;

    address internal principal;
    address internal agent;
    address internal payee = address(0xBEEF);

    uint256 internal mandateId;
    uint64 internal expiry;

    function setUp() public {
        settlement = new A2ASettlement();
        usdc = new MockUSDC();
        principal = vm.addr(PRINCIPAL_PK);
        agent = vm.addr(AGENT_PK);

        usdc.mint(principal, 1_000e6);
        expiry = uint64(block.timestamp) + 7 days;

        vm.prank(principal);
        mandateId = settlement.createMandate(agent, address(usdc), 50e6, 200e6, expiry);

        vm.prank(principal);
        usdc.approve(address(settlement), 200e6);
    }

    function _sign(uint256 pk, uint256 mid, address to, uint256 amount, bytes32 taskRef, bytes32 nonce, uint64 deadline)
        internal
        returns (bytes memory)
    {
        bytes32 structHash =
            keccak256(abi.encode(settlement.AUTH_TYPEHASH(), mid, to, amount, taskRef, nonce, deadline));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", settlement.domainSeparator(), structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function testMandateTermsStored() public {
        (address p, address a, address t, uint256 perTx, uint256 total, uint256 spent, uint64 exp, bool revoked) =
            settlement.mandates(mandateId);
        assertEq(p, principal, "principal");
        assertEq(a, agent, "agent");
        assertEq(t, address(usdc), "token");
        assertEq(perTx, 50e6, "perTxCap");
        assertEq(total, 200e6, "totalCap");
        assertEq(spent, 0, "spent starts 0");
        assertEq(uint256(exp), uint256(expiry), "expiry");
        assertTrue(!revoked, "not revoked");
    }

    function testSettleHappyPath() public {
        bytes32 taskRef = keccak256("task-1: wallet report");
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 10e6, taskRef, bytes32(uint256(1)), expiry);
        uint256 receiptId = settlement.settle(mandateId, payee, 10e6, taskRef, bytes32(uint256(1)), expiry, sig);

        assertEq(usdc.balanceOf(payee), 10e6, "payee received");
        assertEq(usdc.balanceOf(principal), 990e6, "principal debited");
        (, , , , , uint256 spentAfter, , ) = settlement.mandates(mandateId);
        assertEq(spentAfter, 10e6, "spent tracked");

        (uint256 rMid, address rPayer, address rPayee, address rToken, uint256 rAmount, bytes32 rTask, uint64 rAt) =
            settlement.receipts(receiptId);
        assertEq(rMid, mandateId, "receipt mandate");
        assertEq(rPayer, principal, "receipt payer");
        assertEq(rPayee, payee, "receipt payee");
        assertEq(rToken, address(usdc), "receipt token");
        assertEq(rAmount, 10e6, "receipt amount");
        assertTrue(rTask == taskRef, "receipt taskRef");
        assertTrue(rAt == uint64(block.timestamp), "receipt time");
    }

    function testSettleOverPerTxCapReverts() public {
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 51e6, bytes32("t"), bytes32(uint256(2)), expiry);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 51e6, bytes32("t"), bytes32(uint256(2)), expiry, sig);
    }

    function testSettleOverTotalCapReverts() public {
        // 4 x 50e6 = 200e6 hits the total cap exactly; the 5th must fail.
        for (uint256 i = 0; i < 4; i++) {
            bytes32 nonce = bytes32(uint256(100 + i));
            bytes memory sig = _sign(AGENT_PK, mandateId, payee, 50e6, bytes32("t"), nonce, expiry);
            settlement.settle(mandateId, payee, 50e6, bytes32("t"), nonce, expiry, sig);
        }
        bytes32 nonce = bytes32(uint256(999));
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 1e6, bytes32("t"), nonce, expiry);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 1e6, bytes32("t"), nonce, expiry, sig);
    }

    function testSettleReplayReverts() public {
        bytes32 nonce = bytes32(uint256(7));
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 5e6, bytes32("t"), nonce, expiry);
        settlement.settle(mandateId, payee, 5e6, bytes32("t"), nonce, expiry, sig);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 5e6, bytes32("t"), nonce, expiry, sig);
    }

    function testSettleBadSignerReverts() public {
        bytes32 nonce = bytes32(uint256(8));
        bytes memory sig = _sign(STRANGER_PK, mandateId, payee, 5e6, bytes32("t"), nonce, expiry);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 5e6, bytes32("t"), nonce, expiry, sig);
    }

    function testSettleAfterRevokeReverts() public {
        vm.prank(principal);
        settlement.revokeMandate(mandateId);
        bytes32 nonce = bytes32(uint256(9));
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 5e6, bytes32("t"), nonce, expiry);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 5e6, bytes32("t"), nonce, expiry, sig);
    }

    function testSettleAfterMandateExpiryReverts() public {
        vm.warp(uint256(expiry) + 1);
        bytes32 nonce = bytes32(uint256(10));
        uint64 deadline = expiry + 1 days;
        bytes memory sig = _sign(AGENT_PK, mandateId, payee, 5e6, bytes32("t"), nonce, deadline);
        vm.expectRevert();
        settlement.settle(mandateId, payee, 5e6, bytes32("t"), nonce, deadline, sig);
    }

    function testRevokeByNonPrincipalReverts() public {
        vm.prank(payee);
        vm.expectRevert();
        settlement.revokeMandate(mandateId);
    }
}
