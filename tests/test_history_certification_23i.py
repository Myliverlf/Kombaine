import shutil
from pathlib import Path
import pandas as pd
import pytest

from core.history_certification import certify_history

DATA = Path('/root/prop-desk/futures_lab/artifacts/tinkoff_futures_data')
TMP = Path('/tmp/23i_history_tests')
TMP.mkdir(exist_ok=True)


def copy(src_name: str, dst_name: str) -> Path:
    src = DATA / src_name
    dst = TMP / dst_name
    shutil.copy2(src, dst)
    return dst


class Test23IFake1095Filename:
    def test_gazp_1095_filename_has_actual_coverage_shorter(self):
        c = certify_history(DATA / 'GAZP_1095d_15m_continuous.csv', 'GAZP', '15m', 1095)
        assert c.actual_coverage_days < 1095
        assert not c.certified
        assert 'insufficient_coverage' in c.rejection_reasons

    def test_sber_1095_filename_has_actual_coverage_shorter(self):
        c = certify_history(DATA / 'SBER_1095d_15m_continuous.csv', 'SBER', '15m', 1095)
        assert c.actual_coverage_days < 1095
        assert not c.certified
        assert 'insufficient_coverage' in c.rejection_reasons


class Test23IAdversarialCases:
    def test_reverse_order_fails(self):
        p = copy('GAZP_60d_15m_continuous.csv', 'reverse.csv')
        df = pd.read_csv(p)
        df = df.iloc[::-1]
        df.to_csv(p, index=False)
        c = certify_history(p, 'GAZP', '15m', 60)
        assert not c.certified
        assert 'ordering_invalid' in c.rejection_reasons

    def test_duplicate_rows_flagged(self):
        p = copy('SBER_60d_15m_continuous.csv', 'dupe.csv')
        df = pd.read_csv(p)
        df = pd.concat([df, df.iloc[:1]], ignore_index=True)
        df.to_csv(p, index=False)
        c = certify_history(p, 'SBER', '15m', 60)
        assert not c.certified
        assert c.duplicate_rows > 0
        assert 'duplicate_rows' in c.rejection_reasons

    def test_conflicting_duplicate_rows(self):
        p = copy('GAZP_60d_15m_continuous.csv', 'conflict.csv')
        df = pd.read_csv(p)
        row = df.iloc[[0]].copy()
        row['close'] = row['close'] + 1
        df = pd.concat([df, row], ignore_index=True)
        df.to_csv(p, index=False)
        c = certify_history(p, 'GAZP', '15m', 60)
        assert not c.certified
        assert c.duplicate_rows > 0

    def test_missing_timestamps_invalid(self):
        p = copy('SBER_60d_15m_continuous.csv', 'missing_ts.csv')
        df = pd.read_csv(p)
        df = df.iloc[1:].copy()
        df.to_csv(p, index=False)
        c = certify_history(p, 'SBER', '15m', 60)
        assert c.actual_coverage_days <= 60

    def test_invalid_ohlc_rejected(self):
        p = copy('GAZP_60d_15m_continuous.csv', 'bad_ohlc.csv')
        df = pd.read_csv(p)
        df.loc[0, 'high'] = df.loc[0, 'low'] - 1
        df.to_csv(p, index=False)
        c = certify_history(p, 'GAZP', '15m', 60)
        assert not c.certified
        assert 'invalid_ohlc' in c.rejection_reasons

    def test_invalid_instrument_identity(self):
        p = copy('GAZP_60d_15m_continuous.csv', 'wrong_inst.csv')
        c = certify_history(p, 'GAZP', '15m', 60, expected_identity='SBER')
        assert not c.certified
        assert 'instrument_identity_mismatch' in c.rejection_reasons

    def test_wrong_timezone_normalized(self):
        p = copy('SBER_60d_15m_continuous.csv', 'tz.csv')
        df = pd.read_csv(p)
        df['time'] = df['time'].str.replace('+00:00', '+03:00', regex=False)
        df.to_csv(p, index=False)
        c = certify_history(p, 'SBER', '15m', 60)
        assert c.timezone == 'UTC'

    def test_filename_mismatch_does_not_certify(self):
        p = copy('GAZP_60d_15m_continuous.csv', 'fake_1095.csv')
        fake = TMP / 'GAZP_1095d_fake.csv'
        p.rename(fake)
        c = certify_history(fake, 'GAZP', '15m', 1095)
        assert not c.certified
        assert 'insufficient_coverage' in c.rejection_reasons
