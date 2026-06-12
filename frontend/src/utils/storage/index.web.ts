// Web storage (Metro picks index.ts on native).
// Helpers never throw: reads return `fallback`, writes return `false`.
// Values supported: string | number | boolean | null (JSON-serialized on disk).
// Usage: import { storage } from "@/src/utils/storage"; await storage.getItem(key, fallback);
// Sensitive values use an encrypted IndexedDB store backed by a non-extractable WebCrypto key.

import AsyncStorage from "@react-native-async-storage/async-storage";

import { AssertNoExtras, StorageBase, StorageItemValue } from "./storage-base";

const SECURE_DB = "mask-secure-storage";
const SECURE_STORE = "secure";
const SECURE_KEY_ID = "__device_key__";

const openSecureDb = () =>
  new Promise<IDBDatabase>((resolve, reject) => {
    const request = globalThis.indexedDB.open(SECURE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(SECURE_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

const secureRecord = <T,>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
) =>
  openSecureDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const transaction = db.transaction(SECURE_STORE, mode);
        const request = action(transaction.objectStore(SECURE_STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => db.close();
        transaction.onerror = () => db.close();
        transaction.onabort = () => db.close();
      }),
  );

let deviceKeyPromise: Promise<CryptoKey> | null = null;

const loadOrCreateSecureDeviceKey = async (): Promise<CryptoKey> => {
  const existing = await secureRecord<CryptoKey | undefined>("readonly", (store) =>
    store.get(SECURE_KEY_ID),
  );
  if (existing) return existing;
  const generated = await globalThis.crypto.subtle.generateKey(
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
  await secureRecord("readwrite", (store) => store.put(generated, SECURE_KEY_ID));
  return generated;
};

const secureDeviceKey = async (): Promise<CryptoKey> => {
  if (!deviceKeyPromise) {
    deviceKeyPromise = loadOrCreateSecureDeviceKey().catch((error) => {
      deviceKeyPromise = null;
      throw error;
    });
  }
  return deviceKeyPromise;
};

export class Storage extends StorageBase {
  // General KV — backed by AsyncStorage (its built-in web shim uses IndexedDB).
  async getItem<Fallback extends StorageItemValue>(
    key: string,
    fallback: Fallback,
  ): Promise<Fallback | null> {
    try {
      const raw = await AsyncStorage.getItem(key);
      return this.retrieve(raw, fallback);
    } catch (e) {
      this.warn("getItem", key, e);
      return fallback;
    }
  }

  async setItem<Value extends StorageItemValue>(
    key: string,
    value: Value,
  ): Promise<boolean> {
    try {
      await AsyncStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (e) {
      this.warn("setItem", key, e);
      return false;
    }
  }

  async removeItem(key: string): Promise<boolean> {
    try {
      await AsyncStorage.removeItem(key);
      return true;
    } catch (e) {
      this.warn("removeItem", key, e);
      return false;
    }
  }

  async secureGet<Fallback extends StorageItemValue>(
    key: string,
    fallback: Fallback,
  ): Promise<Fallback | null> {
    try {
      const record = await secureRecord<{ iv: ArrayBuffer; ciphertext: ArrayBuffer } | undefined>(
        "readonly",
        (store) => store.get(key),
      );
      if (!record) return fallback;
      const decrypted = await globalThis.crypto.subtle.decrypt(
        { name: "AES-GCM", iv: new Uint8Array(record.iv) },
        await secureDeviceKey(),
        record.ciphertext,
      );
      return this.retrieve(new TextDecoder().decode(decrypted), fallback);
    } catch (e) {
      this.warn("secureGet", key, e);
      return fallback;
    }
  }

  async secureSet<Value extends StorageItemValue>(
    key: string,
    value: Value,
  ): Promise<boolean> {
    try {
      const iv = globalThis.crypto.getRandomValues(new Uint8Array(12));
      const ciphertext = await globalThis.crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        await secureDeviceKey(),
        new TextEncoder().encode(JSON.stringify(value)),
      );
      await secureRecord("readwrite", (store) =>
        store.put({ iv: iv.buffer as ArrayBuffer, ciphertext }, key),
      );
      return true;
    } catch (e) {
      this.warn("secureSet", key, e);
      return false;
    }
  }

  async secureRemove(key: string): Promise<boolean> {
    try {
      await secureRecord("readwrite", (store) => store.delete(key));
      return true;
    } catch (e) {
      this.warn("secureRemove", key, e);
      return false;
    }
  }
}

export const storage = new Storage();

// Compile-time guard: any new method must be declared in storage-base.ts first.
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- intentional compile-time-only assertion
type _NoExtras = AssertNoExtras<Exclude<keyof Storage, keyof StorageBase>>;
