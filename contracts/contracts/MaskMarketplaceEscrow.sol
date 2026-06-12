// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControlDefaultAdminRules} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract MaskMarketplaceEscrow is AccessControlDefaultAdminRules, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant ARBITER_ROLE = keccak256("ARBITER_ROLE");
    bytes32 public constant TOKEN_MANAGER_ROLE = keccak256("TOKEN_MANAGER_ROLE");
    uint16 public constant MAX_FEE_BPS = 2500;

    enum Status { None, Created, Funded, Shipped, Disputed, Completed, Refunded, Cancelled }

    struct Order {
        bytes32 orderRef;
        address buyer;
        address seller;
        address token;
        uint128 amount;
        uint16 feeBps;
        address feeWallet;
        Status status;
    }

    address public feeWallet;
    mapping(bytes32 => Order) public orders;
    mapping(address => bool) public allowedTokens;

    event OrderCreated(bytes32 indexed orderRef, address indexed buyer, address indexed seller, address token, uint256 amount, uint16 feeBps);
    event OrderFunded(bytes32 indexed orderRef, address indexed buyer, uint256 amount);
    event OrderShipped(bytes32 indexed orderRef, address indexed seller);
    event DisputeOpened(bytes32 indexed orderRef, address indexed openedBy);
    event OrderReleased(bytes32 indexed orderRef, uint256 sellerAmount, uint256 platformFee);
    event OrderRefunded(bytes32 indexed orderRef, uint256 amount);
    event OrderCancelled(bytes32 indexed orderRef);
    event TokenAllowed(address indexed token, bool allowed);
    event FeeWalletChanged(address indexed previousWallet, address indexed nextWallet);

    constructor(address admin, address arbiter, address initialFeeWallet, address[] memory initialTokens)
        AccessControlDefaultAdminRules(2 days, admin)
    {
        require(admin != address(0) && arbiter != address(0) && initialFeeWallet != address(0), "zero address");
        feeWallet = initialFeeWallet;
        _grantRole(ARBITER_ROLE, arbiter);
        _grantRole(TOKEN_MANAGER_ROLE, admin);
        for (uint256 i; i < initialTokens.length; i++) {
            _setToken(initialTokens[i], true);
        }
    }

    function createAndFund(bytes32 orderRef, address seller, address token, uint128 amount, uint16 feeBps)
        external
        whenNotPaused
        nonReentrant
    {
        _createOrder(orderRef, msg.sender, seller, token, amount, feeBps);
        _fundOrder(orders[orderRef]);
    }

    function createOrder(bytes32 orderRef, address seller, address token, uint128 amount, uint16 feeBps)
        external
        whenNotPaused
    {
        _createOrder(orderRef, msg.sender, seller, token, amount, feeBps);
    }

    function fundOrder(bytes32 orderRef) external whenNotPaused nonReentrant {
        Order storage order = orders[orderRef];
        require(msg.sender == order.buyer && order.status == Status.Created, "bad state");
        _fundOrder(order);
    }

    function _createOrder(
        bytes32 orderRef,
        address buyer,
        address seller,
        address token,
        uint128 amount,
        uint16 feeBps
    ) internal {
        require(orderRef != bytes32(0) && orders[orderRef].status == Status.None, "reference used");
        require(seller != address(0) && seller != buyer, "bad seller");
        require(allowedTokens[token] && amount > 0 && feeBps <= MAX_FEE_BPS, "bad terms");
        orders[orderRef] = Order(orderRef, buyer, seller, token, amount, feeBps, feeWallet, Status.Created);
        emit OrderCreated(orderRef, buyer, seller, token, amount, feeBps);
    }

    function _fundOrder(Order storage order) internal {
        order.status = Status.Funded;
        uint256 balanceBefore = IERC20(order.token).balanceOf(address(this));
        IERC20(order.token).safeTransferFrom(order.buyer, address(this), order.amount);
        require(IERC20(order.token).balanceOf(address(this)) - balanceBefore == order.amount, "bad received amount");
        emit OrderFunded(order.orderRef, order.buyer, order.amount);
    }

    function markShipped(bytes32 orderRef) external whenNotPaused {
        Order storage order = orders[orderRef];
        require(msg.sender == order.seller && order.status == Status.Funded, "bad state");
        order.status = Status.Shipped;
        emit OrderShipped(orderRef, msg.sender);
    }

    function confirmDelivery(bytes32 orderRef) external whenNotPaused nonReentrant {
        Order storage order = orders[orderRef];
        require(msg.sender == order.buyer && order.status == Status.Shipped, "bad state");
        _release(order);
    }

    function openDispute(bytes32 orderRef) external whenNotPaused {
        Order storage order = orders[orderRef];
        require(msg.sender == order.buyer || msg.sender == order.seller, "not participant");
        require(order.status == Status.Funded || order.status == Status.Shipped, "bad state");
        order.status = Status.Disputed;
        emit DisputeOpened(orderRef, msg.sender);
    }

    function resolve(bytes32 orderRef, bool releaseSeller) external onlyRole(ARBITER_ROLE) nonReentrant {
        Order storage order = orders[orderRef];
        require(order.status == Status.Disputed, "not disputed");
        releaseSeller ? _release(order) : _refund(order);
    }

    function cancel(bytes32 orderRef) external whenNotPaused {
        Order storage order = orders[orderRef];
        require(msg.sender == order.buyer && order.status == Status.Created, "bad state");
        order.status = Status.Cancelled;
        emit OrderCancelled(orderRef);
    }

    function setFeeWallet(address nextWallet) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(nextWallet != address(0), "zero address");
        emit FeeWalletChanged(feeWallet, nextWallet);
        feeWallet = nextWallet;
    }

    function setTokenAllowed(address token, bool allowed) external onlyRole(TOKEN_MANAGER_ROLE) {
        _setToken(token, allowed);
    }

    function setPaused(bool value) external onlyRole(DEFAULT_ADMIN_ROLE) {
        value ? _pause() : _unpause();
    }

    function _release(Order storage order) internal {
        uint256 fee = (uint256(order.amount) * order.feeBps) / 10_000;
        uint256 sellerAmount = uint256(order.amount) - fee;
        order.status = Status.Completed;
        if (fee > 0) IERC20(order.token).safeTransfer(order.feeWallet, fee);
        IERC20(order.token).safeTransfer(order.seller, sellerAmount);
        emit OrderReleased(order.orderRef, sellerAmount, fee);
    }

    function _refund(Order storage order) internal {
        order.status = Status.Refunded;
        IERC20(order.token).safeTransfer(order.buyer, order.amount);
        emit OrderRefunded(order.orderRef, order.amount);
    }

    function _setToken(address token, bool allowed) internal {
        require(token != address(0), "zero token");
        allowedTokens[token] = allowed;
        emit TokenAllowed(token, allowed);
    }
}
