import { useCallback, useMemo, useState } from "react";
import * as LocalAuthentication from "expo-local-authentication";

export const useBiometricAuth = () => {
  const [checking, setChecking] = useState(false);

  const runBiometricCheck = useCallback(async () => {
    setChecking(true);
    try {
      const hasHardware = await LocalAuthentication.hasHardwareAsync();
      const enrolled = await LocalAuthentication.isEnrolledAsync();
      if (!hasHardware || !enrolled) return { success: false, reason: "Brak biometrii" };

      const result = await LocalAuthentication.authenticateAsync({
        promptMessage: "Potwierdź tożsamość",
        fallbackLabel: "Użyj kodu urządzenia",
      });
      return { success: result.success, reason: result.success ? "OK" : "Niepowodzenie" };
    } finally {
      setChecking(false);
    }
  }, []);

  return useMemo(
    () => ({ checking, runBiometricCheck }),
    [checking, runBiometricCheck],
  );
};
