import * as ExpoCrypto from "expo-crypto";
import nacl from "tweetnacl";
import { decodeBase64, encodeBase64 } from "tweetnacl-util";

import { apiRequest } from "@/src/lib/api";
import {
  encryptChatPayload,
  keyIdFor,
} from "@/src/lib/e2ee-primitives";
import type { RoomKey } from "@/src/lib/e2ee-primitives";
import { storage } from "@/src/utils/storage";

export { decryptChatMessage } from "@/src/lib/e2ee-primitives";
export type { RoomKey } from "@/src/lib/e2ee-primitives";

const IDENTITY_PREFIX = "mask_e2ee_identity_";

nacl.setPRNG((target, length) => {
  target.set(ExpoCrypto.getRandomBytes(length));
});

type Identity = {
  publicKey: Uint8Array;
  secretKey: Uint8Array;
};

type StoredIdentity = {
  public_key: string;
  secret_key: string;
};

type E2EEParticipant = {
  id: string;
  e2ee_public_key?: string;
  e2ee_fingerprint?: string;
};

type E2EEEnvelope = {
  recipient_id: string;
  sender_public_key: string;
  ciphertext: string;
  nonce: string;
};

type E2EEContext = {
  transaction_id: string;
  participants: E2EEParticipant[];
  room: null | {
    key_id: string;
    encryption_version: string;
    envelope: E2EEEnvelope | null;
  };
};

const parseIdentity = (raw: string): Identity => {
  const parsed = JSON.parse(raw) as StoredIdentity;
  const publicKey = decodeBase64(parsed.public_key);
  const secretKey = decodeBase64(parsed.secret_key);
  if (
    publicKey.length !== nacl.box.publicKeyLength ||
    secretKey.length !== nacl.box.secretKeyLength
  ) {
    throw new Error("Lokalny klucz E2EE ma niepoprawny format.");
  }
  const derivedPublicKey = nacl.box.keyPair.fromSecretKey(secretKey).publicKey;
  if (!nacl.verify(publicKey, derivedPublicKey)) {
    throw new Error("Lokalny klucz E2EE nie przeszedł kontroli integralności.");
  }
  return { publicKey, secretKey };
};

const readStoredIdentity = async (storageKey: string) => {
  const secured = await storage.secureGet(storageKey, "");
  if (secured) return secured;
  const legacy = await storage.getItem(storageKey, "");
  if (legacy && (await storage.secureSet(storageKey, legacy))) {
    await storage.removeItem(storageKey);
  }
  return legacy;
};

const writeStoredIdentity = (storageKey: string, value: string) =>
  storage.secureSet(storageKey, value);

const roomFromContext = (
  context: E2EEContext,
  identity: Identity,
): RoomKey => {
  if (!context.room?.envelope) {
    throw new Error("Brak koperty klucza E2EE dla tego konta.");
  }
  const envelope = context.room.envelope;
  const roomKey = nacl.box.open(
    decodeBase64(envelope.ciphertext),
    decodeBase64(envelope.nonce),
    decodeBase64(envelope.sender_public_key),
    identity.secretKey,
  );
  if (!roomKey || roomKey.length !== nacl.secretbox.keyLength) {
    throw new Error("Nie można odszyfrować klucza Deal Room.");
  }
  if (keyIdFor(roomKey) !== context.room.key_id) {
    throw new Error("Klucz Deal Room nie przeszedł kontroli integralności.");
  }
  return {
    key: roomKey,
    keyId: context.room.key_id,
    fingerprints: context.participants
      .filter((item) => !!item.e2ee_fingerprint)
      .map((item) => ({ userId: item.id, fingerprint: item.e2ee_fingerprint! })),
  };
};

export const ensureE2EEIdentity = async (userId: string): Promise<Identity> => {
  const storageKey = `${IDENTITY_PREFIX}${userId}`;
  const stored = await readStoredIdentity(storageKey);
  const server = await apiRequest<{ public_key: string | null }>("/me/e2ee-key", {
    auth: true,
  });

  if (stored) {
    const identity = parseIdentity(stored);
    const publicKey = encodeBase64(identity.publicKey);
    if (server.public_key && server.public_key !== publicKey) {
      throw new Error("Serwer zwrócił inny przypięty klucz E2EE. Połączenie przerwane.");
    }
    if (!server.public_key) {
      await apiRequest("/me/e2ee-key", {
        method: "POST",
        auth: true,
        body: { public_key: publicKey },
      });
    }
    return identity;
  }

  if (server.public_key) {
    throw new Error(
      "To konto ma już klucz E2EE, ale ten klient nie posiada klucza prywatnego. Stare wiadomości pozostają niedostępne.",
    );
  }

  const generated = nacl.box.keyPair();
  const serialized = JSON.stringify({
    public_key: encodeBase64(generated.publicKey),
    secret_key: encodeBase64(generated.secretKey),
  } satisfies StoredIdentity);
  if (!(await writeStoredIdentity(storageKey, serialized))) {
    throw new Error("Nie udało się bezpiecznie zapisać prywatnego klucza E2EE.");
  }
  await apiRequest("/me/e2ee-key", {
    method: "POST",
    auth: true,
    body: { public_key: encodeBase64(generated.publicKey) },
  });
  return generated;
};

export const getOrCreateRoomKey = async (
  transactionId: string,
  userId: string,
): Promise<RoomKey> => {
  const identity = await ensureE2EEIdentity(userId);
  let context = await apiRequest<E2EEContext>(
    `/transactions/${transactionId}/e2ee`,
    { auth: true },
  );
  if (context.room) {
    return roomFromContext(context, identity);
  }

  if (
    context.participants.length !== 2 ||
    context.participants.some((item) => !item.e2ee_public_key)
  ) {
    throw new Error(
      "Drugi uczestnik musi najpierw otworzyć Deal Room, aby przypiąć swój klucz E2EE.",
    );
  }

  const roomKey = nacl.randomBytes(nacl.secretbox.keyLength);
  const keyId = keyIdFor(roomKey);
  const senderPublicKey = encodeBase64(identity.publicKey);
  const envelopes = context.participants.map((participant) => {
    const nonce = nacl.randomBytes(nacl.box.nonceLength);
    const ciphertext = nacl.box(
      roomKey,
      nonce,
      decodeBase64(participant.e2ee_public_key!),
      identity.secretKey,
    );
    return {
      recipient_id: participant.id,
      sender_public_key: senderPublicKey,
      ciphertext: encodeBase64(ciphertext),
      nonce: encodeBase64(nonce),
    };
  });

  try {
    await apiRequest(`/transactions/${transactionId}/e2ee/initialize`, {
      method: "POST",
      auth: true,
      body: { key_id: keyId, envelopes },
    });
    return {
      key: roomKey,
      keyId,
      fingerprints: context.participants
        .filter((item) => !!item.e2ee_fingerprint)
        .map((item) => ({ userId: item.id, fingerprint: item.e2ee_fingerprint! })),
    };
  } catch {
    context = await apiRequest<E2EEContext>(
      `/transactions/${transactionId}/e2ee`,
      { auth: true },
    );
    return roomFromContext(context, identity);
  }
};

export const encryptChatMessage = (
  plainText: string,
  transactionId: string,
  senderId: string,
  room: RoomKey,
) =>
  encryptChatPayload(
    plainText,
    transactionId,
    senderId,
    ExpoCrypto.randomUUID(),
    room,
  );
