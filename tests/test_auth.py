import pytest
import uuid
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api import app

client = TestClient(app)


def _unique_email(prefix: str) -> str:
    """Generate a unique email so tests are idempotent against a persistent DB."""
    return f"{prefix}+{uuid.uuid4().hex[:8]}@test.com"


def _register_and_login(email: str, password: str, role: str = "client", **extra):
    reg = client.post("/api/auth/register", json={
        "email": email, "password": password, "role": role, **extra
    })
    assert reg.status_code == 201, reg.json()
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    return res.json()["access_token"]


def test_patch_me_cannot_change_role():
    email = _unique_email("patchrole")
    token = _register_and_login(email, "pass1234", role="client")
    res = client.patch(
        "/api/auth/me",
        json={"role": "agent"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["role"] == "client"  # role must not have changed


def test_agent_register_requires_phone_and_agency():
    res = client.post("/api/auth/register", json={
        "email": _unique_email("agentnokyc"),
        "password": "pass1234",
        "role": "agent",
    })
    assert res.status_code == 422


def test_agent_register_succeeds_with_kyc():
    res = client.post("/api/auth/register", json={
        "email": _unique_email("agentwithkyc"),
        "password": "pass1234",
        "role": "agent",
        "phone": "+212600000000",
        "agency_name": "Atlas Immo",
    })
    assert res.status_code == 201
    assert res.json()["role"] == "agent"
