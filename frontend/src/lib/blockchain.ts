import { BrowserProvider, Contract, parseUnits } from "ethers";
import { Platform } from "react-native";

import { apiRequest } from "@/src/lib/api";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
};

type ChainConfig = {
  name: string;
  display_name: string;
  chain_id: number;
  rpc_url: string;
  explorer_url: string;
  native_symbol: string;
  usdc: string;
  payment_router_contract: string;
  min_confirmations: number;
};

const ERC20_ABI = ["function approve(address spender, uint256 amount) returns (bool)"];
const PAYMENT_ROUTER_ABI = [
  "function pay(bytes32 paymentRef, address token, uint256 amount, uint8 purpose)",
];
const ESCROW_ABI = [
  "function createAndFund(bytes32 orderRef, address seller, address token, uint128 amount, uint16 feeBps)",
  "function markShipped(bytes32 orderRef)",
  "function confirmDelivery(bytes32 orderRef)",
  "function openDispute(bytes32 orderRef)",
  "function cancel(bytes32 orderRef)",
];

const injectedProvider = (): EthereumProvider => {
  const ethereum =
    Platform.OS === "web" && typeof window !== "undefined"
      ? (window as typeof window & { ethereum?: EthereumProvider }).ethereum
      : undefined;
  if (!ethereum) {
    throw new Error("Płatność portfelem wymaga przeglądarki z MetaMask lub kompatybilnym portfelem.");
  }
  return ethereum;
};

const switchChain = async (ethereum: EthereumProvider, chain: ChainConfig) => {
  const chainId = `0x${chain.chain_id.toString(16)}`;
  try {
    await ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId }] });
  } catch (error: any) {
    if (error?.code !== 4902) throw error;
    await ethereum.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId,
          chainName: chain.display_name,
          nativeCurrency: {
            name: chain.native_symbol,
            symbol: chain.native_symbol,
            decimals: 18,
          },
          rpcUrls: [chain.rpc_url],
          blockExplorerUrls: [chain.explorer_url],
        },
      ],
    });
  }
};

export const payListingFeeWithWallet = async ({
  network,
  amount,
  reference,
  routerAddress,
  purpose = 0,
}: {
  network: string;
  amount: number;
  reference: string;
  routerAddress?: string;
  purpose?: 0 | 1 | 2;
}): Promise<string> => {
  const chains = await apiRequest<ChainConfig[]>("/crypto/networks");
  const chain = chains.find((item) => item.name === network);
  if (!chain?.usdc) throw new Error(`Token USDC nie jest skonfigurowany dla sieci ${network}.`);
  const router = routerAddress || chain.payment_router_contract;
  if (!router) throw new Error(`Router płatności nie jest jeszcze wdrożony dla sieci ${network}.`);
  if (!reference) throw new Error("Backend nie zwrócił referencji płatności.");

  const ethereum = injectedProvider();
  await switchChain(ethereum, chain);
  await ethereum.request({ method: "eth_requestAccounts" });

  const provider = new BrowserProvider(ethereum);
  const signer = await provider.getSigner();
  const value = parseUnits(amount.toString(), 6);
  const token = new Contract(chain.usdc, ERC20_ABI, signer);
  const approval = await token.approve(router, value);
  await approval.wait();

  const paymentRouter = new Contract(router, PAYMENT_ROUTER_ABI, signer);
  const payment = await paymentRouter.pay(reference, chain.usdc, value, purpose);
  await payment.wait(chain.min_confirmations);
  return payment.hash;
};

const connectedSigner = async (network: string) => {
  const chains = await apiRequest<ChainConfig[]>("/crypto/networks");
  const chain = chains.find((item) => item.name === network);
  if (!chain?.usdc) throw new Error(`Token USDC nie jest skonfigurowany dla sieci ${network}.`);
  const ethereum = injectedProvider();
  await switchChain(ethereum, chain);
  await ethereum.request({ method: "eth_requestAccounts" });
  return { chain, signer: await new BrowserProvider(ethereum).getSigner() };
};

export const fundEscrowWithWallet = async ({
  network,
  escrowAddress,
  reference,
  seller,
  amount,
  feePercent,
}: {
  network: string;
  escrowAddress: string;
  reference: string;
  seller: string;
  amount: number;
  feePercent: number;
}): Promise<string> => {
  if (!escrowAddress || !reference || !seller) throw new Error("Transakcja nie ma kompletnej konfiguracji escrow.");
  const { chain, signer } = await connectedSigner(network);
  const value = parseUnits(amount.toString(), 6);
  const token = new Contract(chain.usdc, ERC20_ABI, signer);
  const approval = await token.approve(escrowAddress, value);
  await approval.wait();
  const escrow = new Contract(escrowAddress, ESCROW_ABI, signer);
  const transaction = await escrow.createAndFund(
    reference,
    seller,
    chain.usdc,
    value,
    Math.round(feePercent * 100),
  );
  await transaction.wait(chain.min_confirmations);
  return transaction.hash;
};

export const runEscrowActionWithWallet = async ({
  network,
  escrowAddress,
  reference,
  action,
}: {
  network: string;
  escrowAddress: string;
  reference: string;
  action: "markShipped" | "confirmDelivery" | "openDispute" | "cancel";
}): Promise<string> => {
  if (!escrowAddress || !reference) throw new Error("Transakcja nie ma kompletnej konfiguracji escrow.");
  const { chain, signer } = await connectedSigner(network);
  const escrow = new Contract(escrowAddress, ESCROW_ABI, signer);
  const transaction = await escrow[action](reference);
  await transaction.wait(chain.min_confirmations);
  return transaction.hash;
};

export const linkCurrentWallet = async (): Promise<string> => {
  const ethereum = injectedProvider();
  const accounts = (await ethereum.request({ method: "eth_requestAccounts" })) as string[];
  const address = accounts?.[0];
  if (!address) throw new Error("Nie wybrano portfela.");
  const chainIdHex = (await ethereum.request({ method: "eth_chainId" })) as string;
  const challenge = await apiRequest<{ challenge_id: string; message: string }>("/auth/wallet/challenge", {
    method: "POST",
    body: { wallet_address: address, chain_id: Number.parseInt(chainIdHex, 16) },
  });
  const signature = (await ethereum.request({
    method: "personal_sign",
    params: [challenge.message, address],
  })) as string;
  await apiRequest("/me/wallets/link", {
    method: "POST",
    auth: true,
    body: {
      wallet_address: address,
      challenge_id: challenge.challenge_id,
      signature,
    },
  });
  return address;
};
