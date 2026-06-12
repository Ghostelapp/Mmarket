import base64
import os
import uuid

import pyotp
from eth_account import Account
from eth_account.messages import encode_defunct


# Auth: register/login/refresh/logout + 2FA + passkeys
def test_auth_register_login_refresh_logout(base_url, api_client, make_user):
    user = make_user("TEST_AUTH")

    me = api_client.get(
        f"{base_url}/api/me",
        headers={"Authorization": f"Bearer {user['access_token']}"},
        timeout=30,
    )
    assert me.status_code == 200

    refresh = api_client.post(
        f"{base_url}/api/auth/refresh",
        json={"refresh_token": user["refresh_token"], "session_id": user["session_id"]},
        timeout=30,
    )
    assert refresh.status_code == 200

    refreshed_access = refresh.json()["access_token"]
    logout = api_client.post(
        f"{base_url}/api/auth/logout",
        headers={"Authorization": f"Bearer {refreshed_access}"},
        json={"session_id": user["session_id"]},
        timeout=30,
    )
    assert logout.status_code == 200


def test_wallet_login_creates_session_and_rejects_replay(base_url, api_client):
    wallet = Account.create()
    challenge = api_client.post(
        f"{base_url}/api/auth/wallet/challenge",
        json={"wallet_address": wallet.address},
        timeout=30,
    )
    assert challenge.status_code == 200, challenge.text
    challenge_data = challenge.json()
    assert "wants you to sign in with your Ethereum account" in challenge_data["message"]
    assert "\nURI: " in challenge_data["message"]
    assert "\nVersion: 1\nChain ID: " in challenge_data["message"]
    signature = Account.sign_message(
        encode_defunct(text=challenge_data["message"]),
        wallet.key,
    ).signature.hex()

    login_payload = {
        "wallet_address": wallet.address,
        "challenge_id": challenge_data["challenge_id"],
        "signature": signature,
        "device_name": "pytest-wallet",
    }
    login = api_client.post(
        f"{base_url}/api/auth/wallet/login",
        json=login_payload,
        timeout=30,
    )
    assert login.status_code == 200, login.text
    login_data = login.json()
    assert login_data["access_token"]
    assert login_data["refresh_token"]
    assert login_data["session_id"]

    me = api_client.get(
        f"{base_url}/api/me",
        headers={"Authorization": f"Bearer {login_data['access_token']}"},
        timeout=30,
    )
    assert me.status_code == 200, me.text
    assert me.json()["id"] == login_data["user"]["id"]

    replay = api_client.post(
        f"{base_url}/api/auth/wallet/login",
        json=login_payload,
        timeout=30,
    )
    assert replay.status_code == 400

    next_challenge = api_client.post(
        f"{base_url}/api/auth/wallet/challenge",
        json={"wallet_address": wallet.address},
        timeout=30,
    )
    assert next_challenge.status_code == 200, next_challenge.text
    next_challenge_data = next_challenge.json()
    next_signature = Account.sign_message(
        encode_defunct(text=next_challenge_data["message"]),
        wallet.key,
    ).signature.hex()
    next_login = api_client.post(
        f"{base_url}/api/auth/wallet/login",
        json={
            "wallet_address": wallet.address,
            "challenge_id": next_challenge_data["challenge_id"],
            "signature": next_signature,
            "device_name": "pytest-wallet-second-session",
        },
        timeout=30,
    )
    assert next_login.status_code == 200, next_login.text
    assert next_login.json()["user"]["id"] == login_data["user"]["id"]


def test_panic_lock_can_be_recovered_with_account_credentials(base_url, api_client, make_user):
    user = make_user("TEST_PANIC_RECOVERY")
    locked = api_client.post(
        f"{base_url}/api/me/panic-lock",
        headers={"Authorization": f"Bearer {user['access_token']}"},
        timeout=30,
    )
    assert locked.status_code == 200, locked.text
    assert "wypłaty" not in locked.json()["message"]

    unlock = api_client.post(
        f"{base_url}/api/auth/panic-unlock",
        json={
            "email": user["register"]["email"],
            "password": user["register"]["password"],
        },
        timeout=30,
    )
    assert unlock.status_code == 200, unlock.text

    login = api_client.post(
        f"{base_url}/api/auth/login",
        json={
            "email": user["register"]["email"],
            "password": user["register"]["password"],
            "device_name": "panic-recovery",
        },
        timeout=30,
    )
    assert login.status_code == 200, login.text


def test_wallet_login_rejects_signature_from_another_wallet(base_url, api_client):
    requested_wallet = Account.create()
    signing_wallet = Account.create()
    challenge = api_client.post(
        f"{base_url}/api/auth/wallet/challenge",
        json={"wallet_address": requested_wallet.address},
        timeout=30,
    )
    assert challenge.status_code == 200, challenge.text
    challenge_data = challenge.json()
    wrong_signature = Account.sign_message(
        encode_defunct(text=challenge_data["message"]),
        signing_wallet.key,
    ).signature.hex()

    login = api_client.post(
        f"{base_url}/api/auth/wallet/login",
        json={
            "wallet_address": requested_wallet.address,
            "challenge_id": challenge_data["challenge_id"],
            "signature": wrong_signature,
            "device_name": "pytest-wallet-wrong-signature",
        },
        timeout=30,
    )
    assert login.status_code == 401


def test_wallet_signature_recovers_wallet_only_panic_lock(base_url, api_client):
    wallet = Account.create()

    def login_wallet():
        challenge = api_client.post(
            f"{base_url}/api/auth/wallet/challenge",
            json={"wallet_address": wallet.address},
            timeout=30,
        )
        assert challenge.status_code == 200, challenge.text
        signature = Account.sign_message(
            encode_defunct(text=challenge.json()["message"]),
            wallet.key,
        ).signature.hex()
        return api_client.post(
            f"{base_url}/api/auth/wallet/login",
            json={
                "wallet_address": wallet.address,
                "challenge_id": challenge.json()["challenge_id"],
                "signature": signature,
                "device_name": "wallet-panic-recovery",
            },
            timeout=30,
        )

    first = login_wallet()
    assert first.status_code == 200, first.text
    locked = api_client.post(
        f"{base_url}/api/me/panic-lock",
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
        timeout=30,
    )
    assert locked.status_code == 200, locked.text
    recovered = login_wallet()
    assert recovered.status_code == 200, recovered.text


def test_existing_account_can_link_signed_wallet(base_url, api_client, make_user):
    user = make_user("TEST_LINK_WALLET")
    wallet = Account.create()
    challenge = api_client.post(
        f"{base_url}/api/auth/wallet/challenge",
        json={"wallet_address": wallet.address},
        timeout=30,
    )
    assert challenge.status_code == 200, challenge.text
    challenge_data = challenge.json()
    signature = Account.sign_message(
        encode_defunct(text=challenge_data["message"]),
        wallet.key,
    ).signature.hex()
    linked = api_client.post(
        f"{base_url}/api/me/wallets/link",
        headers={"Authorization": f"Bearer {user['access_token']}"},
        json={
            "wallet_address": wallet.address,
            "challenge_id": challenge_data["challenge_id"],
            "signature": signature,
        },
        timeout=30,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["wallets"][0]["address"] == wallet.address.lower()
    assert linked.json()["wallets"][0]["is_primary"] is True

    second_wallet = Account.create()
    second_challenge = api_client.post(
        f"{base_url}/api/auth/wallet/challenge",
        json={"wallet_address": second_wallet.address},
        timeout=30,
    ).json()
    second_signature = Account.sign_message(
        encode_defunct(text=second_challenge["message"]),
        second_wallet.key,
    ).signature.hex()
    second_link = api_client.post(
        f"{base_url}/api/me/wallets/link",
        headers={"Authorization": f"Bearer {user['access_token']}"},
        json={
            "wallet_address": second_wallet.address,
            "challenge_id": second_challenge["challenge_id"],
            "signature": second_signature,
        },
        timeout=30,
    )
    assert second_link.status_code == 200, second_link.text
    primary = api_client.post(
        f"{base_url}/api/me/wallets/primary",
        headers={"Authorization": f"Bearer {user['access_token']}"},
        json={"wallet_address": second_wallet.address},
        timeout=30,
    )
    assert primary.status_code == 200, primary.text
    assert next(item for item in primary.json()["wallets"] if item["is_primary"])["address"] == second_wallet.address.lower()


def test_2fa_enable_verify_and_login_with_otp(base_url, api_client, make_user):
    user = make_user("TEST_2FA")
    auth_header = {"Authorization": f"Bearer {user['access_token']}"}

    enable = api_client.post(
        f"{base_url}/api/auth/2fa/enable",
        headers=auth_header,
        json={"account_password": user["register"]["password"]},
        timeout=30,
    )
    assert enable.status_code == 200
    secret = enable.json()["secret"]

    otp_code = pyotp.TOTP(secret).now()
    verify = api_client.post(
        f"{base_url}/api/auth/2fa/verify",
        headers=auth_header,
        json={"code": otp_code},
        timeout=30,
    )
    assert verify.status_code == 200

    login_without_otp = api_client.post(
        f"{base_url}/api/auth/login",
        json={
            "email": user["register"]["email"],
            "password": user["register"]["password"],
            "device_name": "pytest-2fa-check",
        },
        timeout=30,
    )
    assert login_without_otp.status_code == 401

    login_with_otp = api_client.post(
        f"{base_url}/api/auth/login",
        json={
            "email": user["register"]["email"],
            "password": user["register"]["password"],
            "otp_code": pyotp.TOTP(secret).now(),
            "device_name": "pytest-2fa-check",
        },
        timeout=30,
    )
    assert login_with_otp.status_code == 200


def test_legacy_passkey_shortcut_is_rejected(base_url, api_client, make_user):
    user = make_user("TEST_PASSKEY")
    auth_header = {"Authorization": f"Bearer {user['access_token']}"}
    credential_id = "cred-test-passkey-001"

    register_pk = api_client.post(
        f"{base_url}/api/auth/passkey/register",
        headers=auth_header,
        json={"credential_id": credential_id, "public_key": "pk-test-value", "nickname": "pytest"},
        timeout=30,
    )
    assert register_pk.status_code == 422


# Listings: create + pay listing fee + moderation
def test_listing_create_pay_fee_and_admin_moderation(base_url, api_client, make_user, admin_auth):
    seller = make_user("TEST_SELLER")
    seller_header = {"Authorization": f"Bearer {seller['access_token']}"}

    create_listing = api_client.post(
        f"{base_url}/api/listings",
        headers=seller_header,
        json={
            "title": "TEST listing cyber lamp",
            "description": "TEST description long enough for listing validation in backend.",
            "price_fiat": 120,
            "fiat_currency": "PLN",
            "category": "elektronika",
            "condition": "nowy",
            "location_public": "TEST_Gdansk",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert create_listing.status_code == 200
    listing = create_listing.json()

    pay_fee = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/pay-listing-fee",
        headers=seller_header,
        json={
            "amount": listing["listing_fee"]["amount"],
            "token": listing["listing_fee"]["token"],
            "network": listing["listing_fee"]["network"],
            "payment_tx_hash": f"0xTESTLISTINGFEE001-{uuid.uuid4().hex}",
        },
        timeout=30,
    )
    assert pay_fee.status_code == 200

    admin_header = {"Authorization": f"Bearer {admin_auth['access_token']}"}
    moderate = api_client.post(
        f"{base_url}/api/admin/listings/{listing['id']}/moderate",
        headers=admin_header,
        json={"action": "approve", "reason": "TEST moderation approval"},
        timeout=30,
    )
    assert moderate.status_code == 200


# Transactions + deal room + dispute + admin resolve
def test_transaction_escrow_deal_room_dispute_admin_resolve(base_url, api_client, make_user, admin_auth):
    seller = make_user("TEST_TX_SELLER")
    buyer = make_user("TEST_TX_BUYER")

    seller_header = {"Authorization": f"Bearer {seller['access_token']}"}
    buyer_header = {"Authorization": f"Bearer {buyer['access_token']}"}
    admin_header = {"Authorization": f"Bearer {admin_auth['access_token']}"}

    listing_resp = api_client.post(
        f"{base_url}/api/listings",
        headers=seller_header,
        json={
            "title": "TEST tx listing",
            "description": "TEST transaction listing description with required size.",
            "price_fiat": 220,
            "fiat_currency": "EUR",
            "category": "elektronika",
            "condition": "uzywany",
            "location_public": "TEST_Poznan",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert listing_resp.status_code == 200
    listing = listing_resp.json()

    pay_fee = api_client.post(
        f"{base_url}/api/listings/{listing['id']}/pay-listing-fee",
        headers=seller_header,
        json={
            "amount": listing["listing_fee"]["amount"],
            "token": listing["listing_fee"]["token"],
            "network": listing["listing_fee"]["network"],
            "payment_tx_hash": f"0xTESTTXLISTFEE001-{uuid.uuid4().hex}",
        },
        timeout=30,
    )
    assert pay_fee.status_code == 200

    tx_create = api_client.post(
        f"{base_url}/api/transactions",
        headers=buyer_header,
        json={"listing_id": listing["id"]},
        timeout=30,
    )
    assert tx_create.status_code == 200
    tx = tx_create.json()

    fund = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/fund",
        headers=buyer_header,
        json={
            "amount": tx["gross_amount"],
            "token": tx["token"],
            "network": tx["network"],
            "tx_hash": f"0xTESTFUNDTX001-{uuid.uuid4().hex}",
        },
        timeout=30,
    )
    assert fund.status_code == 200

    shipped = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/mark-shipped",
        headers=seller_header,
        json={"encrypted_address_blob": "cipher-address", "carrier": "InPost"},
        timeout=30,
    )
    assert shipped.status_code == 200

    seller_public_key = base64.b64encode(os.urandom(32)).decode()
    buyer_public_key = base64.b64encode(os.urandom(32)).decode()
    for header, public_key in [
        (seller_header, seller_public_key),
        (buyer_header, buyer_public_key),
    ]:
        registered = api_client.post(
            f"{base_url}/api/me/e2ee-key",
            headers=header,
            json={"public_key": public_key},
            timeout=30,
        )
        assert registered.status_code == 200, registered.text

    key_id = base64.b64encode(os.urandom(32)).decode()
    room_init = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/e2ee/initialize",
        headers=buyer_header,
        json={
            "key_id": key_id,
            "envelopes": [
                {
                    "recipient_id": seller["user"]["id"],
                    "sender_public_key": buyer_public_key,
                    "ciphertext": base64.b64encode(os.urandom(48)).decode(),
                    "nonce": base64.b64encode(os.urandom(24)).decode(),
                },
                {
                    "recipient_id": buyer["user"]["id"],
                    "sender_public_key": buyer_public_key,
                    "ciphertext": base64.b64encode(os.urandom(48)).decode(),
                    "nonce": base64.b64encode(os.urandom(24)).decode(),
                },
            ],
        },
        timeout=30,
    )
    assert room_init.status_code == 200, room_init.text

    msg_send = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/messages",
        headers=buyer_header,
        json={
            "client_message_id": str(uuid.uuid4()),
            "ciphertext": base64.b64encode(os.urandom(48)).decode(),
            "nonce": base64.b64encode(os.urandom(24)).decode(),
            "key_id": key_id,
            "encryption_version": "nacl-secretbox-v1",
            "message_type": "text",
            "expires_in_days": 14,
        },
        timeout=30,
    )
    assert msg_send.status_code == 200

    msg_get = api_client.get(
        f"{base_url}/api/transactions/{tx['id']}/messages",
        headers=buyer_header,
        timeout=30,
    )
    assert msg_get.status_code == 200

    open_dispute = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/open-dispute",
        headers=buyer_header,
        json={"reason": "TEST buyer reports issue with delivered item"},
        timeout=30,
    )
    assert open_dispute.status_code == 200
    dispute_id = open_dispute.json()["dispute_id"]

    resolve = api_client.post(
        f"{base_url}/api/admin/disputes/{dispute_id}/resolve",
        headers=admin_header,
        json={"decision": "refund_buyer", "reason": "TEST admin resolve"},
        timeout=30,
    )
    assert resolve.status_code == 200


# Admin dashboard/users/listings/disputes/reports/fees/wallets/audit
def test_admin_endpoints_access(admin_auth, base_url, api_client):
    header = {"Authorization": f"Bearer {admin_auth['access_token']}"}
    paths = [
        "/admin/dashboard",
        "/admin/users",
        "/admin/listings",
        "/admin/disputes",
        "/admin/reports",
        "/admin/fees",
        "/admin/platform-wallets",
        "/admin/audit-logs",
    ]
    for path in paths:
        response = api_client.get(f"{base_url}/api{path}", headers=header, timeout=30)
        assert response.status_code == 200

    paged = api_client.get(
        f"{base_url}/api/admin/users?limit=1&offset=0",
        headers=header,
        timeout=30,
    )
    assert paged.status_code == 200
    assert len(paged.json()) <= 1

    excessive_page = api_client.get(
        f"{base_url}/api/admin/users?limit=999999&offset=0",
        headers=header,
        timeout=30,
    )
    assert excessive_page.status_code == 422
