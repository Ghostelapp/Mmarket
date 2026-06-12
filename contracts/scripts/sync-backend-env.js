const fs = require("fs");
const path = require("path");
const { isAddress, isHexString } = require("ethers");

const profiles = {
  baseSepolia: { environment: "testnet", network: "BASE" },
  polygonAmoy: { environment: "testnet", network: "POLYGON" },
  base: { environment: "mainnet", network: "BASE" },
  polygon: { environment: "mainnet", network: "POLYGON" },
};

function requireAddress(value, label) {
  if (!isAddress(value)) throw new Error(`${label} is not a valid address`);
  return value;
}

function requireCodeHash(value, label) {
  if (!isHexString(value, 32)) throw new Error(`${label} is not a valid bytes32 hash`);
  return value;
}

function setEnvValue(contents, key, value) {
  const line = `${key}="${value}"`;
  const pattern = new RegExp(`^${key}=.*$`, "m");
  return pattern.test(contents)
    ? contents.replace(pattern, line)
    : `${contents.trimEnd()}\n${line}\n`;
}

function main() {
  const deploymentPath = path.resolve(process.cwd(), process.argv[2] || "deployments/baseSepolia.json");
  const backendEnvPath = process.env.BACKEND_ENV_PATH
    ? path.resolve(process.env.BACKEND_ENV_PATH)
    : path.resolve(__dirname, "../../backend/.env");
  if (!fs.existsSync(deploymentPath)) throw new Error(`Deployment file does not exist: ${deploymentPath}`);
  if (!fs.existsSync(backendEnvPath)) throw new Error(`Backend environment file does not exist: ${backendEnvPath}`);

  const deployment = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
  const profile = profiles[deployment.network];
  if (!profile) throw new Error(`Unsupported deployment network: ${deployment.network}`);

  const suffix = `${profile.network}_${profile.environment.toUpperCase()}`;
  const values = {
    CHAIN_ENV: profile.environment,
    ALLOW_INSECURE_PAYMENT_SIMULATION: "false",
    ENABLE_ONCHAIN_INDEXER: "true",
    VERIFY_CONTRACTS_ON_STARTUP: "true",
    CONTRACT_ADMIN_ADDRESS: requireAddress(deployment.contractAdmin, "contractAdmin"),
    ARBITER_ADDRESS: requireAddress(deployment.arbiter, "arbiter"),
    [`PLATFORM_WALLET_${profile.network}`]: requireAddress(deployment.platformFeeWallet, "platformFeeWallet"),
    [`USDC_CONTRACT_${suffix}`]: requireAddress(deployment.token, "token"),
    [`ESCROW_CONTRACT_${suffix}`]: requireAddress(deployment.escrow, "escrow"),
    [`PAYMENT_ROUTER_${suffix}`]: requireAddress(deployment.paymentRouter, "paymentRouter"),
    [`ESCROW_CODE_HASH_${suffix}`]: requireCodeHash(deployment.escrowCodeHash, "escrowCodeHash"),
    [`PAYMENT_ROUTER_CODE_HASH_${suffix}`]: requireCodeHash(
      deployment.paymentRouterCodeHash,
      "paymentRouterCodeHash",
    ),
  };

  let contents = fs.readFileSync(backendEnvPath, "utf8");
  for (const [key, value] of Object.entries(values)) contents = setEnvValue(contents, key, value);
  fs.writeFileSync(backendEnvPath, contents, { mode: 0o600 });
  console.log(`Synchronized ${deployment.network} deployment with ${backendEnvPath}`);
}

try {
  main();
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
