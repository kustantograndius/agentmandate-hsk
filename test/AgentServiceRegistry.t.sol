// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {TestBase} from "./TestBase.sol";
import {AgentServiceRegistry} from "../src/AgentServiceRegistry.sol";
import {MockUSDC} from "../src/mocks/MockUSDC.sol";

contract AgentServiceRegistryTest is TestBase {
    AgentServiceRegistry internal registry;
    MockUSDC internal usdc;
    address internal agent = address(0xA6E77);
    address internal other = address(0x07E12);

    function setUp() public {
        registry = new AgentServiceRegistry();
        usdc = new MockUSDC();
    }

    function testRegisterAndRead() public {
        vm.prank(agent);
        uint256 id = registry.registerService("Base Wallet Report", "https://example.net/x402/report", address(usdc), 5e6);
        AgentServiceRegistry.Service memory s = registry.getService(id);
        assertEq(s.agent, agent, "agent");
        assertEq(s.token, address(usdc), "token");
        assertEq(s.price, 5e6, "price");
        assertTrue(s.active, "active");
        assertTrue(keccak256(bytes(s.name)) == keccak256(bytes("Base Wallet Report")), "name");
    }

    function testUpdateByAgent() public {
        vm.prank(agent);
        uint256 id = registry.registerService("Svc", "https://example.net", address(usdc), 1e6);
        vm.prank(agent);
        registry.updateService(id, 2e6, false);
        AgentServiceRegistry.Service memory s = registry.getService(id);
        assertEq(s.price, 2e6, "price updated");
        assertTrue(!s.active, "deactivated");
    }

    function testUpdateByStrangerReverts() public {
        vm.prank(agent);
        uint256 id = registry.registerService("Svc", "https://example.net", address(usdc), 1e6);
        vm.prank(other);
        vm.expectRevert();
        registry.updateService(id, 9e6, true);
    }
}
