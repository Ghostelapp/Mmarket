# PRD — MASK Market

## Problem statement
Zbudować legalny privacy-first marketplace P2P crypto-only z escrow, E2EE, aliasami transakcyjnymi, blind delivery, risk/AML i panelem admin.

## Architecture
- Mobile app: Expo Router + TypeScript (dark cyber UI)
- Backend API: FastAPI + MongoDB
- Admin panel: osobna aplikacja Next.js
- Smart contract module: Solidity escrow (podpięcie on-chain jako moduł)

## User personas
- Kupujący dbający o prywatność
- Sprzedający legalnych produktów crypto-first
- Admin/Moderator bezpieczeństwa i sporów

## Core requirements (static)
- Anonimowość wobec użytkowników i odpowiedzialność wobec systemu
- Listing fee + sale fee w crypto na wallet platformy
- Escrow flow + dispute resolution
- E2EE messaging w Deal Room
- Rate limiting, audit log, moderacja

## Implemented (2026-06-09)
- Backend FastAPI: auth (register/login/refresh/logout), 2FA (TOTP), passkey stub, panic lock, devices/sessions
- Backend FastAPI: listings CRUD + listing fee payment + report
- Backend FastAPI: transactions + escrow statuses + blind delivery token + disputes + confirm delivery fee split
- Backend FastAPI: encrypted messages endpoints + report evidence
- Backend FastAPI: crypto endpoints (networks, tokens, rates, payment intent)
- Backend FastAPI: admin endpoints (dashboard/users/listings/transactions/disputes/reports/fees/platform-wallets/audit/moderation/ban/resolve)
- Mobile Expo: pixel-style full marketplace UI with tabs (Market/Sell/Deal Room/Profile/Admin)
- Mobile Expo: biometric auth check, security panel, privacy shield, panic lock action
- Mobile Expo: E2EE client-side encrypt/decrypt helper for deal room chat
- Admin web Next.js: login + dashboard + moderation + dispute actions + wallets + audit log views
- Smart contract: `smart_contract_escrow.sol` with create/fund/ship/confirm/openDispute/resolve/cancel and fee split
- Security hardening: JWT secret fail-fast from env, removed admin credential prefill in web panel
- QA closure: added testID coverage to key mobile controls and working `/admin` fallback route in Expo preview
- WebAuthn upgrade: pełne challenge/options/verify (`/auth/passkey/register/options`, `/auth/passkey/register`, `/auth/passkey/login/options`, `/auth/passkey/login`)
- On-chain architecture: Alchemy RPC + webhook endpoint (`/crypto/webhooks/alchemy`) z podpisem HMAC; fallback mode gdy klucze ENV nieustawione
- Promotions: pakiety Basic/Boost z crypto payment intent + confirm (`/listings/{id}/promote-intent`, `/listings/{id}/promote-confirm`)
- Upload pipeline: `POST /listings/{id}/images/upload` z EXIF-safe processing, thumbnail, AV feature flag, private R2 integration (feature flag)
- Stabilizacja marketplace: fix timezone compare dla promoted sorting w `/listings`
- Pixel redesign (user screens only): synthwave + cyber neon, pełny pixel look (Press Start 2P + VT323), delikatne animacje, hero/banner base64, pixel avatary, neon border system, poprawione touch targets (>=44px) i testID coverage dla nav/auth/filter.

## Prioritized backlog
### P0
- Real passkey WebAuthn attestation + challenge verification
- Real chain indexer/webhooks for on-chain confirmation
- Real EXIF stripping + file scanning pipeline
- Push notifications integration

### P1
- Advanced AML provider integration
- Embedded wallet integration
- Security stake lifecycle and policy automation

### P2
- Solana + BTC Lightning extensions
- Advanced photo moderation ML
- Expanded analytics and exports PDF/CSV UI

## Next tasks list
1. Uzupełnić realne klucze Alchemy i włączyć `ENABLE_ONCHAIN_INDEXER=true`
2. Uzupełnić klucze Cloudflare R2 i włączyć `ENABLE_R2_STORAGE=true`
3. Włączyć ClamAV serwis i `ENABLE_AV_SCAN=true`
4. Dodać produkcyjne originy WebAuthn do `WEBAUTHN_ALLOWED_ORIGINS`