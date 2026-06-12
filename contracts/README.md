# MASK Market contracts

Kontrakty EVM dla opłat platformowych i escrow. Domyślnym testnetem aplikacji jest Base Sepolia.

## Kontrakty

- `MaskPaymentRouter`: listing fee, promocje i premium. Każda płatność posiada unikalny `bytes32 reference`.
- `MaskMarketplaceEscrow`: osobne `createOrder`/`fundOrder` lub atomowe `createAndFund`, wysyłka, potwierdzenie, spór, release/refund i podział prowizji.
- `MockUSDC`: wyłącznie lokalnie lub na prywatnym testnecie bez oficjalnego testowego USDC.

## Instalacja i testy

```bash
cd contracts
cp .env.example .env
yarn install
yarn compile
yarn test
```

## Deploy testnet

1. Zasil deployer testowym ETH na Base Sepolia albo POL na Polygon Amoy.
2. Ustaw `DEPLOYER_PRIVATE_KEY`, `CONTRACT_ADMIN_ADDRESS`, `PLATFORM_FEE_WALLET` i `ARBITER_ADDRESS` w `contracts/.env`.
3. Wdróż:

```bash
yarn deploy:base-sepolia
# albo
yarn deploy:polygon-amoy
```

Adresy zostaną zapisane w `contracts/deployments/<network>.json`. Przepisz je do backendu:

```env
CHAIN_ENV="testnet"
ENABLE_ONCHAIN_INDEXER="true"
ESCROW_CONTRACT_BASE_TESTNET="0x..."
PAYMENT_ROUTER_BASE_TESTNET="0x..."
ONCHAIN_MIN_CONFIRMATIONS="2"
```

## Przełączenie na mainnet

Przełączenie nie wymaga zmian kodu:

```env
CHAIN_ENV="mainnet"
ESCROW_CONTRACT_BASE_MAINNET="0x..."
PAYMENT_ROUTER_BASE_MAINNET="0x..."
```

Przed mainnetem wymagane są niezależny audit Solidity, multisig jako admin/arbiter, monitoring zdarzeń i test procesu awaryjnego.
