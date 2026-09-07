"""Корректный мок _equity для dry-run без реальных API-вызовов.

Факт: core/engine.py:242 вызывает self._equity(client, positions) — 3 аргумента
(self, client, broker_positions). Оригинальный engine_patches.py:82 mock_equity
принимает только (self, client) — 2 аргумента → validate_engine criterion A FAIL.

Решение: мок принимает (self, client, broker_positions=None) и возвращает
deposit_rub из config. Исправляет mismatch в engine_patches.py.

Источник: analysis.md (fact #10), engine.py:297 signature, engine_patches.py:82.
"""
import types
from typing import Any


def mock_equity(self: Any, client: Any, broker_positions: Any = None) -> float:
    """Возвращает deposit_rub без API-вызова.

    Подпись совпадает с core/engine.py:297:
        def _equity(self, client: Client, broker_positions: dict | None = None) -> float

    Вызов из engine.py:242:
        self._equity(client, positions)  # positions = dict[sid -> open_position]
    """
    return self.cfg.deposit_rub


def apply_mock_equity(engine_instance) -> None:
    """Monkeypatch engine._equity через instance attribute (MethodType binding).

    Используется как замена apply_patches[\"_equity\"] из engine_patches.py.
    Вызывается ПОСЛЕ apply_patches() для перезаписи некорректного мока.

    Пример использования:
        from engine_patches import apply_patches
        from code.mock_equity_fix import apply_mock_equity
        eng = Engine()
        apply_patches(eng)        # ставит все моки (включая старый _equity)
        apply_mock_equity(eng)    # перезаписывает _equity корректным
    """
    engine_instance._equity = types.MethodType(mock_equity, engine_instance)


def patched_apply_patches(eng) -> dict:
    """Apply_patches + корректный _equity в одном вызове.

    Возвращает dict {имя: оригинальный_метод} для восстановления.
    """
    from engine_patches import apply_patches as _original_apply
    origs = _original_apply(eng)
    # Перезаписываем _equity корректной версией
    orig_equity_class = origs.get("_equity")
    eng._equity = types.MethodType(mock_equity, eng)
    # Обновляем origs чтобы remove_patches восстановил оригинальный классовый метод
    origs["_equity"] = orig_equity_class
    return origs


# ── Самотест ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Проверяем что мок принимает 3 аргумента (self + client + broker_positions)
    class FakeConfig:
        deposit_rub = 21281

    class FakeEngine:
        cfg = FakeConfig()

    eng = FakeEngine()
    result = mock_equity(eng, None, None)
    assert result == 21281, "mock_equity(eng, None, None) expected 21281, got %s" % result

    result2 = mock_equity(eng, None, {"pos1": {"qty": 1}})
    assert result2 == 21281, "mock_equity(eng, None, dict) expected 21281, got %s" % result2

    result3 = mock_equity(eng, None)
    assert result3 == 21281, "mock_equity(eng, None) expected 21281, got %s" % result3

    print("OK: mock_equity accepts 3 args (self, client, broker_positions=None)")
    print("OK: mock_equity accepts 2 args (self, client) — backward compat")
    print("OK: returns deposit_rub=%d" % result)
