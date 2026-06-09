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
    bool public paused;
    uint256 public nextOrderId;

    mapping(uint256 => Order) public orders;

    event OrderCreated(uint256 indexed orderId, address indexed buyer, address indexed seller, uint256 amount);
    event OrderFunded(uint256 indexed orderId);
    event MarkedShipped(uint256 indexed orderId);
    event DeliveryConfirmed(uint256 indexed orderId);
    event DisputeOpened(uint256 indexed orderId);
    event Resolved(uint256 indexed orderId, bool releaseSeller);

    modifier onlyAdmin() {
        require(msg.sender == admin, "not admin");
        _;
    }

    modifier notPaused() {
        require(!paused, "paused");
        _;
    }

    constructor(address _platformFeeWallet) {
        require(_platformFeeWallet != address(0), "bad fee wallet");
        platformFeeWallet = _platformFeeWallet;
        admin = msg.sender;
    }

    function setPause(bool value) external onlyAdmin {
        paused = value;
    }

    function createOrder(address seller, address token, uint256 amount, uint256 feeBps) external notPaused returns (uint256) {
        require(seller != address(0), "bad seller");
        require(token != address(0), "bad token");
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

    function fundOrder(uint256 orderId) external notPaused {
        Order storage o = orders[orderId];
        require(msg.sender == o.buyer, "not buyer");
        require(o.status == Status.CREATED, "bad status");
        IERC20(o.token).transferFrom(msg.sender, address(this), o.amount);
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

    function confirmDelivery(uint256 orderId) external notPaused {
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

    function resolveDispute(uint256 orderId, bool releaseSeller) external onlyAdmin notPaused {
        Order storage o = orders[orderId];
        require(o.status == Status.DISPUTED, "bad status");

        if (releaseSeller) {
            _release(orderId);
        } else {
            o.status = Status.REFUNDED;
            IERC20(o.token).transfer(o.buyer, o.amount);
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
        IERC20(o.token).transfer(platformFeeWallet, fee);
        IERC20(o.token).transfer(o.seller, sellerPart);
    }
}