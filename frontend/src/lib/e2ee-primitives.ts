import nacl from "tweetnacl";
import { decodeBase64, decodeUTF8, encodeBase64, encodeUTF8 } from "tweetnacl-util";

export type RoomKey = {
  key: Uint8Array;
  keyId: string;
  fingerprints: { userId: string; fingerprint: string }[];
};

export const keyIdFor = (roomKey: Uint8Array) =>
  encodeBase64(nacl.hash(roomKey).slice(0, 32));

export const encryptChatPayload = (
  plainText: string,
  transactionId: string,
  senderId: string,
  clientMessageId: string,
  room: RoomKey,
) => {
  const nonce = nacl.randomBytes(nacl.secretbox.nonceLength);
  const payload = decodeUTF8(
    JSON.stringify({
      v: 1,
      transaction_id: transactionId,
      sender_id: senderId,
      client_message_id: clientMessageId,
      text: plainText,
    }),
  );
  return {
    ciphertext: encodeBase64(nacl.secretbox(payload, nonce, room.key)),
    nonce: encodeBase64(nonce),
    client_message_id: clientMessageId,
    key_id: room.keyId,
    encryption_version: "nacl-secretbox-v1" as const,
  };
};

export const decryptChatMessage = (
  ciphertext: string,
  nonce: string,
  transactionId: string,
  senderId: string,
  clientMessageId: string,
  keyId: string,
  room: RoomKey,
) => {
  if (keyId !== room.keyId) {
    throw new Error("Wiadomość używa nieznanego klucza.");
  }
  const opened = nacl.secretbox.open(
    decodeBase64(ciphertext),
    decodeBase64(nonce),
    room.key,
  );
  if (!opened) {
    throw new Error("Integralność wiadomości jest niepoprawna.");
  }
  const payload = JSON.parse(encodeUTF8(opened));
  if (
    payload.v !== 1 ||
    payload.transaction_id !== transactionId ||
    payload.sender_id !== senderId ||
    payload.client_message_id !== clientMessageId ||
    typeof payload.text !== "string"
  ) {
    throw new Error("Wiadomość nie należy do tego Deal Room.");
  }
  return payload.text as string;
};
