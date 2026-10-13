"""Public readiness fails safely; hosting does not imply successful provider responses."""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from portwatch.cloud import public_app
from portwatch.settings import Settings


def test_hosted_readiness_checks_database_and_keeps_errors_private(monkeypatch):
    import portwatch.cloud as cloud

    @contextmanager
    def unavailable(settings):
        raise RuntimeError("synthetic_password_do_not_expose")
        yield

    monkeypatch.setattr(cloud, "connect_db", unavailable)
    with TestClient(public_app(Settings(ais_api_key=None))) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert "synthetic_password" not in response.text


def test_hosted_readiness_does_not_ingest_or_require_live_keys(monkeypatch):
    import portwatch.cloud as cloud

    class Connection:
        def execute(self, query):
            assert query == "SELECT 1 FROM schema_migrations LIMIT 1"
            return self

        def fetchone(self):
            return (1,)

    @contextmanager
    def connected(settings):
        yield Connection()

    monkeypatch.setattr(cloud, "connect_db", connected)
    with TestClient(public_app(Settings(ais_api_key=None))) as client:
        response = client.get("/readyz")
        assert response.status_code == 200 and response.json() == {"status": "ready"}
