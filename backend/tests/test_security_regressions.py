import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import uuid

import requests


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_listing(base_url, api_client, token: str, suffix: str) -> dict:
    response = api_client.post(
        f"{base_url}/api/listings",
        headers=_auth(token),
        json={
            "title": f"Security listing {suffix}",
            "description": "Security regression listing with a sufficiently long description.",
            "price_fiat": 100,
            "fiat_currency": "PLN",
            "category": "elektronika",
            "condition": "nowy",
            "location_public": "Warszawa",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _pay_listing(base_url, api_client, token: str, listing: dict, tx_hash: str):
    return api_client.post(
        f"{base_url}/api/listings/{listing['id']}/pay-listing-fee",
        headers=_auth(token),
        json={
            "amount": listing["listing_fee"]["amount"],
            "token": listing["listing_fee"]["token"],
            "network": listing["listing_fee"]["network"],
            "payment_tx_hash": tx_hash,
        },
        timeout=30,
    )


def _random_b64(size: int) -> str:
    return base64.b64encode(os.urandom(size)).decode()


def test_private_listing_is_not_public(base_url, api_client, make_user):
    seller = make_user("SEC_PRIVATE")
    listing = _create_listing(base_url, api_client, seller["access_token"], uuid.uuid4().hex[:8])

    anonymous = api_client.get(f"{base_url}/api/listings/{listing['id']}", timeout=30)
    assert anonymous.status_code == 404

    owner = api_client.get(
        f"{base_url}/api/listings/{listing['id']}",
        headers=_auth(seller["access_token"]),
        timeout=30,
    )
    assert owner.status_code == 200


def test_transaction_hash_cannot_be_reused(base_url, api_client, make_user):
    seller = make_user("SEC_HASH")
    first = _create_listing(base_url, api_client, seller["access_token"], "first")
    second = _create_listing(base_url, api_client, seller["access_token"], "second")
    tx_hash = f"0xSECURITY{uuid.uuid4().hex}"

    paid = _pay_listing(base_url, api_client, seller["access_token"], first, tx_hash)
    assert paid.status_code == 200, paid.text
    repeated = _pay_listing(base_url, api_client, seller["access_token"], first, tx_hash)
    assert repeated.status_code == 200, repeated.text

    reused = _pay_listing(base_url, api_client, seller["access_token"], second, tx_hash)
    assert reused.status_code == 409


def test_listing_fee_retry_does_not_override_admin_rejection(
    base_url, api_client, make_user, admin_auth
):
    seller = make_user("SEC_FEE_REPLAY")
    listing = _create_listing(base_url, api_client, seller["access_token"], "fee-replay")
    tx_hash = f"0xSECURITY{uuid.uuid4().hex}"
    paid = _pay_listing(base_url, api_client, seller["access_token"], listing, tx_hash)
    assert paid.status_code == 200, paid.text

    rejected = api_client.post(
        f"{base_url}/api/admin/listings/{listing['id']}/moderate",
        headers=_auth(admin_auth["access_token"]),
        json={"action": "reject", "reason": "security regression"},
        timeout=30,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"

    repeated = _pay_listing(base_url, api_client, seller["access_token"], listing, tx_hash)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["listing"]["status"] == "REJECTED"


def test_parallel_refresh_returns_one_stable_rotation(base_url, make_user):
    user = make_user("SEC_REFRESH_RACE")
    payload = {"refresh_token": user["refresh_token"], "session_id": user["session_id"]}

    def refresh():
        return requests.post(f"{base_url}/api/auth/refresh", json=payload, timeout=30)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: refresh(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    refresh_tokens = {response.json()["refresh_token"] for response in responses}
    assert len(refresh_tokens) == 1

    followup = requests.post(
        f"{base_url}/api/auth/refresh",
        json={"refresh_token": refresh_tokens.pop(), "session_id": user["session_id"]},
        timeout=30,
    )
    assert followup.status_code == 200, followup.text


def test_cancelled_transaction_releases_listing(base_url, api_client, make_user):
    seller = make_user("SEC_CANCEL_SELLER")
    buyer = make_user("SEC_CANCEL_BUYER")
    listing = _create_listing(base_url, api_client, seller["access_token"], "cancel")
    paid = _pay_listing(
        base_url,
        api_client,
        seller["access_token"],
        listing,
        f"0xSECURITY{uuid.uuid4().hex}",
    )
    assert paid.status_code == 200, paid.text

    created = api_client.post(
        f"{base_url}/api/transactions",
        headers=_auth(buyer["access_token"]),
        json={"listing_id": listing["id"]},
        timeout=30,
    )
    assert created.status_code == 200, created.text

    cancelled = api_client.post(
        f"{base_url}/api/transactions/{created.json()['id']}/cancel",
        headers=_auth(buyer["access_token"]),
        timeout=30,
    )
    assert cancelled.status_code == 200, cancelled.text

    detail = api_client.get(f"{base_url}/api/listings/{listing['id']}", timeout=30)
    assert detail.status_code == 200
    assert detail.json()["status"] == "ACTIVE"


def test_transaction_financials_match_six_decimal_contract_math(
    base_url, api_client, make_user, admin_auth
):
    seller = make_user("SEC_PRECISION_SELLER")
    buyer = make_user("SEC_PRECISION_BUYER")
    listing = api_client.post(
        f"{base_url}/api/listings",
        headers=_auth(seller["access_token"]),
        json={
            "title": "Precision listing",
            "description": "Precision regression listing with a sufficiently long description.",
            "price_fiat": 1,
            "fiat_currency": "PLN",
            "category": "elektronika",
            "condition": "nowy",
            "location_public": "Warszawa",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert listing.status_code == 200, listing.text
    listing_data = listing.json()
    paid = _pay_listing(
        base_url,
        api_client,
        seller["access_token"],
        listing_data,
        f"0xSECURITY{uuid.uuid4().hex}",
    )
    assert paid.status_code == 200, paid.text
    approved = api_client.post(
        f"{base_url}/api/admin/listings/{listing_data['id']}/moderate",
        headers=_auth(admin_auth["access_token"]),
        json={"action": "approve", "reason": "precision test approval"},
        timeout=30,
    )
    assert approved.status_code == 200, approved.text

    created = api_client.post(
        f"{base_url}/api/transactions",
        headers=_auth(buyer["access_token"]),
        json={"listing_id": listing_data["id"]},
        timeout=30,
    )
    assert created.status_code == 200, created.text
    assert created.json()["gross_amount"] == 0.24
    assert created.json()["fee_amount"] == 0.012
    assert created.json()["seller_amount"] == 0.228


def test_ban_immediately_invalidates_access_token(base_url, api_client, make_user, admin_auth):
    user = make_user("SEC_BAN")
    banned = api_client.post(
        f"{base_url}/api/admin/users/{user['user']['id']}/ban",
        headers=_auth(admin_auth["access_token"]),
        timeout=30,
    )
    assert banned.status_code == 200, banned.text

    me = api_client.get(
        f"{base_url}/api/me",
        headers=_auth(user["access_token"]),
        timeout=30,
    )
    assert me.status_code == 403


def test_active_listing_edit_returns_to_review_and_recalculates_price(
    base_url, api_client, make_user
):
    seller = make_user("SEC_EDIT")
    listing = _create_listing(base_url, api_client, seller["access_token"], "edit")
    paid = _pay_listing(
        base_url,
        api_client,
        seller["access_token"],
        listing,
        f"0xSECURITY{uuid.uuid4().hex}",
    )
    assert paid.status_code == 200, paid.text

    edited = api_client.patch(
        f"{base_url}/api/listings/{listing['id']}",
        headers=_auth(seller["access_token"]),
        json={"price_fiat": 200},
        timeout=30,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "UNDER_REVIEW"
    assert edited.json()["moderation_status"] == "PENDING"
    assert edited.json()["crypto_amount"] == 48.0


def test_admin_cannot_approve_unpaid_listing(
    base_url, api_client, make_user, admin_auth
):
    seller = make_user("SEC_UNPAID")
    listing = _create_listing(base_url, api_client, seller["access_token"], "unpaid")

    approval = api_client.post(
        f"{base_url}/api/admin/listings/{listing['id']}/moderate",
        headers=_auth(admin_auth["access_token"]),
        json={"action": "approve", "reason": "manual approval"},
        timeout=30,
    )
    assert approval.status_code == 409


def test_generic_payment_confirmation_is_disabled(
    base_url, api_client, make_user
):
    seller = make_user("SEC_INTENT")
    listing = _create_listing(base_url, api_client, seller["access_token"], "intent")
    intent = api_client.post(
        f"{base_url}/api/crypto/payment-intent",
        headers=_auth(seller["access_token"]),
        json={
            "amount": listing["listing_fee"]["amount"],
            "token": listing["listing_fee"]["token"],
            "network": listing["listing_fee"]["network"],
            "purpose": "listing_fee",
            "target_id": listing["id"],
        },
        timeout=30,
    )
    assert intent.status_code == 200, intent.text
    assert intent.json()["receiver_wallet"] == listing["listing_fee"]["receiver_wallet"]

    submit = api_client.post(
        f"{base_url}/api/crypto/payment/{intent.json()['id']}/submit-tx",
        headers=_auth(seller["access_token"]),
        json={"tx_hash": f"0xSECURITY{uuid.uuid4().hex}"},
        timeout=30,
    )
    assert submit.status_code == 400


def test_e2ee_key_is_pinned_and_admin_cannot_read_messages(
    base_url, api_client, make_user, admin_auth
):
    seller = make_user("SEC_E2EE_SELL")
    buyer = make_user("SEC_E2EE_BUY")
    seller_key = _random_b64(32)
    buyer_key = _random_b64(32)

    first_key = api_client.post(
        f"{base_url}/api/me/e2ee-key",
        headers=_auth(seller["access_token"]),
        json={"public_key": seller_key},
        timeout=30,
    )
    assert first_key.status_code == 200, first_key.text
    replacement = api_client.post(
        f"{base_url}/api/me/e2ee-key",
        headers=_auth(seller["access_token"]),
        json={"public_key": _random_b64(32)},
        timeout=30,
    )
    assert replacement.status_code == 409
    buyer_registered = api_client.post(
        f"{base_url}/api/me/e2ee-key",
        headers=_auth(buyer["access_token"]),
        json={"public_key": buyer_key},
        timeout=30,
    )
    assert buyer_registered.status_code == 200, buyer_registered.text

    listing = _create_listing(base_url, api_client, seller["access_token"], "e2ee")
    paid = _pay_listing(
        base_url,
        api_client,
        seller["access_token"],
        listing,
        f"0xSECURITY{uuid.uuid4().hex}",
    )
    assert paid.status_code == 200, paid.text
    tx = api_client.post(
        f"{base_url}/api/transactions",
        headers=_auth(buyer["access_token"]),
        json={"listing_id": listing["id"]},
        timeout=30,
    )
    assert tx.status_code == 200, tx.text
    tx_id = tx.json()["id"]
    key_id = _random_b64(32)

    room = api_client.post(
        f"{base_url}/api/transactions/{tx_id}/e2ee/initialize",
        headers=_auth(buyer["access_token"]),
        json={
            "key_id": key_id,
            "envelopes": [
                {
                    "recipient_id": seller["user"]["id"],
                    "sender_public_key": buyer_key,
                    "ciphertext": _random_b64(48),
                    "nonce": _random_b64(24),
                },
                {
                    "recipient_id": buyer["user"]["id"],
                    "sender_public_key": buyer_key,
                    "ciphertext": _random_b64(48),
                    "nonce": _random_b64(24),
                },
            ],
        },
        timeout=30,
    )
    assert room.status_code == 200, room.text
    seller_context = api_client.get(
        f"{base_url}/api/transactions/{tx_id}/e2ee",
        headers=_auth(seller["access_token"]),
        timeout=30,
    )
    assert seller_context.status_code == 200, seller_context.text
    assert seller_context.json()["room"]["envelope"]["recipient_id"] == seller["user"]["id"]
    assert "envelopes" not in seller_context.json()["room"]

    malformed = api_client.post(
        f"{base_url}/api/transactions/{tx_id}/messages",
        headers=_auth(buyer["access_token"]),
        json={
            "client_message_id": str(uuid.uuid4()),
            "ciphertext": _random_b64(48),
            "nonce": _random_b64(12),
            "key_id": key_id,
            "encryption_version": "nacl-secretbox-v1",
            "message_type": "text",
        },
        timeout=30,
    )
    assert malformed.status_code in {400, 422}

    retention = api_client.patch(
        f"{base_url}/api/me",
        headers=_auth(buyer["access_token"]),
        json={"auto_delete_messages_days": 1},
        timeout=30,
    )
    assert retention.status_code == 200, retention.text
    message = api_client.post(
        f"{base_url}/api/transactions/{tx_id}/messages",
        headers=_auth(buyer["access_token"]),
        json={
            "client_message_id": str(uuid.uuid4()),
            "ciphertext": _random_b64(48),
            "nonce": _random_b64(24),
            "key_id": key_id,
            "encryption_version": "nacl-secretbox-v1",
            "message_type": "text",
            "expires_in_days": 90,
        },
        timeout=30,
    )
    assert message.status_code == 200, message.text
    assert "plaintext" not in message.json()
    expires_at = datetime.fromisoformat(message.json()["expires_at"].replace("Z", "+00:00"))
    assert 0.9 < (expires_at - datetime.now(timezone.utc)).total_seconds() / 86400 < 1.1
    replay = api_client.post(
        f"{base_url}/api/transactions/{tx_id}/messages",
        headers=_auth(buyer["access_token"]),
        json=message.json(),
        timeout=30,
    )
    assert replay.status_code == 409

    admin_read = api_client.get(
        f"{base_url}/api/transactions/{tx_id}/messages",
        headers=_auth(admin_auth["access_token"]),
        timeout=30,
    )
    assert admin_read.status_code == 403
    admin_context = api_client.get(
        f"{base_url}/api/transactions/{tx_id}/e2ee",
        headers=_auth(admin_auth["access_token"]),
        timeout=30,
    )
    assert admin_context.status_code == 403
