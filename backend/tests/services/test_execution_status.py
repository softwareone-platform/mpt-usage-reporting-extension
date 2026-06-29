from mpt_usage_reporting_extension.persistence.models import ExecutionRecord
from mpt_usage_reporting_extension.services.execution_status import StatusReport


def test_report_prints_table_rows(capsys):
    records = [
        ExecutionRecord("run", "success", "2026-06-01T00:00:00Z", "2026-06-01T00:01:00Z"),
        ExecutionRecord("cleanup", "running", "2026-06-02T00:00:00Z", None),
    ]

    StatusReport(records).render()  # act

    out = capsys.readouterr().out
    assert "Command" in out
    assert "run" in out
    assert "cleanup" in out
    assert "-" in out


def test_report_prints_notice_when_empty(capsys):
    StatusReport([]).render()  # act

    out = capsys.readouterr().out
    assert "No command executions recorded yet." in out
    assert "Command" not in out
