# Wdrożenie blockchain: testnet i mainnet

> Po aktualizacji escrow zapisuje portfel prowizji wewnątrz każdego zamówienia.
> Wcześniejsze wdrożenia kontraktu mają niezgodne ABI i muszą zostać wdrożone ponownie.

Domyślnym środowiskiem aplikacji jest **Base Sepolia** (`chainId: 84532`). Kod
obsługuje też Polygon Amoy, ale przed użyciem trzeba podać adres testowego USDC.
Przełączenie na mainnet odbywa się przez `CHAIN_ENV=mainnet`, bez zmian kodu.

## Aktualne wdrożenie Base Sepolia

- Payment router: `0x84734803C7455104142aB8f0AF785B12c6456b57`
- Escrow: `0xFe544E0CBd691F40Ad6dD3C6fd5C1452B6665021`
- USDC: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Deployer/admin/arbiter/portfel opłat:
  `0xb776261A252799eB77185e2926B77628c7c68e63`
- Szczegóły, hashe kodu oraz transakcje wdrożeniowe:
  `contracts/deployments/baseSepolia.json`

## Co znajduje się w projekcie

- `contracts/contracts/MaskPaymentRouter.sol` - opłaty listingowe, promocje i premium.
- `contracts/contracts/MaskMarketplaceEscrow.sol` - escrow zakupu, wysyłka, odbiór,
  spór, release/refund oraz prowizja platformy.
- `contracts/contracts/MockUSDC.sol` - token wyłącznie do lokalnych testów lub
  prywatnego testnetu.
- `contracts/scripts/deploy.js` - wdrożenie i zapis adresów do `contracts/deployments/`.
- `backend/blockchain.env.example` - wszystkie profile sieci backendu.

## 1. Wdrożenie na Base Sepolia

Utwórz osobny portfel deployera i zasil go testowym ETH. Nigdy nie umieszczaj
klucza prywatnego portfela mainnetowego w tym projekcie.

```bash
cd contracts
cp .env.example .env
```

Uzupełnij w `contracts/.env`:

```env
DEPLOYER_PRIVATE_KEY=0x...
PLATFORM_FEE_WALLET=0x...
ARBITER_ADDRESS=0x...
BASE_SEPOLIA_RPC_URL=https://sepolia.base.org
```

`PLATFORM_FEE_WALLET` musi być tym samym adresem, który backend przypisuje jako
odbiorcę opłat. Następnie:

```bash
yarn install
yarn test
yarn deploy:base-sepolia:configure
```

Wynik pojawi się w `contracts/deployments/baseSepolia.json`, a adresy kontraktów,
hashe kodu, role i portfel opłat zostaną automatycznie zapisane w `backend/.env`.

## 2. Konfiguracja backendu

Skopiuj `backend/blockchain.env.example` do używanego pliku środowiskowego i
wstaw adresy zapisane przez deploy:

```env
CHAIN_ENV=testnet
SUPPORTED_NETWORKS=Base
ENABLE_ONCHAIN_INDEXER=true
ALLOW_INSECURE_PAYMENT_SIMULATION=false
VERIFY_CONTRACTS_ON_STARTUP=true
VERIFY_CONTRACTS_FAIL_CLOSED=false
CONTRACT_VERIFY_TIMEOUT_SECONDS=30
RPC_REQUEST_TIMEOUT_SECONDS=15
RECONCILIATION_INTERVAL_SECONDS=60

ESCROW_CONTRACT_BASE_TESTNET=0x...
PAYMENT_ROUTER_BASE_TESTNET=0x...
ESCROW_CODE_HASH_BASE_TESTNET=0x...
PAYMENT_ROUTER_CODE_HASH_BASE_TESTNET=0x...
PLATFORM_WALLET_BASE=0x...
CONTRACT_ADMIN_ADDRESS=0x...
ARBITER_ADDRESS=0x...
```

Jeżeli użyto `yarn deploy:base-sepolia:configure`, adresy i hashe kodu są już
przepisane z `contracts/deployments/baseSepolia.json`.
Backend podczas weryfikacji porówna również runtime bytecode, więc poprawny
adres wskazujący inny kontrakt zostanie odrzucony.

Po restarcie sprawdź profil:

```bash
curl http://localhost:8001/api/crypto/networks
curl http://localhost:8001/api/
```

Frontend pobiera ten profil automatycznie. Przycisk `Opłać portfelem` przełącza
MetaMask na Base Sepolia, wykonuje `approve` USDC i płaci przez router.

Healthcheck raportuje wynik w `contract_verification`. Przy chwilowej awarii RPC
usługa uruchamia się ze stanem `degraded`; ustawienie
`VERIFY_CONTRACTS_FAIL_CLOSED=true` zmienia ten tryb na przerwanie startu.

## 3. Test pełnego przepływu

1. Zdobądź testowy ETH i testowy USDC na Base Sepolia.
2. Zaloguj się do aplikacji portfelem.
3. Dodaj ofertę i kliknij `Opłać portfelem`.
4. Utwórz zakup drugim kontem i użyj referencji escrow zwracanej w transakcji.
5. W Deal Room użyj kolejno `Fund escrow`, `Mark shipped` i `Confirm delivery`.
6. Osobno sprawdź `Open dispute`. Rozstrzygnięcie `resolve` podpisuje portfel
   arbitra z rolą `ARBITER_ROLE` (np. przez explorer kontraktu).

Możesz wykonywać operacje escrow z konsoli Hardhat lub bezpośrednio przez
explorer kontraktu. Backend zapisuje `escrow_reference` jako `bytes32`.

## 4. Przełączenie na mainnet

Najpierw wdróż kontrakty:

```bash
cd contracts
yarn deploy:base
```

Przed wdrożeniem mainnet ustaw również `CONFIRM_MAINNET_MULTISIGS=true`. Skrypt
odrzuci wdrożenie, jeżeli adres administratora i arbitra jest taki sam albo
którykolwiek z nich jest adresem deployera. Oba adresy muszą wskazywać osobne
multisigi zweryfikowane przed uruchomieniem skryptu.

Potem ustaw backend:

```env
CHAIN_ENV=mainnet
ESCROW_CONTRACT_BASE_MAINNET=0x...
PAYMENT_ROUTER_BASE_MAINNET=0x...
ESCROW_CODE_HASH_BASE_MAINNET=0x...
PAYMENT_ROUTER_CODE_HASH_BASE_MAINNET=0x...
ALLOW_INSECURE_PAYMENT_SIMULATION=false
```

Przed mainnetem wymagane są: niezależny audyt Solidity, multisig dla ról admina
i arbitra, monitoring zdarzeń, limity operacyjne oraz przećwiczony proces pause.

## Ważne adresy i dokumentacja

- Base Sepolia: `84532`, RPC `https://sepolia.base.org`
- Base mainnet: `8453`
- Circle USDC Base Sepolia: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Dokumentacja Base: https://docs.base.org/base-chain/quickstart/connecting-to-base
- Adresy USDC Circle: https://developers.circle.com/stablecoins/usdc-contract-addresses
