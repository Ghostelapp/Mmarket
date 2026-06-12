const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("MASK Market contracts", function () {
  async function fixture() {
    const [admin, buyer, seller, feeWallet] = await ethers.getSigners();
    const token = await ethers.deployContract("MockUSDC");
    const router = await ethers.deployContract("MaskPaymentRouter", [admin.address, feeWallet.address, [await token.getAddress()]]);
    const escrow = await ethers.deployContract("MaskMarketplaceEscrow", [admin.address, admin.address, feeWallet.address, [await token.getAddress()]]);
    await token.faucet(buyer.address, 1_000_000_000n);
    await token.connect(buyer).approve(await router.getAddress(), ethers.MaxUint256);
    await token.connect(buyer).approve(await escrow.getAddress(), ethers.MaxUint256);
    return { admin, buyer, seller, feeWallet, token, router, escrow };
  }

  it("collects a platform payment once per backend reference", async function () {
    const { buyer, feeWallet, token, router } = await fixture();
    const reference = ethers.id("listing:test");
    await expect(router.connect(buyer).pay(reference, await token.getAddress(), 1_000_000n, 0))
      .to.emit(router, "PlatformPayment");
    expect(await token.balanceOf(feeWallet.address)).to.equal(1_000_000n);
    await expect(router.connect(buyer).pay(reference, await token.getAddress(), 1_000_000n, 0))
      .to.be.revertedWith("reference paid");
  });

  it("funds escrow and releases seller amount plus platform fee", async function () {
    const { buyer, seller, feeWallet, token, escrow } = await fixture();
    const reference = ethers.id("transaction:test");
    await escrow.connect(buyer).createAndFund(reference, seller.address, await token.getAddress(), 100_000_000n, 500);
    await escrow.connect(seller).markShipped(reference);
    await escrow.connect(buyer).confirmDelivery(reference);
    expect(await token.balanceOf(seller.address)).to.equal(95_000_000n);
    expect(await token.balanceOf(feeWallet.address)).to.equal(5_000_000n);
  });

  it("supports separate creation, funding and cancellation before funding", async function () {
    const { buyer, seller, token, escrow } = await fixture();
    const fundedReference = ethers.id("transaction:separate-funding");
    await escrow.connect(buyer).createOrder(fundedReference, seller.address, await token.getAddress(), 10_000_000n, 500);
    expect((await escrow.orders(fundedReference)).status).to.equal(1);
    await escrow.connect(buyer).fundOrder(fundedReference);
    expect((await escrow.orders(fundedReference)).status).to.equal(2);

    const cancelledReference = ethers.id("transaction:cancelled");
    await escrow.connect(buyer).createOrder(cancelledReference, seller.address, await token.getAddress(), 10_000_000n, 500);
    await escrow.connect(buyer).cancel(cancelledReference);
    expect((await escrow.orders(cancelledReference)).status).to.equal(7);
  });

  it("lets the arbiter refund a disputed order", async function () {
    const { admin, buyer, seller, token, escrow } = await fixture();
    const reference = ethers.id("transaction:refund");
    await escrow.connect(buyer).createAndFund(reference, seller.address, await token.getAddress(), 50_000_000n, 500);
    await escrow.connect(buyer).openDispute(reference);
    await escrow.connect(admin).resolve(reference, false);
    expect(await token.balanceOf(buyer.address)).to.equal(1_000_000_000n);
  });

  it("rejects unauthorized escrow actions and invalid terms", async function () {
    const { buyer, seller, feeWallet, token, escrow } = await fixture();
    const reference = ethers.id("transaction:invalid-actions");
    await expect(
      escrow.connect(buyer).createAndFund(reference, seller.address, await token.getAddress(), 10_000_000n, 2501),
    ).to.be.revertedWith("bad terms");
    await escrow.connect(buyer).createAndFund(reference, seller.address, await token.getAddress(), 10_000_000n, 500);
    await expect(escrow.connect(feeWallet).markShipped(reference)).to.be.revertedWith("bad state");
    await expect(escrow.connect(seller).confirmDelivery(reference)).to.be.revertedWith("bad state");
    await expect(escrow.connect(feeWallet).resolve(reference, true)).to.be.reverted;
  });

  it("enforces pause and synchronizes fee wallet changes", async function () {
    const { admin, buyer, seller, feeWallet, token, router, escrow } = await fixture();
    await router.connect(admin).setPaused(true);
    await expect(
      router.connect(buyer).pay(ethers.id("paused"), await token.getAddress(), 1_000_000n, 0),
    ).to.be.reverted;
    await escrow.connect(admin).setPaused(true);
    await expect(
      escrow.connect(buyer).createOrder(ethers.id("paused-order"), seller.address, await token.getAddress(), 1_000_000n, 500),
    ).to.be.reverted;
    await router.connect(admin).setFeeWallet(seller.address);
    await escrow.connect(admin).setFeeWallet(seller.address);
    expect(await router.feeWallet()).to.equal(seller.address);
    expect(await escrow.feeWallet()).to.equal(seller.address);
    expect(await escrow.feeWallet()).not.to.equal(feeWallet.address);
  });

  it("keeps the fee wallet fixed for an existing order", async function () {
    const { admin, buyer, seller, feeWallet, token, escrow } = await fixture();
    const reference = ethers.id("transaction:fixed-fee-wallet");
    await escrow.connect(buyer).createAndFund(reference, seller.address, await token.getAddress(), 100_000_000n, 500);
    await escrow.connect(admin).setFeeWallet(seller.address);
    await escrow.connect(seller).markShipped(reference);
    await escrow.connect(buyer).confirmDelivery(reference);
    expect(await token.balanceOf(feeWallet.address)).to.equal(5_000_000n);
  });

  it("requires a delayed two-step default admin transfer", async function () {
    const { admin, buyer, router, escrow } = await fixture();
    expect(await router.defaultAdminDelay()).to.equal(2n * 24n * 60n * 60n);
    expect(await escrow.defaultAdminDelay()).to.equal(2n * 24n * 60n * 60n);
    await router.connect(admin).beginDefaultAdminTransfer(buyer.address);
    await expect(router.connect(buyer).acceptDefaultAdminTransfer()).to.be.reverted;
  });

  it("rejects fee-on-transfer tokens in the payment router and escrow", async function () {
    const [admin, buyer, seller, feeWallet] = await ethers.getSigners();
    const token = await ethers.deployContract("FeeOnTransferToken");
    const tokenAddress = await token.getAddress();
    const router = await ethers.deployContract("MaskPaymentRouter", [
      admin.address,
      feeWallet.address,
      [tokenAddress],
    ]);
    const escrow = await ethers.deployContract("MaskMarketplaceEscrow", [
      admin.address,
      admin.address,
      feeWallet.address,
      [tokenAddress],
    ]);
    await token.faucet(buyer.address, 100_000_000n);
    await token.connect(buyer).approve(await router.getAddress(), ethers.MaxUint256);
    await token.connect(buyer).approve(await escrow.getAddress(), ethers.MaxUint256);

    await expect(
      router.connect(buyer).pay(ethers.id("fee-token-payment"), tokenAddress, 10_000_000n, 0),
    ).to.be.revertedWith("bad received amount");
    expect(await router.paidReferences(ethers.id("fee-token-payment"))).to.equal(false);

    await expect(
      escrow.connect(buyer).createAndFund(
        ethers.id("fee-token-order"),
        seller.address,
        tokenAddress,
        10_000_000n,
        500,
      ),
    ).to.be.revertedWith("bad received amount");
    expect((await escrow.orders(ethers.id("fee-token-order"))).status).to.equal(0);
  });
});
