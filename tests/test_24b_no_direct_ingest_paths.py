from pathlib import Path

from core.data import DatasetRegistry, ResearchIngressGate
from core.data.manifest import DatasetManifest


def test_direct_ticker_csv_dataframe_ingest_blocked():
    reg = DatasetRegistry(schema_version='1.0.0', registry_id='r1', created_at='2026-08-31T00:00:00+00:00')
    gate = ResearchIngressGate(reg)
    assert gate.block_direct_ingest('SBER').allowed is False
    assert gate.block_direct_ingest('/tmp/SBER.csv').allowed is False
    class Dummy:
        columns = ['time', 'open']
    assert gate.block_direct_ingest(Dummy()).allowed is False


def test_unknown_schema_version_rejected(tmp_path: Path):
    p = tmp_path / 'registry.json'
    p.write_text('{"schema_version":"9.9.9","registry_id":"r","created_at":"now","datasets":{}}', encoding='utf-8')
    try:
        DatasetRegistry.load(p)
    except Exception as exc:
        assert 'UNKNOWN_SCHEMA_VERSION' in str(exc)
    else:
        raise AssertionError('expected schema rejection')
