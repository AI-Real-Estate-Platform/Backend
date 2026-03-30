import uuid
from fastapi.testclient import TestClient
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api import app

client = TestClient(app)


def _register_and_login(suffix: str = "") -> str:
    suffix = suffix or str(uuid.uuid4())[:8]
    email = f"convtest_{suffix}@example.com"
    reg = client.post("/api/auth/register", json={
        "email": email, "password": "pass1234", "role": "client"
    })
    assert reg.status_code == 201, reg.json()
    login = client.post("/api/auth/login", json={"email": email, "password": "pass1234"})
    assert login.status_code == 200, login.json()
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Task 2 tests ──────────────────────────────────────────────────────────────

def test_list_conversations_empty():
    token = _register_and_login()
    res = client.get("/api/user/conversations", headers=_auth(token))
    assert res.status_code == 200
    assert res.json() == []


def test_create_conversation_returns_id():
    token = _register_and_login()
    res = client.post("/api/user/conversations", json={"lang": "FR"}, headers=_auth(token))
    assert res.status_code == 201
    data = res.json()
    assert "id" in data
    assert isinstance(data["id"], int)


def test_list_conversations_after_create():
    token = _register_and_login()
    client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    res = client.get("/api/user/conversations", headers=_auth(token))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["lang"] == "EN"
    assert items[0]["title"] is None
    assert "preview" in items[0]
    assert items[0]["created_at"] is not None


# ── Task 3 tests ──────────────────────────────────────────────────────────────

def test_get_conversation_returns_messages():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]
    res = client.get(f"/api/user/conversations/{conv_id}", headers=_auth(token))
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == conv_id
    assert data["lang"] == "EN"
    assert data["messages"] == []
    assert data["title"] is None


def test_get_conversation_returns_404_for_other_user():
    token_a = _register_and_login()
    token_b = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token_a))
    conv_id = create_res.json()["id"]
    res = client.get(f"/api/user/conversations/{conv_id}", headers=_auth(token_b))
    assert res.status_code == 404


def test_delete_conversation():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]
    del_res = client.delete(f"/api/user/conversations/{conv_id}", headers=_auth(token))
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True
    # Verify it's gone
    get_res = client.get(f"/api/user/conversations/{conv_id}", headers=_auth(token))
    assert get_res.status_code == 404


def test_delete_conversation_returns_404_for_other_user():
    token_a = _register_and_login()
    token_b = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token_a))
    conv_id = create_res.json()["id"]
    res = client.delete(f"/api/user/conversations/{conv_id}", headers=_auth(token_b))
    assert res.status_code == 404


# ── Task 4 tests ──────────────────────────────────────────────────────────────

from unittest.mock import patch, MagicMock


def test_generate_title_returns_null_when_no_messages():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]
    res = client.post(f"/api/user/conversations/{conv_id}/generate-title", headers=_auth(token))
    assert res.status_code == 200
    assert res.json()["title"] is None


def test_generate_title_calls_gemini_and_saves():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]

    # Seed messages via the legacy chat-history endpoint (still active, updates the row)
    client.post("/api/user/chat-history", json={"messages": [
        {"id": "1", "type": "user", "text": "I want a 3-bedroom apartment in Casablanca"},
        {"id": "2", "type": "bot", "text": "I found several options in Casablanca for you."},
    ]}, headers=_auth(token))

    mock_response = MagicMock()
    mock_response.text = "Apartment search Casablanca"

    with patch("api.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.return_value = mock_response
        res = client.post(f"/api/user/conversations/{conv_id}/generate-title", headers=_auth(token))

    assert res.status_code == 200
    assert res.json()["title"] == "Apartment search Casablanca"

    # Verify it was persisted
    get_res = client.get(f"/api/user/conversations/{conv_id}", headers=_auth(token))
    assert get_res.json()["title"] == "Apartment search Casablanca"


def test_generate_title_returns_null_on_gemini_error():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]
    client.post("/api/user/chat-history", json={"messages": [
        {"id": "1", "type": "user", "text": "Looking for a villa"},
        {"id": "2", "type": "bot", "text": "Here are some villas."},
    ]}, headers=_auth(token))

    with patch("api.genai.Client") as mock_client_cls:
        mock_client_cls.return_value.models.generate_content.side_effect = Exception("API error")
        res = client.post(f"/api/user/conversations/{conv_id}/generate-title", headers=_auth(token))

    assert res.status_code == 200
    assert res.json()["title"] is None


# ── Task 5 tests ──────────────────────────────────────────────────────────────

from unittest.mock import patch as _patch, MagicMock as _MagicMock


def _mock_gemini_chat(reply: str = "Voici quelques appartements."):
    """Context manager that patches the Gemini chat call in /api/chat."""
    mock_resp = _MagicMock()
    mock_resp.text = reply
    return _patch("api.genai.Client", return_value=_MagicMock(
        models=_MagicMock(generate_content=_MagicMock(return_value=mock_resp))
    ))


def test_chat_saves_to_specific_conversation():
    token = _register_and_login()
    create_res = client.post("/api/user/conversations", json={"lang": "EN"}, headers=_auth(token))
    conv_id = create_res.json()["id"]

    with _mock_gemini_chat("Some reply"):
        chat_res = client.post("/api/chat", json={
            "messages": [{"id": "1", "type": "user", "text": "Hello"}],
            "filters": {},
            "conversation_id": conv_id,
        }, headers=_auth(token))

    assert chat_res.status_code == 200

    get_res = client.get(f"/api/user/conversations/{conv_id}", headers=_auth(token))
    msgs = get_res.json()["messages"]
    assert any(m["type"] == "user" and m["text"] == "Hello" for m in msgs)
    assert any(m["type"] == "bot" for m in msgs)
