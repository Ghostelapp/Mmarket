// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControlDefaultAdminRules} from "@openzeppelin/contracts/access/extensions/AccessControlDefaultAdminRules.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract MaskPaymentRouter is AccessControlDefaultAdminRules, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR_ROLE");
    bytes32 public constant TOKEN_MANAGER_ROLE = keccak256("TOKEN_MANAGER_ROLE");

    enum Purpose {
        ListingFee,
        Promotion,
        Premium
    }

    address public feeWallet;
    mapping(address => bool) public allowedTokens;
    mapping(bytes32 => bool) public paidReferences;

    event PlatformPayment(
        bytes32 indexed paymentRef,
        address indexed payer,
        address indexed token,
        uint256 amount,
        Purpose purpose
    );
    event FeeWalletChanged(address indexed previousWallet, address indexed nextWallet);
    event TokenAllowed(address indexed token, bool allowed);

    constructor(address admin, address initialFeeWallet, address[] memory initialTokens)
        AccessControlDefaultAdminRules(2 days, admin)
    {
        require(admin != address(0) && initialFeeWallet != address(0), "zero address");
        feeWallet = initialFeeWallet;
        _grantRole(OPERATOR_ROLE, admin);
        _grantRole(TOKEN_MANAGER_ROLE, admin);
        for (uint256 i; i < initialTokens.length; i++) {
            _setToken(initialTokens[i], true);
        }
    }

    function pay(bytes32 paymentRef, address token, uint256 amount, Purpose purpose)
        external
        whenNotPaused
        nonReentrant
    {
        require(paymentRef != bytes32(0), "empty reference");
        require(!paidReferences[paymentRef], "reference paid");
        require(allowedTokens[token], "token not allowed");
        require(amount > 0, "zero amount");
        paidReferences[paymentRef] = true;
        uint256 balanceBefore = IERC20(token).balanceOf(feeWallet);
        IERC20(token).safeTransferFrom(msg.sender, feeWallet, amount);
        require(IERC20(token).balanceOf(feeWallet) - balanceBefore == amount, "bad received amount");
        emit PlatformPayment(paymentRef, msg.sender, token, amount, purpose);
    }

    function setFeeWallet(address nextWallet) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(nextWallet != address(0), "zero address");
        emit FeeWalletChanged(feeWallet, nextWallet);
        feeWallet = nextWallet;
    }

    function setTokenAllowed(address token, bool allowed) external onlyRole(TOKEN_MANAGER_ROLE) {
        _setToken(token, allowed);
    }

    function setPaused(bool value) external onlyRole(OPERATOR_ROLE) {
        value ? _pause() : _unpause();
    }

    function _setToken(address token, bool allowed) internal {
        require(token != address(0), "zero token");
        allowedTokens[token] = allowed;
        emit TokenAllowed(token, allowed);
    }
}
