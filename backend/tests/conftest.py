import os
import time
import uuid

import pytest
import requests


def _base_url() -> str:
    url = os.environ.get("EXPO_PUBLIC_BACKEND_URL")
    if not url:
        pytest.skip("EXPO_PUBLIC_BACKEND_URL is not set")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def base_url() -> str:
    return _base_url()


@pytest.fixture(scope="session")
def api_client() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="session")
def admin_credentials() -> dict:
    email = os.environ.get("TEST_ADMIN_EMAIL")
    password = os.environ.get("TEST_ADMIN_PASSWORD")
    if not email or not password:
        pytest.skip("TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD are not set")
    return {
        "email": email,
        "password": password,
        "device_name": "pytest-admin",
    }


@pytest.fixture(scope="session")
def admin_auth(base_url: str, api_client: requests.Session, admin_credentials: dict) -> dict:
    response = api_client.post(f"{base_url}/api/auth/login", json=admin_credentials, timeout=30)
    if response.status_code != 200:
        pytest.skip(f"Admin login failed: {response.status_code} {response.text}")
    data = response.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "session_id": data["session_id"],
        "user": data["user"],
    }


def unique_user_payload(prefix: str = "TEST_USER") -> dict:
    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    alias_prefix = prefix[:12]
    return {
        "email": f"{prefix.lower()}_{suffix}@example.com",
        "password": "TEST_Passw0rd!2026",
        "alias": f"TEST_{alias_prefix}_{suffix[:8]}",
        "public_location": "TEST_Warszawa",
    }


def register_and_login(base_url: str, api_client: requests.Session, prefix: str = "TEST_USER") -> dict:
    payload = unique_user_payload(prefix)
    register = api_client.post(f"{base_url}/api/auth/register", json=payload, timeout=30)
    assert register.status_code == 200, register.text

    login = api_client.post(
        f"{base_url}/api/auth/login",
        json={
            "email": payload["email"],
            "password": payload["password"],
            "device_name": f"pytest-{prefix.lower()}",
        },
        timeout=30,
    )
    assert login.status_code == 200, login.text
    data = login.json()
    return {
        "register": payload,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "session_id": data["session_id"],
        "user": data["user"],
    }


@pytest.fixture
def make_user(base_url: str, api_client: requests.Session):
    def _factory(prefix: str = "TEST_USER") -> dict:
        return register_and_login(base_url, api_client, prefix=prefix)

    return _factory
