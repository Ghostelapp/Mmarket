import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import Animated, { FadeInDown, useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";
import { Ionicons, MaterialCommunityIcons } from "@expo/vector-icons";
import { useFonts } from "expo-font";
import { PressStart2P_400Regular } from "@expo-google-fonts/press-start-2p";
import { VT323_400Regular } from "@expo-google-fonts/vt323";

import { useAuth } from "@/src/context/AuthContext";
import { theme } from "@/src/constants/theme";
import { apiRequest } from "@/src/lib/api";
import { e2eeDecrypt, e2eeEncrypt } from "@/src/lib/crypto";
import { useBiometricAuth } from "@/src/hooks/useBiometricAuth";
import { EncryptedMessage, Listing, Transaction } from "@/src/types";

const categories = [
  "elektronika",
  "moda",
  "dom",
  "motoryzacja",
  "sport",
  "dziecko",
  "kolekcje",
  "usługi lokalne",
  "produkty cyfrowe legalne",
  "inne",
];

const base64PixelBlue =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
const pixelHeroImage = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAGAAAABACAIAAABqVuVZAAACqUlEQVR42u2av0vDQBTHX0ujGCitDg52qAWRiuCoiENn/wLB3dFZqLP9Bzq6K/0jBDuJqyDt5uTioEihUgV1iJQz9yOXS95rm74b5O71crn3ue99LwnmykubwEVf8oyAATEgBsSAGBADymop+IUyU2AFJVAQ3tAL+Q/KTD6/l1hB2VKQYUmfB/duY1aKe8SAcmvFXcr7OaOZFCZEQOxB8+FB5fw6wW0e3zsYw26XjsZ1H2et87NLB3VkilNsCL2gUivtECyDD1vsQdl6DvJhi2ALyGbECsqKgobQo3Ef9qCMPgc9vnUoMzlYPmMFZcuDKA0IANbgkBWULQU9vT+wB7GC2INYIxEKqnobSENX4fTq5YQmjePVy5l8F8ObN9ldED3obtQGgFqpjU5oBACwv3jKHpStU0xcUiQzItjCRArCyITG4NA9SHgsStuMRlrBsgfRlRxc3zAFE6Bm5RYAWv0GADTrXfE3ZdAcl4O6eKvf+CmqJjQAXTz5HWOl88+Dgp9DXZVBMR4atFnv6mYsl2a9mxv8db54bui6nVe6BhDKoqMTF80/BUVepgvaT2WqgjZotKeYWU0hKSn1lVBcKcrEfl1jH/M6TPbgIidh2c0yHyUdZ+HwKRbzFLNUdQi/MmJoOnRIPqaDahwBuUFxyAdvNBdAnufyn2tfr0MA8FZ8y+a4jtc0z4H6XSy48XgSumZQ8VZ8MZNQM9aqGOikjiaRgsxqiqxbdkt4Ob+LkZxiyRWkE5SoLOUim4OxLklxT6F/DwpNWq6MPcjSjJTs5DWYsQ9mciZikg4+bZbYLG0x3aaTj7CgomxG9sEWDsUnV6WaxL8yDqVqRChkwuFTbJq2mHnThXafXBdlOC+ADJgMm3TuANlgmiCaaQGkwzRxNNMFCOM9M5XyC133SEKfnMOXAAAAAElFTkSuQmCC";
const pixelAvatarA = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAIAAAADnC86AAAAzUlEQVR42u2YwQqDMBBEp0FQUfBg7vb/vy231lpP7WEhiEqySaPBspLDYNTnbOIsiL4b8PjQOFPf+m6Y3k8AAOqqPUe/jFHIdAj4/8FFxD3jbEjo6k66KfWxji2SeT6NY/t0669GS5qmxtnwrasfva5ehW89rNQRa5kA7KUuyy7fsYBDwN5dE5RiaZLrQMduH9tQSxmZTaltNK6aRES8KG9tLYyzxsvrL96Ptx3JrSVABCxgAedPrt3G4NCXdUzxy8znXS2bKzsYuX43fQEhdPNsCWNwiAAAAABJRU5ErkJggg==";
const pixelAvatarB = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAIAAAADnC86AAAArUlEQVR42u2XPQqAMAyFGxEUHBx09wje/xjew1XQKQ5CBwshqWKCTcb+hZeveaUw9FPQCMCAXTN+nHU71ioohVpicMZfRZ2xZ97hNrK0+EfGUWiqj5h6ypg+Og6mFN7pY0KQFLNtxlcBOZqYK92rLfYxBx7/KogZE23K72AZY9oipM5lmLG0gEwvM/8eS32YUyfbjPMUG2XsXu2MC2VckldnfIr8f2yNMWBAlcQnEwN0jlIzfBsAAAAASUVORK5CYII=";

const base64UrlToBuffer = (value: string): ArrayBuffer => {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const normalized = padded + "=".repeat((4 - (padded.length % 4)) % 4);
  const binary = atob(normalized);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return bytes.buffer;
};

const toBase64Url = (buffer: ArrayBuffer): string =>
  btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const adaptRegistrationOptions = (publicKey: any) => ({
  ...publicKey,
  challenge: base64UrlToBuffer(publicKey.challenge),
  user: {
    ...publicKey.user,
    id: base64UrlToBuffer(publicKey.user.id),
  },
  excludeCredentials: (publicKey.excludeCredentials || []).map((item: any) => ({
    ...item,
    id: base64UrlToBuffer(item.id),
  })),
});

const adaptLoginOptions = (publicKey: any) => ({
  ...publicKey,
  challenge: base64UrlToBuffer(publicKey.challenge),
  allowCredentials: (publicKey.allowCredentials || []).map((item: any) => ({
    ...item,
    id: base64UrlToBuffer(item.id),
  })),
});

type TabKey = "market" | "sell" | "deals" | "profile" | "admin";

type AdminDashboard = {
  users: number;
  listings: number;
  active_transactions: number;
  open_disputes: number;
  open_reports: number;
  suspicious_accounts: number;
  commission_revenue_crypto: number;
  system_status: string;
};

function PixelButton({
  label,
  icon,
  onPress,
  variant = "primary",
  disabled = false,
  testID,
}: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  testID?: string;
}) {
  const color =
    variant === "danger" ? theme.danger : variant === "secondary" ? theme.textMuted : theme.neonBlue;
  const backgroundColor =
    variant === "primary" ? theme.neonBlue : variant === "danger" ? "rgba(255,93,121,0.14)" : "rgba(0,243,255,0.08)";
  const labelColor = variant === "primary" ? theme.bg : color;

  return (
    <Pressable
      testID={testID}
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        {
          borderColor: color,
          backgroundColor,
          opacity: pressed || disabled ? 0.82 : 1,
          transform: [{ scale: pressed ? 0.98 : 1 }],
        },
      ]}
    >
      <Ionicons name={icon} size={14} color={labelColor} />
      <Text style={[styles.buttonText, { color: labelColor }]}>{label}</Text>
    </Pressable>
  );
}

function Tag({ value }: { value: string }) {
  return (
    <View style={styles.tag}>
      <Text style={styles.tagText}>{value}</Text>
    </View>
  );
}

function AuthScreen() {
  const { register, login, loginWithPasskey } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [alias, setAlias] = useState("CipherFox");
  const [location, setLocation] = useState("Warszawa");
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async () => {
    try {
      setBusy(true);
      if (mode === "register") {
        await register({ email, password, alias, public_location: location });
      } else {
        await login({ email, password, otp_code: otp || undefined, device_name: "expo-mobile" });
      }
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się zalogować");
    } finally {
      setBusy(false);
    }
  };

  const webPasskeyLogin = async () => {
    if (typeof window === "undefined" || !("credentials" in navigator)) {
      Alert.alert("Passkey", "Passkey dostępny tylko w web preview/przeglądarce.");
      return;
    }
    if (!email.trim()) {
      Alert.alert("Passkey", "Podaj e-mail.");
      return;
    }

    try {
      setBusy(true);
      const optionsData = await apiRequest<any>("/auth/passkey/login/options", {
        method: "POST",
        body: { email: email.trim() },
      });
      const publicKey = adaptLoginOptions(optionsData.public_key);
      const credential = await (navigator as any).credentials.get({ publicKey });
      if (!credential) throw new Error("Brak danych passkey");

      const response = (credential as any).response;
      await loginWithPasskey(email.trim(), {
        id: (credential as any).id,
        rawId: toBase64Url((credential as any).rawId),
        type: (credential as any).type,
        authenticatorAttachment: (credential as any).authenticatorAttachment,
        response: {
          clientDataJSON: toBase64Url(response.clientDataJSON),
          authenticatorData: toBase64Url(response.authenticatorData),
          signature: toBase64Url(response.signature),
          userHandle: response.userHandle ? toBase64Url(response.userHandle) : null,
        },
      });
    } catch (error: any) {
      Alert.alert("Passkey", error?.message || "Logowanie passkey nieudane");
    } finally {
      setBusy(false);
    }
  };

  return (
    <LinearGradient colors={["#050509", "#180b2c", "#0b1022"]} style={styles.container}>
      <SafeAreaView style={styles.container}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : "height"}
          style={styles.container}
        >
          <View style={styles.authWrap}>
            <Image source={{ uri: pixelHeroImage }} style={styles.pixelHero} />
            <View style={styles.logoWrap}>
              <MaterialCommunityIcons name="shield-lock-outline" size={34} color={theme.neonGreen} />
              <Text style={styles.h1}>MASK Market</Text>
              <Text style={styles.subtitle}>Kupuj i sprzedawaj za crypto. Bez ujawniania danych.</Text>
            </View>

            <View style={styles.segmentRow}>
              {(["login", "register"] as const).map((option) => (
                <Pressable
                  key={option}
                  testID={`auth-mode-${option}`}
                  onPress={() => setMode(option)}
                  style={[styles.segmentBtn, mode === option && styles.segmentBtnActive]}
                >
                  <Text style={[styles.segmentText, mode === option && styles.segmentTextActive]}>
                    {option === "login" ? "Logowanie" : "Rejestracja"}
                  </Text>
                </Pressable>
              ))}
            </View>

            <View style={styles.panel}>
              <TextInput
                testID="auth-email"
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                keyboardType="email-address"
                autoCapitalize="none"
                placeholder="E-mail"
                placeholderTextColor={theme.textMuted}
              />
              <TextInput
                testID="auth-password"
                style={styles.input}
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                placeholder="Hasło"
                placeholderTextColor={theme.textMuted}
              />

              {mode === "register" && (
                <>
                  <TextInput
                    testID="auth-alias"
                    style={styles.input}
                    value={alias}
                    onChangeText={setAlias}
                    placeholder="Alias publiczny"
                    placeholderTextColor={theme.textMuted}
                  />
                  <TextInput
                    testID="auth-location"
                    style={styles.input}
                    value={location}
                    onChangeText={setLocation}
                    placeholder="Lokalizacja przybliżona"
                    placeholderTextColor={theme.textMuted}
                  />
                </>
              )}

              {mode === "login" && (
                <TextInput
                  testID="auth-otp"
                  style={styles.input}
                  value={otp}
                  onChangeText={setOtp}
                  placeholder="Kod 2FA (jeśli aktywny)"
                  placeholderTextColor={theme.textMuted}
                />
              )}

              <PixelButton
                testID="auth-submit"
                label={busy ? "Przetwarzanie..." : mode === "login" ? "Zaloguj" : "Załóż konto"}
                icon="log-in-outline"
                onPress={onSubmit}
                disabled={busy}
              />
              {mode === "login" && (
                <PixelButton
                  testID="auth-passkey-login"
                  label="Zaloguj passkey"
                  icon="key-outline"
                  variant="secondary"
                  onPress={webPasskeyLogin}
                  disabled={busy}
                />
              )}
            </View>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </LinearGradient>
  );
}

function MarketplaceTab({ onBuy }: { onBuy: (listing: Listing) => Promise<void> }) {
  const [items, setItems] = useState<Listing[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const [loading, setLoading] = useState(false);

  const fetchListings = useCallback(async () => {
    setLoading(true);
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set("q", search.trim());
      if (filter !== "all") query.set("category", filter);
      const data = await apiRequest<Listing[]>(`/listings?${query.toString()}`, { auth: true });
      setItems(data);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się pobrać ofert");
    } finally {
      setLoading(false);
    }
  }, [filter, search]);

  useEffect(() => {
    fetchListings();
  }, [fetchListings]);

  return (
    <View style={styles.tabContent}>
      <Image source={{ uri: pixelHeroImage }} style={styles.pixelHero} />
      <Text style={styles.h2}>Marketplace</Text>
      <Text style={styles.subtitle}>Privacy-first P2P • Crypto-only • Escrow-first</Text>

      <View style={styles.panel}>
        <TextInput
          testID="market-search"
          style={styles.input}
          placeholder="Szukaj po tytule/opisie"
          placeholderTextColor={theme.textMuted}
          value={search}
          onChangeText={setSearch}
        />
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
          <Pressable
            testID="filter-all"
            style={[styles.filterChip, filter === "all" && styles.filterChipActive]}
            onPress={() => setFilter("all")}
          >
            <Text style={styles.filterText}>Wszystkie</Text>
          </Pressable>
          {categories.map((cat) => (
            <Pressable
              testID={`filter-category-${cat}`}
              key={cat}
              style={[styles.filterChip, filter === cat && styles.filterChipActive]}
              onPress={() => setFilter(cat)}
            >
              <Text style={styles.filterText}>{cat}</Text>
            </Pressable>
          ))}
        </ScrollView>
        <PixelButton testID="market-refresh" label="Odśwież" icon="refresh-outline" onPress={fetchListings} />
      </View>

      {loading ? (
        <ActivityIndicator size="large" color={theme.neonBlue} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          contentContainerStyle={{ gap: 12, paddingBottom: 140 }}
          renderItem={({ item, index }) => (
            <Animated.View entering={FadeInDown.delay(index * 40).duration(360)} style={styles.panel}>
              <View style={styles.rowBetween}>
                <Text style={styles.cardTitle}>{item.title}</Text>
                <Tag value={item.status} />
              </View>
              <Text style={styles.cardBody} numberOfLines={2}>{item.description}</Text>
              <View style={styles.rowWrap}>
                <Tag value={item.category} />
                <Tag value={`${item.price_fiat} ${item.fiat_currency}`} />
                <Tag value={`${item.crypto_amount} ${item.crypto_token}`} />
                <Tag value={item.location_public} />
              </View>

              <View style={styles.aliasRow}>
                <MaterialCommunityIcons name="incognito" size={15} color={theme.neonViolet} />
                <Text style={styles.caption}>
                  Sprzedający: {item.seller_public?.display_alias || "ukryty"} • Trust {item.seller_public?.trust_score || 0}
                </Text>
              </View>

              <View style={styles.actionsRow}>
                <PixelButton testID={`buy-now-${item.id}`} label="Kup teraz" icon="flash-outline" onPress={() => onBuy(item)} />
                <PixelButton
                  testID={`report-listing-${item.id}`}
                  label="Zgłoś"
                  icon="warning-outline"
                  variant="secondary"
                  onPress={async () => {
                    try {
                      await apiRequest(`/listings/${item.id}/report`, {
                        method: "POST",
                        auth: true,
                        body: { reason: "Podejrzana oferta", details: "Wymaga ręcznej moderacji" },
                      });
                      Alert.alert("OK", "Oferta zgłoszona");
                    } catch (error: any) {
                      Alert.alert("Błąd", error?.message || "Nie udało się zgłosić");
                    }
                  }}
                />
              </View>
            </Animated.View>
          )}
          ListEmptyComponent={<Text style={styles.emptyText}>Brak aktywnych ofert.</Text>}
        />
      )}
    </View>
  );
}

function SellTab() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [price, setPrice] = useState("99");
  const [currency, setCurrency] = useState<"PLN" | "EUR">("PLN");
  const [category, setCategory] = useState(categories[0]);
  const [condition, setCondition] = useState("nowy");
  const [location, setLocation] = useState("Kraków");
  const [feeInfo, setFeeInfo] = useState<{ listingId: string; amount: number; token: string; network: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [lastPaidListingId, setLastPaidListingId] = useState<string | null>(null);
  const [promotionIntent, setPromotionIntent] = useState<null | {
    intentId: string;
    listingId: string;
    packageType: "basic" | "boost";
    network: "Base" | "Polygon";
    amount: number;
  }>(null);

  const createListing = async () => {
    try {
      setBusy(true);
      const listing = await apiRequest<Listing>("/listings", {
        method: "POST",
        auth: true,
        body: {
          title,
          description,
          price_fiat: Number(price),
          fiat_currency: currency,
          category,
          condition,
          location_public: location,
          shipping_options: ["blind-delivery", "punkt partnerski"],
          images: [base64PixelBlue],
        },
      });
      setFeeInfo({
        listingId: listing.id,
        amount: listing.listing_fee.amount,
        token: listing.listing_fee.token,
        network: listing.listing_fee.network,
      });
      Alert.alert("Oferta utworzona", "Aby opublikować, opłać listing fee.");
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się dodać oferty");
    } finally {
      setBusy(false);
    }
  };

  const payListingFee = async () => {
    if (!feeInfo) return;
    try {
      setBusy(true);
      await apiRequest(`/listings/${feeInfo.listingId}/pay-listing-fee`, {
        method: "POST",
        auth: true,
        body: {
          amount: feeInfo.amount,
          token: feeInfo.token,
          network: feeInfo.network,
          payment_tx_hash: `0xLISTING${Date.now()}`,
        },
      });
      Alert.alert("Sukces", "Opłata potwierdzona, oferta aktywna lub w moderacji.");
      setLastPaidListingId(feeInfo.listingId);
      setFeeInfo(null);
      setTitle("");
      setDescription("");
      setPrice("99");
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Płatność nieudana");
    } finally {
      setBusy(false);
    }
  };

  const createPromotionIntent = async (packageType: "basic" | "boost") => {
    const targetListingId = lastPaidListingId || feeInfo?.listingId;
    if (!targetListingId) {
      Alert.alert("Promocja", "Najpierw utwórz i opłać ofertę.");
      return;
    }
    try {
      const intent = await apiRequest<any>(`/listings/${targetListingId}/promote-intent`, {
        method: "POST",
        auth: true,
        body: {
          package_type: packageType,
          network: "Base",
          token: "USDC",
        },
      });
      setPromotionIntent({
        intentId: intent.id,
        listingId: targetListingId,
        packageType,
        network: "Base",
        amount: intent.amount,
      });
      Alert.alert("Promocja", `Intent gotowy: ${intent.amount} USDC (${packageType.toUpperCase()})`);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się utworzyć intent promocji");
    }
  };

  const confirmPromotion = async () => {
    if (!promotionIntent) return;
    try {
      await apiRequest(`/listings/${promotionIntent.listingId}/promote-confirm`, {
        method: "POST",
        auth: true,
        body: {
          intent_id: promotionIntent.intentId,
          tx_hash: `0xPROMO${Date.now()}`,
        },
      });
      Alert.alert("Sukces", "Promowana oferta aktywna.");
      setPromotionIntent(null);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Potwierdzenie promocji nieudane");
    }
  };

  return (
    <ScrollView style={styles.tabContent} contentContainerStyle={{ paddingBottom: 140 }}>
      <Image source={{ uri: pixelHeroImage }} style={styles.pixelHero} />
      <Text style={styles.h2}>Dodaj ofertę</Text>
      <Text style={styles.subtitle}>Listing fee w crypto aktywuje publikację</Text>
      <View style={styles.panel}>
        <TextInput testID="sell-title" style={styles.input} value={title} onChangeText={setTitle} placeholder="Tytuł" placeholderTextColor={theme.textMuted} />
        <TextInput
          testID="sell-description"
          style={[styles.input, { minHeight: 88 }]}
          value={description}
          onChangeText={setDescription}
          placeholder="Opis"
          placeholderTextColor={theme.textMuted}
          multiline
        />
        <TextInput
          testID="sell-price"
          style={styles.input}
          value={price}
          onChangeText={setPrice}
          placeholder="Cena"
          placeholderTextColor={theme.textMuted}
          keyboardType="numeric"
        />

        <View style={styles.rowWrap}>
          <Pressable style={[styles.filterChip, currency === "PLN" && styles.filterChipActive]} onPress={() => setCurrency("PLN")}>
            <Text style={styles.filterText}>PLN</Text>
          </Pressable>
          <Pressable style={[styles.filterChip, currency === "EUR" && styles.filterChipActive]} onPress={() => setCurrency("EUR")}>
            <Text style={styles.filterText}>EUR</Text>
          </Pressable>
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
          {categories.map((cat) => (
            <Pressable key={cat} style={[styles.filterChip, category === cat && styles.filterChipActive]} onPress={() => setCategory(cat)}>
              <Text style={styles.filterText}>{cat}</Text>
            </Pressable>
          ))}
        </ScrollView>

        <TextInput testID="sell-condition" style={styles.input} value={condition} onChangeText={setCondition} placeholder="Stan" placeholderTextColor={theme.textMuted} />
        <TextInput testID="sell-location" style={styles.input} value={location} onChangeText={setLocation} placeholder="Lokalizacja" placeholderTextColor={theme.textMuted} />

        <PixelButton testID="sell-create" label={busy ? "Przetwarzanie..." : "Utwórz ofertę"} icon="add-circle-outline" onPress={createListing} disabled={busy} />

        {feeInfo && (
          <View style={[styles.panelSoft, { marginTop: 10 }]}> 
            <Text style={styles.caption}>Opłata za wystawienie: {feeInfo.amount} {feeInfo.token}</Text>
            <Text style={styles.caption}>Sieć: {feeInfo.network}</Text>
            <PixelButton testID="sell-pay-listing-fee" label="Opłać listing fee" icon="wallet-outline" onPress={payListingFee} disabled={busy} />

            <Text style={[styles.caption, { marginTop: 8 }]}>Promowane oferty (crypto):</Text>
            <View style={styles.actionsRow}>
              <PixelButton testID="promo-basic" label="Basic 1 USDC" icon="rocket-outline" variant="secondary" onPress={() => createPromotionIntent("basic")} />
              <PixelButton testID="promo-boost" label="Boost 3 USDC" icon="flame-outline" variant="secondary" onPress={() => createPromotionIntent("boost")} />
            </View>
            {promotionIntent && (
              <View style={styles.panelSoft}>
                <Text style={styles.caption}>Intent: {promotionIntent.intentId.slice(0, 8)}…</Text>
                <Text style={styles.caption}>Pakiet: {promotionIntent.packageType} • Kwota: {promotionIntent.amount} USDC</Text>
                <PixelButton testID="promo-confirm" label="Potwierdź tx promocji" icon="checkmark-circle-outline" onPress={confirmPromotion} />
              </View>
            )}
          </View>
        )}
      </View>
    </ScrollView>
  );
}

function DealsTab() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [selected, setSelected] = useState<Transaction | null>(null);
  const [messages, setMessages] = useState<EncryptedMessage[]>([]);
  const [messageText, setMessageText] = useState("");

  const refreshTransactions = useCallback(async () => {
    try {
      const data = await apiRequest<Transaction[]>("/transactions", { auth: true });
      setTransactions(data);
      if (selected) {
        const fresh = data.find((item) => item.id === selected.id) || null;
        setSelected(fresh);
      }
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się pobrać transakcji");
    }
  }, [selected]);

  const loadMessages = async (tx: Transaction) => {
    try {
      const data = await apiRequest<EncryptedMessage[]>(`/transactions/${tx.id}/messages`, { auth: true });
      setMessages(data);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się pobrać wiadomości");
    }
  };

  useEffect(() => {
    refreshTransactions();
  }, [refreshTransactions]);

  const sendMessage = async () => {
    if (!selected || !messageText.trim()) return;
    try {
      const encrypted = e2eeEncrypt(messageText.trim(), selected.deal_room_id);
      await apiRequest(`/transactions/${selected.id}/messages`, {
        method: "POST",
        auth: true,
        body: {
          ciphertext: encrypted.ciphertext,
          nonce: encrypted.nonce,
          message_type: "text",
          expires_in_days: 14,
        },
      });
      setMessageText("");
      await loadMessages(selected);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się wysłać");
    }
  };

  const callAction = async (endpoint: string, body?: any) => {
    if (!selected) return;
    try {
      await apiRequest(`/transactions/${selected.id}${endpoint}`, { method: "POST", auth: true, body });
      await refreshTransactions();
      const found = transactions.find((t) => t.id === selected.id);
      if (found) {
        setSelected(found);
        await loadMessages(found);
      }
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Akcja nieudana");
    }
  };

  return (
    <View style={styles.tabContent}>
      <Image source={{ uri: pixelHeroImage }} style={styles.pixelHero} />
      <Text style={styles.h2}>Deal Room</Text>
      <Text style={styles.subtitle}>Alias transakcyjne • E2EE chat • Escrow status</Text>

      {!selected ? (
        <FlatList
          data={transactions}
          keyExtractor={(item) => item.id}
          contentContainerStyle={{ gap: 12, paddingBottom: 140 }}
          renderItem={({ item }) => (
            <Pressable
              style={styles.panel}
              onPress={async () => {
                setSelected(item);
                await loadMessages(item);
              }}
            >
              <Text style={styles.cardTitle}>Deal {item.id.slice(0, 8)}</Text>
              <Text style={styles.cardBody}>Status: {item.status} • Escrow: {item.escrow_status}</Text>
              <Text style={styles.caption}>
                Alias: {item.buyer_alias} / {item.seller_alias}
              </Text>
              <View style={styles.rowWrap}>
                <Tag value={`${item.gross_amount} ${item.token}`} />
                <Tag value={`Fee ${item.fee_percent}%`} />
                <Tag value={item.network} />
              </View>
            </Pressable>
          )}
          ListEmptyComponent={<Text style={styles.emptyText}>Brak aktywnych deal rooms.</Text>}
        />
      ) : (
        <View style={{ flex: 1 }}>
          <View style={styles.panel}>
            <View style={styles.rowBetween}>
              <Text style={styles.cardTitle}>Deal {selected.id.slice(0, 8)}</Text>
              <Pressable onPress={() => setSelected(null)}>
                <Text style={styles.caption}>Wróć</Text>
              </Pressable>
            </View>
            <Text style={styles.cardBody}>
              Status: {selected.status} • Escrow: {selected.escrow_status} • Ship: {selected.shipping_status}
            </Text>
            <Text style={styles.caption}>
              Alias kupującego: {selected.buyer_alias} • Alias sprzedającego: {selected.seller_alias}
            </Text>

            <View style={styles.actionsRow}>
              <PixelButton
                testID="deal-fund-escrow"
                label="Fund escrow"
                icon="wallet-outline"
                variant="secondary"
                onPress={() =>
                  callAction("/fund", {
                    amount: selected.gross_amount,
                    token: selected.token,
                    network: selected.network,
                    tx_hash: `0xFUND${Date.now()}`,
                  })
                }
              />
              <PixelButton
                testID="deal-mark-shipped"
                label="Mark shipped"
                icon="cube-outline"
                variant="secondary"
                onPress={() =>
                  callAction("/mark-shipped", {
                    encrypted_address_blob: `cipher-address-${Date.now()}`,
                    carrier: "InPost",
                  })
                }
              />
            </View>

            <View style={styles.actionsRow}>
              <PixelButton
                testID="deal-confirm-delivery"
                label="Confirm delivery"
                icon="checkmark-done-outline"
                onPress={() => callAction("/confirm-delivery")}
              />
              <PixelButton
                testID="deal-open-dispute"
                label="Open dispute"
                icon="alert-circle-outline"
                variant="danger"
                onPress={() => callAction("/open-dispute", { reason: "Problem z produktem" })}
              />
            </View>
          </View>

          <View style={[styles.panel, { flex: 1 }]}> 
            <Text style={styles.cardTitle}>Czat E2EE</Text>
            <Text style={styles.caption}>Admin nie widzi treści bez dobrowolnego zgłoszenia dowodów.</Text>
            <ScrollView style={{ flex: 1 }} contentContainerStyle={{ gap: 8, paddingVertical: 8 }}>
              {messages.map((msg) => (
                <View key={msg.id} style={styles.messageBubble}>
                  <View style={styles.aliasRow}>
                    <Image
                      source={{ uri: msg.sender_id === selected.buyer_id ? pixelAvatarA : pixelAvatarB }}
                      style={styles.pixelAvatar}
                    />
                    <Text style={styles.caption}>{msg.sender_id === selected.buyer_id ? "Kupujący" : "Sprzedający"}</Text>
                  </View>
                  <Text style={styles.messageText}>{e2eeDecrypt(msg.ciphertext, msg.nonce, selected.deal_room_id)}</Text>
                  <Text style={styles.caption}>{new Date(msg.created_at).toLocaleString()}</Text>
                </View>
              ))}
            </ScrollView>
            <TextInput
              testID="deal-message-input"
              style={styles.input}
              value={messageText}
              onChangeText={setMessageText}
              placeholder="Wiadomość E2EE"
              placeholderTextColor={theme.textMuted}
            />
            <View style={styles.actionsRow}>
              <PixelButton testID="deal-send-message" label="Wyślij" icon="send-outline" onPress={sendMessage} />
              <PixelButton
                testID="deal-report-evidence"
                label="Zgłoś dowód"
                icon="flag-outline"
                variant="secondary"
                onPress={async () => {
                  try {
                    await apiRequest(`/transactions/${selected.id}/messages/report-evidence`, {
                      method: "POST",
                      auth: true,
                      body: {
                        selected_messages: messages.slice(0, 2).map((m) => ({
                          id: m.id,
                          decrypted_text: e2eeDecrypt(m.ciphertext, m.nonce, selected.deal_room_id),
                        })),
                        dispute_reason: "Dobrowolne ujawnienie dowodów",
                      },
                    });
                    Alert.alert("OK", "Wybrane wiadomości przekazano do sporu.");
                  } catch (error: any) {
                    Alert.alert("Błąd", error?.message || "Nie udało się zgłosić dowodów");
                  }
                }}
              />
            </View>
          </View>
        </View>
      )}
    </View>
  );
}

function ProfileTab() {
  const { user, logout, refreshProfile } = useAuth();
  const { checking, runBiometricCheck } = useBiometricAuth();
  const [security, setSecurity] = useState<any>(null);
  const [devices, setDevices] = useState<any[]>([]);
  const [passwordFor2FA, setPasswordFor2FA] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [pendingSecret, setPendingSecret] = useState("");

  const loadSecurity = async () => {
    try {
      const sec = await apiRequest<any>("/me/security", { auth: true });
      const dev = await apiRequest<any[]>("/me/devices", { auth: true });
      setSecurity(sec);
      setDevices(dev);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się pobrać zabezpieczeń");
    }
  };

  useEffect(() => {
    loadSecurity();
  }, []);

  const pulse = useSharedValue(1);
  const animatedStyle = useAnimatedStyle(() => ({ transform: [{ scale: pulse.value }] }));

  const runBiometric = async () => {
    pulse.value = withSpring(1.06, { damping: 9 }, () => {
      pulse.value = withSpring(1);
    });
    const result = await runBiometricCheck();
    Alert.alert("Biometria", result.success ? "Potwierdzono" : result.reason);
  };

  const enable2FA = async () => {
    try {
      const data = await apiRequest<any>("/auth/2fa/enable", {
        method: "POST",
        auth: true,
        body: { account_password: passwordFor2FA },
      });
      setPendingSecret(data.secret);
      Alert.alert("Sekret TOTP", data.secret);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się aktywować 2FA");
    }
  };

  const verify2FA = async () => {
    try {
      await apiRequest("/auth/2fa/verify", {
        method: "POST",
        auth: true,
        body: { code: otpCode },
      });
      Alert.alert("Sukces", "2FA aktywne");
      setOtpCode("");
      setPendingSecret("");
      await refreshProfile();
      await loadSecurity();
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Niepoprawny kod");
    }
  };

  const registerPasskeyDemo = async () => {
    if (typeof window === "undefined" || !("credentials" in navigator)) {
      Alert.alert("Passkey", "Obsługa passkey dostępna w web preview/przeglądarce.");
      return;
    }
    try {
      const optionsData = await apiRequest<any>("/auth/passkey/register/options", {
        method: "POST",
        auth: true,
        body: { nickname: "Device Passkey" },
      });
      const publicKey = adaptRegistrationOptions(optionsData.public_key);
      const credential = await (navigator as any).credentials.create({ publicKey });
      if (!credential) throw new Error("Brak credential passkey");

      const response = (credential as any).response;
      await apiRequest("/auth/passkey/register", {
        method: "POST",
        auth: true,
        body: {
          credential: {
            id: (credential as any).id,
            rawId: toBase64Url((credential as any).rawId),
            type: (credential as any).type,
            authenticatorAttachment: (credential as any).authenticatorAttachment,
            response: {
              clientDataJSON: toBase64Url(response.clientDataJSON),
              attestationObject: toBase64Url(response.attestationObject),
            },
          },
          nickname: "Device Passkey",
        },
      });
      Alert.alert("Sukces", "Passkey zapisany.");
      await loadSecurity();
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się dodać passkey");
    }
  };

  return (
    <ScrollView style={styles.tabContent} contentContainerStyle={{ paddingBottom: 160 }}>
      <Image source={{ uri: pixelHeroImage }} style={styles.pixelHero} />
      <Text style={styles.h2}>Profil i bezpieczeństwo</Text>
      <Text style={styles.subtitle}>Trust without identity • Privacy Shield • Panic Lock</Text>

      <Animated.View style={[styles.panel, animatedStyle]}>
        <Text style={styles.cardTitle}>{user?.alias}</Text>
        <Text style={styles.caption}>Poziom prywatności: {user?.privacy_level || 0}/100</Text>
        <Text style={styles.caption}>Poziom zaufania: {user?.public_trust_level || "starter"}</Text>
        <View style={styles.rowWrap}>
          <Tag value="No public wallet" />
          <Tag value="Alias only" />
          <Tag value="No exact address" />
        </View>
      </Animated.View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Privacy Shield</Text>
        <Text style={styles.caption}>Wynik: {security?.privacy_shield_score || 0}/100</Text>
        {(security?.recommendations || []).map((item: string) => (
          <View key={item} style={styles.aliasRow}>
            <Ionicons name="shield-checkmark-outline" size={16} color={theme.neonGreen} />
            <Text style={styles.caption}>{item}</Text>
          </View>
        ))}

        <View style={styles.actionsRow}>
          <PixelButton label={checking ? "Sprawdzanie..." : "Biometria"} icon="finger-print-outline" onPress={runBiometric} />
          <PixelButton label="Dodaj passkey" icon="key-outline" variant="secondary" onPress={registerPasskeyDemo} />
        </View>
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>2FA</Text>
        <TextInput
          testID="profile-2fa-password"
          style={styles.input}
          value={passwordFor2FA}
          onChangeText={setPasswordFor2FA}
          secureTextEntry
          placeholder="Hasło konta"
          placeholderTextColor={theme.textMuted}
        />
        <PixelButton testID="profile-2fa-generate" label="Wygeneruj sekret" icon="qr-code-outline" onPress={enable2FA} />
        {pendingSecret ? <Text style={styles.caption}>Sekret: {pendingSecret}</Text> : null}
        <TextInput
          testID="profile-2fa-otp"
          style={styles.input}
          value={otpCode}
          onChangeText={setOtpCode}
          placeholder="Kod z aplikacji TOTP"
          placeholderTextColor={theme.textMuted}
        />
        <PixelButton testID="profile-2fa-verify" label="Zweryfikuj 2FA" icon="checkmark-circle-outline" onPress={verify2FA} />
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Urządzenia i sesje</Text>
        {devices.map((device) => (
          <View key={device.id} style={styles.sessionRow}>
            <View>
              <Text style={styles.caption}>{device.device_name}</Text>
              <Text style={styles.mutedText}>{device.user_agent?.slice(0, 24) || "Unknown"}</Text>
            </View>
            <PixelButton
              label="Wyloguj"
              icon="log-out-outline"
              variant="secondary"
              onPress={async () => {
                await apiRequest(`/me/devices/${device.id}`, { method: "DELETE", auth: true });
                await loadSecurity();
              }}
            />
          </View>
        ))}
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Panic Lock</Text>
        <Text style={styles.caption}>Jednym kliknięciem blokujesz konto, wypłaty i nowe transakcje.</Text>
        <View style={styles.actionsRow}>
          <PixelButton
            testID="profile-panic-lock"
            label="Aktywuj Panic Lock"
            icon="lock-closed-outline"
            variant="danger"
            onPress={async () => {
              await apiRequest("/me/panic-lock", { method: "POST", auth: true });
              await logout();
            }}
          />
          <PixelButton testID="profile-logout" label="Wyloguj" icon="exit-outline" variant="secondary" onPress={logout} />
        </View>
      </View>
    </ScrollView>
  );
}

function AdminTab() {
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [listings, setListings] = useState<any[]>([]);
  const [disputes, setDisputes] = useState<any[]>([]);

  const loadAdmin = async () => {
    try {
      const [d, u, l, sp] = await Promise.all([
        apiRequest<AdminDashboard>("/admin/dashboard", { auth: true }),
        apiRequest<any[]>("/admin/users", { auth: true }),
        apiRequest<any[]>("/admin/listings", { auth: true }),
        apiRequest<any[]>("/admin/disputes", { auth: true }),
      ]);
      setDashboard(d);
      setUsers(u);
      setListings(l);
      setDisputes(sp);
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Brak dostępu admin");
    }
  };

  useEffect(() => {
    loadAdmin();
  }, []);

  return (
    <ScrollView style={styles.tabContent} contentContainerStyle={{ paddingBottom: 160 }}>
      <Text style={styles.h2}>Admin Control</Text>
      <Text style={styles.subtitle}>Moderacja • Spory • Opłaty • Audit</Text>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Dashboard</Text>
        <Text style={styles.caption}>Użytkownicy: {dashboard?.users || 0}</Text>
        <Text style={styles.caption}>Oferty: {dashboard?.listings || 0}</Text>
        <Text style={styles.caption}>Aktywne transakcje: {dashboard?.active_transactions || 0}</Text>
        <Text style={styles.caption}>Otwarte spory: {dashboard?.open_disputes || 0}</Text>
        <Text style={styles.caption}>Otwarte zgłoszenia: {dashboard?.open_reports || 0}</Text>
        <Text style={styles.caption}>Przychód prowizyjny: {dashboard?.commission_revenue_crypto || 0} USDC</Text>
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Moderacja ofert</Text>
        {listings.slice(0, 6).map((listing) => (
          <View key={listing.id} style={styles.sessionRow}>
            <View style={{ flex: 1, paddingRight: 8 }}>
              <Text style={styles.caption}>{listing.title}</Text>
              <Text style={styles.mutedText}>{listing.status} / {listing.moderation_status}</Text>
            </View>
            <PixelButton
              label="Approve"
              icon="checkmark-outline"
              variant="secondary"
              onPress={async () => {
                await apiRequest(`/admin/listings/${listing.id}/moderate`, {
                  method: "POST",
                  auth: true,
                  body: { action: "approve", reason: "Przegląd ręczny" },
                });
                await loadAdmin();
              }}
            />
          </View>
        ))}
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Spory</Text>
        {disputes.slice(0, 5).map((dispute) => (
          <View key={dispute.id} style={styles.sessionRow}>
            <View style={{ flex: 1, paddingRight: 8 }}>
              <Text style={styles.caption}>{dispute.reason}</Text>
              <Text style={styles.mutedText}>Status: {dispute.status}</Text>
            </View>
            {dispute.status === "OPEN" && (
              <PixelButton
                label="Refund"
                icon="return-down-back-outline"
                variant="danger"
                onPress={async () => {
                  await apiRequest(`/admin/disputes/${dispute.id}/resolve`, {
                    method: "POST",
                    auth: true,
                    body: { decision: "refund_buyer", reason: "Decyzja admina" },
                  });
                  await loadAdmin();
                }}
              />
            )}
          </View>
        ))}
      </View>

      <View style={styles.panel}>
        <Text style={styles.cardTitle}>Użytkownicy wysokiego ryzyka</Text>
        {users.filter((u) => (u.risk_score || 0) > 70).slice(0, 5).map((u) => (
          <View key={u.id} style={styles.sessionRow}>
            <View>
              <Text style={styles.caption}>{u.display_alias}</Text>
              <Text style={styles.mutedText}>Risk: {u.risk_score}</Text>
            </View>
            <PixelButton
              label="Ban"
              icon="ban-outline"
              variant="danger"
              onPress={async () => {
                await apiRequest(`/admin/users/${u.id}/ban`, { method: "POST", auth: true });
                await loadAdmin();
              }}
            />
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

export default function Index() {
  const [fontsLoaded] = useFonts({
    PressStart2P_400Regular,
    VT323_400Regular,
  });
  const { user, ready } = useAuth();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<TabKey>("market");

  const doBuy = async (listing: Listing) => {
    try {
      const tx = await apiRequest<Transaction>("/transactions", {
        method: "POST",
        auth: true,
        body: { listing_id: listing.id },
      });
      Alert.alert("Deal Room utworzony", `Alias kupującego: ${tx.buyer_alias}\nAlias sprzedającego: ${tx.seller_alias}`);
      setTab("deals");
    } catch (error: any) {
      Alert.alert("Błąd", error?.message || "Nie udało się utworzyć transakcji");
    }
  };

  const tabs = useMemo(
    () => [
      { key: "market" as const, label: "Market", icon: "planet-outline" as const },
      { key: "sell" as const, label: "Sprzedaj", icon: "add-circle-outline" as const },
      { key: "deals" as const, label: "Deal Room", icon: "chatbubbles-outline" as const },
      { key: "profile" as const, label: "Profil", icon: "shield-checkmark-outline" as const },
      ...(user?.role === "admin"
        ? [{ key: "admin" as const, label: "Admin", icon: "settings-outline" as const }]
        : []),
    ],
    [user?.role],
  );

  if (!ready || !fontsLoaded) {
    return (
      <View style={styles.loaderWrap}>
        <ActivityIndicator size="large" color={theme.neonBlue} />
      </View>
    );
  }

  if (!user) return <AuthScreen />;

  return (
    <LinearGradient colors={["#050509", "#180b2c", "#0b1022"]} style={styles.container}>
      <SafeAreaView style={styles.container}>
        <View style={[styles.mainBody, { paddingTop: 14 }]}> 
          {tab === "market" ? <MarketplaceTab onBuy={doBuy} /> : null}
          {tab === "sell" ? <SellTab /> : null}
          {tab === "deals" ? <DealsTab /> : null}
          {tab === "profile" ? <ProfileTab /> : null}
          {tab === "admin" ? <AdminTab /> : null}
        </View>

        <View style={[styles.navBar, { paddingBottom: Math.max(10, insets.bottom) }]}> 
          {tabs.map((entry) => (
            <Pressable
              testID={`nav-${entry.key}`}
              key={entry.key}
              onPress={() => setTab(entry.key)}
              style={styles.navBtn}
            >
              <Ionicons
                name={entry.icon}
                size={20}
                color={tab === entry.key ? theme.neonGreen : theme.textMuted}
              />
              <Text style={[styles.navLabel, tab === entry.key && { color: theme.neonGreen }]}>{entry.label}</Text>
            </Pressable>
          ))}
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: theme.bg,
  },
  loaderWrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.bg,
  },
  mainBody: {
    flex: 1,
    paddingHorizontal: 18,
  },
  authWrap: {
    flex: 1,
    paddingHorizontal: 18,
    justifyContent: "center",
    gap: 14,
  },
  logoWrap: {
    gap: 8,
    marginBottom: 8,
  },
  h1: {
    fontSize: 24,
    fontFamily: "PressStart2P_400Regular",
    color: theme.text,
    textTransform: "uppercase",
  },
  h2: {
    fontSize: 18,
    fontFamily: "PressStart2P_400Regular",
    color: theme.text,
    marginBottom: 6,
    textTransform: "uppercase",
  },
  subtitle: {
    color: theme.textMuted,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
    lineHeight: 24,
  },
  panel: {
    backgroundColor: theme.panel,
    borderRadius: 5,
    padding: 14,
    borderWidth: 1,
    borderColor: theme.border,
    gap: 10,
  },
  panelSoft: {
    backgroundColor: theme.panelSoft,
    borderRadius: 4,
    padding: 10,
    borderWidth: 1,
    borderColor: theme.borderGlow,
    gap: 8,
  },
  input: {
    minHeight: 46,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: theme.border,
    color: theme.text,
    paddingHorizontal: 12,
    backgroundColor: theme.inputBg,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
  },
  button: {
    minHeight: 44,
    borderRadius: 3,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
  },
  buttonText: {
    fontFamily: "VT323_400Regular",
    fontSize: 24,
    textTransform: "uppercase",
  },
  segmentRow: {
    flexDirection: "row",
    gap: 8,
    backgroundColor: theme.panel,
    borderRadius: 4,
    padding: 6,
    borderWidth: 1,
    borderColor: theme.border,
  },
  segmentBtn: {
    flex: 1,
    borderRadius: 2,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  segmentBtnActive: {
    backgroundColor: "#2d0f44",
  },
  segmentText: {
    color: theme.textMuted,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
  },
  segmentTextActive: {
    color: theme.neonBlue,
  },
  tabContent: {
    flex: 1,
    gap: 12,
  },
  rowBetween: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  rowWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  filterRow: {
    gap: 8,
    paddingVertical: 2,
  },
  filterChip: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 4,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12,
    backgroundColor: "#111827",
  },
  filterChipActive: {
    borderColor: theme.neonViolet,
    backgroundColor: "#2c1541",
  },
  filterText: {
    color: theme.text,
    fontFamily: "VT323_400Regular",
    fontSize: 22,
  },
  cardTitle: {
    color: theme.text,
    fontFamily: "PressStart2P_400Regular",
    fontSize: 13,
    textTransform: "uppercase",
  },
  cardBody: {
    color: theme.text,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
    lineHeight: 24,
  },
  tag: {
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 3,
    minHeight: 28,
    paddingHorizontal: 10,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#100f1b",
  },
  tagText: {
    color: theme.neonBlue,
    fontFamily: "VT323_400Regular",
    fontSize: 19,
  },
  aliasRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  caption: {
    color: theme.text,
    fontFamily: "VT323_400Regular",
    fontSize: 22,
  },
  mutedText: {
    color: theme.textMuted,
    fontFamily: "VT323_400Regular",
    fontSize: 21,
  },
  actionsRow: {
    flexDirection: "row",
    gap: 8,
    flexWrap: "wrap",
  },
  emptyText: {
    color: theme.textMuted,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
    textAlign: "center",
    marginTop: 30,
  },
  messageBubble: {
    backgroundColor: "#090d18",
    borderWidth: 1,
    borderColor: theme.border,
    borderRadius: 4,
    padding: 9,
    gap: 6,
  },
  messageText: {
    color: theme.text,
    fontFamily: "VT323_400Regular",
    fontSize: 24,
  },
  sessionRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
  },
  navBar: {
    borderTopWidth: 1,
    borderTopColor: theme.border,
    backgroundColor: theme.navBg,
    paddingTop: 8,
    paddingHorizontal: 8,
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 6,
  },
  navBtn: {
    flex: 1,
    minHeight: 52,
    justifyContent: "center",
    alignItems: "center",
    borderRadius: 4,
    gap: 2,
  },
  navLabel: {
    color: theme.textMuted,
    fontFamily: "VT323_400Regular",
    fontSize: 18,
  },
  pixelHero: {
    width: "100%",
    height: 84,
    borderRadius: 4,
    borderWidth: 1,
    borderColor: theme.borderGlow,
    marginBottom: 8,
  },
  pixelAvatar: {
    width: 24,
    height: 24,
    borderRadius: 2,
    borderWidth: 1,
    borderColor: theme.border,
  },
});
