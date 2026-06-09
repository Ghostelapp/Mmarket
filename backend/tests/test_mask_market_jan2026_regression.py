import io
import time

import requests
from PIL import Image


# Auth/WebAuthn: register options/verify + login options/verify contract and errors
def test_passkey_register_options_and_invalid_register_verify(base_url, api_client, make_user):
    user = make_user("TEST_PASSKEY_REG")
    auth_header = {"Authorization": f"Bearer {user['access_token']}"}

    options_resp = api_client.post(
        f"{base_url}/api/auth/passkey/register/options",
        headers=auth_header,
        json={"nickname": "pytest-passkey"},
        timeout=30,
    )
    assert options_resp.status_code == 200
    options_data = options_resp.json()
    assert "public_key" in options_data
    assert "challenge" in options_data["public_key"]
    assert options_data["nickname"] == "pytest-passkey"

    invalid_verify = api_client.post(
        f"{base_url}/api/auth/passkey/register",
        headers=auth_header,
        json={
            "nickname": "pytest-passkey",
            "credential": {
                "id": "bad-credential-id",
                "rawId": "YmFkLXJhd0lk",
                "type": "public-key",
                "response": {
                    "clientDataJSON": "AA",
                    "attestationObject": "AA",
                },
            },
        },
        timeout=30,
    )
    assert invalid_verify.status_code == 400


def test_passkey_register_verify_without_challenge_returns_error(base_url, api_client, make_user):
    user = make_user("TEST_PK_NC")
    auth_header = {"Authorization": f"Bearer {user['access_token']}"}

    no_challenge = api_client.post(
        f"{base_url}/api/auth/passkey/register",
        headers=auth_header,
        json={
            "nickname": "pytest-passkey",
            "credential": {
                "id": "missing-challenge",
                "rawId": "bWlzc2luZy1jaGFsbGVuZ2U",
                "type": "public-key",
                "response": {
                    "clientDataJSON": "AA",
                    "attestationObject": "AA",
                },
            },
        },
        timeout=30,
    )
    assert no_challenge.status_code == 400
    assert "challenge" in no_challenge.text.lower()


def test_passkey_login_options_and_verify_error_contract(base_url, api_client, make_user):
    user = make_user("TEST_PK_LOGIN")

    no_passkeys_options = api_client.post(
        f"{base_url}/api/auth/passkey/login/options",
        json={"email": user["register"]["email"]},
        timeout=30,
    )
    assert no_passkeys_options.status_code == 400

    no_challenge_verify = api_client.post(
        f"{base_url}/api/auth/passkey/login",
        json={
            "email": user["register"]["email"],
            "device_name": "pytest-passkey-login",
            "credential": {
                "id": "some-id",
                "rawId": "c29tZS1pZA",
                "type": "public-key",
                "response": {
                    "clientDataJSON": "AA",
                    "authenticatorData": "AA",
                    "signature": "AA",
                    "userHandle": None,
                },
            },
        },
        timeout=30,
    )
    assert no_challenge_verify.status_code == 400
    assert "challenge" in no_challenge_verify.text.lower()


def _create_listing(base_url, api_client, token, title_suffix):
    payload = {
        "title": f"TEST listing {title_suffix}",
        "description": "TEST description long enough for listing validation and promotion checks.",
        "price_fiat": 120,
        "fiat_currency": "PLN",
        "category": "elektronika",
        "condition": "nowy",
        "location_public": "TEST_Warszawa",
        "shipping_options": ["blind-delivery"],
        "images": [],
    }
    resp = api_client.post(
        f"{base_url}/api/listings",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pay_listing_fee(base_url, api_client, token, listing, tx_hash):
    resp = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/pay-listing-fee",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "amount": listing["listing_fee"]["amount"],
            "token": listing["listing_fee"]["token"],
            "network": listing["listing_fee"]["network"],
            "payment_tx_hash": tx_hash,
        },
        timeout=30,
    )
    return resp


# Listings + payments: OFFCHAIN fallback mode and verification_mode presence
def test_listing_fee_confirmation_includes_verification_mode_offchain(base_url, api_client, make_user):
    seller = make_user("TEST_LISTING_FEE")
    listing = _create_listing(base_url, api_client, seller["access_token"], "fee")

    pay_fee = _pay_listing_fee(base_url, api_client, seller["access_token"], listing, "0xTESTLISTFEE202601")
    assert pay_fee.status_code == 200
    data = pay_fee.json()
    assert data["verification_mode"] == "OFFCHAIN_FALLBACK"
    assert data["listing"]["listing_fee"]["status"] == "PAID"


# Promotion flow: intent + confirm + promoted activation fields
def test_promotion_flow_intent_confirm_activation(base_url, api_client, make_user):
    seller = make_user("TEST_PROMO")
    token = seller["access_token"]
    listing = _create_listing(base_url, api_client, token, "promo")

    pay_fee = _pay_listing_fee(base_url, api_client, token, listing, "0xTESTPROMOFEE202601")
    assert pay_fee.status_code == 200

    intent = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-intent",
        headers={"Authorization": f"Bearer {token}"},
        json={"package_type": "basic", "network": "Base", "token": "USDC"},
        timeout=30,
    )
    assert intent.status_code == 200
    intent_data = intent.json()
    assert intent_data["verification_mode"] == "OFFCHAIN_FALLBACK"

    confirm = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"intent_id": intent_data["id"], "tx_hash": "0xTESTPROMOCONFIRM202601"},
        timeout=30,
    )
    assert confirm.status_code == 200, confirm.text
    confirm_data = confirm.json()
    assert confirm_data["verification_mode"] == "OFFCHAIN_FALLBACK"
    assert confirm_data["listing"]["promotion"]["is_promoted"] is True
    assert confirm_data["listing"]["promotion"]["package_type"] == "basic"


# Upload pipeline: image process + metadata strip flag + thumbnail keys + scan status
def test_upload_pipeline_image_processing_metadata_and_thumbnail(base_url, api_client, make_user):
    seller = make_user("TEST_UPLOAD")
    token = seller["access_token"]
    listing = _create_listing(base_url, api_client, token, "upload")

    image = Image.new("RGB", (900, 600), color=(15, 60, 120))
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    buf.seek(0)

    upload = requests.post(
        f"{base_url}/api/listings/{listing['id']}/images/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
        timeout=30,
    )
    assert upload.status_code == 200, upload.text
    upload_data = upload.json()
    assert upload_data["scan_status"] == "AV_SCAN_DISABLED"
    assert upload_data["storage_provider"] in {"local", "r2"}

    detail = api_client.get(f"{base_url}/api/listings/{listing['id']}", timeout=30)
    assert detail.status_code == 200
    detail_data = detail.json()
    assert len(detail_data.get("processed_images", [])) >= 1
    first_img = detail_data["processed_images"][0]
    assert first_img.get("thumb_key")
    assert first_img.get("original_key")
    assert first_img.get("scan_status") == "AV_SCAN_DISABLED"


# Crypto payment verification OFFCHAIN fallback when indexer disabled
def test_crypto_payment_submit_and_status_offchain(base_url, api_client, make_user):
    user = make_user("TEST_CRYPTO_PAY")
    token = user["access_token"]

    intent = api_client.post(
        f"{base_url}/api/crypto/payment-intent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "amount": 1.5,
            "token": "USDC",
            "network": "Base",
            "purpose": "promotion",
            "target_id": f"TEST_TARGET_{int(time.time())}",
        },
        timeout=30,
    )
    assert intent.status_code == 200, intent.text
    intent_data = intent.json()
    assert intent_data["verification_mode"] == "OFFCHAIN_FALLBACK"

    submit = api_client.post(
        f"{base_url}/api/crypto/payment/{intent_data['id']}/submit-tx",
        headers={"Authorization": f"Bearer {token}"},
        json={"tx_hash": "0xTESTCRYPTOSUBMIT202601"},
        timeout=30,
    )
    assert submit.status_code == 200, submit.text
    submit_data = submit.json()
    assert submit_data["intent"]["status"] == "CONFIRMED"
    assert submit_data["intent"]["verification_mode"] == "OFFCHAIN_FALLBACK"

    status = api_client.get(
        f"{base_url}/api/crypto/payment/{intent_data['id']}/status",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert status.status_code == 200
    status_data = status.json()
    assert status_data["status"] == "CONFIRMED"


# Webhook security: missing/invalid Alchemy signatures must be rejected
def test_alchemy_webhook_signature_validation(base_url, api_client):
    payload = {"event": {"activity": []}}

    missing_sig = api_client.post(
        f"{base_url}/api/crypto/webhooks/alchemy",
        json=payload,
        timeout=30,
    )
    assert missing_sig.status_code == 401

    invalid_sig = api_client.post(
        f"{base_url}/api/crypto/webhooks/alchemy",
        headers={"X-Alchemy-Signature": "invalid-signature"},
        json=payload,
        timeout=30,
    )
    assert invalid_sig.status_code == 401


# Marketplace sort behavior should support promoted offers first
def test_marketplace_listings_sort_supports_promoted_flag(base_url, api_client, make_user):
    seller = make_user("TEST_SORT_PROMO")
    token = seller["access_token"]

    listing = _create_listing(base_url, api_client, token, "sort-promoted")
    paid = _pay_listing_fee(base_url, api_client, token, listing, "0xTESTSORTFEE202601")
    assert paid.status_code == 200

    intent = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-intent",
        headers={"Authorization": f"Bearer {token}"},
        json={"package_type": "basic", "network": "Base", "token": "USDC"},
        timeout=30,
    )
    assert intent.status_code == 200
    intent_id = intent.json()["id"]

    confirm = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"intent_id": intent_id, "tx_hash": "0xTESTSORTPROMO202601"},
        timeout=30,
    )
    assert confirm.status_code == 200

    listings = api_client.get(f"{base_url}/api/listings?sort=new", timeout=30)
    assert listings.status_code == 200, listings.text
    data = listings.json()
    assert len(data) >= 1
    assert data[0]["is_promoted"] is True
