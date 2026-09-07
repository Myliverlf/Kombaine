#!/usr/bin/env python3
"""HARDENING v2 §5 — тесты research_integrity.py + прошивка в genetic_engine.
Read-only: state-файлы НЕ пишутся (RUN-10 активен, гонка недопустима)."""
import json, sys, tempfile, unittest
from pathlib import Path
import numpy as np
import pandas as pd

SC = Path("/root/prop-desk/strategy_combine")
FL = Path("/root/prop-desk/futures_lab")
sys.path.insert(0, str(SC)); sys.path.insert(0, str(FL)); sys.path.insert(0, str(SC / "tools"))
import research_integrity as ri


class TestDatasetId(unittest.TestCase):
    def test_content_derived_not_name(self):
        """Два файла с РАЗНЫМИ именами но ОДИНАКОВЫМ содержимым → ОДИН id.
        Это ровно баг LKOH_1095d==LKOH_365d."""
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "LKOH_365d_1h_continuous.csv"
            b = Path(d) / "LKOH_1095d_1h_continuous.csv"
            a.write_text("time,close\n2026-01-01,100\n2026-01-02,101\n")
            b.write_text(a.read_text())   # побайтово то же
            self.assertEqual(ri.dataset_id(a), ri.dataset_id(b))
            self.assertTrue(ri.dataset_id(a).startswith("ds_"))

    def test_different_content_different_id(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "x.csv"; b = Path(d) / "y.csv"
            a.write_text("time,close\n2026-01-01,100\n")
            b.write_text("time,close\n2026-01-01,999\n")
            self.assertNotEqual(ri.dataset_id(a), ri.dataset_id(b))

    def test_missing_file(self):
        self.assertEqual(ri.dataset_id("/no/such/file.csv"), "ds_MISSING")

    def test_real_lkoh_collapse(self):
        """На РЕАЛЬНЫХ данных: LKOH 1095d и 365d должны дать один id (подтверждение бага)."""
        a = FL / "artifacts/tinkoff_futures_data/LKOH_1095d_1h_continuous.csv"
        b = FL / "artifacts/tinkoff_futures_data/LKOH_365d_1h_continuous.csv"
        if a.exists() and b.exists():
            self.assertEqual(ri.dataset_id(a), ri.dataset_id(b),
                             "LKOH long/short должны совпадать по контенту (баг данных)")


class TestScoreCandidate(unittest.TestCase):
    def test_fields_present(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0.002, 0.01, 300)
        sc = ri.score_candidate(r, trials_count=1000, sr_std_perperiod=0.3)
        for k in ("raw_score", "selection_adjusted_score", "trials_count",
                  "overfit_risk", "sr_star_under_null"):
            self.assertIn(k, sc)
        self.assertEqual(sc["trials_count"], 1000)
        # overfit_risk = 1 - dsr
        self.assertAlmostEqual(sc["overfit_risk"], round(1 - sc["selection_adjusted_score"], 4), places=3)

    def test_more_trials_higher_overfit_risk(self):
        # реалистичные параметры: дневной SR≈0.2, кросс-секционный std SR≈0.095
        # (как в реальном прогоне: std аннуализированных ~1.5 / √252).
        # При 100 попытках SR*≈0.24 (DSR умеренный), при 100k SR*≈0.41 (DSR→0).
        rng = np.random.default_rng(1)
        r = rng.normal(0.002, 0.01, 300)
        a = ri.score_candidate(r, 10, 0.095)["overfit_risk"]
        b = ri.score_candidate(r, 100000, 0.095)["overfit_risk"]
        self.assertLess(a, b, "больше попыток → выше риск переобучения")
        self.assertLess(a, 0.9, "при малом числе попыток риск не должен saturate в 1.0")

    def test_strong_edge_low_risk(self):
        # сильный честный эдж (дневной SR≈1.0 = аннуализированный ~16) при
        # реалистичном шуме отбора sr_std=0.1 и 1000 попытках: DSR → ~1.
        rng = np.random.default_rng(2)
        r = rng.normal(0.01, 0.01, 500)
        sc = ri.score_candidate(r, 1000, 0.1)
        self.assertGreater(sc["selection_adjusted_score"], 0.9)
        self.assertLess(sc["overfit_risk"], 0.1)


class TestAppendExperiment(unittest.TestCase):
    def test_append_only_jsonl(self):
        import os
        tmp = Path(tempfile.mkdtemp()) / "reg.jsonl"
        old = ri.REGISTRY_PATH
        try:
            ri.REGISTRY_PATH = tmp
            ri.append_experiment([{"engine": "test", "ticker": "X", "trials_count": 5}])
            ri.append_experiment({"engine": "test", "ticker": "Y", "trials_count": 7})
            lines = tmp.read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)   # append, не перезапись
            rec = json.loads(lines[0])
            self.assertEqual(rec["schema_version"], "research-integrity-v1")
            self.assertIn("ts", rec)
            self.assertEqual(rec["ticker"], "X")
        finally:
            ri.REGISTRY_PATH = old


class TestGeneticEngineWiring(unittest.TestCase):
    """Прошивка trials_count/dataset_id в genetic_engine — БЕЗ запуска эволюции
    (дорого + гонка с RUN-10). Проверяем сигнатуры и что load() вернул dataset_id."""
    def test_load_returns_dataset_id(self):
        import engines.genetic_engine as ge
        df, dsid = ge.load("CNY")
        self.assertIsNotNone(df)
        self.assertTrue(str(dsid).startswith("ds_"))
        # dataset_id должен совпадать с content-hash файла
        self.assertEqual(dsid, ri.dataset_id(ge.DATA / "CNY_365d_1h_continuous.csv"))

    def test_load_longsame_as_short(self):
        """--long-data для LKOH вернёт ТОТ ЖЕ id что и 365d (баг виден в кандидате)."""
        import engines.genetic_engine as ge
        _, ds_short = ge.load("LKOH")
        _, ds_long = ge.load("LKOH", prefer_long=True)
        self.assertEqual(ds_short, ds_long,
                         "LKOH long==short по контенту; id обязан это показать")

    def test_evolve_signature_has_dataset_id(self):
        import inspect, engines.genetic_engine as ge
        sig = inspect.signature(ge.evolve)
        self.assertIn("dataset_id", sig.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
