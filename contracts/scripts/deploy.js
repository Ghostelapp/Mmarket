const fs = require("fs");
const path = require("path");
const { ethers, network } = require("hardhat");

const officialTokens = {
  baseSepolia: process.env.BASE_SEPOLIA_USDC || "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
  polygonAmoy: process.env.POLYGON_AMOY_USDC || "",
  base: process.env.BASE_USDC || "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
  polygon: process.env.POLYGON_USDC || "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
};

async function waitForRuntimeCode(address) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const code = await ethers.provider.getCode(address);
    if (code !== "0x") return code;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`Runtime bytecode is not visible for deployed contract ${address}`);
}

async function main() {
  const [deployer] = await ethers.getSigners();
  const isLocal = ["hardhat", "localhost"].includes(network.name);
  const isMainnet = ["base", "polygon"].includes(network.name);
  const feeWallet = process.env.PLATFORM_FEE_WALLET || (isLocal ? deployer.address : "");
  const arbiter = process.env.ARBITER_ADDRESS || (isLocal ? deployer.address : "");
  const admin = process.env.CONTRACT_ADMIN_ADDRESS || (isLocal ? deployer.address : "");
  if (!feeWallet || !arbiter || !admin) {
    throw new Error(
      "Public network deployment requires PLATFORM_FEE_WALLET, ARBITER_ADDRESS and CONTRACT_ADMIN_ADDRESS",
    );
  }
  if (isMainnet) {
    if (process.env.CONFIRM_MAINNET_MULTISIGS !== "true") {
      throw new Error("Set CONFIRM_MAINNET_MULTISIGS=true after verifying admin and arbiter multisigs");
    }
    const deployerAddress = deployer.address.toLowerCase();
    if (
      admin.toLowerCase() === arbiter.toLowerCase()
      || admin.toLowerCase() === deployerAddress
      || arbiter.toLowerCase() === deployerAddress
    ) {
      throw new Error("Mainnet admin and arbiter must be separate multisigs, distinct from the deployer");
    }
  }
  let token = officialTokens[network.name];
  let mockUsdc = null;

  if (!token) {
    if (!isLocal) {
      throw new Error(
        `No official USDC configured for ${network.name}. Set the network-specific USDC environment variable.`,
      );
    }
    mockUsdc = await ethers.deployContract("MockUSDC");
    await mockUsdc.waitForDeployment();
    token = await mockUsdc.getAddress();
  }

  const paymentRouter = await ethers.deployContract("MaskPaymentRouter", [admin, feeWallet, [token]]);
  await paymentRouter.waitForDeployment();
  const paymentRouterReceipt = await paymentRouter.deploymentTransaction().wait();
  const escrow = await ethers.deployContract("MaskMarketplaceEscrow", [admin, arbiter, feeWallet, [token]]);
  await escrow.waitForDeployment();
  const escrowReceipt = await escrow.deploymentTransaction().wait();
  const paymentRouterAddress = await paymentRouter.getAddress();
  const escrowAddress = await escrow.getAddress();
  const paymentRouterCode = await waitForRuntimeCode(paymentRouterAddress);
  const escrowCode = await waitForRuntimeCode(escrowAddress);

  const deployment = {
    network: network.name,
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    deployer: deployer.address,
    contractAdmin: admin,
    platformFeeWallet: feeWallet,
    arbiter,
    token,
    mockUsdc: mockUsdc ? await mockUsdc.getAddress() : null,
    paymentRouter: paymentRouterAddress,
    paymentRouterDeploymentTx: paymentRouterReceipt.hash,
    paymentRouterDeploymentBlock: paymentRouterReceipt.blockNumber,
    paymentRouterCodeHash: ethers.keccak256(paymentRouterCode),
    escrow: escrowAddress,
    escrowDeploymentTx: escrowReceipt.hash,
    escrowDeploymentBlock: escrowReceipt.blockNumber,
    escrowCodeHash: ethers.keccak256(escrowCode),
  };
  fs.mkdirSync(path.join(__dirname, "..", "deployments"), { recursive: true });
  fs.writeFileSync(path.join(__dirname, "..", "deployments", `${network.name}.json`), JSON.stringify(deployment, null, 2));
  console.log(JSON.stringify(deployment, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
