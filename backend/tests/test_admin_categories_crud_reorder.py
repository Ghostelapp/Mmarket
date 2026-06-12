import time
import uuid


# Category admin helpers: CRUD/reorder/list + listing integration assertions
def _admin_headers(admin_auth: dict) -> dict:
    return {"Authorization": f"Bearer {admin_auth['access_token']}"}


def _create_category(base_url, api_client, admin_auth, slug_prefix: str = "test-cat") -> dict:
    slug = f"{slug_prefix}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    payload = {
        "name": f"TEST {slug}",
        "slug": slug,
        "icon": "apps-outline",
        "color": "#12AB34",
        "sort_order": 900,
    }
    response = api_client.post(
        f"{base_url}/api/admin/categories",
        headers=_admin_headers(admin_auth),
        json=payload,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["slug"] == slug
    assert data["is_active"] is True
    return data


def _create_listing(base_url, api_client, token: str, category_slug: str, title_suffix: str) -> dict:
    payload = {
        "title": f"TEST listing {title_suffix}",
        "description": "TEST description long enough for category/listing policy verification.",
        "price_fiat": 120,
        "fiat_currency": "PLN",
        "category": category_slug,
        "condition": "nowy",
        "location_public": "TEST_Krakow",
        "shipping_options": ["blind-delivery"],
        "images": [],
    }
    response = api_client.post(
        f"{base_url}/api/listings",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _pay_listing_fee(base_url, api_client, token: str, listing: dict, tx_hash: str) -> dict:
    tx_hash = f"{tx_hash}-{uuid.uuid4().hex}"
    response = api_client.post(
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
    assert response.status_code == 200, response.text
    return response.json()


# Admin categories CRUD: create/read/update/delete contract + user visibility
def test_admin_categories_crud_and_user_active_filter(base_url, api_client, admin_auth):
    created = _create_category(base_url, api_client, admin_auth, "test-visible")

    admin_list = api_client.get(
        f"{base_url}/api/admin/categories",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    assert admin_list.status_code == 200
    admin_items = admin_list.json()
    assert any(item["id"] == created["id"] for item in admin_items)

    patch = api_client.patch(
        f"{base_url}/api/admin/categories/{created['id']}",
        headers=_admin_headers(admin_auth),
        json={"name": "TEST Updated Category", "color": "#AA22CC", "icon": "star-outline"},
        timeout=30,
    )
    assert patch.status_code == 200, patch.text
    patched = patch.json()
    assert patched["name"] == "TEST Updated Category"
    assert patched["color"] == "#AA22CC"

    hide = api_client.patch(
        f"{base_url}/api/admin/categories/{created['id']}",
        headers=_admin_headers(admin_auth),
        json={"is_active": False},
        timeout=30,
    )
    assert hide.status_code == 200, hide.text
    assert hide.json()["is_active"] is False

    public_list = api_client.get(f"{base_url}/api/categories", timeout=30)
    assert public_list.status_code == 200
    public_slugs = [item["slug"] for item in public_list.json()]
    assert created["slug"] not in public_slugs

    activate = api_client.patch(
        f"{base_url}/api/admin/categories/{created['id']}",
        headers=_admin_headers(admin_auth),
        json={"is_active": True},
        timeout=30,
    )
    assert activate.status_code == 200, activate.text
    assert activate.json()["is_active"] is True

    delete = api_client.delete(
        f"{base_url}/api/admin/categories/{created['id']}",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    assert delete.status_code == 200, delete.text

    admin_after_delete = api_client.get(
        f"{base_url}/api/admin/categories",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    assert admin_after_delete.status_code == 200
    assert all(item["id"] != created["id"] for item in admin_after_delete.json())


# Admin reorder endpoint: updates persisted sort_order values
def test_admin_reorder_categories(base_url, api_client, admin_auth):
    first = _create_category(base_url, api_client, admin_auth, "test-reorder-a")
    second = _create_category(base_url, api_client, admin_auth, "test-reorder-b")

    reorder = api_client.post(
        f"{base_url}/api/admin/categories/reorder",
        headers=_admin_headers(admin_auth),
        json={
            "items": [
                {"id": first["id"], "sort_order": 11},
                {"id": second["id"], "sort_order": 10},
            ]
        },
        timeout=30,
    )
    assert reorder.status_code == 200, reorder.text

    admin_list = api_client.get(
        f"{base_url}/api/admin/categories",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    assert admin_list.status_code == 200
    by_id = {item["id"]: item for item in admin_list.json()}
    assert by_id[first["id"]]["sort_order"] == 11
    assert by_id[second["id"]]["sort_order"] == 10

    api_client.delete(
        f"{base_url}/api/admin/categories/{first['id']}",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    api_client.delete(
        f"{base_url}/api/admin/categories/{second['id']}",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )


# Category lifecycle policy: disable/delete auto-hides linked listings; re-enable restores review state
def test_category_disable_reenable_and_delete_affects_linked_listings(base_url, api_client, admin_auth, make_user):
    seller = make_user("TEST_CAT_POLICY")
    token = seller["access_token"]

    category = _create_category(base_url, api_client, admin_auth, "test-policy")
    listing = _create_listing(base_url, api_client, token, category["slug"], "policy-hide")
    _pay_listing_fee(base_url, api_client, token, listing, "0xTESTCATPOLICYFEE001")

    hide = api_client.patch(
        f"{base_url}/api/admin/categories/{category['id']}",
        headers=_admin_headers(admin_auth),
        json={"is_active": False},
        timeout=30,
    )
    assert hide.status_code == 200

    listing_after_hide = api_client.get(
        f"{base_url}/api/listings/{listing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert listing_after_hide.status_code == 200
    hidden_data = listing_after_hide.json()
    assert hidden_data["status"] == "HIDDEN_CATEGORY"
    assert hidden_data["moderation_status"] == "CATEGORY_DISABLED"

    activate = api_client.patch(
        f"{base_url}/api/admin/categories/{category['id']}",
        headers=_admin_headers(admin_auth),
        json={"is_active": True},
        timeout=30,
    )
    assert activate.status_code == 200

    listing_after_activate = api_client.get(
        f"{base_url}/api/listings/{listing['id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert listing_after_activate.status_code == 200
    restored_data = listing_after_activate.json()
    assert restored_data["status"] == "UNDER_REVIEW"
    assert restored_data["moderation_status"] == "PENDING"

    listing2 = _create_listing(base_url, api_client, token, category["slug"], "policy-delete")
    _pay_listing_fee(base_url, api_client, token, listing2, "0xTESTCATPOLICYFEE002")

    delete = api_client.delete(
        f"{base_url}/api/admin/categories/{category['id']}",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
    assert delete.status_code == 200, delete.text

    listing2_after_delete = api_client.get(
        f"{base_url}/api/listings/{listing2['id']}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert listing2_after_delete.status_code == 200
    deleted_policy_data = listing2_after_delete.json()
    assert deleted_policy_data["status"] == "HIDDEN_CATEGORY"
    assert deleted_policy_data["moderation_status"] == "CATEGORY_DISABLED"


# Listing creation validation: inactive or missing category must be rejected
def test_listing_create_rejects_inactive_and_missing_category(base_url, api_client, admin_auth, make_user):
    seller = make_user("TEST_CAT_REJECT")
    token = seller["access_token"]

    category = _create_category(base_url, api_client, admin_auth, "test-reject")
    deactivate = api_client.patch(
        f"{base_url}/api/admin/categories/{category['id']}",
        headers=_admin_headers(admin_auth),
        json={"is_active": False},
        timeout=30,
    )
    assert deactivate.status_code == 200

    inactive_create = api_client.post(
        f"{base_url}/api/listings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "TEST inactive category listing",
            "description": "TEST description for inactive category validation checks.",
            "price_fiat": 49,
            "fiat_currency": "PLN",
            "category": category["slug"],
            "condition": "nowy",
            "location_public": "TEST_Lodz",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert inactive_create.status_code == 400

    missing_create = api_client.post(
        f"{base_url}/api/listings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "TEST missing category listing",
            "description": "TEST description for missing category validation checks.",
            "price_fiat": 59,
            "fiat_currency": "PLN",
            "category": f"missing-{uuid.uuid4().hex[:8]}",
            "condition": "nowy",
            "location_public": "TEST_Gdynia",
            "shipping_options": ["blind-delivery"],
            "images": [],
        },
        timeout=30,
    )
    assert missing_create.status_code == 400

    api_client.delete(
        f"{base_url}/api/admin/categories/{category['id']}",
        headers=_admin_headers(admin_auth),
        timeout=30,
    )
