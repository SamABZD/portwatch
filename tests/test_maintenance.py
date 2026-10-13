"""A metadata outage must not prevent the independent database backup."""

from portwatch.maintenance import maintenance_report
from portwatch.settings import Settings


def test_backup_runs_when_prefect_retention_fails_and_errors_stay_private(monkeypatch):
    import portwatch.maintenance as maintenance

    async def failed(_):
        raise RuntimeError("synthetic_secret_do_not_expose")

    monkeypatch.setenv("PORTWATCH_CLOUD_LIVE", "1")
    monkeypatch.setattr(maintenance, "retain_runs", failed)
    monkeypatch.setattr(maintenance, "backup_databases", lambda _: [{"status": "complete"}])
    report = maintenance_report(Settings())
    assert report["status"] == "degraded"
    assert report["backups"] == [{"status": "complete"}]
    assert report["metadata_error_class"] == "RuntimeError"
    assert "synthetic_secret" not in str(report)
