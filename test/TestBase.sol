// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal cheatcode interface + assertions so the test suite runs
///         with zero external dependencies (no forge-std download needed).
interface Vm {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address msgSender) external;
    function warp(uint256 newTimestamp) external;
    function expectRevert() external;
}

abstract contract TestBase {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    function assertTrue(bool cond, string memory what) internal pure {
        if (!cond) revert(string.concat("assertTrue failed: ", what));
    }

    function assertEq(uint256 a, uint256 b, string memory what) internal pure {
        if (a != b) revert(string.concat("assertEq failed: ", what));
    }

    function assertEq(address a, address b, string memory what) internal pure {
        if (a != b) revert(string.concat("assertEq(addr) failed: ", what));
    }
}
