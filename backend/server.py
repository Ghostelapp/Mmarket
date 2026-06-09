import logging
import os
import re
import uuid
import json
import hmac
import hashlib
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
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, EmailStr, Field
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

SUPPORTED_NETWORKS = ["Base", "Polygon"]
SUPPORTED_TOKENS = ["USDC"]


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


ENABLE_ONCHAIN_INDEXER = env_bool("ENABLE_ONCHAIN_INDEXER", False)
ENABLE_R2_STORAGE = env_bool("ENABLE_R2_STORAGE", False)
ENABLE_AV_SCAN = env_bool("ENABLE_AV_SCAN", False)

ALCHEMY_API_KEY_BASE = os.environ.get("ALCHEMY_API_KEY_BASE", "")
ALCHEMY_API_KEY_POLYGON = os.environ.get("ALCHEMY_API_KEY_POLYGON", "")
ALCHEMY_WEBHOOK_SIGNING_KEY = os.environ.get("ALCHEMY_WEBHOOK_SIGNING_KEY", "")

USDC_CONTRACTS = {
    "Base": os.environ.get(
        "USDC_CONTRACT_BASE", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    ).lower(),
    "Polygon": os.environ.get(
        "USDC_CONTRACT_POLYGON", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
    ).lower(),
}

ALCHEMY_RPC_URLS = {
    "Base": f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY_BASE}",
    "Polygon": f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY_POLYGON}",
}

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

PROMOTION_PACKAGES = {
    "basic": {"amount_usdc": 1.0, "duration_hours": 24, "label": "Basic Boost"},
    "boost": {"amount_usdc": 3.0, "duration_hours": 72, "label": "Boost Premium"},
}

UPLOAD_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

r2_client = None
if ENABLE_R2_STORAGE and all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME]):
    r2_client = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )

DEFAULT_LISTING_FEES = {
    "elektronika": 1.0,
    "moda": 0.25,
    "dom": 0.35,
    "motoryzacja": 5.0,
    "sport": 0.5,
    "dziecko": 0.2,
    "kolekcje": 0.8,
    "usługi lokalne": 0.3,
    "produkty cyfrowe legalne": 0.4,
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


class RegisterInput(BaseModel):
    email: EmailStr
    password: str
    alias: str = Field(min_length=3, max_length=30)
    public_location: str = Field(default="Unknown")


class LoginInput(BaseModel):
    email: EmailStr
    password: str
    otp_code: Optional[str] = None
    device_name: str = "Mobile"


class RefreshInput(BaseModel):
    refresh_token: str
    session_id: str


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
    condition: str
    location_public: str
    shipping_options: List[str]
    images: List[str] = []


class ListingUpdateInput(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price_fiat: Optional[float] = Field(default=None, gt=0)
    condition: Optional[str] = None
    location_public: Optional[str] = None
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


class OpenDisputeInput(BaseModel):
    reason: str = Field(min_length=5, max_length=300)


class MessageCreateInput(BaseModel):
    ciphertext: str = Field(min_length=2)
    nonce: str
    message_type: Literal["text", "image", "file"] = "text"
    expires_in_days: int = Field(default=14, ge=1, le=90)


class MessageEvidenceInput(BaseModel):
    selected_messages: List[Dict[str, Any]]
    dispute_reason: str


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
    decision: Literal["refund_buyer", "release_seller", "partial_refund"]
    reason: str
    partial_seller_amount: Optional[float] = None


class ModerateListingInput(BaseModel):
    action: Literal["approve", "reject", "hide"]
    reason: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def random_alias() -> str:
    return f"{ALIAS_WORDS[uuid.uuid4().int % len(ALIAS_WORDS)]}-{str(uuid.uuid4().int)[-4:]}"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
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


def tx_status_summary(status: str) -> Dict[str, str]:
    if status == "COMPLETED":
        return {"badge": "completed", "label": "Zakończona"}
    if status == "DISPUTED":
        return {"badge": "warning", "label": "W sporze"}
    if status in {"FUNDED", "AWAITING_SHIPMENT", "SHIPPED"}:
        return {"badge": "progress", "label": "W toku"}
    return {"badge": "neutral", "label": status}


def clean_mongo_doc(doc: dict) -> dict:
    if "_id" in doc:
        doc.pop("_id", None)
    return doc


def compute_wallet_risk(wallet_address: str) -> int:
    lowered = wallet_address.lower()
    for suffix in SANCTIONED_WALLET_SUFFIXES:
        if lowered.endswith(suffix):
            return 95
    return 15


def is_onchain_indexer_ready() -> bool:
    return ENABLE_ONCHAIN_INDEXER and bool(ALCHEMY_API_KEY_BASE and ALCHEMY_API_KEY_POLYGON)


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
    if not url or url.endswith("/"):
        raise HTTPException(status_code=503, detail="Indexer on-chain nie jest skonfigurowany")

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        response = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Błąd połączenia z Alchemy") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Alchemy RPC error")

    data = response.json()
    if data.get("error"):
        raise HTTPException(status_code=400, detail=f"Alchemy: {data['error'].get('message', 'error')}")
    return data


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
        }

    return False, "Nie znaleziono poprawnego transferu USDC", {"receipt": receipt}


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
    challenge = await db.passkey_challenges.find_one(
        {
            "user_id": user_id,
            "purpose": purpose,
            "used": False,
            "expires_at": {"$gte": now_utc()},
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not challenge:
        return None
    await db.passkey_challenges.update_one(
        {"id": challenge["id"]},
        {"$set": {"used": True, "used_at": now_utc()}},
    )
    return challenge


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
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Niepoprawny format obrazu") from exc

    image_format = (image.format or "JPEG").upper()
    if image_format not in {"JPEG", "PNG", "WEBP"}:
        image_format = "JPEG"

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


def signed_r2_get_url(key: str, expires: int = 3600) -> Optional[str]:
    if not r2_client:
        return None
    return r2_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET_NAME, "Key": key},
        ExpiresIn=expires,
    )


def normalize_network_label(raw: str) -> Optional[str]:
    lower = str(raw).lower()
    if "base" in lower:
        return "Base"
    if "polygon" in lower:
        return "Polygon"
    return None


async def activate_listing_after_fee(listing: dict):
    moderation_status = "PENDING" if listing.get("risk_score", 0) > 70 else "APPROVED"
    status = "ACTIVE" if moderation_status == "APPROVED" else "UNDER_REVIEW"
    await db.listings.update_one(
        {"id": listing["id"]},
        {
            "$set": {
                "status": status,
                "moderation_status": moderation_status,
                "listing_fee.status": "PAID",
                "updated_at": now_utc(),
            }
        },
    )


async def activate_listing_promotion(
    listing_id: str,
    package_type: str,
    amount_usdc: float,
    tx_hash: str,
    network: str,
):
    package = PROMOTION_PACKAGES.get(package_type)
    if not package:
        raise HTTPException(status_code=400, detail="Nieznany pakiet promocji")

    starts = now_utc()
    ends = starts + timedelta(hours=package["duration_hours"])
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
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")
    user = await db.users.find_one({"id": payload.get("sub")}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
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
        "ip_hash": hash(request.client.host if request.client else "0.0.0.0"),
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
        "network": "Base",
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
        "ip": request.client.host if request.client else "0.0.0.0",
        "refresh_jti": refresh_jti,
        "is_active": True,
        "created_at": now_utc(),
        "last_seen": now_utc(),
    }
    await db.sessions.insert_one(session_doc)

    return {
        "access_token": create_access_token(user["id"], user.get("role", "user")),
        "refresh_token": create_refresh_token(user["id"], session_id, refresh_jti),
        "session_id": session_id,
        "token_type": "bearer",
    }


request_counters: Dict[str, Dict[str, Any]] = {}


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "0.0.0.0"
    path = request.url.path
    bucket = now_utc().strftime("%Y%m%d%H%M")
    key = f"{ip}:{bucket}:{'auth' if '/auth/' in path else 'general'}"
    limit = 40 if "/auth/" in path else 140

    entry = request_counters.get(key, {"count": 0, "bucket": bucket})
    entry["count"] += 1
    request_counters[key] = entry
    if entry["count"] > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return await call_next(request)


@app.on_event("startup")
async def startup_seed_data():
    admin_user = await db.users.find_one({"email": "admin@maskmarket.io"}, {"_id": 0})
    if not admin_user:
        admin_doc = {
            "id": str(uuid.uuid4()),
            "public_id": str(uuid.uuid4()),
            "email": "admin@maskmarket.io",
            "password_hash": hash_password("MaskAdmin!2026"),
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
        await db.users.insert_one(admin_doc)

    for category, amount in DEFAULT_LISTING_FEES.items():
        exists = await db.listing_fee_rules.find_one(
            {"category": category, "account_type": "default", "is_active": True},
            {"_id": 0},
        )
        if not exists:
            await db.listing_fee_rules.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "category": category,
                    "account_type": "default",
                    "amount": amount,
                    "token": "USDC",
                    "network": "Base",
                    "is_active": True,
                    "created_at": now_utc(),
                }
            )

    default_sale_rule = await db.sale_fee_rules.find_one(
        {"category": "default", "account_type": "default", "is_active": True},
        {"_id": 0},
    )
    if not default_sale_rule:
        await db.sale_fee_rules.insert_one(
            {
                "id": str(uuid.uuid4()),
                "category": "default",
                "account_type": "default",
                "reputation_level": "any",
                "fee_percent": 5.0,
                "is_active": True,
                "created_at": now_utc(),
            }
        )

    for network in ["Base", "Polygon"]:
        wallet_exists = await db.platform_fee_wallets.find_one(
            {"network": network, "token": "USDC", "is_active": True}, {"_id": 0}
        )
        if not wallet_exists:
            await db.platform_fee_wallets.insert_one(
                {
                    "id": str(uuid.uuid4()),
                    "network": network,
                    "token": "USDC",
                    "wallet_address": f"0xPLATFORM{network.upper()}USDC",
                    "is_active": True,
                    "pending_activation_at": now_utc(),
                    "created_at": now_utc(),
                    "updated_at": now_utc(),
                    "changed_by_admin_id": "system",
                }
            )

    await db.passkey_challenges.create_index([("expires_at", 1)], expireAfterSeconds=0)
    await db.passkey_challenges.create_index([("user_id", 1), ("purpose", 1), ("created_at", -1)])
    await db.payment_intents.create_index([("status", 1), ("network", 1), ("created_at", -1)])
    await db.listing_images.create_index([("listing_id", 1), ("created_at", -1)])


@api_router.get("/")
async def root():
    return {
        "name": PLATFORM_NAME,
        "status": "ok",
        "privacy_promise": "Anonimowość wobec użytkowników, odpowiedzialność wobec systemu",
    }


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

    if user.get("status") in {"LOCKED", "BANNED"}:
        raise HTTPException(status_code=403, detail="Konto zablokowane")

    if user.get("two_fa_enabled"):
        if not payload.otp_code:
            raise HTTPException(status_code=401, detail="Wymagany kod 2FA")
        secret = user.get("two_fa_secret")
        if not secret or not pyotp.TOTP(secret).verify(payload.otp_code, valid_window=1):
            raise HTTPException(status_code=401, detail="Niepoprawny kod 2FA")

    tokens = await create_session(user, payload.device_name, request)
    return {
        **tokens,
        "user": {
            "id": user["id"],
            "alias": user["display_alias"],
            "role": user.get("role", "user"),
            "privacy_level": user.get("privacy_level", 70),
            "two_fa_enabled": user.get("two_fa_enabled", False),
            "public_trust_level": user.get("public_trust_level", "starter"),
        },
    }


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

    if session.get("refresh_jti") != token_data.get("jti"):
        await db.sessions.update_many(
            {"user_id": session["user_id"]}, {"$set": {"is_active": False, "last_seen": now_utc()}}
        )
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    user = await db.users.find_one({"id": session["user_id"]}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    next_jti = str(uuid.uuid4())
    await db.sessions.update_one(
        {"id": payload.session_id},
        {"$set": {"refresh_jti": next_jti, "last_seen": now_utc()}},
    )

    return {
        "access_token": create_access_token(user["id"], user.get("role", "user")),
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
                "two_fa_pending": secret,
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
    active_secret = user.get("two_fa_secret")
    pending_secret = user.get("two_fa_pending")

    for secret, activate in [(pending_secret, True), (active_secret, False)]:
        if secret and pyotp.TOTP(secret).verify(payload.code, valid_window=1):
            update_fields = {"updated_at": now_utc()}
            if activate:
                update_fields["two_fa_secret"] = secret
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
            require_user_verification=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Passkey verification failed: {exc}") from exc

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
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje")

    passkeys = user.get("passkeys", [])
    if not passkeys:
        raise HTTPException(status_code=400, detail="Brak aktywnych passkeys")

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
        raise HTTPException(status_code=400, detail="Brak poprawnych passkeys")

    challenge = generate_webauthn_challenge()
    options = generate_authentication_options(
        rp_id=WEBAUTHN_RP_ID,
        challenge=challenge,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    await save_passkey_challenge(user["id"], "login", bytes_to_base64url(challenge))
    return {
        "public_key": serialize_options(options),
        "allowed_origins": WEBAUTHN_ALLOWED_ORIGINS,
        "rp_id": WEBAUTHN_RP_ID,
    }


@api_router.post("/auth/passkey/login")
async def auth_passkey_login(payload: PasskeyLoginInput, request: Request):
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user:
        raise HTTPException(status_code=404, detail="Użytkownik nie istnieje")

    challenge_doc = await pop_passkey_challenge(user["id"], "login")
    if not challenge_doc:
        raise HTTPException(status_code=400, detail="Brak aktywnego challenge logowania")

    credential_id = payload.credential.get("id")
    if not credential_id:
        raise HTTPException(status_code=400, detail="Brak credential id")

    passkeys = user.get("passkeys", [])
    matching = next((pk for pk in passkeys if pk.get("credential_id") == credential_id), None)
    if not matching:
        raise HTTPException(status_code=401, detail="Passkey niepasujący")

    try:
        verification = verify_authentication_response(
            credential=serialize_webauthn_credential_for_verify(payload.credential),
            expected_challenge=base64url_to_bytes(challenge_doc["challenge_b64"]),
            expected_rp_id=WEBAUTHN_RP_ID,
            expected_origin=WEBAUTHN_ALLOWED_ORIGINS,
            credential_public_key=base64url_to_bytes(matching["public_key"]),
            credential_current_sign_count=int(matching.get("sign_count", 0)),
            require_user_verification=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Passkey login verification failed: {exc}") from exc

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
        "user": {
            "id": user["id"],
            "alias": user["display_alias"],
            "role": user.get("role", "user"),
            "privacy_level": user.get("privacy_level", 70),
            "two_fa_enabled": user.get("two_fa_enabled", False),
            "public_trust_level": user.get("public_trust_level", "starter"),
        },
    }


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
        "created_at": user.get("created_at"),
    }


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
        "message": "Panic Lock aktywny: urządzenia wylogowane, konto i wypłaty zamrożone",
    }


@api_router.get("/me/devices")
async def me_devices(user: dict = Depends(get_current_user)):
    devices = await db.sessions.find(
        {"user_id": user["id"]},
        {"_id": 0},
    ).to_list(100)
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
    current_user: Optional[dict] = Depends(get_optional_user),
):
    filters: Dict[str, Any] = {"status": {"$in": ["ACTIVE", "RESERVED"]}}
    if category:
        filters["category"] = category
    if q:
        regex = re.compile(re.escape(q), re.IGNORECASE)
        filters["$or"] = [{"title": regex}, {"description": regex}]

    items = await db.listings.find(filters, {"_id": 0}).to_list(200)
    for item in items:
        promotion = item.get("promotion", {})
        item["is_promoted"] = bool(
            promotion.get("is_promoted") and promotion.get("ends_at") and promotion.get("ends_at") > now_utc()
        )
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

    reverse = sort != "old"
    items.sort(
        key=lambda x: (x.get("is_promoted", False), x.get("created_at", now_utc())),
        reverse=reverse,
    )
    return items


@api_router.get("/listings/{listing_id}")
async def listing_detail(listing_id: str):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
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
                "thumb_signed_url": signed_r2_get_url(img.get("thumb_key", ""))
                if img.get("thumb_key")
                else None,
                "original_signed_url": signed_r2_get_url(img.get("original_key", ""))
                if img.get("original_key")
                else None,
                "scan_status": img.get("scan_status", "UNKNOWN"),
            }
            for img in images_meta
        ]
    return listing


@api_router.post("/listings")
async def listing_create(payload: ListingCreateInput, user: dict = Depends(get_current_user)):
    if user.get("panic_lock_enabled"):
        raise HTTPException(status_code=403, detail="Konto zablokowane przez Panic Lock")

    if payload.category not in DEFAULT_LISTING_FEES:
        raise HTTPException(status_code=400, detail="Nieobsługiwana kategoria")

    listing_fee_rule = await get_listing_fee_rule(payload.category)
    crypto_amount = round(payload.price_fiat * (1.0 if payload.fiat_currency == "EUR" else 0.24), 2)
    risk_score = 85 if payload.price_fiat < 2 else 20

    listing_doc = {
        "id": str(uuid.uuid4()),
        "seller_id": user["id"],
        "title": payload.title,
        "description": payload.description,
        "price_fiat": payload.price_fiat,
        "fiat_currency": payload.fiat_currency,
        "crypto_amount": crypto_amount,
        "crypto_token": "USDC",
        "crypto_network": "Base",
        "category": payload.category,
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

    updates = payload.model_dump(exclude_none=True)
    updates["updated_at"] = now_utc()
    await db.listings.update_one({"id": listing_id}, {"$set": updates})
    updated = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    return updated


@api_router.delete("/listings/{listing_id}")
async def listing_delete(listing_id: str, user: dict = Depends(get_current_user)):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")

    await db.listings.update_one(
        {"id": listing_id},
        {"$set": {"status": "DELETED", "updated_at": now_utc()}},
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

    expected_fee = listing["listing_fee"]
    if payload.token != expected_fee["token"] or payload.network != expected_fee["network"]:
        raise HTTPException(status_code=400, detail="Błędny token lub sieć")
    if payload.amount < expected_fee["amount"]:
        raise HTTPException(status_code=400, detail="Kwota opłaty za niska")

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

    verification_mode = "OFFCHAIN_FALLBACK"
    verification_ok = True
    verification_meta: Dict[str, Any] = {}
    if is_onchain_indexer_ready():
        verification_ok, _, verification_meta = verify_usdc_transfer_onchain(
            payload.network,
            payload.payment_tx_hash,
            platform_wallet["wallet_address"],
            payload.amount,
        )
        verification_mode = "ONCHAIN_ALCHEMY"

    if not verification_ok:
        raise HTTPException(status_code=400, detail="Transakcja niepotwierdzona on-chain")

    fee_doc = {
        "id": str(uuid.uuid4()),
        "listing_id": listing_id,
        "seller_id": user["id"],
        "amount": payload.amount,
        "token": payload.token,
        "network": payload.network,
        "payment_tx_hash": payload.payment_tx_hash,
        "status": "CONFIRMED",
        "verification_mode": verification_mode,
        "verification_meta": verification_meta,
        "created_at": now_utc(),
    }
    await db.listing_fees.insert_one(fee_doc)

    await activate_listing_after_fee(listing)
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
        "status": "PENDING",
        "verification_mode": "ONCHAIN_ALCHEMY" if is_onchain_indexer_ready() else "OFFCHAIN_FALLBACK",
        "created_at": now_utc(),
    }
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
        return {
            "message": "Promocja już aktywna",
            "intent": intent,
        }

    verification_mode = intent.get("verification_mode", "OFFCHAIN_FALLBACK")
    verification_ok = True
    verification_meta: Dict[str, Any] = {}
    if is_onchain_indexer_ready():
        verification_ok, _, verification_meta = verify_usdc_transfer_onchain(
            intent["network"],
            payload.tx_hash,
            intent["receiver_wallet"],
            float(intent["amount"]),
        )
        verification_mode = "ONCHAIN_ALCHEMY"

    if not verification_ok:
        raise HTTPException(status_code=400, detail="Promocja niepotwierdzona on-chain")

    await db.payment_intents.update_one(
        {"id": intent["id"]},
        {
            "$set": {
                "status": "CONFIRMED",
                "tx_hash": payload.tx_hash,
                "verified_at": now_utc(),
                "verification_mode": verification_mode,
                "verification_meta": verification_meta,
            }
        },
    )

    await activate_listing_promotion(
        listing_id=listing_id,
        package_type=intent["package_type"],
        amount_usdc=float(intent["amount"]),
        tx_hash=payload.tx_hash,
        network=intent["network"],
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
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    listing = await db.listings.find_one({"id": listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Brak dostępu")
    if not file.content_type or file.content_type not in UPLOAD_ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Nieobsługiwany typ pliku")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Pusty plik")
    if len(raw_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Plik zbyt duży (max 8MB)")

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
    listing = await db.listings.find_one({"id": payload.listing_id}, {"_id": 0})
    if not listing:
        raise HTTPException(status_code=404, detail="Oferta nie istnieje")
    if listing.get("status") != "ACTIVE":
        raise HTTPException(status_code=400, detail="Oferta nieaktywna")
    if listing["seller_id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Nie możesz kupić własnej oferty")

    fee_rule = await get_sales_fee_rule(listing["category"])
    gross_amount = float(listing["crypto_amount"])
    fee_percent = float(fee_rule["fee_percent"])
    fee_amount = round(gross_amount * fee_percent / 100, 2)
    seller_amount = round(gross_amount - fee_amount, 2)

    txn_doc = {
        "id": str(uuid.uuid4()),
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
        "token": listing["crypto_token"],
        "network": listing["crypto_network"],
        "buyer_alias": random_alias(),
        "seller_alias": random_alias(),
        "deal_room_id": str(uuid.uuid4()),
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    await db.transactions.insert_one(txn_doc)

    escrow_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": txn_doc["id"],
        "smart_contract_address": "0xMASK_ESCROW_MODULE",
        "order_hash": str(uuid.uuid4()),
        "tx_hash": None,
        "status": "CREATED",
        "amount": gross_amount,
        "token": txn_doc["token"],
        "network": txn_doc["network"],
        "created_at": now_utc(),
    }
    await db.escrow_orders.insert_one(escrow_doc)

    await db.listings.update_one(
        {"id": listing["id"]}, {"$set": {"status": "RESERVED", "updated_at": now_utc()}}
    )

    return clean_mongo_doc(txn_doc)


@api_router.get("/transactions")
async def transactions_list(user: dict = Depends(get_current_user)):
    filters: Dict[str, Any] = {"$or": [{"buyer_id": user["id"]}, {"seller_id": user["id"]}]}
    if user.get("role") == "admin":
        filters = {}
    txs = await db.transactions.find(filters, {"_id": 0}).to_list(300)
    for tx in txs:
        tx["status_summary"] = tx_status_summary(tx["status"])
    txs.sort(key=lambda item: item["created_at"], reverse=True)
    return txs


@api_router.get("/transactions/{transaction_id}")
async def transaction_detail(transaction_id: str, user: dict = Depends(get_current_user)):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

    is_participant = user["id"] in {tx["buyer_id"], tx["seller_id"]}
    if not is_participant and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")

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

    if payload.token != tx["token"] or payload.network != tx["network"]:
        raise HTTPException(status_code=400, detail="Błędna sieć lub token")
    if round(payload.amount, 2) != round(float(tx["gross_amount"]), 2):
        raise HTTPException(status_code=400, detail="Kwota niezgodna z transakcją")

    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "FUNDED",
                "escrow_status": "FUNDED",
                "shipping_status": "AWAITING_SHIPMENT",
                "fund_tx_hash": payload.tx_hash,
                "updated_at": now_utc(),
            }
        },
    )
    await db.escrow_orders.update_one(
        {"transaction_id": transaction_id},
        {"$set": {"tx_hash": payload.tx_hash, "status": "FUNDED"}},
    )
    updated = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
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
    if tx["status"] != "FUNDED":
        raise HTTPException(status_code=400, detail="Najpierw wymagane finansowanie escrow")

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
    await db.shipping_labels.insert_one(shipping_doc)

    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "SHIPPED",
                "shipping_status": "SHIPPED",
                "escrow_status": "FUNDED",
                "updated_at": now_utc(),
            }
        },
    )
    updated = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    updated["blind_delivery"] = {
        "label_token": label_token,
        "carrier": payload.carrier,
        "address_visible_to_seller": False,
    }
    return updated


@api_router.post("/transactions/{transaction_id}/confirm-delivery")
async def transaction_confirm_delivery(transaction_id: str, user: dict = Depends(get_current_user)):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if tx["buyer_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Tylko kupujący może potwierdzić odbiór")
    if tx["status"] != "SHIPPED":
        raise HTTPException(status_code=400, detail="Nie można potwierdzić tego statusu")

    fee_wallet = await db.platform_fee_wallets.find_one(
        {"network": tx["network"], "token": tx["token"], "is_active": True},
        {"_id": 0},
    )

    sale_fee_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": tx["id"],
        "seller_id": tx["seller_id"],
        "buyer_id": tx["buyer_id"],
        "gross_amount": tx["gross_amount"],
        "fee_percent": tx["fee_percent"],
        "fee_amount": tx["fee_amount"],
        "seller_amount": tx["seller_amount"],
        "token": tx["token"],
        "network": tx["network"],
        "fee_wallet_address": fee_wallet["wallet_address"] if fee_wallet else "UNCONFIGURED",
        "fee_tx_hash": tx.get("fund_tx_hash", "pending-split"),
        "status": "SETTLED",
        "created_at": now_utc(),
    }
    await db.sale_fees.insert_one(sale_fee_doc)

    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "COMPLETED",
                "shipping_status": "DELIVERED",
                "escrow_status": "RELEASED_TO_SELLER",
                "updated_at": now_utc(),
            }
        },
    )

    await db.listings.update_one(
        {"id": tx["listing_id"]},
        {"$set": {"status": "SOLD", "updated_at": now_utc()}},
    )
    updated = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
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
    }
    await db.disputes.insert_one(dispute_doc)
    await db.transactions.update_one(
        {"id": transaction_id},
        {
            "$set": {
                "status": "DISPUTED",
                "dispute_status": "OPEN",
                "escrow_status": "FROZEN",
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
    return {"message": "Transakcja anulowana"}


@api_router.get("/transactions/{transaction_id}/messages")
async def messages_list(transaction_id: str, user: dict = Depends(get_current_user)):
    tx = await db.transactions.find_one({"id": transaction_id}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")
    if user["id"] not in {tx["buyer_id"], tx["seller_id"]} and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Brak dostępu")

    messages = await db.encrypted_messages.find(
        {"transaction_id": transaction_id}, {"_id": 0}
    ).to_list(300)
    messages.sort(key=lambda x: x["created_at"])
    return messages


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

    msg_doc = {
        "id": str(uuid.uuid4()),
        "transaction_id": transaction_id,
        "sender_id": user["id"],
        "ciphertext": payload.ciphertext,
        "nonce": payload.nonce,
        "message_type": payload.message_type,
        "created_at": now_utc(),
        "expires_at": now_utc() + timedelta(days=payload.expires_in_days),
    }
    await db.encrypted_messages.insert_one(msg_doc)
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
        "details": "Dobrowolnie ujawnione wiadomości E2EE",
        "evidence_status": "provided",
        "evidence_messages": payload.selected_messages,
        "status": "OPEN",
        "created_at": now_utc(),
    }
    await db.reports.insert_one(report_doc)
    return {"message": "Dowody przekazane", "report_id": report_doc["id"]}


@api_router.get("/crypto/networks")
async def crypto_networks():
    return [{"name": name, "status": "active"} for name in SUPPORTED_NETWORKS]


@api_router.get("/crypto/tokens")
async def crypto_tokens():
    return [{"symbol": symbol, "status": "active"} for symbol in SUPPORTED_TOKENS]


@api_router.get("/crypto/rates")
async def crypto_rates():
    return {
        "base_currency": "EUR",
        "lock_seconds": 900,
        "rates": {
            "USDC": 1.0,
            "EURC": 1.0,
            "PLN_to_USDC": 0.24,
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

    platform_wallet = await db.platform_fee_wallets.find_one(
        {"network": payload.network, "token": payload.token, "is_active": True}, {"_id": 0}
    )
    if not platform_wallet:
        raise HTTPException(status_code=400, detail="Brak aktywnego portfela platformy")

    intent = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "amount": payload.amount,
        "token": payload.token,
        "network": payload.network,
        "purpose": payload.purpose,
        "target_id": payload.target_id,
        "receiver_wallet": platform_wallet["wallet_address"],
        "status": "PENDING",
        "verification_mode": "ONCHAIN_ALCHEMY" if is_onchain_indexer_ready() else "OFFCHAIN_FALLBACK",
        "created_at": now_utc(),
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

    verify_ok = True
    verify_mode = intent.get("verification_mode", "OFFCHAIN_FALLBACK")
    verify_meta: Dict[str, Any] = {}
    if is_onchain_indexer_ready():
        verify_ok, _, verify_meta = verify_usdc_transfer_onchain(
            intent["network"],
            payload.tx_hash,
            intent["receiver_wallet"],
            float(intent["amount"]),
        )
        verify_mode = "ONCHAIN_ALCHEMY"

    if not verify_ok:
        raise HTTPException(status_code=400, detail="Transakcja on-chain niezweryfikowana")

    await db.payment_intents.update_one(
        {"id": intent_id},
        {
            "$set": {
                "status": "CONFIRMED",
                "tx_hash": payload.tx_hash,
                "verified_at": now_utc(),
                "verification_mode": verify_mode,
                "verification_meta": verify_meta,
            }
        },
    )
    updated_intent = await db.payment_intents.find_one({"id": intent_id}, {"_id": 0})
    return {
        "message": "Płatność potwierdzona",
        "intent": updated_intent,
    }


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
        token_addr = str(item.get("rawContract", {}).get("address") or item.get("contractAddress") or "").lower()
        amount_raw = item.get("value")
        try:
            amount = float(amount_raw)
        except Exception:
            amount = 0.0

        network = normalize_network_label(item.get("network") or payload.get("network") or "")
        if not network:
            continue
        if token_addr and token_addr != USDC_CONTRACTS.get(network, ""):
            continue

        pending = await db.payment_intents.find_one(
            {
                "status": "PENDING",
                "network": network,
                "receiver_wallet": {"$regex": f"^{re.escape(to_addr)}$", "$options": "i"},
                "amount": {"$lte": amount + 0.000001},
            },
            {"_id": 0},
        )
        if not pending:
            continue

        await db.payment_intents.update_one(
            {"id": pending["id"]},
            {
                "$set": {
                    "status": "CONFIRMED",
                    "tx_hash": tx_hash,
                    "verified_at": now_utc(),
                    "verification_mode": "ONCHAIN_ALCHEMY_WEBHOOK",
                    "webhook_payload": item,
                }
            },
        )
        updates += 1

        if pending.get("purpose") == "promotion":
            await activate_listing_promotion(
                listing_id=pending["listing_id"],
                package_type=pending["package_type"],
                amount_usdc=float(pending["amount"]),
                tx_hash=tx_hash,
                network=network,
            )

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

    sale_fees = await db.sale_fees.find({}, {"_id": 0, "fee_amount": 1, "token": 1}).to_list(2000)
    revenue = round(sum(float(item.get("fee_amount", 0)) for item in sale_fees), 2)

    return {
        "users": users_count,
        "listings": listings_count,
        "active_transactions": tx_active,
        "open_disputes": disputes,
        "open_reports": reports,
        "suspicious_accounts": suspicious,
        "commission_revenue_crypto": revenue,
        "system_status": "healthy",
    }


@api_router.get("/admin/users")
async def admin_users(admin: dict = Depends(get_current_admin)):
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
    ).to_list(2000)
    return users


@api_router.get("/admin/listings")
async def admin_listings(admin: dict = Depends(get_current_admin)):
    _ = admin
    listings = await db.listings.find({}, {"_id": 0}).to_list(2000)
    return listings


@api_router.get("/admin/transactions")
async def admin_transactions(admin: dict = Depends(get_current_admin)):
    _ = admin
    txs = await db.transactions.find({}, {"_id": 0}).to_list(2000)
    return txs


@api_router.get("/admin/disputes")
async def admin_disputes(admin: dict = Depends(get_current_admin)):
    _ = admin
    disputes = await db.disputes.find({}, {"_id": 0}).to_list(2000)
    return disputes


@api_router.get("/admin/reports")
async def admin_reports(admin: dict = Depends(get_current_admin)):
    _ = admin
    reports = await db.reports.find({}, {"_id": 0}).to_list(2000)
    return reports


@api_router.get("/admin/fees")
async def admin_fees(admin: dict = Depends(get_current_admin)):
    _ = admin
    listing_rules = await db.listing_fee_rules.find({"is_active": True}, {"_id": 0}).to_list(1000)
    sale_rules = await db.sale_fee_rules.find({"is_active": True}, {"_id": 0}).to_list(1000)
    fees_collected = await db.sale_fees.find({}, {"_id": 0}).to_list(2000)
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
    if not re.match(r"^(0x[a-fA-F0-9]{8,}|[A-Za-z0-9_\-]{12,})$", payload.wallet_address):
        raise HTTPException(status_code=400, detail="Nieprawidłowy format adresu")

    doc = {
        "id": str(uuid.uuid4()),
        "network": payload.network,
        "token": payload.token,
        "wallet_address": payload.wallet_address,
        "risk_score": compute_wallet_risk(payload.wallet_address),
        "is_active": True,
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

    updated = {
        **existing,
        "network": payload.network,
        "token": payload.token,
        "wallet_address": payload.wallet_address,
        "risk_score": compute_wallet_risk(payload.wallet_address),
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


@api_router.get("/admin/audit-logs")
async def admin_audit_logs(admin: dict = Depends(get_current_admin)):
    _ = admin
    logs = await db.admin_audit_logs.find({}, {"_id": 0}).to_list(3000)
    logs.sort(key=lambda item: item["created_at"], reverse=True)
    return logs


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
    if dispute["status"] != "OPEN":
        raise HTTPException(status_code=400, detail="Spór już rozstrzygnięty")

    tx = await db.transactions.find_one({"id": dispute["transaction_id"]}, {"_id": 0})
    if not tx:
        raise HTTPException(status_code=404, detail="Transakcja nie istnieje")

    tx_status = "DISPUTED"
    escrow_status = "FROZEN"
    resolution = {
        "decision": payload.decision,
        "reason": payload.reason,
    }
    if payload.decision == "refund_buyer":
        tx_status = "REFUNDED"
        escrow_status = "REFUNDED"
    elif payload.decision == "release_seller":
        tx_status = "COMPLETED"
        escrow_status = "RELEASED_TO_SELLER"
    elif payload.decision == "partial_refund":
        tx_status = "COMPLETED"
        escrow_status = "PARTIAL_REFUND"
        resolution["partial_seller_amount"] = payload.partial_seller_amount

    await db.disputes.update_one(
        {"id": dispute_id},
        {
            "$set": {
                "status": "RESOLVED",
                "resolution": resolution,
                "resolved_by_admin_id": admin["id"],
                "resolved_at": now_utc(),
            }
        },
    )
    await db.transactions.update_one(
        {"id": tx["id"]},
        {
            "$set": {
                "status": tx_status,
                "escrow_status": escrow_status,
                "dispute_status": "RESOLVED",
                "updated_at": now_utc(),
            }
        },
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
    updated_dispute = await db.disputes.find_one({"id": dispute_id}, {"_id": 0})
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

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
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
