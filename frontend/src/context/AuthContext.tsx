import { createContext, PropsWithChildren, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { apiRequest, clearAuthSession, setAuthSession } from "@/src/lib/api";
import { storage } from "@/src/utils/storage";
import { AuthSession, User } from "@/src/types";

type RegisterPayload = {
  email: string;
  password: string;
  alias: string;
  public_location: string;
};

type LoginPayload = {
  email: string;
  password: string;
  otp_code?: string;
  device_name?: string;
};

type WalletLoginPayload = {
  wallet_address: string;
  challenge_id: string;
  signature: string;
  device_name?: string;
};

type AuthContextType = {
  user: User | null;
  ready: boolean;
  login: (payload: LoginPayload) => Promise<void>;
  loginWithPasskey: (email: string, credential: unknown, deviceName?: string) => Promise<void>;
  loginWithWallet: (payload: WalletLoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  setUser: (user: User | null) => void;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider = ({ children }: PropsWithChildren) => {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refreshProfile = useCallback(async () => {
    const me = await apiRequest<any>("/me", { auth: true });
    setUser({
      id: me.id,
      alias: me.alias,
      role: me.role || "user",
      privacy_level: me.privacy_level || 70,
      two_fa_enabled: !!me.two_fa_enabled,
      public_trust_level: me.public_trust_level || "starter",
      wallets: me.wallets || [],
    });
  }, []);

  const login = useCallback(async (payload: LoginPayload) => {
    const data = await apiRequest<AuthSession>("/auth/login", {
      method: "POST",
      body: {
        ...payload,
        device_name: payload.device_name || "mobile",
      },
    });
    await setAuthSession(data.access_token, data.refresh_token, data.session_id);
    setUser(data.user);
    await storage.setItem("mask_alias", data.user.alias);
  }, []);

  const register = useCallback(async (payload: RegisterPayload) => {
    await apiRequest("/auth/register", { method: "POST", body: payload });
    await login({ email: payload.email, password: payload.password, device_name: "mobile-register" });
  }, [login]);

  const loginWithPasskey = useCallback(async (email: string, credential: unknown, deviceName: string = "mobile-passkey") => {
    const data = await apiRequest<AuthSession>("/auth/passkey/login", {
      method: "POST",
      body: {
        email,
        credential,
        device_name: deviceName,
      },
    });
    await setAuthSession(data.access_token, data.refresh_token, data.session_id);
    setUser(data.user);
    await storage.setItem("mask_alias", data.user.alias);
  }, []);

  const loginWithWallet = useCallback(async (payload: WalletLoginPayload) => {
    const data = await apiRequest<AuthSession>("/auth/wallet/login", {
      method: "POST",
      body: {
        ...payload,
        device_name: payload.device_name || "web-wallet",
      },
    });
    await setAuthSession(data.access_token, data.refresh_token, data.session_id);
    setUser(data.user);
    await storage.setItem("mask_alias", data.user.alias);
  }, []);

  const logout = useCallback(async () => {
    try {
      const sessionId = await storage.secureGet("mask_session_id", "");
      await apiRequest("/auth/logout", {
        method: "POST",
        auth: true,
        body: { session_id: sessionId },
      });
    } catch {
      // noop
    }
    await clearAuthSession();
    setUser(null);
  }, []);

  useEffect(() => {
    const bootstrap = async () => {
      const token = await storage.secureGet("mask_access", "");
      if (token) {
        try {
          await refreshProfile();
        } catch {
          await clearAuthSession();
          setUser(null);
        }
      }
      setReady(true);
    };
    bootstrap();
  }, [refreshProfile]);

  const value = useMemo(
    () => ({ user, ready, login, loginWithPasskey, loginWithWallet, register, logout, refreshProfile, setUser: (u: User | null) => setUser(u) }),
    [user, ready, login, loginWithPasskey, loginWithWallet, register, logout, refreshProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
};
