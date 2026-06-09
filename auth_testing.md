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

## Current environment mode
- `ENABLE_ONCHAIN_INDEXER=false`
- `ENABLE_R2_STORAGE=false`
- `ENABLE_AV_SCAN=false`
