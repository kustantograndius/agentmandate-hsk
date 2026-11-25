// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal ERC-20 surface used by the settlement contract.
interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title A2ASettlement — mandate-based agent-to-agent settlement on HSK Chain.
/// @notice Implements the payment shape published for HSP (HashKey Settlement
///         Protocol): a principal issues a capped, expiring, revocable payment
///         mandate to its agent; the agent authorizes each individual payment
///         with an EIP-712 wallet signature; any relayer may settle, funds move
///         principal -> payee by ERC-20 transferFrom, and every settlement
///         writes an auditable on-chain receipt (the "financial credential").
/// @dev Fail-closed: every check runs before any state change or transfer, and
///      each signed authorization can be used exactly once (replay-proof).
contract A2ASettlement {
    struct Mandate {
        address principal; // funds owner who created the mandate
        address agent; // agent allowed to authorize payments under it
        address token; // settlement token (USDC on HSK Chain)
        uint256 perTxCap; // max amount per single payment
        uint256 totalCap; // max cumulative amount over the mandate lifetime
        uint256 spent; // cumulative amount already settled
        uint64 expiry; // unix time after which the mandate is dead
        bool revoked;
    }

    struct Receipt {
        uint256 mandateId;
        address payer;
        address payee;
        address token;
        uint256 amount;
        bytes32 taskRef; // hash referencing the paid task / deliverable
        uint64 settledAt;
    }

    bytes32 public constant AUTH_TYPEHASH = keccak256(
        "PaymentAuthorization(uint256 mandateId,address payee,uint256 amount,bytes32 taskRef,bytes32 nonce,uint64 deadline)"
    );

    bytes32 public immutable domainSeparator;

    uint256 public nextMandateId = 1;
    uint256 public nextReceiptId = 1;

    mapping(uint256 => Mandate) public mandates;
    mapping(uint256 => Receipt) public receipts;
    mapping(bytes32 => bool) public usedAuthorizations;

    event MandateCreated(
        uint256 indexed mandateId,
        address indexed principal,
        address indexed agent,
        address token,
        uint256 perTxCap,
        uint256 totalCap,
        uint64 expiry
    );
    event MandateRevoked(uint256 indexed mandateId);
    event Settled(
        uint256 indexed receiptId, uint256 indexed mandateId, address indexed payee, uint256 amount, bytes32 taskRef
    );

    error MandateMissing();
    error MandateInactive();
    error MandateExpired();
    error AuthExpired();
    error CapExceeded();
    error AuthUsed();
    error BadSignature();
    error NotPrincipal();
    error InvalidTerms();
    error TransferFailed();

    constructor() {
        domainSeparator = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes("A2ASettlement")),
                keccak256(bytes("1")),
                block.chainid,
                address(this)
            )
        );
    }

    /// @notice Create a payment mandate for `agent`. Caller is the principal.
    /// @dev The principal must separately ERC-20 `approve` this contract for
    ///      at least `totalCap` of `token` for settlements to succeed.
    function createMandate(address agent, address token, uint256 perTxCap, uint256 totalCap, uint64 expiry)
        external
        returns (uint256 mandateId)
    {
        if (agent == address(0) || token == address(0)) revert InvalidTerms();
        if (perTxCap == 0 || totalCap < perTxCap) revert InvalidTerms();
        if (expiry <= block.timestamp) revert InvalidTerms();

        mandateId = nextMandateId++;
        mandates[mandateId] = Mandate({
            principal: msg.sender,
            agent: agent,
            token: token,
            perTxCap: perTxCap,
            totalCap: totalCap,
            spent: 0,
            expiry: expiry,
            revoked: false
        });
        emit MandateCreated(mandateId, msg.sender, agent, token, perTxCap, totalCap, expiry);
    }

    /// @notice Revoke a mandate. Only its principal. Irreversible.
    function revokeMandate(uint256 mandateId) external {
        Mandate storage m = mandates[mandateId];
        if (m.principal == address(0)) revert MandateMissing();
        if (msg.sender != m.principal) revert NotPrincipal();
        m.revoked = true;
        emit MandateRevoked(mandateId);
    }

    /// @notice Settle one payment authorized by the mandate's agent.
    /// @param signature EIP-712 signature of the PaymentAuthorization by the agent.
    /// @return receiptId Id of the on-chain receipt written for this payment.
    function settle(
        uint256 mandateId,
        address payee,
        uint256 amount,
        bytes32 taskRef,
        bytes32 nonce,
        uint64 deadline,
        bytes calldata signature
    ) external returns (uint256 receiptId) {
        Mandate storage m = mandates[mandateId];
        if (m.principal == address(0)) revert MandateMissing();
        if (m.revoked) revert MandateInactive();
        if (block.timestamp > m.expiry) revert MandateExpired();
        if (block.timestamp > deadline) revert AuthExpired();
        if (amount == 0 || amount > m.perTxCap || m.spent + amount > m.totalCap) revert CapExceeded();

        bytes32 structHash = keccak256(abi.encode(AUTH_TYPEHASH, mandateId, payee, amount, taskRef, nonce, deadline));
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", domainSeparator, structHash));
        if (usedAuthorizations[digest]) revert AuthUsed();
        if (_recover(digest, signature) != m.agent) revert BadSignature();

        usedAuthorizations[digest] = true;
        m.spent += amount;

        if (!IERC20(m.token).transferFrom(m.principal, payee, amount)) revert TransferFailed();

        receiptId = nextReceiptId++;
        receipts[receiptId] = Receipt({
            mandateId: mandateId,
            payer: m.principal,
            payee: payee,
            token: m.token,
            amount: amount,
            taskRef: taskRef,
            settledAt: uint64(block.timestamp)
        });
        emit Settled(receiptId, mandateId, payee, amount, taskRef);
    }

    function _recover(bytes32 digest, bytes calldata signature) internal pure returns (address) {
        if (signature.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        return ecrecover(digest, v, r, s);
    }
}
