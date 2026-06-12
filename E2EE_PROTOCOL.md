# Deal Room E2EE protocol

## Cryptography

- Account identity: Curve25519 key pair generated on the client.
- Native private-key storage: Expo SecureStore.
- Web private-key storage: browser origin storage; deploy with a strict CSP.
- Public-key pinning: trust on first use (TOFU). The API rejects silent replacement.
- Room key: random 32-byte key generated after both participants publish public keys.
- Room-key envelopes: NaCl `box` (X25519 + XSalsa20-Poly1305), one envelope per participant.
- Messages: NaCl `secretbox` (XSalsa20-Poly1305) with a random 24-byte nonce.
- Key identifier: first 32 bytes of SHA-512 over the room key.

The encrypted message payload binds:

- protocol version,
- transaction ID,
- sender ID,
- client-generated message ID,
- plaintext.

The database enforces unique client message IDs and nonces per sender and room key.

## Server knowledge

The server stores public keys, fingerprints, encrypted room-key envelopes, ciphertext,
nonces and message metadata. It never receives a private key, room key or message
plaintext. Administrators cannot list Deal Room messages.

## Verification and recovery

Participants should compare the full public-key fingerprints through another trusted
channel. A fingerprint change is treated as an attack.

There is deliberately no silent key reset. Losing the local private key makes existing
rooms unreadable. Adding a recovery or multi-device flow requires explicit participant
approval and a separately reviewed protocol.

This protocol provides end-to-end confidentiality and authenticated encryption, but it
is not a Signal Double Ratchet and does not currently provide per-message forward secrecy.

## Legacy migration

Production startup refuses to run while pre-E2EE messages exist. Permanently purge them:

```bash
cd backend
python scripts/purge_legacy_chat.py --confirm-permanent-delete
```
