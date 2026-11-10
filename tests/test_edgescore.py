import importlib

import pytest
from fastapi.testclient import TestClient

LOCAL = {"host": "localhost:8765"}
AUTH = {**LOCAL, "X-Donna-Token": "tok"}


def _client(tmp_path, monkeypatch, token):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("DONNA_FILES_DIR", str(tmp_path / "files"))
    if token:
        monkeypatch.setenv("DONNA_EDGESCORE_TOKEN", token)
    else:
        monkeypatch.delenv("DONNA_EDGESCORE_TOKEN", raising=False)
    from donna import console
    monkeypatch.setattr(console, "LOCAL_CLIENTS", {"testclient"})
    import donna.app as m
    return TestClient(importlib.reload(m).app)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "tok") as c:
        yield c


def test_refuses_until_token_is_set_up(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, "") as c:
        r = c.post("/api/edgescore", headers={**LOCAL, "X-Donna-Token": ""}, json={"symbol": "XAUUSD"})
        assert r.status_code == 503 and "vertex1-connect edgescore" in r.text


def test_needs_tunnel_and_token(client):
    body = {"symbol": "XAUUSD", "score": 7}
    assert client.post("/api/edgescore", headers=LOCAL, json=body).status_code == 401
    assert client.post("/api/edgescore", headers={**LOCAL, "X-Donna-Token": "bad"}, json=body).status_code == 401
    assert client.post("/api/edgescore", headers={**AUTH, "X-Forwarded-For": "1.2.3.4"}, json=body).status_code == 404
    assert client.post("/api/edgescore", headers={**AUTH, "host": "evil.example"}, json=body).status_code == 404


def test_stores_object_and_list_and_reads_back(client):
    r = client.post("/api/edgescore", headers=AUTH, json={"symbol": "XAUUSD", "score": 7.5, "bias": "long"})
    assert r.status_code == 200 and r.json()["stored"] == 1
    bearer = {**LOCAL, "Authorization": "Bearer tok"}
    r = client.post("/api/edgescore", headers=bearer, json=[{"ticker": "EURUSD"}, {"note": "x"}])
    assert r.json()["stored"] == 2
    items = client.get("/api/edgescore/latest", headers=AUTH).json()["items"]
    assert len(items) == 3 and items[-1]["symbol"] == "XAUUSD"
    assert "score=7.5" in items[-1]["summary"] and items[-1]["payload"]["bias"] == "long"


def test_rejects_bad_bodies(client):
    assert client.post("/api/edgescore", headers=AUTH, content=b"not json").status_code == 400
    assert client.post("/api/edgescore", headers=AUTH, json=[1, 2]).status_code == 422
    assert client.post("/api/edgescore", headers=AUTH, content=b"x" * (300 * 1024)).status_code == 413
