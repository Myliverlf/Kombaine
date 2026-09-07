"""State Backup — snapshot & atomic-write utilities for state/ directory.

Предоставляет:
  - snapshot_state(state_dir): создаёт .bak копии всех JSON в state/
  - atomic_write_json(path, data): запись через temp + rename (crash-safe)
  - restore_from_backup(path): восстановление из .bak

Не трогает live orders. Все операции — read/write JSON файлов.

Источники:
  - plan.md Фича 2
  - analysis.md §3.2 (backup перед state changes)
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def snapshot_state(
    state_dir: str | Path,
    backup_suffix: str = ".bak",
) -> dict:
    """Create .bak copies of all JSON files in state_dir.

    Args:
        state_dir: path to state/ directory
        backup_suffix: suffix for backup files (default ".bak")

    Returns:
        {"created": [list of .bak files], "errors": [list of error strings]}
    """
    state_dir = Path(state_dir)
    created: List[str] = []
    errors: List[str] = []

    if not state_dir.exists():
        errors.append(f"state_dir does not exist: {state_dir}")
        return {"created": created, "errors": errors}

    for json_file in sorted(state_dir.glob("*.json")):
        bak_path = json_file.with_suffix(json_file.suffix + backup_suffix)
        try:
            shutil.copy2(json_file, bak_path)
            created.append(str(bak_path))
        except OSError as e:
            errors.append(f"failed to backup {json_file.name}: {e}")

    return {"created": created, "errors": errors}


def atomic_write_json(
    path: str | Path,
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """Write JSON data to file atomically via temp file + rename.

    Guarantees no partial writes on crash. Existing file is replaced
    only after successful write of the temp file.

    Args:
        path: target file path
        data: JSON-serializable data
        indent: JSON indent (default 2)
        ensure_ascii: passed to json.dumps
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=path.stem + ".tmp.",
        suffix=path.suffix,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.write("\n")  # trailing newline
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            tmp_path = None
        raise


def restore_from_backup(
    path: str | Path,
    backup_suffix: str = ".bak",
) -> dict:
    """Restore a JSON file from its .bak backup.

    Args:
        path: path to the original JSON file
        backup_suffix: suffix of the backup file

    Returns:
        {"restored": bool, "source": str, "error": str | None}
    """
    path = Path(path)
    bak_path = path.with_suffix(path.suffix + backup_suffix)

    if not bak_path.exists():
        return {
            "restored": False,
            "source": str(bak_path),
            "error": f"backup not found: {bak_path}",
        }

    try:
        # Validate backup is valid JSON before restoring
        content = bak_path.read_text(encoding="utf-8")
        json.loads(content)  # will raise on invalid JSON
        shutil.copy2(bak_path, path)
        return {"restored": True, "source": str(bak_path), "error": None}
    except (json.JSONDecodeError, OSError) as e:
        return {
            "restored": False,
            "source": str(bak_path),
            "error": f"restore failed: {e}",
        }


def list_backups(state_dir: str | Path, backup_suffix: str = ".bak") -> List[dict]:
    """List all .bak files in state_dir with metadata.

    Returns:
        [{"path": str, "original": str, "size_bytes": int, "mtime": float}, ...]
    """
    state_dir = Path(state_dir)
    backups = []

    if not state_dir.exists():
        return backups

    for bak_file in sorted(state_dir.glob(f"*{backup_suffix}")):
        original_name = bak_file.name.removesuffix(backup_suffix)
        stat = bak_file.stat()
        backups.append({
            "path": str(bak_file),
            "original": original_name,
            "size_bytes": stat.st_size,
            "mtime": stat.st_mtime,
        })

    return backups


# ── Self-test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    state_dir = sys.argv[1] if len(sys.argv) > 1 else "/root/prop-desk/strategy_combine/state"

    print("=== State Backup Tool ===")

    # Step 1: Snapshot
    result = snapshot_state(state_dir)
    print(f"Snapshot: {len(result['created'])} files backed up, {len(result['errors'])} errors")
    for f in result["created"]:
        print(f"  + {f}")
    for e in result["errors"]:
        print(f"  ! {e}")

    # Step 2: List backups
    backups = list_backups(state_dir)
    print(f"\nBackups in {state_dir}: {len(backups)}")
    for b in backups:
        print(f"  {b['original']} ({b['size_bytes']} bytes)")

    print("\nOK")
