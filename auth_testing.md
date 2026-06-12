# Auth Testing Notes — MASK Market

## Password login
- Endpoint: `POST /api/auth/login`
- Requires: `email`, `password`, `device_name`

## 2FA
- Enable: `POST /api/auth/2fa/enable` (auth + hasło konta)
- Verify: `POST /api/auth/2fa/verify` (kod TOTP)

## WebAuthn Passkey
- Register options: `POST /api/auth/passkey/register/options` (auth required)
- Register verify: `POST /api/auth/passkey/register`
- Login options: `POST /api/auth/passkey/login/options`
- Login verify: `POST /api/auth/passkey/login`

## Crypto wallet login
- Challenge: `POST /api/auth/wallet/challenge` z `wallet_address`
- Podpisz dokładne pole `message` metodą portfela `personal_sign`
- Login: `POST /api/auth/wallet/login` z `wallet_address`, `challenge_id`, `signature`, `device_name`
- Challenge jest jednorazowy i wygasa po 5 minutach; konto powstaje dopiero po poprawnej weryfikacji podpisu

## Deal Room E2EE
- Pin account public key: `POST /api/me/e2ee-key`
- Read own pinned key: `GET /api/me/e2ee-key`
- Read participant keys and own room-key envelope: `GET /api/transactions/{id}/e2ee`
- Initialize encrypted room-key envelopes: `POST /api/transactions/{id}/e2ee/initialize`
- Only transaction participants can list ciphertext messages; admin access returns `403`
- New messages require `nacl-secretbox-v1`, a 24-byte nonce and the room `key_id`

## Current environment mode
- `ENABLE_ONCHAIN_INDEXER=false`
- `ENABLE_R2_STORAGE=false`
- `ENABLE_AV_SCAN=false`
- `ALLOW_INSECURE_PAYMENT_SIMULATION=true` wyłącznie w izolowanym środowisku testowym
- `APP_ENV` nie może mieć wartości `production` przy włączonej symulacji
- produkcyjne finansowanie escrow jest blokowane do czasu integracji
  `createOrder`/`fundOrder` i weryfikacji zdarzeń kontraktu
