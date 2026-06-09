import { useState } from "react";
import { Alert, ScrollView, StyleSheet, Text, TextInput, View, Pressable } from "react-native";

import { API_BASE } from "@/src/lib/api";

export default function AdminWebRoute() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [dashboard, setDashboard] = useState<any>(null);

  const login = async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, device_name: "admin-route" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Błąd logowania");
      setToken(data.access_token);

      const db = await fetch(`${API_BASE}/admin/dashboard`, {
        headers: { Authorization: `Bearer ${data.access_token}` },
      });
      const dbj = await db.json();
      if (!db.ok) throw new Error(dbj.detail || "Brak dostępu");
      setDashboard(dbj);
    } catch (error: any) {
      Alert.alert("Admin", error?.message || "Błąd");
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 18, gap: 12 }}>
      <Text style={styles.h1}>MASK Admin (Web Route)</Text>
      <Text style={styles.subtitle}>Awaryjny dostęp panelu admin przez /admin w Expo preview.</Text>

      {!token ? (
        <View style={styles.panel}>
          <TextInput
            testID="admin-route-email"
            style={styles.input}
            value={email}
            onChangeText={setEmail}
            placeholder="Admin email"
            placeholderTextColor="#7f8ca8"
            autoCapitalize="none"
          />
          <TextInput
            testID="admin-route-password"
            style={styles.input}
            value={password}
            onChangeText={setPassword}
            placeholder="Hasło"
            placeholderTextColor="#7f8ca8"
            secureTextEntry
          />
          <Pressable testID="admin-route-login" style={styles.button} onPress={login}>
            <Text style={styles.buttonText}>Zaloguj admina</Text>
          </Pressable>
        </View>
      ) : (
        <View style={styles.panel}>
          <Text style={styles.kv}>Users: {dashboard?.users || 0}</Text>
          <Text style={styles.kv}>Listings: {dashboard?.listings || 0}</Text>
          <Text style={styles.kv}>Open disputes: {dashboard?.open_disputes || 0}</Text>
          <Text style={styles.kv}>Revenue: {dashboard?.commission_revenue_crypto || 0} USDC</Text>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#060912" },
  h1: { color: "#eaf0ff", fontSize: 28, fontWeight: "800" },
  subtitle: { color: "#93a1c4", fontSize: 13 },
  panel: {
    borderWidth: 1,
    borderColor: "#223057",
    borderRadius: 14,
    backgroundColor: "#11182f",
    padding: 12,
    gap: 10,
  },
  input: {
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#223057",
    color: "#eaf0ff",
    backgroundColor: "#0d1325",
    paddingHorizontal: 10,
  },
  button: {
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#4db6ff",
    alignItems: "center",
    justifyContent: "center",
  },
  buttonText: { color: "#4db6ff", fontWeight: "700" },
  kv: { color: "#eaf0ff", fontSize: 15 },
});