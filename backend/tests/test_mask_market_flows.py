import pyotp


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


def test_passkey_register_and_login(base_url, api_client, make_user):
    user = make_user("TEST_PASSKEY")
    auth_header = {"Authorization": f"Bearer {user['access_token']}"}
    credential_id = "cred-test-passkey-001"

    register_pk = api_client.post(
        f"{base_url}/api/auth/passkey/register",
        headers=auth_header,
        json={"credential_id": credential_id, "public_key": "pk-test-value", "nickname": "pytest"},
        timeout=30,
    )
    assert register_pk.status_code == 200

    passkey_login = api_client.post(
        f"{base_url}/api/auth/passkey/login",
        json={
            "email": user["register"]["email"],
            "credential_id": credential_id,
            "device_name": "pytest-passkey",
        },
        timeout=30,
    )
    assert passkey_login.status_code == 200


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
            "payment_tx_hash": "0xTESTLISTINGFEE001",
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
            "payment_tx_hash": "0xTESTTXLISTFEE001",
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
            "tx_hash": "0xTESTFUNDTX001",
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

    msg_send = api_client.post(
        f"{base_url}/api/transactions/{tx['id']}/messages",
        headers=buyer_header,
        json={"ciphertext": "YQ==", "nonce": "nonce-1", "message_type": "text", "expires_in_days": 14},
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
