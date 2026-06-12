import Constants from "expo-constants";

import { storage } from "@/src/utils/storage";

const expoConfig = Constants.expoConfig;
const backendBase =
  expoConfig?.extra?.EXPO_PUBLIC_BACKEND_URL || process.env.EXPO_PUBLIC_BACKEND_URL;

const API_BASE = `${backendBase}/api`;

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  auth?: boolean;
};

let refreshPromise: Promise<boolean> | null = null;

const performTokenRefresh = async () => {
  const refresh = await storage.secureGet("mask_refresh", "");
  const sessionId = await storage.secureGet("mask_session_id", "");
  if (!refresh || !sessionId) return false;

  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh, session_id: sessionId }),
  });
  if (!response.ok) return false;
  const data = await response.json();
  await storage.secureSet("mask_access", data.access_token);
  await storage.secureSet("mask_refresh", data.refresh_token);
  return true;
};

const refreshTokenIfNeeded = async () => {
  if (!refreshPromise) {
    refreshPromise = performTokenRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
};

export const setAuthSession = async (
  accessToken: string,
  refreshToken: string,
  sessionId: string,
) => {
  await storage.secureSet("mask_access", accessToken);
  await storage.secureSet("mask_refresh", refreshToken);
  await storage.secureSet("mask_session_id", sessionId);
};

export const clearAuthSession = async () => {
  await storage.secureRemove("mask_access");
  await storage.secureRemove("mask_refresh");
  await storage.secureRemove("mask_session_id");
};

export const apiUpload = async <T>(endpoint: string, formData: FormData): Promise<T> => {
  const token = await storage.secureGet("mask_access", "");
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers,
      body: formData,
    });
  } catch {
    throw new Error(`Backend jest niedostępny pod adresem ${API_BASE}`);
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Upload nieudany");
  }
  return data as T;
};

export const apiRequest = async <T>(
  endpoint: string,
  { method = "GET", body, auth = false }: RequestOptions = {},
): Promise<T> => {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (auth) {
    const token = await storage.secureGet("mask_access", "");
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${endpoint}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(`Backend jest niedostępny pod adresem ${API_BASE}`);
  }

  if (response.status === 401 && auth) {
    const refreshed = await refreshTokenIfNeeded();
    if (refreshed) {
      const nextToken = await storage.secureGet("mask_access", "");
      if (nextToken) headers.Authorization = `Bearer ${nextToken}`;
      try {
        response = await fetch(`${API_BASE}${endpoint}`, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
        });
      } catch {
        throw new Error(`Backend jest niedostępny pod adresem ${API_BASE}`);
      }
    }
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Błąd API");
  }
  return data as T;
};

export { API_BASE };
