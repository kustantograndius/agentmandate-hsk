// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AgentServiceRegistry — discovery layer for agent services on HSK Chain.
/// @notice Agents list payable services (name, endpoint, token, price). Buyer
///         agents discover a service here, then pay for it through
///         A2ASettlement mandates. Adapted from the AgentMark registry pattern
///         (BNB Chain) to HSK Chain for the Horizon Hackathon, Track 2.
contract AgentServiceRegistry {
    struct Service {
        address agent; // owner / payee of the service
        string name;
        string endpoint; // URL or agent-card location
        address token; // accepted settlement token
        uint256 price; // price per call, in token base units
        bool active;
    }

    uint256 public nextServiceId = 1;
    mapping(uint256 => Service) public services;

    event ServiceRegistered(uint256 indexed serviceId, address indexed agent, address token, uint256 price);
    event ServiceUpdated(uint256 indexed serviceId, uint256 price, bool active);

    error NotServiceAgent();
    error InvalidService();

    function registerService(string calldata name, string calldata endpoint, address token, uint256 price)
        external
        returns (uint256 serviceId)
    {
        if (token == address(0) || bytes(name).length == 0) revert InvalidService();
        serviceId = nextServiceId++;
        services[serviceId] = Service({
            agent: msg.sender, name: name, endpoint: endpoint, token: token, price: price, active: true
        });
        emit ServiceRegistered(serviceId, msg.sender, token, price);
    }

    function updateService(uint256 serviceId, uint256 price, bool active) external {
        Service storage s = services[serviceId];
        if (s.agent == address(0)) revert InvalidService();
        if (msg.sender != s.agent) revert NotServiceAgent();
        s.price = price;
        s.active = active;
        emit ServiceUpdated(serviceId, price, active);
    }

    function getService(uint256 serviceId) external view returns (Service memory) {
        return services[serviceId];
    }
}
