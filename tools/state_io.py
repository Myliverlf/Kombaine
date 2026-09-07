"""state_io.py — атомарная запись state-файлов (tmp+fsync+rename).

Падение посреди json.dump раньше рвало state-файлы (stable_pool.json и др.).
Все записи состояния комбайна обязаны идти через atomic_write_json/atomic_write_text.
Paper-only, брокерских вызовов нет.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path


def atomic_write_text(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_json(path, obj, **dumps_kw) -> None:
    dumps_kw.setdefault("indent", 2)
    dumps_kw.setdefault("ensure_ascii", False)
    dumps_kw.setdefault("allow_nan", False)
    atomic_write_text(path, json.dumps(obj, **dumps_kw))
