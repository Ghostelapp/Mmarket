export type User = {
  id: string;
  alias: string;
  role: "user" | "admin";
  privacy_level: number;
  two_fa_enabled: boolean;
  public_trust_level: string;
  wallets?: {
    address: string;
    is_primary?: boolean;
  }[];
};

export type AuthSession = {
  access_token: string;
  refresh_token: string;
  session_id: string;
  token_type: string;
  user: User;
};

export type Listing = {
  id: string;
  title: string;
  description: string;
  price_fiat: number;
  fiat_currency: "PLN" | "EUR";
  crypto_amount: number;
  crypto_token: string;
  crypto_network: string;
  category: string;
  category_label?: string;
  category_icon?: string;
  category_color?: string;
  condition: string;
  location_public: string;
  status: string;
  moderation_status: string;
  images?: {
    type: string;
    image_id?: string;
    thumb_signed_url?: string | null;
    original_signed_url?: string | null;
  }[];
  promotion?: {
    is_promoted: boolean;
    package_type: "basic" | "boost" | null;
    package_label: string | null;
    amount_usdc: number;
    network: "Base" | "Polygon" | null;
    tx_hash: string | null;
    starts_at: string | null;
    ends_at: string | null;
  };
  is_promoted?: boolean;
  listing_fee: {
    amount: number;
    token: string;
    network: string;
    receiver_wallet: string;
    payment_reference?: string;
    payment_router_contract?: string;
    status: string;
  };
  seller_public?: {
    id: string;
    display_alias: string;
    public_trust_level: string;
    trust_score: number;
  };
  created_at: string;
};

export type Category = {
  id: string;
  name: string;
  slug: string;
  icon: string;
  color: string;
  sort_order: number;
  is_active: boolean;
};

export type Transaction = {
  id: string;
  buyer_id: string;
  seller_id: string;
  listing_id: string;
  status: string;
  escrow_status: string;
  shipping_status: string;
  dispute_status: string;
  gross_amount: number;
  fee_percent: number;
  fee_amount: number;
  seller_amount: number;
  token: string;
  network: string;
  buyer_alias: string;
  seller_alias: string;
  deal_room_id: string;
  escrow_receiver: string;
  escrow_reference: string;
  seller_wallet: string;
  created_at: string;
};

export type EncryptedMessage = {
  id: string;
  client_message_id?: string;
  sender_id: string;
  ciphertext: string;
  nonce: string;
  key_id?: string;
  encryption_version?: "nacl-secretbox-v1";
  message_type: "text" | "image" | "file";
  created_at: string;
  plaintext?: string;
};
