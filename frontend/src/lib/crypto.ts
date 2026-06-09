import { decode as base64Decode, encode as base64Encode } from "base-64";

const utf8ToBase64 = (input: string): string => {
  return base64Encode(unescape(encodeURIComponent(input)));
};

const base64ToUtf8 = (input: string): string => {
  return decodeURIComponent(escape(base64Decode(input)));
};

const xorCipher = (text: string, key: string) => {
  const chars = text.split("").map((char, i) => {
    const code = char.charCodeAt(0) ^ key.charCodeAt(i % key.length);
    return String.fromCharCode(code);
  });
  return chars.join("");
};

export const e2eeEncrypt = (plainText: string, dealRoomId: string) => {
  const nonce = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  const mixed = xorCipher(plainText, `${dealRoomId}:${nonce}`);
  return {
    ciphertext: utf8ToBase64(mixed),
    nonce,
  };
};

export const e2eeDecrypt = (ciphertext: string, nonce: string, dealRoomId: string) => {
  try {
    const decoded = base64ToUtf8(ciphertext);
    return xorCipher(decoded, `${dealRoomId}:${nonce}`);
  } catch {
    return "[Nie udało się odszyfrować wiadomości]";
  }
};
