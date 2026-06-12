import logging
import asyncio
import contextlib
import os
import re
import uuid
import json
import base64
import hmac
import hashlib
import shutil
import subprocess
import tempfile
from decimal import Decimal
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import boto3
import pyotp
import requests
from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from PIL import Image, UnidentifiedImageError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, EmailStr, Field
from starlette.responses import FileResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement


ROOT_DIR = Path(__file__).parent
LOCAL_UPLOAD_DIR = ROOT_DIR / "uploads"
LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET must be set in backend environment")
JWT_ALG = "HS256"
ACCESS_TTL_MIN = 20
REFRESH_TTL_DAYS = 14
PLATFORM_NAME = "MASK Market"
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
CHAIN_ENV = os.environ.get("CHAIN_ENV", "testnet").strip().lower()
if CHAIN_ENV not in {"testnet", "mainnet"}:
    raise RuntimeError("CHAIN_ENV must be testnet or mainnet")

SUPPORTED_NETWORKS = [
    network.strip()
    for network in os.environ.get("SUPPORTED_NETWORKS", "Base,Polygon").split(",")
    if network.strip()
]
if not SUPPORTED_NETWORKS or any(network not in {"Base", "Polygon"} for network in SUPPORTED_NETWORKS):
    raise RuntimeError("SUPPORTED_NETWORKS must contain Base and/or Polygon")
DEFAULT_NETWORK = SUPPORTED_NETWORKS[0]
SUPPORTED_TOKENS = ["USDC"]


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


ENABLE_ONCHAIN_INDEXER = env_bool("ENABLE_ONCHAIN_INDEXER", False)
ENABLE_R2_STORAGE = env_bool("ENABLE_R2_STORAGE", False)
ENABLE_AV_SCAN = env_bool("ENABLE_AV_SCAN", False)
ALLOW_INSECURE_PAYMENT_SIMULATION = env_bool("ALLOW_INSECURE_PAYMENT_SIMULATION", False)
SEED_ADMIN = env_bool("SEED_ADMIN", False)
RATE_LIMIT_AUTH_PER_MINUTE = int(os.environ.get("RATE_LIMIT_AUTH_PER_MINUTE", "40"))
RATE_LIMIT_GENERAL_PER_MINUTE = int(os.environ.get("RATE_LIMIT_GENERAL_PER_MINUTE", "140"))
ONCHAIN_MIN_CONFIRMATIONS = int(os.environ.get("ONCHAIN_MIN_CONFIRMATIONS", "2" if CHAIN_ENV == "testnet" else "12"))
ONCHAIN_CONFIRMATIONS = {
    network: int(os.environ.get(f"ONCHAIN_MIN_CONFIRMATIONS_{network.upper()}", str(ONCHAIN_MIN_CONFIRMATIONS)))
    for network in SUPPORTED_NETWORKS
}
PLN_TO_USDC_RATE = float(os.environ.get("PLN_TO_USDC_RATE", "0.24"))
FX_RATE_UPDATED_AT_RAW = os.environ.get("FX_RATE_UPDATED_AT", "")
FX_RATE_MAX_AGE_MINUTES = int(os.environ.get("FX_RATE_MAX_AGE_MINUTES", "1440"))
FX_RATE_UPDATED_AT = (
    datetime.fromisoformat(FX_RATE_UPDATED_AT_RAW.replace("Z", "+00:00"))
    if FX_RATE_UPDATED_AT_RAW
    else None
)
RECONCILIATION_INTERVAL_SECONDS = int(os.environ.get("RECONCILIATION_INTERVAL_SECONDS", "60"))
RESERVATION_TTL_MINUTES = int(os.environ.get("RESERVATION_TTL_MINUTES", "15"))
ONCHAIN_STATE_BLOCK_TAG = os.environ.get("ONCHAIN_STATE_BLOCK_TAG", "finalized").strip().lower()
if ONCHAIN_STATE_BLOCK_TAG not in {"safe", "finalized"}:
    raise RuntimeError("ONCHAIN_STATE_BLOCK_TAG must be safe or finalized")
VERIFY_CONTRACTS_ON_STARTUP = env_bool("VERIFY_CONTRACTS_ON_STARTUP", APP_ENV == "production")
VERIFY_CONTRACTS_FAIL_CLOSED = env_bool("VERIFY_CONTRACTS_FAIL_CLOSED", False)
CONTRACT_VERIFY_TIMEOUT_SECONDS = int(os.environ.get("CONTRACT_VERIFY_TIMEOUT_SECONDS", "30"))
RPC_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("RPC_REQUEST_TIMEOUT_SECONDS", "15"))
ADMIN_PAGE_MAX_LIMIT = int(os.environ.get("ADMIN_PAGE_MAX_LIMIT", "500"))
API_PAGE_MAX_LIMIT = int(os.environ.get("API_PAGE_MAX_LIMIT", "200"))

if APP_ENV == "production" and ALLOW_INSECURE_PAYMENT_SIMULATION:
    raise RuntimeError("ALLOW_INSECURE_PAYMENT_SIMULATION cannot be enabled in production")

ALCHEMY_API_KEY_BASE = os.environ.get("ALCHEMY_API_KEY_BASE", "")
ALCHEMY_API_KEY_POLYGON = os.environ.get("ALCHEMY_API_KEY_POLYGON", "")
ALCHEMY_WEBHOOK_SIGNING_KEY = os.environ.get("ALCHEMY_WEBHOOK_SIGNING_KEY", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").lower().strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CONTRACT_ADMIN_ADDRESS = os.environ.get("CONTRACT_ADMIN_ADDRESS", "").lower()
ARBITER_ADDRESS = os.environ.get("ARBITER_ADDRESS", "").lower()
TWO_FA_ENCRYPTION_KEY = os.environ.get("TWO_FA_ENCRYPTION_KEY", JWT_SECRET)

CHAIN_PROFILES = {
    "testnet": {
        "Base": {
            "display_name": "Base Sepolia",
            "chain_id": 84532,
            "rpc_url": os.environ.get("RPC_URL_BASE_TESTNET", "https://sepolia.base.org"),
            "explorer_url": "https://sepolia.basescan.org",
            "native_symbol": "ETH",
            "usdc": os.environ.get("USDC_CONTRACT_BASE_TESTNET", "0x036cbd53842c5426634e7929541ec2318f3dcf7e").lower(),
        },
        "Polygon": {
            "display_name": "Polygon Amoy",
            "chain_id": 80002,
            "rpc_url": os.environ.get("RPC_URL_POLYGON_TESTNET", "https://polygon-amoy.drpc.org"),
            "explorer_url": "https://amoy.polygonscan.com",
            "native_symbol": "POL",
            "usdc": os.environ.get("USDC_CONTRACT_POLYGON_TESTNET", "").lower(),
        },
    },
    "mainnet": {
        "Base": {
            "display_name": "Base",
            "chain_id": 8453,
            "rpc_url": os.environ.get("RPC_URL_BASE_MAINNET", "https://mainnet.base.org"),
            "explorer_url": "https://basescan.org",
            "native_symbol": "ETH",
            "usdc": os.environ.get("USDC_CONTRACT_BASE_MAINNET", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913").lower(),
        },
        "Polygon": {
            "display_name": "Polygon",
            "chain_id": 137,
            "rpc_url": os.environ.get("RPC_URL_POLYGON_MAINNET", "https://polygon-rpc.com"),
            "explorer_url": "https://polygonscan.com",
            "native_symbol": "POL",
            "usdc": os.environ.get("USDC_CONTRACT_POLYGON_MAINNET", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359").lower(),
        },
    },
}
ACTIVE_CHAIN_PROFILE = CHAIN_PROFILES[CHAIN_ENV]
USDC_CONTRACTS = {name: config["usdc"] for name, config in ACTIVE_CHAIN_PROFILE.items()}
ALCHEMY_RPC_URLS = {name: config["rpc_url"] for name, config in ACTIVE_CHAIN_PROFILE.items()}

ESCROW_CONTRACTS = {
    "Base": os.environ.get(f"ESCROW_CONTRACT_BASE_{CHAIN_ENV.upper()}", os.environ.get("ESCROW_CONTRACT_BASE", "")).lower(),
    "Polygon": os.environ.get(f"ESCROW_CONTRACT_POLYGON_{CHAIN_ENV.upper()}", os.environ.get("ESCROW_CONTRACT_POLYGON", "")).lower(),
}
PAYMENT_ROUTER_CONTRACTS = {
    "Base": os.environ.get(f"PAYMENT_ROUTER_BASE_{CHAIN_ENV.upper()}", "").lower(),
    "Polygon": os.environ.get(f"PAYMENT_ROUTER_POLYGON_{CHAIN_ENV.upper()}", "").lower(),
}
EXPECTED_CONTRACT_CODE_HASHES = {
    network: {
        "escrow": os.environ.get(
            f"ESCROW_CODE_HASH_{network.upper()}_{CHAIN_ENV.upper()}", ""
        ).lower(),
        "payment router": os.environ.get(
            f"PAYMENT_ROUTER_CODE_HASH_{network.upper()}_{CHAIN_ENV.upper()}", ""
        ).lower(),
    }
    for network in {"Base", "Polygon"}
}
PLATFORM_WALLETS = {
    "Base": os.environ.get("PLATFORM_WALLET_BASE", ""),
    "Polygon": os.environ.get("PLATFORM_WALLET_POLYGON", ""),
}
if ALLOW_INSECURE_PAYMENT_SIMULATION and APP_ENV != "production":
    PLATFORM_WALLETS[DEFAULT_NETWORK] = PLATFORM_WALLETS[DEFAULT_NETWORK] or ("0x" + "1" * 40)

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8081,http://localhost:19006",
    ).split(",")
    if origin.strip()
]

WEBAUTHN_RP_ID = os.environ.get("WEBAUTHN_RP_ID", "localhost")
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "MASK Market")
WEBAUTHN_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("WEBAUTHN_ALLOWED_ORIGINS", "http://localhost:8081").split(",")
    if origin.strip()
]

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "")
CLAMAV_COMMAND = os.environ.get("CLAMAV_COMMAND", "clamscan")

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
PLATFORM_PAYMENT_TOPIC = "0x239496241b5b892fb4e2da1b26c71fe8207a3cbdb31b2721b560df940ba7fe3d"
ORDER_FUNDED_TOPIC = "0xadb9a93ea8bceee8e3737993899df5f543f23f69aec43671a18c57ded462250d"
ORDER_SHIPPED_TOPIC = "0xdeb574de1e78e0bf57d427ed95276ae59795192841eb9424711567f1ebc978b6"
ORDER_RELEASED_TOPIC = "0x62bb74181fb43ab2baf2f646842bc30018b13e41be62b8277e7b84b997ada77b"
DISPUTE_OPENED_TOPIC = "0xe7b614d99462ab012c8191c9348164cd62a4aec6d211f42371fd1f0759e5c220"
ORDER_REFUNDED_TOPIC = "0xbff5487f6422ba4acbcde6bd5e0ccb83124c240b9deb6a72e7b5eb8c7b71d6fc"

ESCROW_STATUS_NAMES = {
    0: "NONE",
    1: "CREATED",
    2: "FUNDED",
    3: "SHIPPED",
    4: "DISPUTED",
    5: "COMPLETED",
    6: "REFUNDED",
    7: "CANCELLED",
}

PROMOTION_PACKAGES = {
    "basic": {"amount_usdc": 1.0, "duration_hours": 24, "label": "Basic Boost"},
    "boost": {"amount_usdc": 3.0, "duration_hours": 72, "label": "Boost Premium"},
}

UPLOAD_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
UPLOAD_MAX_PIXELS = int(os.environ.get("UPLOAD_MAX_PIXELS", "24000000"))
UPLOAD_MAX_DIMENSION = int(os.environ.get("UPLOAD_MAX_DIMENSION", "8000"))
TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
ETH_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
BYTECODE_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")

if APP_ENV == "production":
    if len(JWT_SECRET) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters in production")
    if len(TWO_FA_ENCRYPTION_KEY) < 32:
        raise RuntimeError("TWO_FA_ENCRYPTION_KEY must contain at least 32 characters in production")
    if "CORS_ALLOWED_ORIGINS" not in os.environ or "WEBAUTHN_ALLOWED_ORIGINS" not in os.environ:
        raise RuntimeError("Production CORS and WebAuthn origins must be configured explicitly")
    if "*" in CORS_ALLOWED_ORIGINS:
        raise RuntimeError("Wildcard CORS origin is forbidden in production")
    if any(not origin.startswith("https://") for origin in CORS_ALLOWED_ORIGINS):
        raise RuntimeError("Production CORS origins must use HTTPS")
    if any(not origin.startswith("https://") for origin in WEBAUTHN_ALLOWED_ORIGINS):
        raise RuntimeError("Production WebAuthn origins must use HTTPS")
    if not ETH_ADDRESS_RE.fullmatch(CONTRACT_ADMIN_ADDRESS) or not ETH_ADDRESS_RE.fullmatch(ARBITER_ADDRESS):
        raise RuntimeError("CONTRACT_ADMIN_ADDRESS and ARBITER_ADDRESS must be configured in production")
    if CONTRACT_ADMIN_ADDRESS == ARBITER_ADDRESS:
        raise RuntimeError("Production contract admin and arbiter must use separate addresses")
    if (
        not FX_RATE_UPDATED_AT
        or FX_RATE_UPDATED_AT.tzinfo is None
        or datetime.now(timezone.utc) - FX_RATE_UPDATED_AT.astimezone(timezone.utc)
        > timedelta(minutes=FX_RATE_MAX_AGE_MINUTES)
    ):
        raise RuntimeError("FX_RATE_UPDATED_AT is missing or stale")
    configured_addresses = {
        **{f"PLATFORM_WALLET_{network.upper()}": PLATFORM_WALLETS.get(network, "") for network in SUPPORTED_NETWORKS},
        **{f"ESCROW_CONTRACT_{network.upper()}": ESCROW_CONTRACTS.get(network, "") for network in SUPPORTED_NETWORKS},
        **{f"PAYMENT_ROUTER_{network.upper()}": PAYMENT_ROUTER_CONTRACTS.get(network, "") for network in SUPPORTED_NETWORKS},
    }
    for name, address in configured_addresses.items():
        if not address or not ETH_ADDRESS_RE.fullmatch(address):
            raise RuntimeError(f"{name} must be configured with a valid Ethereum address")
    for network in SUPPORTED_NETWORKS:
        for label, code_hash in EXPECTED_CONTRACT_CODE_HASHES[network].items():
            if code_hash and not BYTECODE_HASH_RE.fullmatch(code_hash):
                raise RuntimeError(f"{network} {label} code hash is invalid")

r2_client = None
if ENABLE_R2_STORAGE and all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
    r2_client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

DEFAULT_CATEGORY_LABELS = {
    "elektronika": "elektronika",
    "moda": "moda",
    "dom": "dom",
    "motoryzacja": "motoryzacja",
    "sport": "sport",
    "dziecko": "dziecko",
    "kolekcje": "kolekcje",
    "usugi-lokalne": "usługi lokalne",
    "produkty-cyfrowe-legalne": "produkty cyfrowe legalne",
    "inne": "inne",
}

DEFAULT_LISTING_FEES = {
    "elektronika": 1.0,
    "moda": 0.25,
    "dom": 0.35,
    "motoryzacja": 5.0,
    "sport": 0.5,
    "dziecko": 0.2,
    "kolekcje": 0.8,
    "usugi-lokalne": 0.3,
    "produkty-cyfrowe-legalne": 0.4,
    "inne": 0.25,
}

ALIAS_WORDS = [
    "Raven",
    "Nova",
    "Viper",
    "Pixel",
    "Cipher",
    "Ghost",
    "Drift",
    "Pulse",
    "Zen",
    "Astra",
]

SANCTIONED_WALLET_SUFFIXES = {"0000", "dead", "bad0"}

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

app = FastAPI(title="MASK Market API", version="1.0.0")
api_router = APIRouter(prefix="/api")
logger = logging.getLogger("mmarket")
reconciliation_task: Optional[asyncio.Task] = None
RECONCILIATION_WORKER_ID = str(uuid.uuid4())
contract_verification_status: Dict[str, Any] = {
    "status": "pending" if VERIFY_CONTRACTS_ON_STARTUP else "disabled",
    "checked_at": None,
    "networks": {},
}


class RegisterInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    alias: str = Field(min_length=3, max_length=30)
    public_location: str = Field(default="Unknown")


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    otp_code: Optional[str] = None
    device_name: str = "Mobile"


class RefreshInput(BaseModel):
    refresh_token: str
    session_id: str


class PanicUnlockInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    otp_code: Optional[str] = None


class LogoutInput(BaseModel):
    all_devices: bool = False
    session_id: Optional[str] = None


class TwoFAEnableInput(BaseModel):
    account_password: str


class TwoFAVerifyInput(BaseModel):
    code: str


class PasskeyRegisterInput(BaseModel):
    credential: Dict[str, Any]
    nickname: str = "main"


class PasskeyLoginInput(BaseModel):
    email: EmailStr
    credential: Dict[str, Any]
    device_name: str = "Passkey device"


class PasskeyRegisterOptionsInput(BaseModel):
    nickname: str = "main"


class PasskeyLoginOptionsInput(BaseModel):
    email: EmailStr


class WalletChallengeInput(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)
    chain_id: Optional[int] = None


class WalletLoginInput(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)
    challenge_id: str = Field(min_length=36, max_length=36)
    signature: str = Field(
        min_length=130,
        max_length=132,
        pattern=r"^(0x)?[a-fA-F0-9]{130}$",
    )
    device_name: str = Field(default="wallet", min_length=1, max_length=100)


class WalletLinkInput(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)
    challenge_id: str = Field(min_length=36, max_length=36)
    signature: str = Field(
        min_length=130,
        max_length=132,
        pattern=r"^(0x)?[a-fA-F0-9]{130}$",
    )


class WalletAddressInput(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)


class MeUpdateInput(BaseModel):
    alias: Optional[str] = Field(default=None, min_length=3, max_length=30)
    public_location: Optional[str] = None
    auto_delete_messages_days: Optional[int] = Field(default=None, ge=1, le=90)


class ListingCreateInput(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=15, max_length=2000)
    price_fiat: float = Field(gt=0)
    fiat_currency: Literal["PLN", "EUR"]
    category: str
    condition: str = Field(min_length=2, max_length=100)
    location_public: str = Field(min_length=2, max_length=120)
    shipping_options: List[str] = Field(min_length=1, max_length=5)
    images: List[str] = Field(default_factory=list, max_length=10)


class ListingUpdateInput(BaseModel):
    title: Optional[str] = Field(default=None, min_length=4, max_length=120)
    description: Optional[str] = Field(default=None, min_length=15, max_length=2000)
    price_fiat: Optional[float] = Field(default=None, gt=0)
    category: Optional[str] = None
    condition: Optional[str] = Field(default=None, max_length=100)
    location_public: Optional[str] = Field(default=None, max_length=120)
    shipping_options: Optional[List[str]] = None


class PayListingFeeInput(BaseModel):
    amount: float = Field(gt=0)
    token: str
    network: str
    payment_tx_hash: str = Field(min_length=10, max_length=120)
    intent_id: Optional[str] = None


class ReportInput(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    details: Optional[str] = None


class TransactionCreateInput(BaseModel):
    listing_id: str


class FundInput(BaseModel):
    amount: float = Field(gt=0)
    token: str
    network: str
    tx_hash: str = Field(min_length=10, max_length=120)


class MarkShippedInput(BaseModel):
    label_token: Optional[str] = None
    encrypted_address_blob: str
    carrier: str = "InPost"
    onchain_tx_hash: Optional[str] = None


class OpenDisputeInput(BaseModel):
    reason: str = Field(min_length=5, max_length=300)
    onchain_tx_hash: Optional[str] = None


class OnchainActionInput(BaseModel):
    onchain_tx_hash: Optional[str] = None


class E2EEPublicKeyInput(BaseModel):
    public_key: str = Field(min_length=44, max_length=44)


class E2EEKeyEnvelopeInput(BaseModel):
    recipient_id: str
    sender_public_key: str = Field(min_length=44, max_length=44)
    ciphertext: str = Field(min_length=64, max_length=128)
    nonce: str = Field(min_length=32, max_length=64)


class E2EERoomInitializeInput(BaseModel):
    key_id: str = Field(min_length=44, max_length=44)
    envelopes: List[E2EEKeyEnvelopeInput] = Field(min_length=2, max_length=2)


class MessageCreateInput(BaseModel):
    client_message_id: str = Field(min_length=36, max_length=36)
    ciphertext: str = Field(min_length=24, max_length=100_000)
    nonce: str = Field(min_length=32, max_length=64)
    key_id: str = Field(min_length=44, max_length=44)
    encryption_version: Literal["nacl-secretbox-v1"]
    message_type: Literal["text", "image", "file"] = "text"
    expires_in_days: int = Field(default=14, ge=1, le=90)


class MessageEvidenceInput(BaseModel):
    selected_message_ids: List[str] = Field(min_length=1, max_length=50)
    dispute_reason: str = Field(min_length=5, max_length=300)


class PaymentIntentInput(BaseModel):
    amount: float = Field(gt=0)
    token: str
    network: str
    purpose: Literal["listing_fee", "escrow_funding", "premium", "promotion"]
    target_id: str


class PaymentTxSubmitInput(BaseModel):
    tx_hash: str = Field(min_length=10, max_length=120)


class PromoteIntentInput(BaseModel):
    package_type: Literal["basic", "boost"]
    network: Literal["Base", "Polygon"]
    token: Literal["USDC"] = "USDC"


class PromoteConfirmInput(BaseModel):
    intent_id: str
    tx_hash: str = Field(min_length=10, max_length=120)


class ListingFeeRulePatchInput(BaseModel):
    category: str
    account_type: str = "default"
    amount: float = Field(gt=0)
    token: str = "USDC"
    network: str = "Base"


class SalesFeeRulePatchInput(BaseModel):
    category: str = "default"
    account_type: str = "default"
    reputation_level: str = "any"
    fee_percent: float = Field(gt=0, le=25)


class PlatformWalletInput(BaseModel):
    network: str
    token: str
    wallet_address: str = Field(min_length=10, max_length=120)


class ResolveDisputeInput(BaseModel):
    decision: Literal["refund_buyer", "release_seller"]
    reason: str = Field(min_length=5, max_length=1000)
    onchain_tx_hash: Optional[str] = None


class ModerateListingInput(BaseModel):
    action: Literal["approve", "reject", "hide"]
    reason: str = Field(min_length=3, max_length=1000)


class CategoryCreateInput(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    slug: str = Field(min_length=2, max_length=64)
    icon: str = Field(min_length=1, max_length=64)
    color: str = Field(min_length=4, max_length=20)
    sort_order: int = Field(default=100, ge=0, le=10000)


class CategoryUpdateInput(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    icon: Optional[str] = Field(default=None, min_length=1, max_length=64)
    color: Optional[str] = Field(default=None, min_length=4, max_length=20)
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)
    is_active: Optional[bool] = None


class CategoryReorderItem(BaseModel):
    id: str
    sort_order: int = Field(ge=0, le=10000)


class CategoryReorderInput(BaseModel):
    items: List[CategoryReorderItem]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def random_alias() -> str:
    return f"{ALIAS_WORDS[uuid.uuid4().int % len(ALIAS_WORDS)]}-{str(uuid.uuid4().int)[-4:]}"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def _secret_cipher() -> Fernet:
    key = hashlib.sha256(TWO_FA_ENCRYPTION_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(secret: str) -> str:
    return "enc:" + _secret_cipher().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(secret: Optional[str]) -> Optional[str]:
    if not secret or not secret.startswith("enc:"):
        return secret
    try:
        return _secret_cipher().decrypt(secret[4:].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=500, detail="Nie można odczytać sekretu 2FA") from exc


def decode_base64_field(value: str, expected_bytes: Optional[int], field_name: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Niepoprawne base64: {field_name}") from exc
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise HTTPException(status_code=400, detail=f"Niepoprawna długość: {field_name}")
    return decoded


def e2ee_fingerprint(public_key: str) -> str:
    raw = decode_base64_field(public_key, 32, "public_key")
    return hashlib.sha256(raw).hexdigest()


def create_access_token(user_id: str, role: str, session_id: str) -> str:
    payload = {
        "sub": user_id,
        "sid": session_id,
        "role": role,
        "type": "access",
        "exp": now_utc() + timedelta(minutes=ACCESS_TTL_MIN),
        "iat": now_utc(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def create_refresh_token(user_id: str, session_id: str, refresh_jti: str) -> str:
    payload = {
        "sub": user_id,
        "sid": session_id,
        "jti": refresh_jti,
        "type": "refresh",
        "exp": now_utc() + timedelta(days=REFRESH_TTL_DAYS),
        "iat": now_utc(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def ensure_account_active(user: dict):
    if user.get("status") != "ACTIVE" or user.get("panic_lock_enabled"):
        raise HTTPException(status_code=403, detail="Konto zablokowane")


def payments_may_be_simulated() -> bool:
    return APP_ENV != "production" and ALLOW_INSECURE_PAYMENT_SIMULATION


def validate_tx_hash(tx_hash: str):
    if payments_may_be_simulated():
        return
    if not TX_HASH_RE.fullmatch(tx_hash):
        raise HTTPException(status_code=400, detail="Niepoprawny hash transakcji")


async def claim_tx_hash(
    tx_hash: str,
    network: str,
    purpose: str,
    target_id: str,
    user_id: str,
) -> bool:
    validate_tx_hash(tx_hash)
    try:
        await db.used_transaction_hashes.insert_one(
            {
                "tx_hash": tx_hash.lower(),
                "network": network,
                "purpose": purpose,
                "target_id": target_id,
                "user_id": user_id,
                "created_at": now_utc(),
            }
        )
        return True
    except DuplicateKeyError as exc:
        existing = await db.used_transaction_hashes.find_one(
            {"tx_hash": tx_hash.lower(), "network": network},
            {"_id": 0},
        )
        if existing and all(
            existing.get(key) == value
            for key, value in {
                "purpose": purpose,
                "target_id": target_id,
                "user_id": user_id,
            }.items()
        ):
            return False
        raise HTTPException(status_code=409, detail="Transakcja została już wykorzystana") from exc


async def release_tx_hash_claim(tx_hash: str, network: str):
    await db.used_transaction_hashes.delete_one(
        {"tx_hash": tx_hash.lower(), "network": network}
    )


async def run_blocking(func, *args):
    return await asyncio.to_thread(func, *args)


def verify_payment_transfer(
    network: str,
    tx_hash: str,
    expected_to: str,
    expected_amount: float,
) -> Tuple[str, dict]:
    validate_tx_hash(tx_hash)
    if is_onchain_indexer_ready(network):
        verification_ok, _, verification_meta = verify_usdc_transfer_onchain(
            network,
            tx_hash,
            expected_to,
            expected_amount,
        )
        if not verification_ok:
            raise HTTPException(status_code=400, detail="Transakcja niepotwierdzona on-chain")
        return "ONCHAIN_ALCHEMY", verification_meta

    if payments_may_be_simulated():
        return "TEST_SIMULATION", {"warning": "Payment simulated outside production"}

    raise HTTPException(
        status_code=503,
        detail="Weryfikacja płatności on-chain nie jest skonfigurowana",
    )


def verify_payment_transfer_to_any(
    network: str,
    tx_hash: str,
    expected_receivers: List[str],
    expected_amount: float,
) -> Tuple[str, dict]:
    receivers = list(dict.fromkeys(address for address in expected_receivers if address))
    if not receivers:
        raise HTTPException(status_code=503, detail="Brak przypisanego portfela opłaty")
    last_unconfirmed: Optional[HTTPException] = None
    for receiver in receivers:
        try:
            return verify_payment_transfer(network, tx_hash, receiver, expected_amount)
        except HTTPException as exc:
            if exc.status_code != 400:
                raise
            last_unconfirmed = exc
    raise last_unconfirmed or HTTPException(status_code=400, detail="Transakcja niepotwierdzona on-chain")


def tx_status_summary(status: str) -> Dict[str, str]:
    if status == "COMPLETED":
        return {"badge": "completed", "label": "Zakończona"}
    if status == "DISPUTED":
        return {"badge": "warning", "label": "W sporze"}
    if status in {"FUNDED", "AWAITING_SHIPMENT", "SHIPPED"}:
        return {"badge": "progress", "label": "W toku"}
    return {"badge": "neutral", "label": status}


def clean_mongo_doc(doc: Any) -> Any:
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, list):
        return [clean_mongo_doc(item) for item in doc]
    if isinstance(doc, dict):
        return {
            key: clean_mongo_doc(value)
            for key, value in doc.items()
            if key != "_id"
        }
    return doc


def compute_wallet_risk(wallet_address: str) -> int:
    lowered = wallet_address.lower()
    for suffix in SANCTIONED_WALLET_SUFFIXES:
        if lowered.endswith(suffix):
            return 95
    return 15


def primary_wallet(user: dict) -> str:
    wallets = user.get("wallets") or []
    selected = next((wallet for wallet in wallets if wallet.get("is_primary")), None)
    return (selected or (wallets[0] if wallets else {})).get("address", "")


def is_onchain_indexer_ready(network: Optional[str] = None) -> bool:
    if not ENABLE_ONCHAIN_INDEXER:
        return False
    if network is not None:
        return bool(ALCHEMY_RPC_URLS.get(network) and USDC_CONTRACTS.get(network))
    return any(ALCHEMY_RPC_URLS.get(name) and USDC_CONTRACTS.get(name) for name in SUPPORTED_NETWORKS)


def to_topic_address(address: str) -> str:
    normalized = address.lower().replace("0x", "")
    return "0x" + ("0" * 24) + normalized


def parse_hex_amount(hex_value: str) -> float:
    if not hex_value:
        return 0.0
    raw = int(hex_value, 16)
    return float(Decimal(raw) / Decimal(10**6))


def call_alchemy_rpc(network: str, method: str, params: list) -> dict:
    url = ALCHEMY_RPC_URLS.get(network)
    if not url:
        raise HTTPException(status_code=503, detail="Indexer on-chain nie jest skonfigurowany")

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        response = requests.post(url, json=payload, timeout=RPC_REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("RPC request failed for %s/%s: %s", network, method, exc)
        raise HTTPException(status_code=502, detail="Błąd połączenia z dostawcą RPC") from exc

    if response.status_code >= 400:
        logger.warning(
            "RPC provider returned HTTP %s for %s/%s",
            response.status_code,
            network,
            method,
        )
        raise HTTPException(status_code=502, detail="Dostawca RPC odrzucił żądanie")

    try:
        data = response.json()
    except requests.JSONDecodeError as exc:
        logger.warning("RPC provider returned invalid JSON for %s/%s", network, method)
        raise HTTPException(status_code=502, detail="Niepoprawna odpowiedź dostawcy RPC") from exc
    if data.get("error"):
        logger.warning("RPC provider error for %s/%s: %s", network, method, data["error"])
        raise HTTPException(status_code=502, detail="Dostawca RPC zwrócił błąd")
    return data


def verify_network_deployment(network: str):
    expected_chain_id = ACTIVE_CHAIN_PROFILE[network]["chain_id"]
    actual_chain_id = int(call_alchemy_rpc(network, "eth_chainId", []).get("result", "0x0"), 16)
    if actual_chain_id != expected_chain_id:
        raise RuntimeError(f"{network}: RPC chainId {actual_chain_id} != {expected_chain_id}")
    for label, address in {
        "escrow": ESCROW_CONTRACTS.get(network, ""),
        "payment router": PAYMENT_ROUTER_CONTRACTS.get(network, ""),
        "USDC": USDC_CONTRACTS.get(network, ""),
    }.items():
        if not ETH_ADDRESS_RE.fullmatch(address):
            raise RuntimeError(f"{network}: {label} address is missing or invalid")
        code = call_alchemy_rpc(network, "eth_getCode", [address, "latest"]).get("result", "0x")
        if not code or code == "0x":
            raise RuntimeError(f"{network}: {label} address has no deployed bytecode")
        expected_code_hash = EXPECTED_CONTRACT_CODE_HASHES[network].get(label)
        if expected_code_hash:
            actual_code_hash = "0x" + keccak(bytes.fromhex(code[2:])).hex()
            if actual_code_hash.lower() != expected_code_hash:
                raise RuntimeError(f"{network}: {label} bytecode hash does not match deployment")
    expected_fee_wallet = PLATFORM_WALLETS[network].lower()
    for label, contract_address in {
        "escrow": ESCROW_CONTRACTS[network],
        "payment router": PAYMENT_ROUTER_CONTRACTS[network],
    }.items():
        actual_fee_wallet = read_contract_address(network, contract_address, "feeWallet()")
        if actual_fee_wallet != expected_fee_wallet:
            raise RuntimeError(f"{network}: {label} feeWallet does not match PLATFORM_WALLET")
    selector = keccak(text="allowedTokens(address)")[:4].hex()
    token_arg = USDC_CONTRACTS[network][2:].rjust(64, "0")
    for label, contract_address in {
        "escrow": ESCROW_CONTRACTS[network],
        "payment router": PAYMENT_ROUTER_CONTRACTS[network],
    }.items():
        allowed = call_alchemy_rpc(
            network,
            "eth_call",
            [{"to": contract_address, "data": f"0x{selector}{token_arg}"}, "latest"],
        ).get("result", "0x0")
        if int(allowed, 16) != 1:
            raise RuntimeError(f"{network}: USDC is not allowed by {label}")
    for role, account, label in [
        ("0x" + "0" * 64, CONTRACT_ADMIN_ADDRESS, "contract admin"),
        ("0x" + keccak(text="ARBITER_ROLE").hex(), ARBITER_ADDRESS, "arbiter"),
    ]:
        selector = keccak(text="hasRole(bytes32,address)")[:4].hex()
        account_arg = account[2:].rjust(64, "0")
        has_role = call_alchemy_rpc(
            network,
            "eth_call",
            [{"to": ESCROW_CONTRACTS[network], "data": f"0x{selector}{role[2:]}{account_arg}"}, "latest"],
        ).get("result", "0x0")
        if int(has_role, 16) != 1:
            raise RuntimeError(f"{network}: configured {label} does not have the required escrow role")
    admin_role = "0x" + "0" * 64
    selector = keccak(text="hasRole(bytes32,address)")[:4].hex()
    admin_arg = CONTRACT_ADMIN_ADDRESS[2:].rjust(64, "0")
    router_has_admin = call_alchemy_rpc(
        network,
        "eth_call",
        [
            {
                "to": PAYMENT_ROUTER_CONTRACTS[network],
                "data": f"0x{selector}{admin_role[2:]}{admin_arg}",
            },
            "latest",
        ],
    ).get("result", "0x0")
    if int(router_has_admin, 16) != 1:
        raise RuntimeError(f"{network}: configured contract admin does not have the router admin role")


async def verify_contract_deployments_on_startup():
    if not VERIFY_CONTRACTS_ON_STARTUP:
        return

    contract_verification_status["status"] = "checking"
    contract_verification_status["networks"] = {}
    failed_networks: List[str] = []
    for network in SUPPORTED_NETWORKS:
        try:
            await asyncio.wait_for(
                run_blocking(verify_network_deployment, network),
                timeout=CONTRACT_VERIFY_TIMEOUT_SECONDS,
            )
            contract_verification_status["networks"][network] = "verified"
        except Exception as exc:
            failed_networks.append(network)
            contract_verification_status["networks"][network] = "failed"
            logger.exception("Contract deployment verification failed for %s", network)
            if VERIFY_CONTRACTS_FAIL_CLOSED:
                raise RuntimeError(f"Contract deployment verification failed for {network}") from exc

    contract_verification_status["checked_at"] = now_utc()
    contract_verification_status["status"] = "degraded" if failed_networks else "healthy"


def require_receipt_finality(network: str, receipt: dict) -> int:
    block_hex = receipt.get("blockNumber")
    if not block_hex:
        raise HTTPException(status_code=409, detail="Transakcja nie została jeszcze umieszczona w bloku")
    latest = call_alchemy_rpc(network, "eth_blockNumber", []).get("result")
    if not latest:
        raise HTTPException(status_code=502, detail="Nie można ustalić finalności transakcji")
    confirmations = int(latest, 16) - int(block_hex, 16) + 1
    required_confirmations = ONCHAIN_CONFIRMATIONS.get(network, ONCHAIN_MIN_CONFIRMATIONS)
    if confirmations < required_confirmations:
        raise HTTPException(
            status_code=409,
            detail=f"Transakcja wymaga {required_confirmations} potwierdzeń; obecnie: {confirmations}",
        )
    return confirmations


def read_escrow_order(network: str, contract_address: str, order_ref: str) -> dict:
    if not ETH_ADDRESS_RE.fullmatch(contract_address) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", order_ref):
        raise HTTPException(status_code=503, detail="Niepoprawna konfiguracja zlecenia escrow")
    selector = keccak(text="orders(bytes32)")[:4].hex()
    result = call_alchemy_rpc(
        network,
        "eth_call",
        [{"to": contract_address, "data": f"0x{selector}{order_ref[2:]}"}, ONCHAIN_STATE_BLOCK_TAG],
    ).get("result", "0x")
    raw = result[2:]
    if len(raw) < 64 * 8:
        raise HTTPException(status_code=502, detail="Kontrakt escrow zwrócił niepoprawne dane")
    words = [raw[index:index + 64] for index in range(0, 64 * 8, 64)]
    status_number = int(words[7], 16)
    return {
        "order_ref": "0x" + words[0],
        "buyer": "0x" + words[1][-40:],
        "seller": "0x" + words[2][-40:],
        "token": "0x" + words[3][-40:],
        "amount_raw": int(words[4], 16),
        "fee_bps": int(words[5], 16),
        "fee_wallet": "0x" + words[6][-40:],
        "status_number": status_number,
        "status": ESCROW_STATUS_NAMES.get(status_number, "UNKNOWN"),
    }


def read_contract_address(network: str, contract_address: str, signature: str) -> str:
    if not ETH_ADDRESS_RE.fullmatch(contract_address):
        raise HTTPException(status_code=503, detail="Kontrakt nie jest skonfigurowany")
    selector = keccak(text=signature)[:4].hex()
    result = call_alchemy_rpc(
        network,
        "eth_call",
        [{"to": contract_address, "data": f"0x{selector}"}, "latest"],
    ).get("result", "0x")
    if len(result) < 66:
        raise HTTPException(status_code=502, detail="Kontrakt zwrócił niepoprawny adres")
    return "0x" + result[-40:].lower()


def validate_escrow_order_terms(tx: dict, order: dict, buyer_wallets: Optional[List[str]] = None):
    expected_amount_raw = int((Decimal(str(tx["gross_amount"])) * Decimal(10**6)).to_integral_value())
    expected_fee_bps = round(float(tx["fee_percent"]) * 100)
    if order["order_ref"].lower() != tx["escrow_reference"].lower():
        raise HTTPException(status_code=400, detail="Referencja zlecenia escrow jest niezgodna")
    if order["seller"].lower() != tx["seller_wallet"].lower():
        raise HTTPException(status_code=400, detail="Sprzedający zapisany w escrow jest niezgodny")
    if order["token"].lower() != USDC_CONTRACTS.get(tx["network"], "").lower():
        raise HTTPException(status_code=400, detail="Token zapisany w escrow jest niezgodny")
    if order["amount_raw"] != expected_amount_raw:
        raise HTTPException(status_code=400, detail="Kwota zapisana w escrow jest niezgodna")
    if order["fee_bps"] != expected_fee_bps:
        raise HTTPException(status_code=400, detail="Prowizja zapisana w escrow jest niezgodna")
    if tx.get("fee_wallet") and order["fee_wallet"].lower() != tx["fee_wallet"].lower():
        raise HTTPException(status_code=400, detail="Portfel prowizji zapisany w escrow jest niezgodny")
    if buyer_wallets is not None and order["buyer"].lower() not in {wallet.lower() for wallet in buyer_wallets}:
        raise HTTPException(status_code=400, detail="Kupujący zapisany w escrow nie jest przypiętym portfelem konta")


async def ensure_sale_fee_record(tx: dict, release_tx_hash: Optional[str] = None):
    if tx.get("status") != "COMPLETED":
        return
    effective_release_hash = release_tx_hash or tx.get("release_tx_hash")
    await db.sale_fees.update_one(
        {"transaction_id": tx["id"]},
        {
            "$set": {
                "transaction_id": tx["id"],
                "seller_id": tx["seller_id"],
                "buyer_id": tx["buyer_id"],
                "gross_amount": tx["gross_amount"],
                "fee_percent": tx["fee_percent"],
                "fee_amount": tx["fee_amount"],
                "seller_amount": tx["seller_amount"],
                "token": tx["token"],
                "network": tx["network"],
                "fee_wallet_address": tx.get("fee_wallet") or "UNCONFIGURED",
                "fee_tx_hash": effective_release_hash or "unknown",
                "status": "CONFIRMED_ONCHAIN",
                "release_tx_hash": effective_release_hash,
                "reconciled": release_tx_hash is None,
                "updated_at": now_utc(),
            },
            "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now_utc()},
        },
        upsert=True,
    )


async def ensure_reconciled_dispute(tx: dict):
    await db.disputes.update_one(
        {"transaction_id": tx["id"], "status": "OPEN"},
        {
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "transaction_id": tx["id"],
                "opened_by": None,
                "reason": "Spór wykryty podczas rekoncyliacji stanu on-chain",
                "status": "OPEN",
                "resolution": None,
                "resolved_by_admin_id": None,
                "created_at": now_utc(),
                "resolved_at": None,
                "reconciled": True,
            }
        },
        upsert=True,
    )


async def reconcile_transaction_onchain(tx: dict) -> dict:
    if payments_may_be_simulated() or not is_onchain_indexer_ready(tx.get("network")):
        return tx
    if not ETH_ADDRESS_RE.fullmatch(tx.get("escrow_receiver", "")):
        return tx
    order = await run_blocking(read_escrow_order, tx["network"], tx["escrow_receiver"], tx["escrow_reference"])
    if order["status"] == "NONE":
        return tx
    validate_escrow_order_terms(tx, order)
    state_updates = {
        "CREATED": {"status": "AWAITING_PAYMENT", "escrow_status": "CREATED"},
        "FUNDED": {"status": "FUNDED", "escrow_status": "FUNDED", "shipping_status": "AWAITING_SHIPMENT"},
        "SHIPPED": {"status": "SHIPPED", "escrow_status": "FUNDED", "shipping_status": "SHIPPED"},
        "DISPUTED": {"status": "DISPUTED", "escrow_status": "FROZEN", "dispute_status": "OPEN"},
        "COMPLETED": {"status": "COMPLETED", "escrow_status": "RELEASED", "shipping_status": "DELIVERED"},
        "REFUNDED": {"status": "REFUNDED", "escrow_status": "REFUNDED", "dispute_status": "RESOLVED"},
        "CANCELLED": {"status": "CANCELLED", "escrow_status": "CANCELLED"},
    }.get(order["status"])
    if not state_updates:
        return tx
    await db.escrow_orders.update_one(
        {"transaction_id": tx["id"]},
        {
            "$set": {
                "status": order["status"],
                "last_reconciled_at": now_utc(),
            },
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "transaction_id": tx["id"],
                "smart_contract_address": tx["escrow_receiver"],
                "order_hash": tx["escrow_reference"],
                "reference": tx["escrow_reference"],
                "amount": tx["gross_amount"],
                "token": tx["token"],
                "network": tx["network"],
                "created_at": now_utc(),
            },
        },
        upsert=True,
    )
    if order["status"] == "DISPUTED":
        await ensure_reconciled_dispute(tx)
    elif order["status"] == "COMPLETED":
        await db.disputes.update_many(
            {"transaction_id": tx["id"], "status": "OPEN"},
            {
                "$set": {
                    "status": "RESOLVED",
                    "resolution": {"decision": "release_seller", "source": "onchain_reconciliation"},
                    "resolved_at": now_utc(),
                }
            },
        )
    elif order["status"] == "REFUNDED":
        await db.disputes.update_many(
            {"transaction_id": tx["id"], "status": "OPEN"},
            {
                "$set": {
                    "status": "RESOLVED",
                    "resolution": {"decision": "refund_buyer", "source": "onchain_reconciliation"},
                    "resolved_at": now_utc(),
                }
            },
        )
    if any(tx.get(key) != value for key, value in state_updates.items()):
        state_updates["last_reconciled_at"] = now_utc()
        state_updates["updated_at"] = now_utc()
        await db.transactions.update_one({"id": tx["id"]}, {"$set": state_updates})
        tx = {**tx, **state_updates}
        if order["status"] == "CANCELLED":
            await db.listings.update_one(
                {"id": tx["listing_id"], "status": "RESERVED"},
                {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
            )
    if order["status"] == "COMPLETED":
        await ensure_sale_fee_record(tx)
    tx["onchain_order"] = order
    return tx


async def acquire_reconciliation_lease() -> bool:
    current = now_utc()
    try:
        lease = await db.worker_leases.find_one_and_update(
            {
                "_id": "onchain-reconciliation",
                "$or": [
                    {"locked_until": {"$lt": current}},
                    {"owner": RECONCILIATION_WORKER_ID},
                ],
            },
            {
                "$set": {
                    "owner": RECONCILIATION_WORKER_ID,
                    "locked_until": current + timedelta(seconds=max(RECONCILIATION_INTERVAL_SECONDS, 30)),
                    "updated_at": current,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(lease and lease.get("owner") == RECONCILIATION_WORKER_ID)


async def expire_unfunded_reservations():
    cursor = db.transactions.find(
        {
            "status": "AWAITING_PAYMENT",
            "reservation_expires_at": {"$lt": now_utc()},
        },
        {"_id": 0},
    ).sort("reservation_expires_at", 1)
    async for tx in cursor:
        if (
            not payments_may_be_simulated()
            and is_onchain_indexer_ready(tx.get("network"))
            and ETH_ADDRESS_RE.fullmatch(tx.get("escrow_receiver", ""))
        ):
            order = await run_blocking(
                read_escrow_order,
                tx["network"],
                tx["escrow_receiver"],
                tx["escrow_reference"],
            )
            if order["status"] != "NONE":
                continue
        expired = await db.transactions.find_one_and_update(
            {
                "id": tx["id"],
                "status": "AWAITING_PAYMENT",
                "reservation_expires_at": {"$lt": now_utc()},
            },
            {
                "$set": {
                    "status": "CANCELLED",
                    "escrow_status": "CANCELLED",
                    "cancel_reason": "reservation_expired",
                    "updated_at": now_utc(),
                }
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if expired:
            await db.listings.update_one(
                {"id": tx["listing_id"], "status": "RESERVED"},
                {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
            )


async def run_reconciliation_batch():
    if not await acquire_reconciliation_lease():
        return
    await db.payment_intents.update_many(
        {"status": "PENDING", "expires_at": {"$lt": now_utc()}},
        {"$set": {"status": "EXPIRED", "expired_at": now_utc()}},
    )
    cursor = db.transactions.find(
        {
            "status": {
                "$in": [
                    "AWAITING_PAYMENT",
                    "FUNDED",
                    "SHIPPED",
                    "DISPUTED",
                    "COMPLETED",
                    "REFUNDED",
                    "CANCELLED",
                ]
            }
        },
        {"_id": 0},
    ).sort("_id", 1)
    async for tx in cursor:
        try:
            await reconcile_transaction_onchain(tx)
        except Exception:
            logger.exception("On-chain reconciliation failed for transaction %s", tx.get("id"))
    await expire_unfunded_reservations()


async def reconciliation_worker():
    while True:
        try:
            await run_reconciliation_batch()
        except Exception:
            logger.exception("On-chain reconciliation batch failed")
        await asyncio.sleep(RECONCILIATION_INTERVAL_SECONDS)


def verify_usdc_transfer_onchain(
    network: str,
    tx_hash: str,
    expected_to: str,
    expected_amount: float,
) -> Tuple[bool, str, dict]:
    if network not in {"Base", "Polygon"}:
        return False, "Sieć poza zakresem MVP on-chain", {}

    data = call_alchemy_rpc(network, "eth_getTransactionReceipt", [tx_hash])
    receipt = data.get("result")
    if not receipt:
        return False, "Brak potwierdzenia transakcji", {}

    status_hex = receipt.get("status", "0x0")
    if int(status_hex, 16) != 1:
        return False, "Transakcja on-chain nieudana", {"receipt": receipt}
    confirmations = require_receipt_finality(network, receipt)

    expected_to_topic = to_topic_address(expected_to)
    usdc_contract = USDC_CONTRACTS[network]
    tolerance = 0.000001

    for log in receipt.get("logs", []):
        log_address = str(log.get("address", "")).lower()
        topics = log.get("topics", [])
        if log_address != usdc_contract:
            continue
        if len(topics) < 3:
            continue
        if topics[0].lower() != TRANSFER_TOPIC:
            continue
        if topics[2].lower() != expected_to_topic:
            continue

        amount = parse_hex_amount(log.get("data", "0x0"))
        if amount + tolerance < expected_amount:
            continue
        return True, "Potwierdzono transfer USDC on-chain", {
            "amount": amount,
            "network": network,
            "tx_hash": tx_hash,
            "confirmations": confirmations,
        }

    return False, "Nie znaleziono poprawnego transferu USDC", {"receipt": receipt}


def verify_indexed_contract_event(
    network: str,
    tx_hash: str,
    contract_address: str,
    event_topic: str,
    indexed_reference: str,
):
    data = call_alchemy_rpc(network, "eth_getTransactionReceipt", [tx_hash])
    receipt = data.get("result") or {}
    require_receipt_finality(network, receipt)
    for log in receipt.get("logs", []):
        topics = [str(topic).lower() for topic in log.get("topics", [])]
        if (
            str(log.get("address", "")).lower() == contract_address.lower()
            and len(topics) >= 2
            and topics[0] == event_topic
            and topics[1] == indexed_reference.lower()
        ):
            return
    raise HTTPException(status_code=400, detail="Transakcja nie zawiera oczekiwanego zdarzenia kontraktu")


def verify_escrow_action(
    tx: dict,
    tx_hash: Optional[str],
    event_topic: str,
    expected_chain_statuses: Optional[set] = None,
) -> Optional[dict]:
    if payments_may_be_simulated():
        return None
    if not tx_hash:
        raise HTTPException(status_code=400, detail="Brak hasha akcji on-chain")
    validate_tx_hash(tx_hash)
    verify_indexed_contract_event(
        tx["network"],
        tx_hash,
        tx["escrow_receiver"],
        event_topic,
        tx["escrow_reference"],
    )
    order = read_escrow_order(tx["network"], tx["escrow_receiver"], tx["escrow_reference"])
    validate_escrow_order_terms(tx, order)
    if expected_chain_statuses and order["status"] not in expected_chain_statuses:
        raise HTTPException(status_code=409, detail=f"Niezgodny stan escrow on-chain: {order['status']}")
    return order


def hmac_matches(raw_body: bytes, signature: str) -> bool:
    if not ALCHEMY_WEBHOOK_SIGNING_KEY:
        return False
    expected = hmac.new(
        ALCHEMY_WEBHOOK_SIGNING_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def generate_webauthn_challenge() -> bytes:
    return os.urandom(32)


def serialize_options(options_obj: Any) -> dict:
    return json.loads(options_to_json(options_obj))


async def save_passkey_challenge(user_id: str, purpose: str, challenge_b64: str):
    await db.passkey_challenges.insert_one(
        {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "purpose": purpose,
            "challenge_b64": challenge_b64,
            "used": False,
            "created_at": now_utc(),
            "expires_at": now_utc() + timedelta(minutes=10),
        }
    )


async def pop_passkey_challenge(user_id: str, purpose: str) -> Optional[dict]:
    return await db.passkey_challenges.find_one_and_update(
        {
            "user_id": user_id,
            "purpose": purpose,
            "used": False,
            "expires_at": {"$gte": now_utc()},
        },
        {"$set": {"used": True, "used_at": now_utc()}},
        projection={"_id": 0},
        sort=[("created_at", -1)],
        return_document=ReturnDocument.BEFORE,
    )


async def pop_wallet_challenge(challenge_id: str, wallet_address: str) -> Optional[dict]:
    return await db.wallet_login_challenges.find_one_and_update(
        {
            "id": challenge_id,
            "wallet_address": wallet_address,
            "used": False,
            "expires_at": {"$gte": now_utc()},
        },
        {"$set": {"used": True, "used_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.BEFORE,
    )


def public_auth_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "alias": user["display_alias"],
        "role": user.get("role", "user"),
        "privacy_level": user.get("privacy_level", 70),
        "two_fa_enabled": user.get("two_fa_enabled", False),
        "public_trust_level": user.get("public_trust_level", "starter"),
        "wallets": user.get("wallets", []),
    }


def run_av_scan_if_enabled(path: str) -> Tuple[bool, str]:
    if not ENABLE_AV_SCAN:
        return True, "AV_SCAN_DISABLED"
    try:
        proc = subprocess.run(
            [CLAMAV_COMMAND, "--no-summary", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
            check=False,
        )
    except Exception as exc:
        return False, f"AV_SCAN_ERROR:{exc}"

    output = f"{proc.stdout} {proc.stderr}".upper()
    if "FOUND" in output:
        return False, "MALWARE_DETECTED"
    if proc.returncode not in {0}:
        return False, "AV_SCAN_FAILED"
    return True, "CLEAN"


def process_image_bytes(raw_bytes: bytes) -> Tuple[bytes, bytes, str]:
    try:
        image = Image.open(BytesIO(raw_bytes))
        width, height = image.size
        if width > UPLOAD_MAX_DIMENSION or height > UPLOAD_MAX_DIMENSION or width * height > UPLOAD_MAX_PIXELS:
            raise HTTPException(status_code=400, detail="Obraz ma zbyt dużą rozdzielczość")
        image.verify()
        image = Image.open(BytesIO(raw_bytes))
        image.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Niepoprawny format obrazu") from exc

    image_format = (image.format or "JPEG").upper()
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        image_format = "JPEG"
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if image_format == "PNG" else "RGB")
    if image_format == "JPEG" and image.mode != "RGB":
        image = image.convert("RGB")

    cleaned_buffer = BytesIO()
    image.save(cleaned_buffer, format=image_format, quality=90)
    cleaned = cleaned_buffer.getvalue()

    thumb_image = image.copy()
    thumb_image.thumbnail((512, 512))
    thumb_buffer = BytesIO()
    thumb_image.save(thumb_buffer, format=image_format, quality=84)
    thumb = thumb_buffer.getvalue()

    ext = "jpg" if image_format == "JPEG" else image_format.lower()
    return cleaned, thumb, ext


def upload_bytes_to_r2(key: str, blob: bytes, content_type: str):
    if not r2_client:
        raise HTTPException(status_code=503, detail="R2 storage nie jest skonfigurowany")
    r2_client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=blob,
        ContentType=content_type,
    )


def signed_r2_get_url(key: str, expires: int = 300) -> Optional[str]:
    if not r2_client:
        return None
    return r2_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET_NAME, "Key": key},
        ExpiresIn=expires,
    )


def signed_local_get_url(request: Request, key: str, expires: int = 300) -> str:
    expires_at = int(now_utc().timestamp()) + expires
    payload = f"{key}:{expires_at}".encode("utf-8")
    signature = hmac.new(JWT_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    media_base = str(request.base_url).rstrip("/") + "/api/media"
    return f"{media_base}/{key}?expires={expires_at}&signature={signature}"


def valid_local_media_signature(key: str, expires: Optional[int], signature: Optional[str]) -> bool:
    if not expires or not signature or expires < int(now_utc().timestamp()):
        return False
    payload = f"{key}:{expires}".encode("utf-8")
    expected = hmac.new(JWT_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def delete_r2_keys(keys: List[str]):
    if not r2_client or not keys:
        return
    response = r2_client.delete_objects(
        Bucket=R2_BUCKET_NAME,
        Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
    )
    if response.get("Errors"):
        raise RuntimeError("R2 did not delete all listing media objects")


async def delete_listing_media(listing_id: str):
    images = await db.listing_images.find({"listing_id": listing_id}, {"_id": 0}).to_list(30)
    r2_keys = [
        key
        for image in images
        if image.get("storage_provider") == "r2"
        for key in [image.get("original_key"), image.get("thumb_key")]
        if key
    ]
    if r2_keys:
        await run_blocking(delete_r2_keys, r2_keys)

    local_listing_dir = (LOCAL_UPLOAD_DIR / "listings" / listing_id).resolve()
    try:
        local_listing_dir.relative_to(LOCAL_UPLOAD_DIR.resolve())
    except ValueError as exc:
        raise RuntimeError("Invalid local listing media path") from exc
    await run_blocking(shutil.rmtree, local_listing_dir, True)
    await db.listing_images.delete_many({"listing_id": listing_id})


def normalize_network_label(raw: str) -> Optional[str]:
    lower = str(raw).lower()
    if "base" in lower:
        return "Base"
    if "polygon" in lower:
        return "Polygon"
    return None


def normalize_datetime_for_compare(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def normalize_slug(raw: str) -> str:
    lowered = raw.strip().lower()
    lowered = re.sub(r"\s+", "-", lowered)
    lowered = re.sub(r"[^a-z0-9\-]", "", lowered)
    lowered = re.sub(r"\-+", "-", lowered).strip("-")
    return lowered


def ensure_hex_color(value: str) -> bool:
    return bool(re.match(r"^#[0-9a-fA-F]{6}$", value.strip()))


async def get_active_categories() -> List[dict]:
    categories = await db.categories.find(
        {"is_active": True},
        {"_id": 0},
    ).to_list(300)
    categories.sort(key=lambda c: (c.get("sort_order", 1000), c.get("name", "")))
    return categories


async def category_exists_by_slug(slug: str) -> Optional[dict]:
    return await db.categories.find_one({"slug": slug}, {"_id": 0})


async def hide_listings_for_category_slug(slug: str):
    await db.listings.update_many(
        {
            "category": slug,
            "status": {"$nin": ["SOLD", "DELETED", "REJECTED", "CANCELLED"]},
        },
        {
            "$set": {
                "status": "HIDDEN_CATEGORY",
                "moderation_status": "CATEGORY_DISABLED",
                "moderation_reason": "Kategoria wyłączona/usunięta przez admina",
                "updated_at": now_utc(),
            }
        },
    )


async def restore_listings_for_category_slug(slug: str):
    await db.listings.update_many(
        {
            "category": slug,
            "status": "HIDDEN_CATEGORY",
        },
        {
            "$set": {
                "status": "UNDER_REVIEW",
                "moderation_status": "PENDING",
                "moderation_reason": "Kategoria ponownie aktywna — wymagany przegląd",
                "updated_at": now_utc(),
            }
        },
    )


async def ensure_default_categories_seeded():
    default_icons = {
        "elektronika": "hardware-chip-outline",
        "moda": "shirt-outline",
        "dom": "home-outline",
        "motoryzacja": "car-sport-outline",
        "sport": "barbell-outline",
        "dziecko": "happy-outline",
        "kolekcje": "diamond-outline",
        "usugi-lokalne": "location-outline",
        "produkty-cyfrowe-legalne": "cloud-outline",
        "inne": "apps-outline",
    }
    default_colors = {
        "elektronika": "#00f3ff",
        "moda": "#ff00ff",
        "dom": "#39ff14",
        "motoryzacja": "#ffcc66",
        "sport": "#00f3ff",
        "dziecko": "#39ff14",
        "kolekcje": "#ff00ff",
        "usugi-lokalne": "#00f3ff",
        "produkty-cyfrowe-legalne": "#39ff14",
        "inne": "#a1a1aa",
    }

    order = 10
    for slug in DEFAULT_LISTING_FEES.keys():
        await db.categories.update_one(
            {"slug": slug},
            {
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "name": DEFAULT_CATEGORY_LABELS.get(slug, slug),
                    "slug": slug,
                    "icon": default_icons.get(slug, "apps-outline"),
                    "color": default_colors.get(slug, "#00f3ff"),
                    "sort_order": order,
                    "is_active": True,
                    "created_at": now_utc(),
                    "updated_at": now_utc(),
                }
            },
            upsert=True,
        )
        order += 10


async def activate_listing_after_fee(listing: dict):
    moderation_status = "PENDING" if listing.get("risk_score", 0) > 70 else "APPROVED"
    status = "ACTIVE" if moderation_status == "APPROVED" else "UNDER_REVIEW"
    return await db.listings.find_one_and_update(
        {
            "id": listing["id"],
            "status": "DRAFT",
            "listing_fee.status": "AWAITING_PAYMENT",
        },
        {
            "$set": {
                "status": status,
                "moderation_status": moderation_status,
                "listing_fee.status": "PAID",
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def activate_listing_promotion(
    listing_id: str,
    package_type: str,
    amount_usdc: float,
    tx_hash: str,
    network: str,
    starts_at: Optional[datetime] = None,
    ends_at: Optional[datetime] = None,
):
    package = PROMOTION_PACKAGES.get(package_type)
    if not package:
        raise HTTPException(status_code=400, detail="Nieznany pakiet promocji")

    starts = starts_at or now_utc()
    ends = ends_at or (starts + timedelta(hours=package["duration_hours"]))
    await db.listings.update_one(
        {"id": listing_id},
        {
            "$set": {
                "promotion": {
                    "is_promoted": True,
                    "package_type": package_type,
                    "package_label": package["label"],
                    "amount_usdc": amount_usdc,
                    "network": network,
                    "tx_hash": tx_hash,
                    "starts_at": starts,
                    "ends_at": ends,
                },
                "updated_at": now_utc(),
            }
        },
    )


def serialize_webauthn_credential_for_verify(credential: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": credential.get("id"),
        "rawId": credential.get("rawId"),
        "type": credential.get("type"),
        "response": credential.get("response", {}),
        "clientExtensionResults": credential.get("clientExtensionResults", {}),
        "authenticatorAttachment": credential.get("authenticatorAttachment"),
    }


async def get_optional_user(token: Optional[str] = Depends(oauth2_optional)) -> Optional[dict]:
    if not token:
        return None
    payload = decode_token(token)
    if payload.get("type") != "access":
        return None
    user = await db.users.find_one({"id": payload.get("sub")}, {"_id": 0})
    if not user or user.get("status") != "ACTIVE" or user.get("panic_lock_enabled"):
        return None
    session = await db.sessions.find_one(
        {"id": payload.get("sid"), "user_id": user["id"], "is_active": True},
        {"_id": 0, "id": 1},
    )
    if not session:
        return None
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")
    user = await db.users.find_one({"id": payload.get("sub")}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    ensure_account_active(user)
    session = await db.sessions.find_one(
        {"id": payload.get("sid"), "user_id": user["id"], "is_active": True},
        {"_id": 0, "id": 1},
    )
    if not session:
        raise HTTPException(status_code=401, detail="Inactive session")
    return user


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if APP_ENV == "production" and not user.get("two_fa_enabled"):
        raise HTTPException(status_code=403, detail="Admin musi mieć aktywne 2FA")
    return user


async def write_audit_log(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    reason: str,
    old_value: Any,
    new_value: Any,
    request: Request,
):
    log_doc = {
        "id": str(uuid.uuid4()),
        "admin_id": admin_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "reason": reason,
        "old_value": old_value,
        "new_value": new_value,
        "ip_hash": hmac.new(
            JWT_SECRET.encode("utf-8"),
            (request.client.host if request.client else "unknown").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
        "created_at": now_utc(),
    }
    await db.admin_audit_logs.insert_one(log_doc)


async def get_listing_fee_rule(category: str, account_type: str = "default") -> dict:
    rule = await db.listing_fee_rules.find_one(
        {
            "category": category,
            "account_type": account_type,
            "is_active": True,
        },
        {"_id": 0},
    )
    if rule:
        return rule

    fallback_amount = DEFAULT_LISTING_FEES.get(category, DEFAULT_LISTING_FEES["inne"])
    return {
        "category": category,
        "account_type": "default",
        "amount": fallback_amount,
        "token": "USDC",
        "network": DEFAULT_NETWORK,
        "is_active": True,
    }


async def get_sales_fee_rule(category: str, account_type: str = "default") -> dict:
    rule = await db.sale_fee_rules.find_one(
        {
            "$or": [{"category": category}, {"category": "default"}],
            "account_type": account_type,
            "is_active": True,
        },
        {"_id": 0},
    )
    if rule:
        return rule
    return {
        "category": "default",
        "account_type": "default",
        "reputation_level": "any",
        "fee_percent": 5.0,
        "is_active": True,
    }


async def create_session(
    user: dict,
    device_name: str,
    request: Request,
) -> dict:
    session_id = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())
    session_doc = {
        "id": session_id,
        "user_id": user["id"],
        "device_name": device_name,
        "user_agent": request.headers.get("user-agent", "unknown"),
        "ip": request.client.host if request.client else "unknown",
        "refresh_jti": refresh_jti,
        "is_active": True,
        "created_at": now_utc(),
        "last_seen": now_utc(),
    }
    await db.sessions.insert_one(session_doc)

    return {
        "access_token": create_access_token(user["id"], user.get("role", "user"), session_id),
        "refresh_token": create_refresh_token(user["id"], session_id, refresh_jti),
        "session_id": session_id,
        "token_type": "bearer",
    }


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    current = now_utc()
    bucket = current.strftime("%Y%m%d%H%M")
    path_parts = [part for part in path.strip("/").split("/") if part]
    if "auth" in path_parts:
        auth_index = path_parts.index("auth")
        scope = "auth:" + ":".join(path_parts[auth_index + 1:auth_index + 3])
    else:
        scope = path_parts[1] if len(path_parts) > 1 else (path_parts[0] if path_parts else "root")
    authorization = request.headers.get("authorization", "")
    identity = ip
    if authorization.lower().startswith("bearer "):
        try:
            token_data = decode_token(authorization.split(" ", 1)[1])
            if token_data.get("type") == "access" and token_data.get("sub"):
                identity = f"user:{token_data['sub']}"
        except HTTPException:
            identity = ip
    key = hashlib.sha256(f"{identity}:{bucket}:{scope}".encode("utf-8")).hexdigest()
    limit = (
        RATE_LIMIT_AUTH_PER_MINUTE
        if "/auth/" in path
        else RATE_LIMIT_GENERAL_PER_MINUTE
    )
    entry = await db.rate_limits.find_one_and_update(
        {"_id": key},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "expires_at": current + timedelta(minutes=2),
                "scope": scope,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if int(entry.get("count", 0)) > limit:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    return await call_next(request)


async def ensure_seed_indexes():
    await db.users.create_index(
        "email",
        unique=True,
        partialFilterExpression={"email": {"$type": "string"}},
    )
    await db.categories.create_index([("slug", 1)], unique=True)
    await db.listing_fee_rules.create_index(
        [("category", 1), ("account_type", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
    )
    await db.sale_fee_rules.create_index(
        [("category", 1), ("account_type", 1), ("reputation_level", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
    )
    await db.platform_fee_wallets.create_index(
        [("network", 1), ("token", 1)],
        unique=True,
        partialFilterExpression={"is_active": True},
    )


@app.on_event("startup")
async def startup_seed_data():
    await ensure_seed_indexes()
    if SEED_ADMIN:
        if not ADMIN_EMAIL or len(ADMIN_PASSWORD) < 16:
            raise RuntimeError("SEED_ADMIN requires ADMIN_EMAIL and ADMIN_PASSWORD with at least 16 characters")
        admin_doc = {
            "id": str(uuid.uuid4()),
            "public_id": str(uuid.uuid4()),
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "display_alias": "MASK-ADMIN",
            "public_location": "HQ",
            "status": "ACTIVE",
            "role": "admin",
            "risk_score": 0,
            "trust_score": 100,
            "privacy_level": 90,
            "public_trust_level": "verified",
            "verification_level": "system",
            "two_fa_enabled": False,
            "two_fa_secret": None,
            "two_fa_pending": None,
            "passkeys": [],
            "panic_lock_enabled": False,
            "wallets": [],
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$setOnInsert": admin_doc},
            upsert=True,
        )

    for category, amount in DEFAULT_LISTING_FEES.items():
        await db.listing_fee_rules.update_one(
            {"category": category, "account_type": "default", "is_active": True},
            {
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "category": category,
                    "account_type": "default",
                    "amount": amount,
                    "token": "USDC",
                    "network": DEFAULT_NETWORK,
                    "is_active": True,
                    "created_at": now_utc(),
                }
            },
            upsert=True,
        )

    await db.sale_fee_rules.update_one(
        {
            "category": "default",
            "account_type": "default",
            "reputation_level": "any",
            "is_active": True,
        },
        {
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "category": "default",
                "account_type": "default",
                "reputation_level": "any",
                "fee_percent": 5.0,
                "is_active": True,
                "created_at": now_utc(),
            }
        },
        upsert=True,
    )

    for network in SUPPORTED_NETWORKS:
        configured_wallet = PLATFORM_WALLETS.get(network)
        if not configured_wallet:
            continue
        wallet_exists = await db.platform_fee_wallets.find_one(
            {"network": network, "token": "USDC", "is_active": True}, {"_id": 0}
        )
        if wallet_exists and wallet_exists["wallet_address"].lower() != configured_wallet.lower():
            raise RuntimeError(
                f"Active {network} USDC fee wallet does not match PLATFORM_WALLET_{network.upper()}"
            )
        if not wallet_exists:
            await db.platform_fee_wallets.update_one(
                {
                    "network": network,
                    "token": "USDC",
                    "wallet_address": configured_wallet,
                    "is_active": True,
                },
                {
                    "$setOnInsert": {
                        "id": str(uuid.uuid4()),
                        "network": network,
                        "token": "USDC",
                        "wallet_address": configured_wallet,
                        "is_active": True,
                        "pending_activation_at": now_utc(),
                        "created_at": now_utc(),
                        "updated_at": now_utc(),
                        "changed_by_admin_id": "system",
                    }
                },
                upsert=True,
            )

    await ensure_default_categories_seeded()

    await db.passkey_challenges.create_index([("expires_at", 1)], expireAfterSeconds=0)
    await db.wallet_login_challenges.create_index([("expires_at", 1)], expireAfterSeconds=0)
    await db.wallet_login_challenges.create_index([("wallet_address", 1), ("created_at", -1)])
    await db.rate_limits.create_index([("expires_at", 1)], expireAfterSeconds=0)
    await db.passkey_challenges.create_index([("user_id", 1), ("purpose", 1), ("created_at", -1)])
    await db.payment_intents.create_index([("status", 1), ("network", 1), ("created_at", -1)])
    await db.payment_intents.create_index([("status", 1), ("expires_at", 1)])
    await db.used_transaction_hashes.create_index([("network", 1), ("tx_hash", 1)], unique=True)
    await db.e2ee_rooms.create_index([("transaction_id", 1)], unique=True)
    await db.encrypted_messages.create_index([("transaction_id", 1), ("created_at", 1)])
    await db.encrypted_messages.create_index([("expires_at", 1)], expireAfterSeconds=0)
    await db.encrypted_messages.create_index(
        [("transaction_id", 1), ("client_message_id", 1)],
        unique=True,
        partialFilterExpression={"encryption_version": "nacl-secretbox-v1"},
    )
    await db.encrypted_messages.create_index(
        [("transaction_id", 1), ("key_id", 1), ("sender_id", 1), ("nonce", 1)],
        unique=True,
        partialFilterExpression={"encryption_version": "nacl-secretbox-v1"},
    )
    await db.listing_fees.create_index([("listing_id", 1)], unique=True)
    await db.sale_fees.create_index([("transaction_id", 1)], unique=True)
    await db.shipping_labels.create_index([("transaction_id", 1)], unique=True)
    await db.disputes.create_index(
        [("transaction_id", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"status": "OPEN"},
    )
    await db.listing_images.create_index([("listing_id", 1), ("created_at", -1)])
    await db.listings.create_index(
        [("status", 1), ("promotion.is_promoted", -1), ("created_at", -1)]
    )
    await db.transactions.create_index([("buyer_id", 1), ("created_at", -1)])
    await db.transactions.create_index([("seller_id", 1), ("created_at", -1)])
    await db.sessions.create_index([("user_id", 1), ("created_at", -1)])
    await db.admin_audit_logs.create_index([("created_at", -1)])
    await db.sale_fees.create_index([("status", 1), ("network", 1), ("token", 1)])
    await db.categories.create_index([("slug", 1)], unique=True)
    await db.categories.create_index([("is_active", 1), ("sort_order", 1)])
    await db.users.create_index(
        "wallets.address",
        unique=True,
        partialFilterExpression={"wallets.address": {"$type": "string"}},
    )
    await db.users.create_index(
        "email",
        unique=True,
        partialFilterExpression={"email": {"$type": "string"}},
    )
    if APP_ENV == "production":
        legacy_messages = await db.encrypted_messages.count_documents(
            {"encryption_version": {"$ne": "nacl-secretbox-v1"}}
        )
        if legacy_messages:
            raise RuntimeError(
                "Legacy non-E2EE messages exist. Purge them before production startup."
            )
    await verify_contract_deployments_on_startup()
    global reconciliation_task
    if RECONCILIATION_INTERVAL_SECONDS > 0:
        reconciliation_task = asyncio.create_task(reconciliation_worker())


@app.on_event("shutdown")
async def stop_reconciliation_worker():
    global reconciliation_task
    if reconciliation_task:
        reconciliation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconciliation_task
        reconciliation_task = None


@api_router.get("/")
async def root():
    return {
        "name": PLATFORM_NAME,
        "status": "ok",
        "contract_verification": clean_mongo_doc(contract_verification_status),
        "privacy_promise": "Anonimowość wobec użytkowników, odpowiedzialność wobec systemu",
    }


@api_router.get("/media/{media_path:path}")
async def local_listing_media(
    media_path: str,
    expires: Optional[int] = Query(default=None),
    signature: Optional[str] = Query(default=None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    normalized_path = media_path.lstrip("/")
    image = await db.listing_images.find_one(
        {
            "storage_provider": "local",
            "$or": [{"original_key": normalized_path}, {"thumb_key": normalized_path}],
        },
        {"_id": 0, "listing_id": 1},
    )
    if not image:
        raise HTTPException(status_code=404, detail="Plik nie istnieje")

    listing = await db.listings.find_one(
        {"id": image["listing_id"]},
        {"_id": 0, "seller_id": 1, "status": 1},
    )
    is_owner = bool(current_user and listing and current_user["id"] == listing["seller_id"])
    is_admin = bool(current_user and current_user.get("role") == "admin")
    is_public = bool(listing and listing.get("status") in {"ACTIVE", "RESERVED", "SOLD"})
    has_signed_access = bool(
        listing
        and listing.get("status") not in {"DELETED", "DELETING"}
        and valid_local_media_signature(normalized_path, expires, signature)
    )
    if not listing or not (is_public or is_owner or is_admin or has_signed_access):
        raise HTTPException(status_code=404, detail="Plik nie istnieje")

    file_path = (LOCAL_UPLOAD_DIR / normalized_path).resolve()
    try:
        file_path.relative_to(LOCAL_UPLOAD_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Plik nie istnieje") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Plik nie istnieje")

    cache_control = "public, max-age=300" if is_public else "private, no-store"
    return FileResponse(file_path, headers={"Cache-Control": cache_control})


@api_router.get("/categories")
async def list_categories(current_user: Optional[dict] = Depends(get_optional_user)):
    if current_user and current_user.get("role") == "admin":
        categories = await db.categories.find({}, {"_id": 0}).to_list(500)
    else:
        categories = await get_active_categories()
    categories.sort(key=lambda c: (c.get("sort_order", 1000), c.get("name", "")))
    return categories


@api_router.post("/auth/register")
async def auth_register(payload: RegisterInput):
    email = payload.email.lower().strip()
    if len(payload.password) < 10:
        raise HTTPException(status_code=400, detail="Hasło musi mieć minimum 10 znaków")

    exists = await db.users.find_one({"email": email}, {"_id": 0})
    if exists:
        raise HTTPException(status_code=409, detail="Użytkownik już istnieje")

    user_doc = {
        "id": str(uuid.uuid4()),
        "public_id": str(uuid.uuid4()),
        "email": email,
        "password_hash": hash_password(payload.password),
        "display_alias": payload.alias,
        "public_location": payload.public_location,
        "status": "ACTIVE",
        "role": "user",
        "risk_score": 10,
        "trust_score": 50,
        "privacy_level": 76,
        "public_trust_level": "starter",
        "verification_level": "none",
        "two_fa_enabled": False,
        "two_fa_secret": None,
        "two_fa_pending": None,
        "passkeys": [],
        "panic_lock_enabled": False,
        "wallets": [],
        "auto_delete_messages_days": 14,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.users.insert_one(user_doc)

    return {
        "message": "Konto utworzone",
        "user": {
            "id": user_doc["id"],
            "alias": user_doc["display_alias"],
            "public_trust_level": user_doc["public_trust_level"],
        },
    }


@api_router.post("/auth/login")
async def auth_login(payload: LoginInput, request: Request):
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Niepoprawne dane logowania")

    ensure_account_active(user)

    if user.get("two_fa_enabled"):
        if not payload.otp_code:
            raise HTTPException(status_code=401, detail="Wymagany kod 2FA")
        secret = decrypt_secret(user.get("two_fa_secret"))
        if not secret or not pyotp.TOTP(secret).verify(payload.otp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Niepoprawny kod 2FA")

    tokens = await create_session(user, payload.device_name, request)
    return {
        **tokens,
        "user": public_auth_user(user),
    }


@api_router.post("/auth/panic-unlock")
async def auth_panic_unlock(payload: PanicUnlockInput):
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Niepoprawne dane odzyskiwania")
    if not user.get("panic_lock_enabled"):
        raise HTTPException(status_code=409, detail="Panic Lock nie jest aktywny")
    if user.get("two_fa_enabled"):
        secret = decrypt_secret(user.get("two_fa_secret"))
        if (
            not payload.otp_code
            or not secret
            or not pyotp.TOTP(secret).verify(payload.otp_code, valid_window=1)
        ):
            raise HTTPException(status_code=401, detail="Wymagany poprawny kod 2FA")
    await db.users.update_one(
        {"id": user["id"], "panic_lock_enabled": True},
        {
            "$set": {
                "panic_lock_enabled": False,
                "status": "ACTIVE",
                "panic_unlocked_at": now_utc(),
                "updated_at": now_utc(),
            }
        },
    )
    return {"message": "Panic Lock wyłączony. Zaloguj się ponownie."}


@api_router.post("/auth/refresh")
async def auth_refresh(payload: RefreshInput):
    token_data = decode_token(payload.refresh_token)
    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if token_data.get("sid") != payload.session_id:
        raise HTTPException(status_code=401, detail="Session mismatch")

    session = await db.sessions.find_one({"id": payload.session_id}, {"_id": 0})
    if not session or not session.get("is_active"):
        raise HTTPException(status_code=401, detail="Inactive session")
    if token_data.get("sub") != session.get("user_id"):
        raise HTTPException(status_code=401, detail="Session user mismatch")

    user = await db.users.find_one({"id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    ensure_account_active(user)

    token_jti = token_data.get("jti")
    if session.get("refresh_jti") != token_jti:
        rotated_at = normalize_datetime_for_compare(session.get("refresh_rotated_at"))
        within_parallel_grace = bool(
            session.get("previous_refresh_jti") == token_jti
            and rotated_at
            and rotated_at >= now_utc() - timedelta(seconds=10)
        )
        if within_parallel_grace:
            current_jti = session["refresh_jti"]
            return {
                "access_token": create_access_token(
                    user["id"], user.get("role", "user"), payload.session_id
                ),
                "refresh_token": create_refresh_token(user["id"], payload.session_id, current_jti),
                "session_id": payload.session_id,
                "token_type": "bearer",
            }
        await db.sessions.update_one(
            {"id": payload.session_id},
            {"$set": {"is_active": False, "last_seen": now_utc()}},
        )
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    next_jti = str(uuid.uuid4())
    rotated_at = now_utc()
    rotated = await db.sessions.find_one_and_update(
        {
            "id": payload.session_id,
            "is_active": True,
            "refresh_jti": token_jti,
        },
        {
            "$set": {
                "refresh_jti": next_jti,
                "previous_refresh_jti": token_jti,
                "refresh_rotated_at": rotated_at,
                "last_seen": rotated_at,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not rotated:
        latest = await db.sessions.find_one({"id": payload.session_id}, {"_id": 0})
        latest_rotated_at = normalize_datetime_for_compare((latest or {}).get("refresh_rotated_at"))
        if (
            latest
            and latest.get("is_active")
            and latest.get("previous_refresh_jti") == token_jti
            and latest_rotated_at
            and latest_rotated_at >= now_utc() - timedelta(seconds=10)
        ):
            next_jti = latest["refresh_jti"]
        else:
            raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    return {
        "access_token": create_access_token(
            user["id"], user.get("role", "user"), payload.session_id
        ),
        "refresh_token": create_refresh_token(user["id"], payload.session_id, next_jti),
        "session_id": payload.session_id,
        "token_type": "bearer",
    }


@api_router.post("/auth/logout")
async def auth_logout(
    payload: LogoutInput,
    user: dict = Depends(get_current_user),
):
    if payload.all_devices:
        await db.sessions.update_many(
            {"user_id": user["id"]}, {"$set": {"is_active": False, "last_seen": now_utc()}}
        )
        return {"message": "Wylogowano ze wszystkich urządzeń"}

    if payload.session_id:
        await db.sessions.update_one(
            {"id": payload.session_id, "user_id": user["id"]},
            {"$set": {"is_active": False, "last_seen": now_utc()}},
        )
        return {"message": "Wylogowano z urządzenia"}

    return {"message": "Brak session_id - nic nie zmieniono"}


@api_router.post("/auth/2fa/enable")
async def auth_enable_2fa(payload: TwoFAEnableInput, user: dict = Depends(get_current_user)):
    if not verify_password(payload.account_password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Niepoprawne hasło")

    secret = pyotp.random_base32()
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "two_fa_pending": encrypt_secret(secret),
                "updated_at": now_utc(),
            }
        },
    )

    return {
        "secret": secret,
        "otpauth_url": pyotp.TOTP(secret).provisioning_uri(
            name=user["email"], issuer_name=PLATFORM_NAME
        ),
        "message": "Zeskanuj sekret w aplikacji TOTP i potwierdź kodem",
    }


@api_router.post("/auth/2fa/verify")
async def auth_verify_2fa(payload: TwoFAVerifyInput, user: dict = Depends(get_current_user)):
    active_secret = decrypt_secret(user.get("two_fa_secret"))
    pending_secret = decrypt_secret(user.get("two_fa_pending"))

    for secret, activate in [(pending_secret, True), (active_secret, False)]:
        if secret and pyotp.TOTP(secret).verify(payload.code, valid_window=1):
            update_fields = {"updated_at": now_utc()}
            if activate:
                update_fields["two_fa_secret"] = encrypt_secret(secret)
                update_fields["two_fa_pending"] = None
                update_fields["two_fa_enabled"] = True
            await db.users.update_one({"id": user["id"]}, {"$set": update_fields})
            return {"message": "2FA aktywne", "enabled": True}

    raise HTTPException(status_code=401, detail="Kod 2FA niepoprawny")


@api_router.post("/auth/passkey/register/options")
async def auth_passkey_register_options(
    payload: PasskeyRegisterOptionsInput,
    user: dict = Depends(get_current_user),
):
    passkeys = user.get("passkeys", [])
    exclude_credentials: List[PublicKeyCredentialDescriptor] = []
    for item in passkeys:
        credential_id = item.get("credential_id")
        if not credential_id:
            continue
        try:
            exclude_credentials.append(
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id))
            )
        except Exception:
            continue

    challenge = generate_webauthn_challenge()
    options = generate_registration_options(
        rp_id=WEBAUTHN_RP_ID,
        rp_name=WEBAUTHN_RP_NAME,
        user_id=user["id"].encode("utf-8"),
        user_name=user["email"],
        user_display_name=user.get("display_alias", user["email"]),
        challenge=challenge,
        exclude_credentials=exclude_credentials or None,
    )

    await save_passkey_challenge(user["id"], "register", bytes_to_base64url(challenge))
    return {
        "public_key": serialize_options(options),
        "allowed_origins": WEBAUTHN_ALLOWED_ORIGINS,
        "rp_id": WEBAUTHN_RP_ID,
        "nickname": payload.nickname,
    }


@api_router.post("/auth/passkey/register")
async def auth_passkey_register(payload: PasskeyRegisterInput, user: dict = Depends(get_current_user)):
    challenge_doc = await pop_passkey_challenge(user["id"], "register")
    if not challenge_doc:
        raise HTTPException(status_code=400, detail="Brak aktywnego challenge rejestracji")

    try:
        verification = verify_registration_response(
            credential=serialize_webauthn_credential_for_verify(payload.credential),
            expected_challenge=base64url_to_bytes(challenge_doc["challenge_b64"]),
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ALLOWED_ORIGINS,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Passkey verification failed") from exc

    credential_id_b64 = bytes_to_base64url(verification.credential_id)
    passkeys = user.get("passkeys", [])
    if any(pk.get("credential_id") == credential_id_b64 for pk in passkeys):
        raise HTTPException(status_code=409, detail="Passkey już istnieje")

    passkeys.append(
        {
            "id": str(uuid.uuid4()),
            "credential_id": credential_id_b64,
            "public_key": bytes_to_base64url(verification.credential_public_key),
            "sign_count": verification.sign_count,
            "nickname": payload.nickname,
            "created_at": now_utc(),
        }
    )
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"passkeys": passkeys, "updated_at": now_utc()}},
    )
    return {
        "message": "Passkey zapisany (WebAuthn)",
        "count": len(passkeys),
        "credential_id": credential_id_b64,
    }


@api_router.post("/auth/passkey/login/options")
async def auth_passkey_login_options(payload: PasskeyLoginOptionsInput):
    normalized_email = payload.email.lower().strip()
    user = await db.users.find_one({"email": normalized_email}, {"_id": 0})
    passkeys = (user or {}).get("passkeys", [])
    allow_credentials: List[PublicKeyCredentialDescriptor] = []
    for item in passkeys:
        credential_id = item.get("credential_id")
        if not credential_id:
            continue
        try:
            allow_credentials.append(
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(credential_id))
            )
        except Exception:
            continue

    if not allow_credentials:
        allow_credentials.append(PublicKeyCredentialDescriptor(id=os.urandom(32)))

    challenge = generate_webauthn_challenge()
    options = generate_authentication_options(
        rp_id=WEBAUTHN_RP_ID,
        challenge=challenge,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_owner = (user or {}).get("id") or (
        "decoy:"
        + hmac.new(JWT_SECRET.encode("utf-8"), normalized_email.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    await save_passkey_challenge(challenge_owner, "login", bytes_to_base64url(challenge))
    return {
        "public_key": serialize_options(options),
        "allowed_origins": WEBAUTHN_ALLOWED_ORIGINS,
        "rp_id": WEBAUTHN_RP_ID,
    }


@api_router.post("/auth/passkey/login")
async def auth_passkey_login(payload: PasskeyLoginInput, request: Request):
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="Logowanie passkey nieudane")
    ensure_account_active(user)

    challenge_doc = await pop_passkey_challenge(user["id"], "login")
    if not challenge_doc:
        raise HTTPException(status_code=401, detail="Logowanie passkey nieudane")

    credential_id = payload.credential.get("id")
    if not credential_id:
        raise HTTPException(status_code=401, detail="Logowanie passkey nieudane")

    passkeys = user.get("passkeys", [])
    matching = next((pk for pk in passkeys if pk.get("credential_id") == credential_id), None)
    if not matching:
        raise HTTPException(status_code=401, detail="Logowanie passkey nieudane")

    try:
        verification = verify_authentication_response(
            credential=serialize_webauthn_credential_for_verify(payload.credential),
            expected_challenge=base64url_to_bytes(challenge_doc["challenge_b64"]),
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ALLOWED_ORIGINS,
            credential_public_key=base64url_to_bytes(matching["public_key"]),
            credential_current_sign_count=int(matching.get("sign_count", 0)),
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Logowanie passkey nieudane") from exc

    for index, item in enumerate(passkeys):
        if item.get("credential_id") == credential_id:
            passkeys[index]["sign_count"] = verification.new_sign_count
            passkeys[index]["last_used_at"] = now_utc()
            break
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"passkeys": passkeys, "updated_at": now_utc()}},
    )

    tokens = await create_session(user, payload.device_name, request)
    return {
        **tokens,
        "user": public_auth_user(user),
    }


@api_router.post("/auth/wallet/challenge")
async def auth_wallet_challenge(payload: WalletChallengeInput, request: Request):
    wallet_address = payload.wallet_address.lower()
    if not ETH_ADDRESS_RE.fullmatch(wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy adres Ethereum")
    supported_chain_ids = {config["chain_id"] for config in ACTIVE_CHAIN_PROFILE.values()}
    chain_id = payload.chain_id or ACTIVE_CHAIN_PROFILE[DEFAULT_NETWORK]["chain_id"]
    if chain_id not in supported_chain_ids:
        raise HTTPException(status_code=400, detail="Portfel jest połączony z nieobsługiwaną siecią")
    origin = request.headers.get("origin", "").rstrip("/")
    if origin not in CORS_ALLOWED_ORIGINS:
        if APP_ENV == "production":
            raise HTTPException(status_code=400, detail="Nieobsługiwana domena logowania wallet")
        origin = CORS_ALLOWED_ORIGINS[0].rstrip("/")
    domain = origin.split("://", 1)[-1]

    challenge_id = str(uuid.uuid4())
    nonce = os.urandom(16).hex()
    issued_at = now_utc()
    expires_at = issued_at + timedelta(minutes=5)
    message = (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{wallet_address}\n\n"
        f"Sign in to {PLATFORM_NAME}.\n\n"
        f"URI: {origin}\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at.isoformat()}\n"
        f"Expiration Time: {expires_at.isoformat()}"
    )
    await db.wallet_login_challenges.insert_one(
        {
            "id": challenge_id,
            "wallet_address": wallet_address,
            "message": message,
            "domain": domain,
            "uri": origin,
            "chain_id": chain_id,
            "used": False,
            "created_at": issued_at,
            "expires_at": expires_at,
        }
    )
    return {
        "challenge_id": challenge_id,
        "message": message,
        "chain_id": chain_id,
        "expires_at": expires_at,
    }


@api_router.post("/auth/wallet/login")
async def auth_wallet_login(payload: WalletLoginInput, request: Request):
    wallet_address = payload.wallet_address.lower()
    if not ETH_ADDRESS_RE.fullmatch(wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy adres Ethereum")

    challenge = await pop_wallet_challenge(payload.challenge_id, wallet_address)
    if not challenge:
        raise HTTPException(status_code=400, detail="Challenge wygasł, nie istnieje lub został użyty")

    try:
        recovered = Account.recover_message(
            encode_defunct(text=challenge["message"]),
            signature=payload.signature,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Weryfikacja podpisu nieudana") from exc
    if recovered.lower() != wallet_address:
        raise HTTPException(status_code=401, detail="Podpis nieprawidłowy")

    user = await db.users.find_one({"wallets.address": wallet_address}, {"_id": 0})
    if not user:
        user = {
            "id": str(uuid.uuid4()),
            "public_id": str(uuid.uuid4()),
            "email": f"wallet_{wallet_address}@mask.market",
            "password_hash": hash_password(str(uuid.uuid4())),
            "display_alias": f"Wallet_{wallet_address[2:8]}",
            "public_location": "Unknown",
            "status": "ACTIVE",
            "role": "user",
            "risk_score": 10,
            "trust_score": 50,
            "privacy_level": 76,
            "public_trust_level": "starter",
            "verification_level": "wallet",
            "two_fa_enabled": False,
            "two_fa_secret": None,
            "two_fa_pending": None,
            "passkeys": [],
            "wallets": [{"address": wallet_address, "linked_at": now_utc(), "is_primary": True}],
            "panic_lock_enabled": False,
            "auto_delete_messages_days": 14,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        try:
            await db.users.insert_one(user)
        except DuplicateKeyError:
            user = await db.users.find_one({"wallets.address": wallet_address}, {"_id": 0})
            if not user:
                raise HTTPException(status_code=409, detail="Adres portfela jest już przypisany")

    if user.get("panic_lock_enabled") and user.get("status") == "LOCKED":
        await db.users.update_one(
            {"id": user["id"], "panic_lock_enabled": True, "status": "LOCKED"},
            {
                "$set": {
                    "panic_lock_enabled": False,
                    "status": "ACTIVE",
                    "panic_unlocked_with_wallet_at": now_utc(),
                    "updated_at": now_utc(),
                }
            },
        )
        user = {**user, "panic_lock_enabled": False, "status": "ACTIVE"}

    ensure_account_active(user)

    tokens = await create_session(user, payload.device_name, request)
    return {
        **tokens,
        "user": public_auth_user(user),
    }


@api_router.post("/me/wallets/link")
async def me_link_wallet(payload: WalletLinkInput, user: dict = Depends(get_current_user)):
    wallet_address = payload.wallet_address.lower()
    if not ETH_ADDRESS_RE.fullmatch(wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy adres Ethereum")
    challenge = await pop_wallet_challenge(payload.challenge_id, wallet_address)
    if not challenge:
        raise HTTPException(status_code=400, detail="Challenge wygasł, nie istnieje lub został użyty")
    try:
        recovered = Account.recover_message(
            encode_defunct(text=challenge["message"]),
            signature=payload.signature,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Weryfikacja podpisu nieudana") from exc
    if recovered.lower() != wallet_address:
        raise HTTPException(status_code=401, detail="Podpis nieprawidłowy")
    owner = await db.users.find_one({"wallets.address": wallet_address}, {"_id": 0, "id": 1})
    if owner and owner["id"] != user["id"]:
        raise HTTPException(status_code=409, detail="Portfel jest już przypisany do innego konta")
    try:
        await db.users.update_one(
            {"id": user["id"], "wallets.address": {"$ne": wallet_address}},
            {
                "$push": {
                    "wallets": {
                        "address": wallet_address,
                        "linked_at": now_utc(),
                        "is_primary": not bool(user.get("wallets")),
                    }
                },
                "$set": {"verification_level": "wallet", "updated_at": now_utc()},
            },
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Portfel jest już przypisany do innego konta") from exc
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0, "wallets": 1})
    return {"message": "Portfel został przypięty", "wallets": (updated or {}).get("wallets", [])}


@api_router.post("/me/wallets/primary")
async def me_set_primary_wallet(payload: WalletAddressInput, user: dict = Depends(get_current_user)):
    wallet_address = payload.wallet_address.lower()
    if wallet_address not in {wallet.get("address", "").lower() for wallet in user.get("wallets", [])}:
        raise HTTPException(status_code=404, detail="Portfel nie jest przypięty do konta")
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "wallets.$[].is_primary": False,
                "updated_at": now_utc(),
            }
        },
    )
    await db.users.update_one(
        {"id": user["id"], "wallets.address": wallet_address},
        {"$set": {"wallets.$.is_primary": True}},
    )
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0, "wallets": 1})
    return {"message": "Ustawiono główny portfel", "wallets": (updated or {}).get("wallets", [])}


@api_router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "email": user["email"],
        "alias": user["display_alias"],
        "public_location": user.get("public_location", "Unknown"),
        "risk_score": user.get("risk_score", 0),
        "trust_score": user.get("trust_score", 0),
        "privacy_level": user.get("privacy_level", 0),
        "public_trust_level": user.get("public_trust_level", "starter"),
        "verification_level": user.get("verification_level", "none"),
        "two_fa_enabled": user.get("two_fa_enabled", False),
        "panic_lock_enabled": user.get("panic_lock_enabled", False),
        "wallets": user.get("wallets", []),
        "created_at": user.get("created_at"),
        "e2ee_public_key": user.get("e2ee_public_key"),
        "e2ee_fingerprint": user.get("e2ee_fingerprint"),
    }


@api_router.get("/me/e2ee-key")
async def get_me_e2ee_key(user: dict = Depends(get_current_user)):
    return {
        "public_key": user.get("e2ee_public_key"),
        "fingerprint": user.get("e2ee_fingerprint"),
        "registered_at": user.get("e2ee_key_registered_at"),
    }


@api_router.post("/me/e2ee-key")
async def register_me_e2ee_key(
    payload: E2EEPublicKeyInput,
    user: dict = Depends(get_current_user),
):
    fingerprint = e2ee_fingerprint(payload.public_key)
    current = user.get("e2ee_public_key")
    if current and not hmac.compare_digest(current, payload.public_key):
        raise HTTPException(
            status_code=409,
            detail="Klucz E2EE konta jest już przypięty i nie może zostać cicho zastąpiony",
        )
    if not current:
        updated = await db.users.update_one(
            {"id": user["id"], "e2ee_public_key": {"$exists": False}},
            {
                "$set": {
                    "e2ee_public_key": payload.public_key,
                    "e2ee_fingerprint": fingerprint,
                    "e2ee_key_registered_at": now_utc(),
                    "updated_at": now_utc(),
                }
            },
        )
        if updated.modified_count != 1:
            fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "e2ee_public_key": 1})
            if not fresh or fresh.get("e2ee_public_key") != payload.public_key:
                raise HTTPException(status_code=409, detail="Klucz E2EE został już przypięty")
    return {"public_key": payload.public_key, "fingerprint": fingerprint}


@api_router.patch("/me")
async def patch_me(payload: MeUpdateInput, user: dict = Depends(get_current_user)):
    updates = payload.model_dump(exclude_none=True)
    if "alias" in updates:
        updates["display_alias"] = updates.pop("alias")
    updates["updated_at"] = now_utc()

    await db.users.update_one({"id": user["id"]}, {"$set": updates})
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return {
        "id": updated["id"],
        "alias": updated["display_alias"],
        "public_location": updated.get("public_location", "Unknown"),
        "auto_delete_messages_days": updated.get("auto_delete_messages_days", 14),
    }


@api_router.get("/me/security")
async def get_me_security(user: dict = Depends(get_current_user)):
    active_sessions = await db.sessions.count_documents({"user_id": user["id"], "is_active": True})
    recommendations = []
    if not user.get("two_fa_enabled"):
        recommendations.append("Włącz 2FA")
    if not user.get("passkeys"):
        recommendations.append("Włącz passkey")
    recommendations.extend(
        [
            "Usuń stare sesje",
            "Używaj osobnego portfela zakupowego",
            "Nie podawaj danych w czacie",
        ]
    )
    return {
        "privacy_shield_score": user.get("privacy_level", 70),
        "two_fa_enabled": user.get("two_fa_enabled", False),
        "passkeys_count": len(user.get("passkeys", [])),
        "active_sessions": active_sessions,
        "recommendations": recommendations,
    }


@api_router.post("/me/panic-lock")
async def me_panic_lock(user: dict = Depends(get_current_user)):
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "panic_lock_enabled": True,
                "status": "LOCKED",
                "updated_at": now_utc(),
            }
        },
    )
    await db.sessions.update_many(
        {"user_id": user["id"]}, {"$set": {"is_active": False, "last_seen": now_utc()}}
    )
    return {
        "message": "Panic Lock aktywny: urządzenia wylogowane i konto aplikacji zablokowane",
    }


@api_router.get("/me/devices")
async def me_devices(
    limit: int = Query(default=50, ge=1, le=API_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    devices = await db.sessions.find(
        {"user_id": user["id"]},
        {"_id": 0},
    ).sort("created_at", -1).skip(offset).to_list(limit)
    return devices


@api_router.delete("/me/devices/{device_id}")
async def me_delete_device(device_id: str, user: dict = Depends(get_current_user)):
    await db.sessions.update_one(
        {"id": device_id, "user_id": user["id"]},
        {"$set": {"is_active": False, "last_seen": now_utc()}},
    )
    return {"message": "Urządzenie usunięte"}


@api_router.get("/listings")
async def listings_list(
    q: Optional[str] = None,
    category: Optional[str] = None,
    sort: Optional[str] = "new",
    limit: int = Query(default=50, ge=1, le=API_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    filters: Dict[str, Any] = {"status": {"$in": ["ACTIVE", "RESERVED"]}}
    if category:
        cat_obj = await db.categories.find_one({"slug": category, "is_active": True}, {"_id": 0})
        if not cat_obj:
            return []
        filters["category"] = category

    if not current_user or current_user.get("role") != "admin":
        active_categories = await get_active_categories()
        allowed_slugs = [cat["slug"] for cat in active_categories]
        if allowed_slugs:
            filters["category"] = filters.get("category", {"$in": allowed_slugs})
        else:
            return []
    if q:
        regex = re.compile(re.escape(q), re.IGNORECASE)
        filters["$or"] = [{"title": regex}, {"description": regex}]

    created_direction = 1 if sort == "old" else -1
    items = await db.listings.aggregate(
        [
            {"$match": filters},
            {
                "$addFields": {
                    "is_promoted": {
                        "$and": [
                            {"$eq": ["$promotion.is_promoted", True]},
                            {"$gt": ["$promotion.ends_at", now_utc()]},
                        ]
                    }
                }
            },
            {"$sort": {"is_promoted": -1, "created_at": created_direction}},
            {"$skip": offset},
            {"$limit": limit},
            {"$project": {"_id": 0}},
        ]
    ).to_list(limit)
    for item in items:
        seller = await db.users.find_one(
            {"id": item["seller_id"]},
            {
                "_id": 0,
                "id": 1,
                "display_alias": 1,
                "public_trust_level": 1,
                "trust_score": 1,
            },
        )
        item["seller_public"] = seller or {
            "id": "unknown",
            "display_alias": "hidden",
            "public_trust_level": "unknown",
            "trust_score": 0,
        }
        item["is_owner"] = bool(current_user and current_user["id"] == item["seller_id"])

    return items


@api_router.get("/listings/{listing_id}")
async def listing_detail(
    listing_id: str,
    request: Request,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    is_owner = bool(current_user and current_user["id"] == listing["seller_id"])
    is_admin = bool(current_user and current_user.get("role") == "admin")
    if listing.get("status") not in {"ACTIVE", "RESERVED", "SOLD"} and not (is_owner or is_admin):
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")

    seller = await db.users.find_one(
        {"id": listing["seller_id"]},
        {
            "_id": 0,
            "id": 1,
            "display_alias": 1,
            "public_trust_level": 1,
            "trust_score": 1,
            "verification_level": 1,
            "public_location": 1,
        },
    )
    listing["seller_public"] = seller
    images_meta = await db.listing_images.find(
        {"listing_id": listing_id}, {"_id": 0}
    ).to_list(30)
    if images_meta:
        listing["processed_images"] = [
            {
                "id": img["id"],
                "thumb_key": img.get("thumb_key"),
                "original_key": img.get("original_key"),
                "thumb_signed_url": (
                    signed_r2_get_url(img["thumb_key"])
                    if img.get("storage_provider") == "r2"
                    else signed_local_get_url(request, img["thumb_key"])
                )
                if img.get("thumb_key") else None,
                "original_signed_url": (
                    signed_r2_get_url(img["original_key"])
                    if img.get("storage_provider") == "r2"
                    else signed_local_get_url(request, img["original_key"])
                )
                if img.get("original_key") else None,
                "scan_status": img.get("scan_status", "UNKNOWN"),
            }
            for img in images_meta
        ]
    listing["is_owner"] = is_owner
    return listing


@api_router.post("/listings")
async def listing_create(payload: ListingCreateInput, user: dict = Depends(get_current_user)):
    if user.get("panic_lock_enabled"):
        raise HTTPException(status_code=403, detail="Konto zablokowane przez Panic Lock")

    category_slug = normalize_slug(payload.category)
    category_obj = await db.categories.find_one(
        {"slug": category_slug, "is_active": True},
        {"_id": 0},
    )
    if not category_obj:
        raise HTTPException(status_code=400, detail="Nieobsługiwana lub nieaktywna kategoria")

    listing_fee_rule = await get_listing_fee_rule(category_slug)
    fee_wallet = await db.platform_fee_wallets.find_one(
        {
            "network": listing_fee_rule["network"],
            "token": listing_fee_rule["token"],
            "is_active": True,
        },
        {"_id": 0},
    )
    if not fee_wallet:
        raise HTTPException(status_code=503, detail="Portfel opłat platformy nie jest skonfigurowany")
    crypto_amount = round(payload.price_fiat * (1.0 if payload.fiat_currency == "EUR" else PLN_TO_USDC_RATE), 2)
    risk_score = 85 if payload.price_fiat < 2 else 20

    listing_id = str(uuid.uuid4())
    listing_doc = {
        "id": listing_id,
        "seller_id": user["id"],
        "title": payload.title,
        "description": payload.description,
        "price_fiat": payload.price_fiat,
        "fiat_currency": payload.fiat_currency,
        "crypto_amount": crypto_amount,
        "pricing_rate": 1.0 if payload.fiat_currency == "EUR" else PLN_TO_USDC_RATE,
        "pricing_rate_locked_at": now_utc(),
        "crypto_token": "USDC",
        "crypto_network": DEFAULT_NETWORK,
        "category": category_slug,
        "category_label": category_obj["name"],
        "category_icon": category_obj["icon"],
        "category_color": category_obj["color"],
        "condition": payload.condition,
        "location_public": payload.location_public,
        "shipping_options": payload.shipping_options,
        "images": payload.images,
        "status": "DRAFT",
        "moderation_status": "PENDING",
        "risk_score": risk_score,
        "listing_fee": {
            "amount": listing_fee_rule["amount"],
            "token": listing_fee_rule["token"],
            "network": listing_fee_rule["network"],
            "receiver_wallet": fee_wallet["wallet_address"],
            "accepted_receiver_wallets": [fee_wallet["wallet_address"]],
            "payment_reference": "0x" + hashlib.sha256(f"listing_fee:{listing_id}".encode("utf-8")).hexdigest(),
            "payment_router_contract": PAYMENT_ROUTER_CONTRACTS.get(listing_fee_rule["network"], ""),
            "status": "AWAITING_PAYMENT",
        },
        "promotion": {
            "is_promoted": False,
            "package_type": None,
            "package_label": None,
            "amount_usdc": 0,
            "network": None,
            "tx_hash": None,
            "starts_at": None,
            "ends_at": None,
        },
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.listings.insert_one(listing_doc)
    return clean_mongo_doc(listing_doc)


@api_router.patch("/listings/{listing_id}")
async def listing_update(
    listing_id: str,
    payload: ListingUpdateInput,
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if listing.get("status") in {"RESERVED", "DELETED"}:
        raise HTTPException(status_code=409, detail="Tej oferty nie można teraz edytować")
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        return listing
    if "category" in updates:
        next_slug = normalize_slug(str(updates["category"]))
        category_obj = await db.categories.find_one(
            {"slug": next_slug, "is_active": True},
            {"_id": 0},
        )
        if not category_obj:
            raise HTTPException(status_code=400, detail="Nieobsługiwana lub nieaktywna kategoria")
        updates["category"] = next_slug
        updates["category_label"] = category_obj["name"]
        updates["category_icon"] = category_obj["icon"]
        updates["category_color"] = category_obj["color"]
        if next_slug != listing.get("category"):
            next_fee_rule = await get_listing_fee_rule(next_slug)
            fee_wallet = await db.platform_fee_wallets.find_one(
                {
                    "network": next_fee_rule["network"],
                    "token": next_fee_rule["token"],
                    "is_active": True,
                },
                {"_id": 0},
            )
            if not fee_wallet:
                raise HTTPException(status_code=503, detail="Brak aktywnego portfela opłat dla nowej kategorii")
            payment_nonce = str(uuid.uuid4())
            updates["listing_fee"] = {
                "amount": next_fee_rule["amount"],
                "token": next_fee_rule["token"],
                "network": next_fee_rule["network"],
                "receiver_wallet": fee_wallet["wallet_address"],
                "accepted_receiver_wallets": [fee_wallet["wallet_address"]],
                "payment_reference": "0x" + hashlib.sha256(
                    f"listing_fee:{listing_id}:{payment_nonce}".encode("utf-8")
                ).hexdigest(),
                "payment_router_contract": PAYMENT_ROUTER_CONTRACTS.get(next_fee_rule["network"], ""),
                "status": "AWAITING_PAYMENT",
            }
    if "price_fiat" in updates:
        currency = listing.get("fiat_currency", "EUR")
        pricing_rate = 1.0 if currency == "EUR" else PLN_TO_USDC_RATE
        updates["crypto_amount"] = round(
            float(updates["price_fiat"]) * pricing_rate,
            2,
        )
        updates["pricing_rate"] = pricing_rate
        updates["pricing_rate_locked_at"] = now_utc()
    content_fields = {
        "title",
        "description",
        "price_fiat",
        "category",
        "condition",
        "location_public",
        "shipping_options",
    }
    if listing.get("status") == "ACTIVE" and content_fields.intersection(updates):
        updates["status"] = "UNDER_REVIEW"
        updates["moderation_status"] = "PENDING"
    updates["updated_at"] = now_utc()
    result = await db.listings.update_one(
        {
            "id": listing_id,
            "seller_id": user["id"],
            "status": {"$nin": ["RESERVED", "SOLD", "DELETING", "DELETED"]},
        },
        {"$set": updates},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Stan oferty zmienił się")
    if "listing_fee" in updates:
        await db.listing_fees.delete_one({"listing_id": listing_id})
    updated = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return updated


@api_router.delete("/listings/{listing_id}")
async def listing_delete(listing_id: str, user: dict = Depends(get_current_user)):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if listing.get("status") == "DELETED":
        return {"message": "Oferta usunięta"}
    if listing.get("status") in {"RESERVED", "SOLD"}:
        raise HTTPException(
            status_code=409,
            detail="Nie można usunąć oferty powiązanej z transakcją",
        )

    deleting = await db.listings.find_one_and_update(
        {"id": listing_id, "status": {"$nin": ["RESERVED", "SOLD", "DELETED"]}},
        {"$set": {"status": "DELETING", "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not deleting:
        raise HTTPException(status_code=409, detail="Stan oferty zmienił się")

    await delete_listing_media(listing_id)
    await db.listings.update_one(
        {"id": listing_id, "status": "DELETING"},
        {"$set": {"status": "DELETED", "images": [], "updated_at": now_utc()}},
    )
    return {"message": "Oferta usunięta"}


@api_router.post("/listings/{listing_id}/pay-listing-fee")
async def listing_pay_fee(
    listing_id: str,
    payload: PayListingFeeInput,
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    existing_fee = await db.listing_fees.find_one({"listing_id": listing_id}, {"_id": 0})
    if existing_fee:
        if existing_fee.get("payment_tx_hash", "").lower() != payload.payment_tx_hash.lower():
            raise HTTPException(status_code=409, detail="Opłata została już potwierdzona inną transakcją")
        if (
            listing.get("status") == "DRAFT"
            and listing.get("listing_fee", {}).get("status") == "AWAITING_PAYMENT"
        ):
            await activate_listing_after_fee(listing)
        updated_listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
        return {
            "message": "Opłata listingowa potwierdzona",
            "tx_hash": existing_fee["payment_tx_hash"],
            "verification_mode": existing_fee.get("verification_mode"),
            "listing": updated_listing,
        }
    if listing.get("listing_fee", {}).get("status") == "PAID":
        raise HTTPException(status_code=409, detail="Opłata została już potwierdzona")

    expected_fee = listing["listing_fee"]
    if payload.token != expected_fee["token"] or payload.network != expected_fee["network"]:
        raise HTTPException(status_code=400, detail="Błędny token lub sieć")
    if round(payload.amount, 6) != round(float(expected_fee["amount"]), 6):
        raise HTTPException(status_code=400, detail="Kwota opłaty jest niezgodna")

    receiver_wallet = expected_fee.get("receiver_wallet")
    if not receiver_wallet:
        raise HTTPException(status_code=503, detail="Oferta nie ma przypisanego portfela opłaty")

    await claim_tx_hash(
        payload.payment_tx_hash,
        payload.network,
        "listing_fee",
        listing_id,
        user["id"],
    )
    try:
        verification_mode, verification_meta = await run_blocking(
            verify_payment_transfer_to_any,
            payload.network,
            payload.payment_tx_hash,
            expected_fee.get("accepted_receiver_wallets") or [receiver_wallet],
            float(expected_fee["amount"]),
        )
        router = expected_fee.get("payment_router_contract")
        payment_reference = expected_fee.get("payment_reference")
        if verification_mode == "ONCHAIN_ALCHEMY" and router and payment_reference:
            await run_blocking(
                verify_indexed_contract_event,
                payload.network,
                payload.payment_tx_hash,
                router,
                PLATFORM_PAYMENT_TOPIC,
                payment_reference,
            )
    except Exception:
        await release_tx_hash_claim(payload.payment_tx_hash, payload.network)
        raise

    fee_doc = {
        "id": str(uuid.uuid4()),
        "listing_id": listing_id,
        "seller_id": user["id"],
        "amount": expected_fee["amount"],
        "token": payload.token,
        "network": payload.network,
        "payment_tx_hash": payload.payment_tx_hash,
        "status": "CONFIRMED",
        "verification_mode": verification_mode,
        "verification_meta": verification_meta,
        "created_at": now_utc(),
    }
    await db.listing_fees.update_one(
        {"listing_id": listing_id},
        {"$setOnInsert": fee_doc},
        upsert=True,
    )

    activated = await activate_listing_after_fee(listing)
    if not activated:
        raise HTTPException(status_code=409, detail="Stan oferty zmienił się podczas potwierdzania opłaty")
    updated_listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return {
        "message": "Opłata listingowa potwierdzona",
        "tx_hash": payload.payment_tx_hash,
        "verification_mode": verification_mode,
        "listing": updated_listing,
    }


@api_router.post("/listings/{listing_id}/report")
async def listing_report(
    listing_id: str,
    payload: ReportInput,
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")

    report_doc = {
        "id": str(uuid.uuid4()),
        "reporter_id": user["id"],
        "target_type": "listing",
        "target_id": listing_id,
        "reason": payload.reason,
        "details": payload.details,
        "evidence_status": "none",
        "status": "OPEN",
        "created_at": now_utc(),
    }
    await db.reports.insert_one(report_doc)
    return {"message": "Zgłoszenie przyjęte", "report_id": report_doc["id"]}


@api_router.post("/listings/{listing_id}/promote-intent")
async def listing_promote_intent(
    listing_id: str,
    payload: PromoteIntentInput,
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if listing.get("status") not in {"ACTIVE", "UNDER_REVIEW"}:
        raise HTTPException(status_code=400, detail="Oferta musi być aktywna lub w moderacji")

    package = PROMOTION_PACKAGES.get(payload.package_type)
    if not package:
        raise HTTPException(status_code=400, detail="Nieznany pakiet")

    platform_wallet = await db.platform_fee_wallets.find_one(
        {
            "network": payload.network,
            "token": payload.token,
            "is_active": True,
        },
        {"_id": 0},
    )
    if not platform_wallet:
        raise HTTPException(status_code=400, detail="Brak aktywnego walleta platformy")

    intent = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "listing_id": listing_id,
        "amount": package["amount_usdc"],
        "token": payload.token,
        "network": payload.network,
        "purpose": "promotion",
        "target_id": listing_id,
        "package_type": payload.package_type,
        "package_label": package["label"],
        "duration_hours": package["duration_hours"],
        "receiver_wallet": platform_wallet["wallet_address"],
        "accepted_receiver_wallets": [platform_wallet["wallet_address"]],
        "payer_wallet": primary_wallet(user),
        "payment_router_contract": PAYMENT_ROUTER_CONTRACTS.get(payload.network, ""),
        "payment_event_topic": PLATFORM_PAYMENT_TOPIC,
        "status": "PENDING",
        "verification_mode": "ONCHAIN_ALCHEMY" if is_onchain_indexer_ready(payload.network) else "UNAVAILABLE",
        "created_at": now_utc(),
        "expires_at": now_utc() + timedelta(minutes=15),
    }
    intent["payment_reference"] = "0x" + hashlib.sha256(
        f"promotion:{intent['id']}:{listing_id}".encode("utf-8")
    ).hexdigest()
    await db.payment_intents.insert_one(intent)
    return clean_mongo_doc(intent)


@api_router.post("/listings/{listing_id}/promote-confirm")
async def listing_promote_confirm(
    listing_id: str,
    payload: PromoteConfirmInput,
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")

    intent = await db.payment_intents.find_one(
        {
            "id": payload.intent_id,
            "listing_id": listing_id,
            "user_id": user["id"],
            "purpose": "promotion",
        },
        {"_id": 0},
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent promocji nie istnieje")
    if intent.get("status") == "CONFIRMED":
        starts_at = normalize_datetime_for_compare(intent.get("promotion_starts_at"))
        ends_at = normalize_datetime_for_compare(intent.get("promotion_ends_at"))
        current_promotion_tx = (listing.get("promotion") or {}).get("tx_hash")
        if starts_at and ends_at and current_promotion_tx in {None, intent.get("tx_hash")}:
            await activate_listing_promotion(
                listing_id=listing_id,
                package_type=intent["package_type"],
                amount_usdc=float(intent["amount"]),
                tx_hash=intent["tx_hash"],
                network=intent["network"],
                starts_at=starts_at,
                ends_at=ends_at,
            )
        updated_listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
        return {
            "message": "Promocja już aktywna",
            "intent": intent,
            "listing": updated_listing,
        }
    expires_at = normalize_datetime_for_compare(intent.get("expires_at"))
    if expires_at and expires_at < now_utc():
        await db.payment_intents.update_one(
            {"id": intent["id"], "status": "PENDING"},
            {"$set": {"status": "EXPIRED", "expired_at": now_utc()}},
        )
        raise HTTPException(status_code=410, detail="Intent promocji wygasł")
    if intent.get("status") != "PENDING":
        raise HTTPException(status_code=409, detail="Intent promocji nie oczekuje na potwierdzenie")

    await claim_tx_hash(payload.tx_hash, intent["network"], "promotion", listing_id, user["id"])
    try:
        verification_mode, verification_meta = await run_blocking(
            verify_payment_transfer_to_any,
            intent["network"],
            payload.tx_hash,
            intent.get("accepted_receiver_wallets") or [intent["receiver_wallet"]],
            float(intent["amount"]),
        )
        if verification_mode == "ONCHAIN_ALCHEMY":
            await run_blocking(
                verify_indexed_contract_event,
                intent["network"],
                payload.tx_hash,
                intent["payment_router_contract"],
                PLATFORM_PAYMENT_TOPIC,
                intent["payment_reference"],
            )
    except Exception:
        await release_tx_hash_claim(payload.tx_hash, intent["network"])
        raise

    starts_at = now_utc()
    ends_at = starts_at + timedelta(hours=intent["duration_hours"])
    confirmed_intent = await db.payment_intents.find_one_and_update(
        {"id": intent["id"], "status": "PENDING"},
        {
            "$set": {
                "status": "CONFIRMED",
                "tx_hash": payload.tx_hash,
                "verified_at": now_utc(),
                "verification_mode": verification_mode,
                "verification_meta": verification_meta,
                "promotion_starts_at": starts_at,
                "promotion_ends_at": ends_at,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not confirmed_intent:
        updated_listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
        return {
            "message": "Promocja już aktywna",
            "intent": await db.payment_intents.find_one({"id": intent["id"]}, {"_id": 0}),
            "listing": updated_listing,
        }

    await db.listings.update_one(
        {"id": listing_id},
        {
            "$set": {
                "promotion": {
                    "is_promoted": True,
                    "package_type": intent["package_type"],
                    "package_label": PROMOTION_PACKAGES[intent["package_type"]]["label"],
                    "amount_usdc": float(intent["amount"]),
                    "network": intent["network"],
                    "tx_hash": payload.tx_hash,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                },
                "updated_at": now_utc(),
            }
        },
    )
    updated_listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return {
        "message": "Promowana oferta aktywna",
        "listing": updated_listing,
        "verification_mode": verification_mode,
    }


@api_router.post("/listings/{listing_id}/images/upload")
async def listing_upload_image(
    listing_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if listing.get("status") in {"RESERVED", "SOLD", "DELETING", "DELETED"}:
        raise HTTPException(status_code=409, detail="Nie można dodać zdjęcia w tym stanie oferty")
    image_count = await db.listing_images.count_documents({"listing_id": listing_id})
    if image_count >= 10:
        raise HTTPException(status_code=409, detail="Oferta może mieć maksymalnie 10 zdjęć")
    if not file.content_type or file.content_type not in UPLOAD_ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Nieobsługiwany typ pliku")

    chunks: List[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Plik zbyt duży (max 8MB)")
        chunks.append(chunk)
    raw_bytes = b"".join(chunks)
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Pusty plik")

    with tempfile.NamedTemporaryFile(delete=True, suffix=".upload") as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        clean, scan_status = run_av_scan_if_enabled(tmp.name)
    if not clean:
        raise HTTPException(status_code=400, detail=f"Upload blocked: {scan_status}")

    cleaned, thumb, ext = process_image_bytes(raw_bytes)
    image_id = str(uuid.uuid4())
    original_key = f"listings/{listing_id}/{image_id}/original.{ext}"
    thumb_key = f"listings/{listing_id}/{image_id}/thumb.{ext}"

    storage_provider = "local"
    original_signed = None
    thumb_signed = None
    if r2_client:
        upload_bytes_to_r2(original_key, cleaned, file.content_type)
        upload_bytes_to_r2(thumb_key, thumb, file.content_type)
        storage_provider = "r2"
        original_signed = signed_r2_get_url(original_key)
        thumb_signed = signed_r2_get_url(thumb_key)
    else:
        original_path = LOCAL_UPLOAD_DIR / original_key
        thumb_path = LOCAL_UPLOAD_DIR / thumb_key
        original_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(cleaned)
        thumb_path.write_bytes(thumb)
        original_signed = signed_local_get_url(request, original_key)
        thumb_signed = signed_local_get_url(request, thumb_key)

    image_doc = {
        "id": image_id,
        "listing_id": listing_id,
        "original_key": original_key,
        "thumb_key": thumb_key,
        "metadata_removed": True,
        "scan_status": scan_status,
        "storage_provider": storage_provider,
        "created_at": now_utc(),
    }
    await db.listing_images.insert_one(image_doc)
    await db.listings.update_one(
        {"id": listing_id},
        {
            "$push": {
                "images": {
                    "$each": [
                        {
                            "type": "processed",
                            "image_id": image_id,
                            "thumb_signed_url": thumb_signed,
                            "original_signed_url": original_signed,
                        }
                    ]
                }
            },
            "$set": {"updated_at": now_utc()},
        },
    )

    return {
        "message": "Zdjęcie przetworzone i zapisane",
        "image_id": image_id,
        "scan_status": scan_status,
        "storage_provider": storage_provider,
        "original_signed_url": original_signed,
        "thumb_signed_url": thumb_signed,
    }


@api_router.post("/transactions")
async def transaction_create(payload: TransactionCreateInput, user: dict = Depends(get_current_user)):
    buyer_wallets = user.get("wallets", [])
    if not buyer_wallets and not payments_may_be_simulated():
        raise HTTPException(status_code=409, detail="Przypnij portfel do konta przed zakupem on-chain")
    listing = await db.listings.find_one_and_update(
        {"id": payload.listing_id, "status": "ACTIVE", "seller_id": {"$ne": user["id"]}},
        {"$set": {"status": "RESERVED", "updated_at": now_utc()}},
        projection={"_id": 0},
        return_document=ReturnDocument.BEFORE,
    )
    if not listing:
        existing = await db.listings.find_one({"id": payload.listing_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Oferta nie istnieje")
        raise HTTPException(status_code=409, detail="Oferta jest nieaktywna lub zarezerwowana")

    fee_rule = await get_sales_fee_rule(listing["category"])
    gross_amount = float(listing["crypto_amount"])
    fee_percent = float(fee_rule["fee_percent"])
    amount_raw = int((Decimal(str(gross_amount)) * Decimal(10**6)).to_integral_value())
    fee_bps = round(fee_percent * 100)
    fee_amount_raw = amount_raw * fee_bps // 10_000
    fee_amount = float(Decimal(fee_amount_raw) / Decimal(10**6))
    seller_amount = float(Decimal(amount_raw - fee_amount_raw) / Decimal(10**6))
    fee_wallet = await db.platform_fee_wallets.find_one(
        {"network": listing["crypto_network"], "token": listing["crypto_token"], "is_active": True},
        {"_id": 0, "wallet_address": 1},
    )
    transaction_id = str(uuid.uuid4())
    escrow_reference = "0x" + hashlib.sha256(transaction_id.encode("utf-8")).hexdigest()
    seller = await db.users.find_one({"id": listing["seller_id"]}, {"_id": 0, "wallets": 1})
    seller_wallet = primary_wallet(seller or {})
    if not seller_wallet and not payments_may_be_simulated():
        await db.listings.update_one(
            {"id": listing["id"], "status": "RESERVED"},
            {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
        )
        raise HTTPException(status_code=409, detail="Sprzedający musi przypiąć portfel przed zakupem on-chain")

    txn_doc = {
        "id": transaction_id,
        "buyer_id": user["id"],
        "seller_id": listing["seller_id"],
        "listing_id": listing["id"],
        "status": "AWAITING_PAYMENT",
        "escrow_status": "CREATED",
        "shipping_status": "AWAITING_SHIPMENT",
        "dispute_status": "NONE",
        "gross_amount": gross_amount,
        "fee_percent": fee_percent,
        "fee_amount": fee_amount,
        "seller_amount": seller_amount,
        "fee_wallet": (fee_wallet or {}).get("wallet_address"),
        "token": listing["crypto_token"],
        "network": listing["crypto_network"],
        "buyer_alias": random_alias(),
        "seller_alias": random_alias(),
        "deal_room_id": str(uuid.uuid4()),
        "escrow_receiver": ESCROW_CONTRACTS.get(listing["crypto_network"], "") or (
            "TEST_SIMULATED_ESCROW" if payments_may_be_simulated() else ""
        ),
        "escrow_reference": escrow_reference,
        "seller_wallet": seller_wallet,
        "buyer_wallet": primary_wallet(user),
        "created_at": now_utc(),
        "reservation_expires_at": now_utc() + timedelta(minutes=RESERVATION_TTL_MINUTES),
        "updated_at": now_utc(),
    }
    escrow_address = txn_doc["escrow_receiver"]
    if not escrow_address and not payments_may_be_simulated():
        await db.listings.update_one(
            {"id": listing["id"], "status": "RESERVED"},
            {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
        )
        raise HTTPException(status_code=503, detail="Escrow nie jest skonfigurowane")

    try:
        await db.transactions.insert_one(txn_doc)
    except Exception:
        await db.listings.update_one(
            {"id": listing["id"], "status": "RESERVED"},
            {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
        )
        raise

    escrow_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": txn_doc["id"],
        "smart_contract_address": escrow_address or "TEST_SIMULATED_ESCROW",
        "order_hash": escrow_reference,
        "reference": escrow_reference,
        "tx_hash": None,
        "status": "CREATED",
        "amount": gross_amount,
        "token": txn_doc["token"],
        "network": txn_doc["network"],
        "created_at": now_utc(),
    }
    try:
        await db.escrow_orders.insert_one(escrow_doc)
    except Exception:
        await db.transactions.delete_one({"id": txn_doc["id"], "status": "AWAITING_PAYMENT"})
        await db.listings.update_one(
            {"id": listing["id"], "status": "RESERVED"},
            {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
        )
        raise

    return clean_mongo_doc(txn_doc)


@api_router.get("/transactions")
async def transactions_list(
    limit: int = Query(default=100, ge=1, le=API_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    filters: Dict[str, Any] = {"$or": [{"buyer_id": user["id"]}, {"seller_id": user["id"]}]}
    if user.get("role") == "admin":
        filters = {}
    txs = await db.transactions.find(filters, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    for tx in txs:
        tx["status_summary"] = tx_status_summary(tx["status"])
    return txs


@api_router.get("/transactions/{transaction_id}")
async def transaction_detail(transaction_id: str, user: dict = Depends(get_current_user)):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

    is_participant = user["id"] in {tx["buyer_id"], tx["seller_id"]}
    if not is_participant and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")

    tx = await reconcile_transaction_onchain(tx)
    listing = await db.listings.find_one({"id": tx["listing_id"]}, {"_id": 0})
    tx["listing"] = listing
    tx["status_summary"] = tx_status_summary(tx["status"])
    return tx


@api_router.post("/transactions/{transaction_id}/fund")
async def transaction_fund(
    transaction_id: str,
    payload: FundInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if tx["buyer_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Tylko kupujący może zasilić escrow")
    if tx.get("status") == "FUNDED" and tx.get("fund_tx_hash") == payload.tx_hash:
        await db.escrow_orders.update_one(
            {"transaction_id": transaction_id},
            {"$set": {"tx_hash": payload.tx_hash, "status": "FUNDED"}},
        )
        return tx
    if tx.get("status") != "AWAITING_PAYMENT":
        raise HTTPException(status_code=409, detail="Transakcja nie oczekuje na płatność")
    if not payments_may_be_simulated() and not ETH_ADDRESS_RE.fullmatch(tx.get("escrow_receiver", "")):
        raise HTTPException(
            status_code=503,
            detail="Escrow nie jest poprawnie skonfigurowane dla aktywnej sieci",
        )

    if payload.token != tx["token"] or payload.network != tx["network"]:
        raise HTTPException(status_code=400, detail="Błędna sieć lub token")
    if round(payload.amount, 2) != round(float(tx["gross_amount"]), 2):
        raise HTTPException(status_code=400, detail="Kwota niezgodna z transakcją")

    escrow = await db.escrow_orders.find_one({"transaction_id": transaction_id}, {"_id": 0})
    if not escrow:
        raise HTTPException(status_code=503, detail="Brak zlecenia escrow")

    await claim_tx_hash(payload.tx_hash, payload.network, "escrow_funding", transaction_id, user["id"])
    try:
        verification_mode, verification_meta = await run_blocking(
            verify_payment_transfer,
            payload.network,
            payload.tx_hash,
            escrow["smart_contract_address"],
            float(tx["gross_amount"]),
        )
        if verification_mode == "ONCHAIN_ALCHEMY":
            await run_blocking(
                verify_indexed_contract_event,
                payload.network,
                payload.tx_hash,
                escrow["smart_contract_address"],
                ORDER_FUNDED_TOPIC,
                tx["escrow_reference"],
            )
            order = await run_blocking(
                read_escrow_order,
                payload.network,
                escrow["smart_contract_address"],
                tx["escrow_reference"],
            )
            buyer_wallets = [item.get("address", "") for item in user.get("wallets", [])]
            validate_escrow_order_terms(tx, order, buyer_wallets)
            if order["status"] not in {"FUNDED", "SHIPPED", "DISPUTED", "COMPLETED", "REFUNDED"}:
                raise HTTPException(status_code=409, detail=f"Escrow nie jest zasilone: {order['status']}")
            verification_meta["escrow_order"] = order
    except Exception:
        await release_tx_hash_claim(payload.tx_hash, payload.network)
        raise

    updated = await db.transactions.find_one_and_update(
        {"id": transaction_id, "status": "AWAITING_PAYMENT"},
        {
            "$set": {
                "status": "FUNDED",
                "escrow_status": "FUNDED",
                "shipping_status": "AWAITING_SHIPMENT",
                "fund_tx_hash": payload.tx_hash,
                "fund_verification_mode": verification_mode,
                "fund_verification_meta": verification_meta,
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        await release_tx_hash_claim(payload.tx_hash, payload.network)
        raise HTTPException(status_code=409, detail="Stan transakcji zmienił się")
    await db.escrow_orders.update_one(
        {"transaction_id": transaction_id},
        {
            "$set": {
                "tx_hash": payload.tx_hash,
                "status": "FUNDED",
                "verification_mode": verification_mode,
                "verification_meta": verification_meta,
            }
        },
    )
    return updated


@api_router.post("/transactions/{transaction_id}/mark-shipped")
async def transaction_mark_shipped(
    transaction_id: str,
    payload: MarkShippedInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if tx["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Tylko sprzedający może oznaczyć wysyłkę")
    if tx["status"] not in {"FUNDED", "SHIPPED"}:
        raise HTTPException(status_code=400, detail="Najpierw wymagane finansowanie escrow")
    await run_blocking(verify_escrow_action, tx, payload.onchain_tx_hash, ORDER_SHIPPED_TOPIC, {"SHIPPED"})

    label_token = payload.label_token or f"LBL-{str(uuid.uuid4()).split('-')[0].upper()}"
    shipping_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": transaction_id,
        "encrypted_address": payload.encrypted_address_blob,
        "label_token": label_token,
        "carrier": payload.carrier,
        "status": "SHIPPED",
        "created_at": now_utc(),
    }
    updated = await db.transactions.find_one_and_update(
        {"id": transaction_id, "status": {"$in": ["FUNDED", "SHIPPED"]}},
        {
            "$set": {
                "status": "SHIPPED",
                "shipping_status": "SHIPPED",
                "escrow_status": "FUNDED",
                "shipping_tx_hash": payload.onchain_tx_hash,
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Stan transakcji zmienił się")
    await db.shipping_labels.update_one(
        {"transaction_id": transaction_id},
        {"$setOnInsert": shipping_doc},
        upsert=True,
    )
    updated["blind_delivery"] = {
        "label_token": label_token,
        "carrier": payload.carrier,
        "address_visible_to_seller": False,
    }
    return updated


@api_router.post("/transactions/{transaction_id}/confirm-delivery")
async def transaction_confirm_delivery(
    transaction_id: str,
    payload: Optional[OnchainActionInput] = None,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if tx["buyer_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Tylko kupujący może potwierdzić odbiór")
    if tx["status"] == "COMPLETED":
        await ensure_sale_fee_record(tx, payload.onchain_tx_hash if payload else tx.get("release_tx_hash"))
        return tx
    if tx["status"] != "SHIPPED":
        raise HTTPException(status_code=400, detail="Nie można potwierdzić tego statusu")
    await run_blocking(
        verify_escrow_action,
        tx,
        payload.onchain_tx_hash if payload else None,
        ORDER_RELEASED_TOPIC,
        {"COMPLETED"},
    )

    released_onchain = not payments_may_be_simulated()
    updated = await db.transactions.find_one_and_update(
        {"id": transaction_id, "status": "SHIPPED"},
        {
            "$set": {
                "status": "COMPLETED" if released_onchain else "RELEASE_PENDING",
                "shipping_status": "DELIVERED",
                "escrow_status": "RELEASED" if released_onchain else "RELEASE_PENDING",
                "release_tx_hash": payload.onchain_tx_hash if payload else None,
                "updated_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Stan transakcji zmienił się")
    await ensure_sale_fee_record(updated, payload.onchain_tx_hash if payload else None)

    return updated


@api_router.post("/transactions/{transaction_id}/open-dispute")
async def transaction_open_dispute(
    transaction_id: str,
    payload: OpenDisputeInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]}:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if tx.get("status") not in {"FUNDED", "SHIPPED", "DISPUTED"}:
        raise HTTPException(status_code=409, detail="Nie można otworzyć sporu na tym etapie")
    await run_blocking(verify_escrow_action, tx, payload.onchain_tx_hash, DISPUTE_OPENED_TOPIC, {"DISPUTED"})
    existing_dispute = await db.disputes.find_one(
        {"transaction_id": transaction_id, "status": "OPEN"}, {"_id": 0, "id": 1}
    )
    if existing_dispute:
        await db.disputes.update_one(
            {"id": existing_dispute["id"]},
            {
                "$set": {
                    "opened_by": user["id"],
                    "reason": payload.reason,
                    "onchain_tx_hash": payload.onchain_tx_hash,
                    "reconciled": False,
                }
            },
        )
        await db.transactions.update_one(
            {"id": transaction_id},
            {
                "$set": {
                    "status": "DISPUTED",
                    "dispute_status": "OPEN",
                    "escrow_status": "FROZEN",
                    "dispute_tx_hash": payload.onchain_tx_hash,
                    "updated_at": now_utc(),
                }
            },
        )
        return {"message": "Spór został otwarty", "dispute_id": existing_dispute["id"]}

    dispute_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": transaction_id,
        "opened_by": user["id"],
        "reason": payload.reason,
        "status": "OPEN",
        "resolution": None,
        "resolved_by_admin_id": None,
        "created_at": now_utc(),
        "resolved_at": None,
        "onchain_tx_hash": payload.onchain_tx_hash,
    }
    await db.disputes.insert_one(dispute_doc)
    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "DISPUTED",
                "dispute_status": "OPEN",
                "escrow_status": "FROZEN",
                "dispute_tx_hash": payload.onchain_tx_hash,
                "updated_at": now_utc(),
            }
        },
    )
    return {"message": "Spór został otwarty", "dispute_id": dispute_doc["id"]}


@api_router.post("/transactions/{transaction_id}/cancel")
async def transaction_cancel(transaction_id: str, user: dict = Depends(get_current_user)):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]} and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if tx["status"] not in {"AWAITING_PAYMENT", "CREATED"}:
        raise HTTPException(status_code=400, detail="Nie można anulować na tym etapie")
    if not payments_may_be_simulated():
        order = await run_blocking(read_escrow_order, tx["network"], tx["escrow_receiver"], tx["escrow_reference"])
        if order["status"] != "NONE":
            validate_escrow_order_terms(tx, order)
        if order["status"] not in {"NONE", "CANCELLED"}:
            raise HTTPException(
                status_code=409,
                detail="Najpierw anuluj utworzone zlecenie w kontrakcie; zasilonego escrow nie można anulować",
            )

    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "CANCELLED",
                "escrow_status": "CANCELLED",
                "updated_at": now_utc(),
            }
        },
    )
    await db.listings.update_one(
        {"id": tx["listing_id"], "status": "RESERVED"},
        {"$set": {"status": "ACTIVE", "updated_at": now_utc()}},
    )
    return {"message": "Transakcja anulowana"}


@api_router.get("/transactions/{transaction_id}/messages")
async def messages_list(
    transaction_id: str,
    limit: int = Query(default=100, ge=1, le=API_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]}:
        raise HTTPException(status_code=403, detail="Brak dostępu")

    messages = await db.encrypted_messages.find(
        {
            "transaction_id": transaction_id,
            "expires_at": {"$gt": now_utc()},
        },
        {"_id": 0},
    ).sort("created_at", -1).skip(offset).to_list(limit)
    messages.reverse()
    return messages


@api_router.get("/transactions/{transaction_id}/e2ee")
async def transaction_e2ee_context(
    transaction_id: str,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    participant_ids = {tx["buyer_id"], tx["seller_id"]}
    if user["id"] not in participant_ids:
        raise HTTPException(status_code=403, detail="Brak dostępu")

    participants = await db.users.find(
        {"id": {"$in": list(participant_ids)}},
        {"_id": 0, "id": 1, "e2ee_public_key": 1, "e2ee_fingerprint": 1},
    ).to_list(2)
    room = await db.e2ee_rooms.find_one({"transaction_id": transaction_id}, {"_id": 0})
    envelope = None
    if room:
        envelope = next(
            (
                item
                for item in room.get("envelopes", [])
                if item.get("recipient_id") == user["id"]
            ),
            None,
        )
    return {
        "transaction_id": transaction_id,
        "participants": participants,
        "room": {
            "key_id": room["key_id"],
            "encryption_version": room["encryption_version"],
            "envelope": envelope,
            "created_at": room["created_at"],
        }
        if room
        else None,
    }


@api_router.post("/transactions/{transaction_id}/e2ee/initialize")
async def transaction_e2ee_initialize(
    transaction_id: str,
    payload: E2EERoomInitializeInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    participant_ids = {tx["buyer_id"], tx["seller_id"]}
    if user["id"] not in participant_ids:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    sender_public_key = user.get("e2ee_public_key")
    if not sender_public_key:
        raise HTTPException(status_code=409, detail="Najpierw przypnij klucz E2EE konta")

    participants = await db.users.find(
        {"id": {"$in": list(participant_ids)}},
        {"_id": 0, "id": 1, "e2ee_public_key": 1},
    ).to_list(2)
    if len(participants) != 2 or any(not item.get("e2ee_public_key") for item in participants):
        raise HTTPException(
            status_code=409,
            detail="Obaj uczestnicy muszą najpierw otworzyć Deal Room i przypiąć klucz E2EE",
        )

    decode_base64_field(payload.key_id, 32, "key_id")
    recipient_ids = {item.recipient_id for item in payload.envelopes}
    if recipient_ids != participant_ids:
        raise HTTPException(status_code=400, detail="Koperty muszą obejmować obu uczestników")
    for envelope in payload.envelopes:
        if not hmac.compare_digest(envelope.sender_public_key, sender_public_key):
            raise HTTPException(status_code=400, detail="Niezgodny klucz nadawcy koperty")
        decode_base64_field(envelope.sender_public_key, 32, "sender_public_key")
        decode_base64_field(envelope.nonce, 24, "envelope_nonce")
        decode_base64_field(envelope.ciphertext, 48, "encrypted_room_key")

    room_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": transaction_id,
        "key_id": payload.key_id,
        "encryption_version": "nacl-box+secretbox-v1",
        "created_by": user["id"],
        "envelopes": [item.model_dump() for item in payload.envelopes],
        "created_at": now_utc(),
    }
    try:
        await db.e2ee_rooms.insert_one(room_doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Pokój E2EE został już zainicjalizowany") from exc
    return {
        "transaction_id": transaction_id,
        "key_id": room_doc["key_id"],
        "encryption_version": room_doc["encryption_version"],
        "created_at": room_doc["created_at"],
    }


@api_router.post("/transactions/{transaction_id}/messages")
async def messages_create(
    transaction_id: str,
    payload: MessageCreateInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]}:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    room = await db.e2ee_rooms.find_one({"transaction_id": transaction_id}, {"_id": 0})
    if not room:
        raise HTTPException(status_code=409, detail="Pokój E2EE nie został zainicjalizowany")
    if not hmac.compare_digest(payload.key_id, room["key_id"]):
        raise HTTPException(status_code=400, detail="Wiadomość używa niewłaściwego klucza pokoju")
    decode_base64_field(payload.key_id, 32, "key_id")
    decode_base64_field(payload.nonce, 24, "message_nonce")
    decode_base64_field(payload.ciphertext, None, "ciphertext")
    try:
        uuid.UUID(payload.client_message_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Niepoprawny client_message_id") from exc

    msg_doc = {
        "id": str(uuid.uuid4()),
        "client_message_id": payload.client_message_id,
        "transaction_id": transaction_id,
        "sender_id": user["id"],
        "ciphertext": payload.ciphertext,
        "nonce": payload.nonce,
        "key_id": payload.key_id,
        "encryption_version": payload.encryption_version,
        "message_type": payload.message_type,
        "created_at": now_utc(),
        "expires_at": now_utc()
        + timedelta(days=int(user.get("auto_delete_messages_days", payload.expires_in_days))),
    }
    try:
        await db.encrypted_messages.insert_one(msg_doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Wiadomość lub nonce zostały już użyte") from exc
    return clean_mongo_doc(msg_doc)


@api_router.post("/transactions/{transaction_id}/messages/report-evidence")
async def messages_report_evidence(
    transaction_id: str,
    payload: MessageEvidenceInput,
    user: dict = Depends(get_current_user),
):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]}:
        raise HTTPException(status_code=403, detail="Brak dostępu")

    report_doc = {
        "id": str(uuid.uuid4()),
        "reporter_id": user["id"],
        "target_type": "transaction_messages",
        "target_id": transaction_id,
        "reason": payload.dispute_reason,
        "details": "Zaszyfrowane wiadomości wskazane przez uczestnika; administrator nie posiada klucza",
        "evidence_status": "encrypted_only",
        "evidence_messages": await db.encrypted_messages.find(
            {
                "transaction_id": transaction_id,
                "id": {"$in": payload.selected_message_ids},
            },
            {"_id": 0},
        ).to_list(50),
        "status": "OPEN",
        "created_at": now_utc(),
    }
    await db.reports.insert_one(report_doc)
    return {"message": "Dowody przekazane", "report_id": report_doc["id"]}


@api_router.get("/crypto/networks")
async def crypto_networks():
    return [
        {
            "name": name,
            **ACTIVE_CHAIN_PROFILE[name],
            "environment": CHAIN_ENV,
            "status": "active" if ACTIVE_CHAIN_PROFILE[name]["usdc"] else "token_unconfigured",
            "escrow_contract": ESCROW_CONTRACTS.get(name, ""),
            "payment_router_contract": PAYMENT_ROUTER_CONTRACTS.get(name, ""),
            "min_confirmations": ONCHAIN_CONFIRMATIONS.get(name, ONCHAIN_MIN_CONFIRMATIONS),
        }
        for name in SUPPORTED_NETWORKS
    ]


@api_router.get("/crypto/tokens")
async def crypto_tokens():
    return [{"symbol": symbol, "status": "active"} for symbol in SUPPORTED_TOKENS]


@api_router.get("/crypto/rates")
async def crypto_rates():
    return {
        "base_currency": "EUR",
        "lock_seconds": 900,
        "source": "operator-configured",
        "rate_updated_at": FX_RATE_UPDATED_AT,
        "rates": {
            "USDC": 1.0,
            "EURC": 1.0,
            "PLN_to_USDC": PLN_TO_USDC_RATE,
            "EUR_to_USDC": 1.0,
        },
        "timestamp": now_utc(),
    }


@api_router.post("/crypto/payment-intent")
async def crypto_payment_intent(payload: PaymentIntentInput, user: dict = Depends(get_current_user)):
    if payload.token not in SUPPORTED_TOKENS:
        raise HTTPException(status_code=400, detail="Nieobsługiwany token")
    if payload.network not in SUPPORTED_NETWORKS:
        raise HTTPException(status_code=400, detail="Nieobsługiwana sieć")
    if payload.purpose == "escrow_funding":
        tx = await db.transactions.find_one(
            {"id": payload.target_id, "buyer_id": user["id"], "status": "AWAITING_PAYMENT"},
            {"_id": 0},
        )
        if not tx or round(payload.amount, 2) != round(float(tx["gross_amount"]), 2):
            raise HTTPException(status_code=400, detail="Niepoprawny cel lub kwota escrow")
        if payload.network != tx["network"] or payload.token != tx["token"]:
            raise HTTPException(status_code=400, detail="Błędna sieć lub token escrow")
        receiver_wallet = tx.get("escrow_receiver")
        payment_reference = tx.get("escrow_reference")
        payment_contract = tx.get("escrow_receiver")
        payment_event_topic = ORDER_FUNDED_TOPIC
    elif payload.purpose == "listing_fee":
        listing = await db.listings.find_one(
            {"id": payload.target_id, "seller_id": user["id"], "listing_fee.status": "AWAITING_PAYMENT"},
            {"_id": 0},
        )
        if not listing or round(payload.amount, 6) != round(float(listing["listing_fee"]["amount"]), 6):
            raise HTTPException(status_code=400, detail="Niepoprawny cel lub kwota opłaty")
        if payload.network != listing["listing_fee"]["network"] or (
            payload.token != listing["listing_fee"]["token"]
        ):
            raise HTTPException(status_code=400, detail="Błędna sieć lub token opłaty")
        receiver_wallet = listing["listing_fee"].get("receiver_wallet")
        payment_reference = listing["listing_fee"].get("payment_reference")
        payment_contract = listing["listing_fee"].get("payment_router_contract")
        payment_event_topic = PLATFORM_PAYMENT_TOPIC
    else:
        raise HTTPException(
            status_code=400,
            detail="Dla tej płatności użyj dedykowanego endpointu",
        )

    if not receiver_wallet:
        raise HTTPException(status_code=503, detail="Brak skonfigurowanego odbiorcy płatności")

    intent = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "amount": payload.amount,
        "token": payload.token,
        "network": payload.network,
        "purpose": payload.purpose,
        "target_id": payload.target_id,
        "receiver_wallet": receiver_wallet,
        "payer_wallet": primary_wallet(user),
        "payment_reference": payment_reference,
        "payment_router_contract": payment_contract,
        "payment_event_topic": payment_event_topic,
        "status": "PENDING",
        "verification_mode": "ONCHAIN_ALCHEMY" if is_onchain_indexer_ready(payload.network) else "UNAVAILABLE",
        "created_at": now_utc(),
        "expires_at": now_utc() + timedelta(minutes=15),
    }
    await db.payment_intents.insert_one(intent)
    return clean_mongo_doc(intent)


@api_router.post("/crypto/payment/{intent_id}/submit-tx")
async def crypto_submit_payment_tx(
    intent_id: str,
    payload: PaymentTxSubmitInput,
    user: dict = Depends(get_current_user),
):
    intent = await db.payment_intents.find_one(
        {"id": intent_id, "user_id": user["id"]},
        {"_id": 0},
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent nie istnieje")
    if intent.get("status") == "CONFIRMED":
        return {"message": "Płatność już potwierdzona", "intent": intent}
    raise HTTPException(
        status_code=400,
        detail="Potwierdź płatność przez dedykowany endpoint opłaty lub escrow",
    )


@api_router.get("/crypto/payment/{intent_id}/status")
async def crypto_payment_status(intent_id: str, user: dict = Depends(get_current_user)):
    intent = await db.payment_intents.find_one(
        {"id": intent_id, "user_id": user["id"]}, {"_id": 0}
    )
    if not intent:
        raise HTTPException(status_code=404, detail="Intent nie istnieje")
    return intent


@api_router.post("/crypto/webhooks/alchemy")
async def crypto_alchemy_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Alchemy-Signature", "")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing Alchemy signature")
    if not hmac_matches(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid Alchemy signature")

    payload = await request.json()
    activity = payload.get("event", {}).get("activity", [])
    if isinstance(activity, dict):
        activity = [activity]

    updates = 0
    for item in activity:
        tx_hash = item.get("hash") or item.get("transactionHash")
        if not tx_hash:
            continue
        to_addr = str(item.get("toAddress") or item.get("to") or "").lower()
        from_addr = str(item.get("fromAddress") or item.get("from") or "").lower()
        token_addr = str(item.get("rawContract", {}).get("address") or item.get("contractAddress") or "").lower()
        amount_raw = item.get("value")
        try:
            amount = float(amount_raw)
        except Exception:
            amount = 0.0

        network = normalize_network_label(item.get("network") or payload.get("network") or "")
        if not network:
            continue
        if token_addr != USDC_CONTRACTS.get(network, ""):
            continue

        candidates = await db.payment_intents.find(
            {
                "status": "PENDING",
                "network": network,
                "expires_at": {"$gte": now_utc()},
                "$or": [
                    {"receiver_wallet": {"$regex": f"^{re.escape(to_addr)}$", "$options": "i"}},
                    {"accepted_receiver_wallets": {"$regex": f"^{re.escape(to_addr)}$", "$options": "i"}},
                ],
                "payer_wallet": {"$regex": f"^{re.escape(from_addr)}$", "$options": "i"},
                "amount": {"$gte": amount - 0.000001, "$lte": amount + 0.000001},
            },
            {"_id": 0},
        ).to_list(20)
        pending = None
        for candidate in candidates:
            router = candidate.get("payment_router_contract")
            reference = candidate.get("payment_reference")
            if router and reference:
                try:
                    await run_blocking(
                        verify_indexed_contract_event,
                        network,
                        tx_hash,
                        router,
                        candidate.get("payment_event_topic", PLATFORM_PAYMENT_TOPIC),
                        reference,
                    )
                except HTTPException:
                    continue
                pending = candidate
                break
        if pending is None and len(candidates) == 1 and not candidates[0].get("payment_reference"):
            pending = candidates[0]
        if not pending:
            continue

        try:
            await claim_tx_hash(
                tx_hash,
                network,
                pending.get("purpose", "payment"),
                pending.get("target_id", pending["id"]),
                pending["user_id"],
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                continue
            raise

        verified_at = now_utc()
        intent_updates = {
            "status": "CONFIRMED",
            "tx_hash": tx_hash,
            "verified_at": verified_at,
            "verification_mode": "ONCHAIN_ALCHEMY_WEBHOOK",
            "webhook_metadata": {
                "from": from_addr,
                "to": to_addr,
                "token": token_addr,
                "amount": amount,
            },
        }
        if pending.get("purpose") == "promotion":
            intent_updates["promotion_starts_at"] = verified_at
            intent_updates["promotion_ends_at"] = verified_at + timedelta(
                hours=pending["duration_hours"]
            )
        confirmed = await db.payment_intents.find_one_and_update(
            {"id": pending["id"], "status": "PENDING"},
            {"$set": intent_updates},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if not confirmed:
            continue
        if pending.get("purpose") == "promotion":
            await activate_listing_promotion(
                listing_id=pending["listing_id"],
                package_type=pending["package_type"],
                amount_usdc=float(pending["amount"]),
                tx_hash=tx_hash,
                network=network,
                starts_at=intent_updates["promotion_starts_at"],
                ends_at=intent_updates["promotion_ends_at"],
            )
        updates += 1

    return {
        "status": "ok",
        "confirmed_intents": updates,
    }


@api_router.get("/admin/dashboard")
async def admin_dashboard(admin: dict = Depends(get_current_admin)):
    _ = admin
    users_count = await db.users.count_documents({"role": "user"})
    listings_count = await db.listings.count_documents({})
    tx_active = await db.transactions.count_documents({"status": {"$in": ["FUNDED", "SHIPPED"]}})
    disputes = await db.disputes.count_documents({"status": "OPEN"})
    reports = await db.reports.count_documents({"status": "OPEN"})
    suspicious = await db.users.count_documents({"risk_score": {"$gte": 70}})

    revenue_groups = await db.sale_fees.aggregate(
        [
            {"$match": {"status": "CONFIRMED_ONCHAIN"}},
            {
                "$group": {
                    "_id": {"network": "$network", "token": "$token"},
                    "amount": {"$sum": "$fee_amount"},
                    "transactions": {"$sum": 1},
                }
            },
            {"$sort": {"_id.network": 1, "_id.token": 1}},
        ]
    ).to_list(100)
    revenue_by_asset = [
        {
            "network": item["_id"].get("network", "unknown"),
            "token": item["_id"].get("token", "unknown"),
            "amount": round(float(item.get("amount", 0)), 6),
            "transactions": item.get("transactions", 0),
        }
        for item in revenue_groups
    ]
    legacy_revenue = revenue_by_asset[0]["amount"] if len(revenue_by_asset) == 1 else None
    contract_status = contract_verification_status.get("status", "unknown")

    return {
        "users": users_count,
        "listings": listings_count,
        "active_transactions": tx_active,
        "open_disputes": disputes,
        "open_reports": reports,
        "suspicious_accounts": suspicious,
        "commission_revenue_crypto": legacy_revenue,
        "commission_revenue_by_asset": revenue_by_asset,
        "system_status": "healthy" if contract_status in {"healthy", "disabled"} else "degraded",
        "contract_verification": clean_mongo_doc(contract_verification_status),
    }


@api_router.get("/admin/users")
async def admin_users(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    users = await db.users.find(
        {"role": "user"},
        {
            "_id": 0,
            "id": 1,
            "email": 1,
            "display_alias": 1,
            "status": 1,
            "risk_score": 1,
            "trust_score": 1,
            "verification_level": 1,
            "created_at": 1,
        },
    ).sort("created_at", -1).skip(offset).to_list(limit)
    return users


@api_router.get("/admin/listings")
async def admin_listings(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    listings = await db.listings.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    return listings


@api_router.get("/admin/transactions")
async def admin_transactions(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    return await db.transactions.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)


@api_router.get("/admin/disputes")
async def admin_disputes(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    disputes = await db.disputes.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    transaction_ids = [item["transaction_id"] for item in disputes]
    transactions = await db.transactions.find(
        {"id": {"$in": transaction_ids}},
        {"_id": 0, "id": 1, "network": 1, "escrow_receiver": 1, "escrow_reference": 1},
    ).to_list(max(len(transaction_ids), 1))
    transaction_map = {item["id"]: item for item in transactions}
    for dispute in disputes:
        dispute["transaction"] = transaction_map.get(dispute["transaction_id"])
        if dispute["transaction"]:
            network = dispute["transaction"]["network"]
            dispute["transaction"]["min_confirmations"] = ONCHAIN_CONFIRMATIONS.get(network, ONCHAIN_MIN_CONFIRMATIONS)
            dispute["transaction"]["chain_id"] = ACTIVE_CHAIN_PROFILE[network]["chain_id"]
    return disputes


@api_router.get("/admin/reports")
async def admin_reports(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    reports = await db.reports.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    return reports


@api_router.get("/admin/fees")
async def admin_fees(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    listing_rules = await db.listing_fee_rules.find({"is_active": True}, {"_id": 0}).to_list(1000)
    sale_rules = await db.sale_fee_rules.find({"is_active": True}, {"_id": 0}).to_list(1000)
    fees_collected = await db.sale_fees.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    return {
        "listing_rules": listing_rules,
        "sale_rules": sale_rules,
        "fees_collected": fees_collected,
    }


@api_router.patch("/admin/fees/listing")
async def admin_patch_listing_fee(
    payload: ListingFeeRulePatchInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    old = await db.listing_fee_rules.find_one(
        {
            "category": payload.category,
            "account_type": payload.account_type,
            "is_active": True,
        },
        {"_id": 0},
    )
    doc = {
        "id": old["id"] if old else str(uuid.uuid4()),
        "category": payload.category,
        "account_type": payload.account_type,
        "amount": payload.amount,
        "token": payload.token,
        "network": payload.network,
        "is_active": True,
        "created_at": old["created_at"] if old else now_utc(),
        "updated_at": now_utc(),
    }
    await db.listing_fee_rules.update_one(
        {
            "category": payload.category,
            "account_type": payload.account_type,
            "is_active": True,
        },
        {"$set": doc},
        upsert=True,
    )
    await write_audit_log(
        admin_id=admin["id"],
        action="patch_listing_fee",
        target_type="listing_fee_rules",
        target_id=doc["id"],
        reason="Aktualizacja opłaty za wystawienie",
        old_value=old,
        new_value=doc,
        request=request,
    )
    return clean_mongo_doc(doc)


@api_router.patch("/admin/fees/sales")
async def admin_patch_sales_fee(
    payload: SalesFeeRulePatchInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    old = await db.sale_fee_rules.find_one(
        {
            "category": payload.category,
            "account_type": payload.account_type,
            "reputation_level": payload.reputation_level,
            "is_active": True,
        },
        {"_id": 0},
    )
    doc = {
        "id": old["id"] if old else str(uuid.uuid4()),
        "category": payload.category,
        "account_type": payload.account_type,
        "reputation_level": payload.reputation_level,
        "fee_percent": payload.fee_percent,
        "is_active": True,
        "created_at": old["created_at"] if old else now_utc(),
        "updated_at": now_utc(),
    }
    await db.sale_fee_rules.update_one(
        {
            "category": payload.category,
            "account_type": payload.account_type,
            "reputation_level": payload.reputation_level,
            "is_active": True,
        },
        {"$set": doc},
        upsert=True,
    )
    await write_audit_log(
        admin_id=admin["id"],
        action="patch_sales_fee",
        target_type="sale_fee_rules",
        target_id=doc["id"],
        reason="Aktualizacja prowizji sprzedażowej",
        old_value=old,
        new_value=doc,
        request=request,
    )
    return clean_mongo_doc(doc)


@api_router.get("/admin/platform-wallets")
async def admin_platform_wallets(admin: dict = Depends(get_current_admin)):
    _ = admin
    wallets = await db.platform_fee_wallets.find({}, {"_id": 0}).to_list(1000)
    return wallets


@api_router.get("/admin/categories")
async def admin_categories(admin: dict = Depends(get_current_admin)):
    _ = admin
    categories = await db.categories.find({}, {"_id": 0}).to_list(500)
    categories.sort(key=lambda c: (c.get("sort_order", 1000), c.get("name", "")))
    return categories


@api_router.post("/admin/categories")
async def admin_create_category(
    payload: CategoryCreateInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    slug = normalize_slug(payload.slug or payload.name)
    if not slug:
        raise HTTPException(status_code=400, detail="Niepoprawny slug")
    if not ensure_hex_color(payload.color):
        raise HTTPException(status_code=400, detail="Kolor musi być w formacie #RRGGBB")

    existing = await category_exists_by_slug(slug)
    if existing:
        raise HTTPException(status_code=409, detail="Kategoria o tym slug już istnieje")

    category_doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "slug": slug,
        "icon": payload.icon.strip(),
        "color": payload.color.strip(),
        "sort_order": payload.sort_order,
        "is_active": True,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.categories.insert_one(category_doc)

    await write_audit_log(
        admin_id=admin["id"],
        action="create_category",
        target_type="category",
        target_id=category_doc["id"],
        reason="Dodanie kategorii",
        old_value=None,
        new_value=category_doc,
        request=request,
    )
    return clean_mongo_doc(category_doc)


@api_router.patch("/admin/categories/{category_id}")
async def admin_update_category(
    category_id: str,
    payload: CategoryUpdateInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    category = await db.categories.find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(status_code=404, detail="Kategoria nie istnieje")

    updates = payload.model_dump(exclude_none=True)
    if "color" in updates and not ensure_hex_color(updates["color"]):
        raise HTTPException(status_code=400, detail="Kolor musi być w formacie #RRGGBB")
    updates["updated_at"] = now_utc()

    await db.categories.update_one({"id": category_id}, {"$set": updates})
    updated = await db.categories.find_one({"id": category_id}, {"_id": 0})

    if "is_active" in updates:
        if updates["is_active"] is False:
            await hide_listings_for_category_slug(category["slug"])
        elif updates["is_active"] is True:
            await restore_listings_for_category_slug(category["slug"])

    await write_audit_log(
        admin_id=admin["id"],
        action="update_category",
        target_type="category",
        target_id=category_id,
        reason="Edycja kategorii",
        old_value=category,
        new_value=updated,
        request=request,
    )
    return updated


@api_router.delete("/admin/categories/{category_id}")
async def admin_delete_category(
    category_id: str,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    category = await db.categories.find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(status_code=404, detail="Kategoria nie istnieje")

    if category.get("slug") == "inne":
        raise HTTPException(status_code=400, detail="Nie można usunąć kategorii 'inne'")

    await hide_listings_for_category_slug(category["slug"])
    await db.categories.delete_one({"id": category_id})

    await write_audit_log(
        admin_id=admin["id"],
        action="delete_category",
        target_type="category",
        target_id=category_id,
        reason="Usunięcie kategorii",
        old_value=category,
        new_value=None,
        request=request,
    )
    return {"message": "Kategoria usunięta, oferty ukryte do decyzji admina"}


@api_router.post("/admin/categories/reorder")
async def admin_reorder_categories(
    payload: CategoryReorderInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Brak danych do reorder")

    ids = [item.id for item in payload.items]
    existing = await db.categories.find({"id": {"$in": ids}}, {"_id": 0}).to_list(500)
    if len(existing) != len(ids):
        raise HTTPException(status_code=400, detail="Niektóre kategorie nie istnieją")

    old_snapshot = [{"id": item["id"], "sort_order": item.get("sort_order", 1000)} for item in existing]
    for item in payload.items:
        await db.categories.update_one(
            {"id": item.id},
            {"$set": {"sort_order": item.sort_order, "updated_at": now_utc()}},
        )

    new_snapshot = await db.categories.find({"id": {"$in": ids}}, {"_id": 0}).to_list(500)
    await write_audit_log(
        admin_id=admin["id"],
        action="reorder_categories",
        target_type="category",
        target_id="bulk",
        reason="Zmiana kolejności kategorii",
        old_value=old_snapshot,
        new_value=[{"id": item["id"], "sort_order": item.get("sort_order", 1000)} for item in new_snapshot],
        request=request,
    )
    return {"message": "Kolejność kategorii zaktualizowana"}


@api_router.post("/admin/platform-wallets")
async def admin_add_platform_wallet(
    payload: PlatformWalletInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    if payload.network not in SUPPORTED_NETWORKS:
        raise HTTPException(status_code=400, detail="Sieć nieobsługiwana")
    if payload.token not in SUPPORTED_TOKENS:
        raise HTTPException(status_code=400, detail="Token nieobsługiwany")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", payload.wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy format adresu")

    doc = {
        "id": str(uuid.uuid4()),
        "network": payload.network,
        "token": payload.token,
        "wallet_address": payload.wallet_address,
        "risk_score": compute_wallet_risk(payload.wallet_address),
        "is_active": False,
        "pending_activation_at": now_utc() + timedelta(hours=24),
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "changed_by_admin_id": admin["id"],
    }
    await db.platform_fee_wallets.insert_one(doc)

    await write_audit_log(
        admin_id=admin["id"],
        action="create_platform_wallet",
        target_type="platform_wallet",
        target_id=doc["id"],
        reason="Nowy portfel prowizyjny",
        old_value=None,
        new_value=doc,
        request=request,
    )
    return clean_mongo_doc(doc)


@api_router.patch("/admin/platform-wallets/{wallet_id}")
async def admin_patch_platform_wallet(
    wallet_id: str,
    payload: PlatformWalletInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    existing = await db.platform_fee_wallets.find_one({"id": wallet_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Portfel nie istnieje")
    if payload.network not in SUPPORTED_NETWORKS or payload.token not in SUPPORTED_TOKENS:
        raise HTTPException(status_code=400, detail="Nieobsługiwana sieć lub token")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", payload.wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy format adresu")

    updated = {
        **existing,
        "pending_network": payload.network,
        "pending_token": payload.token,
        "pending_wallet_address": payload.wallet_address,
        "pending_risk_score": compute_wallet_risk(payload.wallet_address),
        "pending_activation_at": now_utc() + timedelta(hours=24),
        "updated_at": now_utc(),
        "changed_by_admin_id": admin["id"],
    }
    await db.platform_fee_wallets.update_one({"id": wallet_id}, {"$set": updated})

    await write_audit_log(
        admin_id=admin["id"],
        action="update_platform_wallet",
        target_type="platform_wallet",
        target_id=wallet_id,
        reason="Zmiana adresu portfela prowizyjnego",
        old_value=existing,
        new_value=updated,
        request=request,
    )
    return updated


@api_router.post("/admin/platform-wallets/{wallet_id}/activate")
async def admin_activate_platform_wallet(
    wallet_id: str,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    existing = await db.platform_fee_wallets.find_one({"id": wallet_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Portfel nie istnieje")
    if existing.get("is_active") and not existing.get("pending_activation_at"):
        return existing
    activation_at = normalize_datetime_for_compare(existing.get("pending_activation_at"))
    if not activation_at or activation_at > now_utc():
        raise HTTPException(status_code=409, detail="Okres bezpieczeństwa jeszcze nie minął")

    updated_fields = {
        "network": existing.get("pending_network", existing["network"]),
        "token": existing.get("pending_token", existing["token"]),
        "wallet_address": existing.get("pending_wallet_address", existing["wallet_address"]),
        "risk_score": existing.get("pending_risk_score", existing.get("risk_score", 0)),
        "is_active": True,
        "pending_network": None,
        "pending_token": None,
        "pending_wallet_address": None,
        "pending_risk_score": None,
        "pending_activation_at": None,
        "updated_at": now_utc(),
        "changed_by_admin_id": admin["id"],
    }
    if not payments_may_be_simulated():
        expected_wallet = updated_fields["wallet_address"].lower()
        router_wallet = await run_blocking(
            read_contract_address,
            updated_fields["network"],
            PAYMENT_ROUTER_CONTRACTS.get(updated_fields["network"], ""),
            "feeWallet()",
        )
        escrow_wallet = await run_blocking(
            read_contract_address,
            updated_fields["network"],
            ESCROW_CONTRACTS.get(updated_fields["network"], ""),
            "feeWallet()",
        )
        if router_wallet != expected_wallet or escrow_wallet != expected_wallet:
            raise HTTPException(
                status_code=409,
                detail="Najpierw ustaw nowy feeWallet w routerze płatności i escrow na blockchainie",
            )
    active_wallet = await db.platform_fee_wallets.find_one(
        {
            "network": updated_fields["network"],
            "token": updated_fields["token"],
            "is_active": True,
        },
        {"_id": 0},
    )
    accepted_wallets = list(
        dict.fromkeys(
            address
            for address in [
                (active_wallet or {}).get("wallet_address"),
                updated_fields["wallet_address"],
            ]
            if address
        )
    )
    await db.platform_fee_wallets.update_many(
        {
            "network": updated_fields["network"],
            "token": updated_fields["token"],
            "id": {"$ne": wallet_id},
        },
        {"$set": {"is_active": False, "updated_at": now_utc()}},
    )
    await db.listings.update_many(
        {
            "listing_fee.network": updated_fields["network"],
            "listing_fee.token": updated_fields["token"],
            "listing_fee.status": "AWAITING_PAYMENT",
        },
        {
            "$set": {
                "listing_fee.receiver_wallet": updated_fields["wallet_address"],
                "updated_at": now_utc(),
            },
            "$addToSet": {
                "listing_fee.accepted_receiver_wallets": {"$each": accepted_wallets},
            },
        },
    )
    await db.payment_intents.update_many(
        {
            "network": updated_fields["network"],
            "token": updated_fields["token"],
            "status": "PENDING",
        },
        {
            "$set": {
                "receiver_wallet": updated_fields["wallet_address"],
                "updated_at": now_utc(),
            },
            "$addToSet": {"accepted_receiver_wallets": {"$each": accepted_wallets}},
        },
    )
    await db.platform_fee_wallets.update_one({"id": wallet_id}, {"$set": updated_fields})
    await write_audit_log(
        admin_id=admin["id"],
        action="activate_platform_wallet",
        target_type="platform_wallet",
        target_id=wallet_id,
        reason="Aktywacja po okresie bezpieczeństwa",
        old_value=existing,
        new_value=updated_fields,
        request=request,
    )
    return await db.platform_fee_wallets.find_one({"id": wallet_id}, {"_id": 0})


@api_router.get("/admin/audit-logs")
async def admin_audit_logs(
    limit: int = Query(default=100, ge=1, le=ADMIN_PAGE_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin),
):
    _ = admin
    logs = await db.admin_audit_logs.find({}, {"_id": 0}).sort("created_at", -1).skip(offset).to_list(limit)
    return clean_mongo_doc(logs)


@api_router.post("/admin/disputes/{dispute_id}/resolve")
async def admin_resolve_dispute(
    dispute_id: str,
    payload: ResolveDisputeInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    dispute = await db.disputes.find_one({"id": dispute_id}, {"_id": 0})
    if not dispute:
        raise HTTPException(status_code=404, detail="Spór nie istnieje")

    tx = await db.transactions.find_one({"id": dispute["transaction_id"]}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

    release_seller = payload.decision == "release_seller"
    expected_event = ORDER_RELEASED_TOPIC if release_seller else ORDER_REFUNDED_TOPIC
    expected_status = {"COMPLETED"} if release_seller else {"REFUNDED"}
    await run_blocking(verify_escrow_action, tx, payload.onchain_tx_hash, expected_event, expected_status)

    tx_status = "COMPLETED" if release_seller else "REFUNDED"
    escrow_status = "RELEASED" if release_seller else "REFUNDED"
    resolution = {
        "decision": payload.decision,
        "reason": payload.reason,
        "onchain_tx_hash": payload.onchain_tx_hash,
    }
    existing_decision = (dispute.get("resolution") or {}).get("decision")
    if existing_decision and existing_decision != payload.decision:
        raise HTTPException(status_code=409, detail="Decyzja jest sprzeczna ze stanem blockchain")
    updated_dispute = await db.disputes.find_one_and_update(
        {"id": dispute_id},
        {
            "$set": {
                "status": "RESOLVED",
                "resolution": resolution,
                "resolved_by_admin_id": admin["id"],
                "resolved_at": now_utc(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    await db.transactions.update_one(
        {"id": tx["id"]},
        {
            "$set": {
                "status": tx_status,
                "escrow_status": escrow_status,
                "dispute_status": "RESOLVED",
                "resolution_tx_hash": payload.onchain_tx_hash,
                "updated_at": now_utc(),
            }
        },
    )
    if tx_status == "COMPLETED":
        await ensure_sale_fee_record(
            {**tx, "status": tx_status, "release_tx_hash": payload.onchain_tx_hash},
            payload.onchain_tx_hash,
        )

    await write_audit_log(
        admin_id=admin["id"],
        action="resolve_dispute",
        target_type="dispute",
        target_id=dispute_id,
        reason=payload.reason,
        old_value=dispute,
        new_value=resolution,
        request=request,
    )
    return updated_dispute


@api_router.post("/admin/users/{user_id}/ban")
async def admin_ban_user(
    user_id: str,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    victim = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not victim:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje")
    if victim.get("role") == "admin":
        raise HTTPException(status_code=400, detail="Nie można banować admina")

    await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": "BANNED", "updated_at": now_utc()}},
    )
    await db.sessions.update_many(
        {"user_id": user_id},
        {"$set": {"is_active": False, "last_seen": now_utc()}},
    )

    await write_audit_log(
        admin_id=admin["id"],
        action="ban_user",
        target_type="user",
        target_id=user_id,
        reason="Akcja moderacyjna",
        old_value={"status": victim.get("status")},
        new_value={"status": "BANNED"},
        request=request,
    )
    return {"message": "Użytkownik zbanowany"}


@api_router.post("/admin/listings/{listing_id}/moderate")
async def admin_moderate_listing(
    listing_id: str,
    payload: ModerateListingInput,
    request: Request,
    admin: dict = Depends(get_current_admin),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")

    status = listing.get("status", "DRAFT")
    moderation_status = listing.get("moderation_status", "PENDING")
    if payload.action == "approve":
        if listing.get("listing_fee", {}).get("status") != "PAID":
            raise HTTPException(status_code=409, detail="Oferta nie ma potwierdzonej opłaty")
        status = "ACTIVE"
        moderation_status = "APPROVED"
    elif payload.action == "reject":
        status = "REJECTED"
        moderation_status = "REJECTED"
    elif payload.action == "hide":
        status = "HIDDEN"
        moderation_status = "HIDDEN"

    await db.listings.update_one(
        {"id": listing_id},
        {
            "$set": {
                "status": status,
                "moderation_status": moderation_status,
                "moderation_reason": payload.reason,
                "updated_at": now_utc(),
            }
        },
    )

    await write_audit_log(
        admin_id=admin["id"],
        action="moderate_listing",
        target_type="listing",
        target_id=listing_id,
        reason=payload.reason,
        old_value={"status": listing.get("status")},
        new_value={"status": status},
        request=request,
    )
    updated = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return updated


app.include_router(api_router)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    if APP_ENV == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
