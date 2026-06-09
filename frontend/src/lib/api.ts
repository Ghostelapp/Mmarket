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

const refreshTokenIfNeeded = async () => {
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

  let response = await fetch(`${API_BASE}${endpoint}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401 && auth) {
    const refreshed = await refreshTokenIfNeeded();
    if (refreshed) {
      const nextToken = await storage.secureGet("mask_access", "");
      if (nextToken) headers.Authorization = `Bearer ${nextToken}`;
      response = await fetch(`${API_BASE}${endpoint}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
      });
    }
  }

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "Błąd API");
  }
  return data as T;
};

export { API_BASE };