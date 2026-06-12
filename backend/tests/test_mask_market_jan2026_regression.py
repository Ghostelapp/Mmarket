import io
import time
import uuid

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
    assert no_passkeys_options.status_code == 200
    assert no_passkeys_options.json()["public_key"]["allowCredentials"]

    unknown_account_options = api_client.post(
        f"{base_url}/api/auth/passkey/login/options",
        json={"email": f"missing-{uuid.uuid4().hex}@example.com"},
        timeout=30,
    )
    assert unknown_account_options.status_code == 200
    assert unknown_account_options.json().keys() == no_passkeys_options.json().keys()

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
    assert no_challenge_verify.status_code == 401
    assert "logowanie passkey nieudane" in no_challenge_verify.text.lower()


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
    tx_hash = f"{tx_hash}-{uuid.uuid4().hex}"
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


# Listings + payments: explicit test simulation mode and verification_mode presence
def test_listing_fee_confirmation_includes_verification_mode_test_simulation(base_url, api_client, make_user):
    seller = make_user("TEST_LISTING_FEE")
    listing = _create_listing(base_url, api_client, seller["access_token"], "fee")

    pay_fee = _pay_listing_fee(base_url, api_client, seller["access_token"], listing, "0xTESTLISTFEE202601")
    assert pay_fee.status_code == 200
    data = pay_fee.json()
    assert data["verification_mode"] == "TEST_SIMULATION"
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
    assert intent_data["verification_mode"] == "UNAVAILABLE"

    confirm = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"intent_id": intent_data["id"], "tx_hash": f"0xTESTPROMOCONFIRM202601-{uuid.uuid4().hex}"},
        timeout=30,
    )
    assert confirm.status_code == 200, confirm.text
    repeated_confirm = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/promote-confirm",
        headers={"Authorization": f"Bearer {token}"},
        json={"intent_id": intent_data["id"], "tx_hash": confirm.json()["listing"]["promotion"]["tx_hash"]},
        timeout=30,
    )
    assert repeated_confirm.status_code == 200, repeated_confirm.text
    confirm_data = confirm.json()
    assert (
        repeated_confirm.json()["listing"]["promotion"]["starts_at"]
        == confirm_data["listing"]["promotion"]["starts_at"]
    )
    assert (
        repeated_confirm.json()["listing"]["promotion"]["ends_at"]
        == confirm_data["listing"]["promotion"]["ends_at"]
    )
    assert confirm_data["verification_mode"] == "TEST_SIMULATION"
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

    detail = api_client.get(
        f"{base_url}/api/listings/{listing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert detail.status_code == 200
    detail_data = detail.json()
    assert len(detail_data.get("processed_images", [])) >= 1
    first_img = detail_data["processed_images"][0]
    assert first_img.get("thumb_key")
    assert first_img.get("original_key")
    assert first_img.get("scan_status") == "AV_SCAN_DISABLED"

    if upload_data["storage_provider"] == "local":
        raw_media_url = upload_data["thumb_signed_url"].split("?", 1)[0]
        anonymous_media = requests.get(raw_media_url, timeout=30)
        assert anonymous_media.status_code == 404
        signed_media = requests.get(upload_data["thumb_signed_url"], timeout=30)
        assert signed_media.status_code == 200
        owner_media = requests.get(
            raw_media_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert owner_media.status_code == 200

    deleted = api_client.delete(
        f"{base_url}/api/listings/{listing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert deleted.status_code == 200, deleted.text
    if upload_data["storage_provider"] == "local":
        deleted_media = requests.get(upload_data["thumb_signed_url"], timeout=30)
        assert deleted_media.status_code == 404

    upload_after_delete = requests.post(
        f"{base_url}/api/listings/{listing['id']}/images/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
        timeout=30,
    )
    assert upload_after_delete.status_code == 409


def test_listing_create_rejects_empty_shipping_options(base_url, api_client, make_user):
    seller = make_user("TEST_LISTING_VALIDATION")
    response = api_client.post(
        f"{base_url}/api/listings",
        headers={"Authorization": f"Bearer {seller['access_token']}"},
        json={
            "title": "Valid listing title",
            "description": "Valid listing description long enough for API validation.",
            "price_fiat": 100,
            "fiat_currency": "PLN",
            "category": "elektronika",
            "condition": "nowy",
            "location_public": "Kraków",
            "shipping_options": [],
            "images": [],
        },
        timeout=30,
    )
    assert response.status_code == 422


# Generic payment intents reject unsupported purposes.
def test_crypto_payment_intent_rejects_unsupported_purpose(base_url, api_client, make_user):
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
    assert intent.status_code == 400, intent.text


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
        json={"intent_id": intent_id, "tx_hash": f"0xTESTSORTPROMO202601-{uuid.uuid4().hex}"},
        timeout=30,
    )
    assert confirm.status_code == 200

    listings = api_client.get(f"{base_url}/api/listings?sort=new", timeout=30)
    assert listings.status_code == 200, listings.text
    data = listings.json()
    assert len(data) >= 1
    assert data[0]["is_promoted"] is True
