from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

POLICY_PATH = Path('/root/prop-desk/strategy_combine/config/research_qualification_policy.json')


def load_policy(path: Path = POLICY_PATH) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def policy_hash(policy: Dict[str, Any] | None = None, path: Path = POLICY_PATH) -> str:
    if policy is None:
        policy = load_policy(path)
    canonical = json.dumps(policy, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
