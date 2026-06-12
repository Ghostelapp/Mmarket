// DEPRECATED: historyczny prototyp. Nie wdrażać. Aktywny kontrakt:
// ../contracts/contracts/MaskMarketplaceEscrow.sol
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function transfer(address to, uint256 value) external returns (bool);
}

contract MaskEscrow {
    enum Status {
        CREATED,
        FUNDED,
        SHIPPED,
        DELIVERED,
        COMPLETED,
        DISPUTED,
        REFUNDED,
        CANCELLED
    }

    struct Order {
        uint256 id;
        address buyer;
        address seller;
        address token;
        uint256 amount;
        uint256 feeBps;
        Status status;
    }

    address public immutable platformFeeWallet;
    address public admin;
    address public pendingAdmin;
    bool public paused;
    bool private entered;
    uint256 public nextOrderId;

    mapping(uint256 => Order) public orders;
    mapping(address => bool) public allowedTokens;

    event OrderCreated(uint256 indexed orderId, address indexed buyer, address indexed seller, uint256 amount);
    event OrderFunded(uint256 indexed orderId);
    event MarkedShipped(uint256 indexed orderId);
    event DeliveryConfirmed(uint256 indexed orderId);
    event DisputeOpened(uint256 indexed orderId);
    event Resolved(uint256 indexed orderId, bool releaseSeller);
    event AdminTransferStarted(address indexed pendingAdmin);
    event AdminTransferred(address indexed newAdmin);
    event TokenAllowanceChanged(address indexed token, bool allowed);

    modifier onlyAdmin() {
        require(msg.sender == admin, "not admin");
        _;
    }

    modifier notPaused() {
        require(!paused, "paused");
        _;
    }

    modifier nonReentrant() {
        require(!entered, "reentrant call");
        entered = true;
        _;
        entered = false;
    }

    constructor(address _platformFeeWallet, address[] memory initialTokens) {
        require(_platformFeeWallet != address(0), "bad fee wallet");
        platformFeeWallet = _platformFeeWallet;
        admin = msg.sender;
        for (uint256 i = 0; i < initialTokens.length; i++) {
            require(initialTokens[i] != address(0), "bad token");
            allowedTokens[initialTokens[i]] = true;
            emit TokenAllowanceChanged(initialTokens[i], true);
        }
    }

    function setPause(bool value) external onlyAdmin {
        paused = value;
    }

    function setTokenAllowed(address token, bool allowed) external onlyAdmin {
        require(token != address(0), "bad token");
        allowedTokens[token] = allowed;
        emit TokenAllowanceChanged(token, allowed);
    }

    function startAdminTransfer(address nextAdmin) external onlyAdmin {
        require(nextAdmin != address(0), "bad admin");
        pendingAdmin = nextAdmin;
        emit AdminTransferStarted(nextAdmin);
    }

    function acceptAdminTransfer() external {
        require(msg.sender == pendingAdmin, "not pending admin");
        admin = pendingAdmin;
        pendingAdmin = address(0);
        emit AdminTransferred(admin);
    }

    function createOrder(address seller, address token, uint256 amount, uint256 feeBps) external notPaused returns (uint256) {
        require(seller != address(0), "bad seller");
        require(seller != msg.sender, "buyer is seller");
        require(token != address(0), "bad token");
        require(allowedTokens[token], "token not allowed");
        require(amount > 0, "bad amount");
        require(feeBps <= 2500, "fee too high");

        uint256 orderId = ++nextOrderId;
        orders[orderId] = Order({
            id: orderId,
            buyer: msg.sender,
            seller: seller,
            token: token,
            amount: amount,
            feeBps: feeBps,
            status: Status.CREATED
        });

        emit OrderCreated(orderId, msg.sender, seller, amount);
        return orderId;
    }

    function fundOrder(uint256 orderId) external notPaused nonReentrant {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "not buyer");
        require(o.status == Status.CREATED, "bad status");
        _safeTransferFrom(o.token, msg.sender, address(this), o.amount);
        o.status = Status.FUNDED;
        emit OrderFunded(orderId);
    }

    function markShipped(uint256 orderId) external notPaused {
        Order storage o = orders[orderId];
        require(msg.sender == o.seller, "not seller");
        require(o.status == Status.FUNDED, "bad status");
        o.status = Status.SHIPPED;
        emit MarkedShipped(orderId);
    }

    function confirmDelivery(uint256 orderId) external notPaused nonReentrant {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "not buyer");
        require(o.status == Status.SHIPPED, "bad status");
        _release(orderId);
        emit DeliveryConfirmed(orderId);
    }

    function openDispute(uint256 orderId) external notPaused {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer || msg.sender == o.seller, "not participant");
        require(o.status == Status.FUNDED || o.status == Status.SHIPPED, "bad status");
        o.status = Status.DISPUTED;
        emit DisputeOpened(orderId);
    }

    function resolveDispute(uint256 orderId, bool releaseSeller) external onlyAdmin nonReentrant {
        Order storage o = orders[orderId];
        require(o.status == Status.DISPUTED, "bad status");

        if (releaseSeller) {
            _release(orderId);
        } else {
            o.status = Status.REFUNDED;
            _safeTransfer(o.token, o.buyer, o.amount);
        }
        emit Resolved(orderId, releaseSeller);
    }

    function cancelOrder(uint256 orderId) external notPaused {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "not buyer");
        require(o.status == Status.CREATED, "bad status");
        o.status = Status.CANCELLED;
    }

    function _release(uint256 orderId) internal {
        Order storage o = orders[orderId];
        uint256 fee = (o.amount * o.feeBps) / 10000;
        uint256 sellerPart = o.amount - fee;
        o.status = Status.COMPLETED;
        if (fee > 0) {
            _safeTransfer(o.token, platformFeeWallet, fee);
        }
        _safeTransfer(o.token, o.seller, sellerPart);
    }

    function _safeTransfer(address token, address to, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IERC20.transfer.selector, to, amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "transfer failed");
    }

    function _safeTransferFrom(address token, address from, address to, uint256 amount) internal {
        (bool success, bytes memory data) = token.call(
            abi.encodeWithSelector(IERC20.transferFrom.selector, from, to, amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "transferFrom failed");
    }
}
