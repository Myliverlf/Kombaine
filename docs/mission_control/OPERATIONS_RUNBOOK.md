# Operations Runbook — Strategy Combine

**Status:** VERIFIED · 2026-08-30
**Iteration:** 11 — System Modularity & Operability Foundation
**Scope:** Read-only diagnostics and bounded safe recovery procedures
**Hard boundary:** No destructive auto-repair. No broker orders. No registry mutation.

---

## How to use this runbook

1. Run `python -m core.system_health --json` or `python -m core.system_health` for current status
2. Identify the failing component/domain from the health report
3. Find the matching scenario below
4. Follow SAFE DIAGNOSTICS first
5. Only proceed to RECOVERY STEPS if diagnostics confirm the scenario
6. STOP at ESCALATION conditions — do not proceed further

---

## 1. Research Lock Stuck

**SYMPTOM:** `state/.research.lock` exists and is older than expected; health shows `research_run_contract` as BLOCKED
**HEALTH STATUS:** research_health BLOCKED
**DO NOT DO:**
- Do NOT delete the lock file without checking the owning PID
- Do NOT force-kill processes without understanding their state
**SAFE DIAGNOSTICS:**
```bash
cat state/.research.lock
# Check if PID in lock file is alive
ps aux | grep <PID>
# Check lock file age
stat state/.research.lock
```
**RECOVERY STEPS:**
1. Verify the owning PID is not alive: `kill -0 <PID>`
2. If process is dead, the lock is stale — safe to remove: `rm state/.research.lock`
3. If process is alive, wait for it to complete or investigate why it's hung
4. After removing stale lock, run `python -m core.system_health` to verify research_health improves
**ESCALATION / STOP CONDITION:** If lock is fresh (< 1 hour) and process is alive, DO NOT remove. Investigate process state first.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --component research`

---

## 2. Latest Run Corrupt

**SYMPTOM:** `reports/strategy_architect/latest_run.json` references a run with missing/corrupt manifest; health shows DEGRADED
**HEALTH STATUS:** research_health DEGRADED
**DO NOT DO:**
- Do NOT delete the run directory without backing up
- Do NOT overwrite latest_run.json without a valid replacement
**SAFE DIAGNOSTICS:**
```bash
cat reports/strategy_architect/latest_run.json
ls -la reports/strategy_architect/runs/<run_id>/
cat reports/strategy_architect/runs/<run_id>/manifest.json
```
**RECOVERY STEPS:**
1. Identify the latest valid completed run: `ls -lt reports/strategy_architect/runs/*/manifest.json`
2. Find a run with `"status": "COMPLETED"` in its manifest
3. Update `latest_run.json` to point to that valid run:
   ```bash
   echo '{"run_id": "<valid_run_id>"}' > reports/strategy_architect/latest_run.json
   ```
4. Verify: `python -m core.system_health --component research`
**ESCALATION / STOP CONDITION:** If no valid completed run exists, research_health will remain STALE. This is expected — do NOT fabricate a run.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .overall_status`

---

## 3. Experiment Memory DB Unavailable

**SYMPTOM:** `state/experiment_memory.db` missing or corrupted; health shows BLOCKED
**HEALTH STATUS:** experiment_memory_health BLOCKED
**DO NOT DO:**
- Do NOT delete and recreate the DB — this loses experiment history
- Do NOT modify the DB schema manually
**SAFE DIAGNOSTICS:**
```bash
ls -la state/experiment_memory.db
sqlite3 state/experiment_memory.db "PRAGMA integrity_check;"
sqlite3 state/experiment_memory.db "SELECT COUNT(*) FROM experiments;"
```
**RECOVERY STEPS:**
1. If DB is missing: check if backup exists in `backups/`
2. If DB is corrupt but readable: export data first, then rebuild
3. If DB is completely unreadable: log the loss, create empty DB with correct schema
4. Re-index experiments from run bundles if needed: `python -c "from core.experiment_memory import ExperimentMemory; ..."`
**ESCALATION / STOP CONDITION:** Do NOT recreate from scratch without human approval. Experiment history loss is irreversible.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --component experiment_memory`

---

## 4. Knowledge DB Unavailable

**SYMPTOM:** `state/research_knowledge.db` missing or corrupted; health shows BLOCKED
**HEALTH STATUS:** knowledge_health BLOCKED
**DO NOT DO:**
- Do NOT delete the DB — knowledge findings are derived from experiments
- Do NOT force a knowledge rebuild without experiment memory being healthy
**SAFE DIAGNOSTICS:**
```bash
ls -la state/research_knowledge.db
sqlite3 state/research_knowledge.db "PRAGMA integrity_check;"
sqlite3 state/research_knowledge.db "SELECT COUNT(*) FROM findings;"
```
**RECOVERY STEPS:**
1. Verify experiment_memory is HEALTHY first
2. If DB is corrupt: export available data, create fresh DB
3. Trigger knowledge rebuild: knowledge layer can re-distill from experiment memory
4. Verify: `python -m core.system_health --component research_knowledge`
**ESCALATION / STOP CONDITION:** Knowledge rebuild without healthy experiment memory produces empty/stale knowledge. Ensure upstream is healthy.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .domain_health.knowledge_health`

---

## 5. Lifecycle DB Unavailable

**SYMPTOM:** `state/strategy_lifecycle.db` missing or corrupted; health shows BLOCKED
**HEALTH STATUS:** lifecycle_health BLOCKED
**DO NOT DO:**
- Do NOT auto-promote or auto-retire strategies while lifecycle is down
- Do NOT modify strategy registry based on stale lifecycle data
**SAFE DIAGNOSTICS:**
```bash
ls -la state/strategy_lifecycle.db
sqlite3 state/strategy_lifecycle.db "PRAGMA integrity_check;"
```
**RECOVERY STEPS:**
1. Verify research_knowledge is HEALTHY
2. If DB is corrupt: export available data, create fresh DB
3. Lifecycle can re-build from knowledge findings + experiment records
4. Verify: `python -m core.system_health --component strategy_lifecycle`
**ESCALATION / STOP CONDITION:** Do NOT make lifecycle decisions (promote/retire) while lifecycle DB is rebuilding.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --component strategy_lifecycle`

---

## 6. Seeder Handoff Blocked

**SYMPTOM:** `state/eligible_candidates.json` missing or invalid; seeder cannot proceed
**HEALTH STATUS:** selection_health DEGRADED
**DO NOT DO:**
- Do NOT manually create eligible_candidates.json with fabricated data
- Do NOT bypass the seeder_handoff validation gate
**SAFE DIAGNOSTICS:**
```bash
ls -la state/eligible_candidates.json
python -c "import json; json.load(open('state/eligible_candidates.json'))"
```
**RECOVERY STEPS:**
1. Verify a completed research run exists
2. Re-run canonical seeder handoff from the latest completed run
3. Verify: `python -m core.system_health --component seeder_registry`
**ESCALATION / STOP CONDITION:** If no completed run exists, handoff cannot produce candidates. Run research first.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .domain_health.selection_health`

---

## 7. Registry Parse/Write Failure

**SYMPTOM:** `state/strategy_registry.json` is malformed; health shows UNSAFE
**HEALTH STATUS:** selection_health UNSAFE, overall UNSAFE
**DO NOT DO:**
- Do NOT delete the registry
- Do NOT rebuild from derived views (waitlist.json, signal_pool.json)
- Do NOT merge conflicting registry states without understanding the diff
**SAFE DIAGNOSTICS:**
```bash
python -c "import json; json.load(open('state/strategy_registry.json'))"
wc -c state/strategy_registry.json
ls -la state/strategy_registry.json
# Check for backup
ls -lt state/strategy_registry.json.* 2>/dev/null
```
**RECOVERY STEPS:**
1. Check if a valid backup exists (state/strategy_registry.json.*)
2. If backup is valid: restore from backup
3. If no backup: use the `code/init_strategy_registry.py` to rebuild from scratch
4. After restore: verify registry structure and re-export derived views
5. Verify: `python -m core.system_health --component seeder_registry`
**ESCALATION / STOP CONDITION:** Registry corruption is P0. Do NOT proceed without understanding the cause. Investigate what wrote the corrupt data.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .overall_status`

---

## 8. Execution UNKNOWN Intent

**SYMPTOM:** Unresolved UNKNOWN or SUBMITTED execution intents; health shows DEGRADED
**HEALTH STATUS:** execution_health DEGRADED
**DO NOT DO:**
- Do NOT auto-resubmit UNKNOWN intents
- Do NOT delete intents without recording the reason
**SAFE DIAGNOSTICS:**
```bash
sqlite3 analytics.db "SELECT intent_id, status, created_at FROM execution_intents WHERE status IN ('UNKNOWN','SUBMITTED');"
```
**RECOVERY STEPS:**
1. Review each UNKNOWN/SUBMITTED intent manually
2. Determine if intent should be: RESUBMITTED / CANCELLED / EXPIRED
3. Update intent status with reason
4. Verify: `python -m core.system_health --component execution_journal`
**ESCALATION / STOP CONDITION:** UNKNOWN intents in production require human review before any action.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .components[] | select(.component_id=="execution_journal")`

---

## 9. Broker Unavailable

**SYMPTOM:** Tinkoff API unreachable; broker evidence resolver cannot fetch positions
**HEALTH STATUS:** broker_health DEGRADED/BLOCKED
**DO NOT DO:**
- Do NOT attempt to trade during broker outage
- Do NOT reconcile local state without broker truth
**SAFE DIAGNOSTICS:**
```bash
# Check network connectivity (read-only)
curl -s -o /dev/null -w "%{http_code}" https://api-invest.tinkoff.ru/openapi/ 2>&1
# Check if broker token is configured (DO NOT print the token)
python -c "import json; c=json.load(open('config.json')); print('token_configured' if c.get('tinkoff_token') else 'no_token')"
```
**RECOVERY STEPS:**
1. Wait for broker API to recover (monitor health check periodically)
2. When broker recovers, verify positions match local state
3. If mismatch: follow reconciliation procedure
**ESCALATION / STOP CONDITION:** Extended broker outage (> 30 min) during market hours requires human escalation.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --component broker_evidence`

---

## 10. Analytics Reconciliation Stale

**SYMPTOM:** `analytics.db` exists but data is old; reconciliation freshness check fails
**HEALTH STATUS:** analytics_health DEGRADED
**DO NOT DO:**
- Do NOT delete analytics data to "refresh" it
- Do NOT run reconciliation without broker connectivity
**SAFE DIAGNOSTICS:**
```bash
sqlite3 analytics.db "SELECT MAX(timestamp) FROM trades;"
ls -la analytics.db
```
**RECOVERY STEPS:**
1. Verify broker is available
2. Re-run reconciliation: `python -c "from core.analytics import Analytics; ..."`
3. Verify: `python -m core.system_health --component analytics`
**ESCALATION / STOP CONDITION:** Do NOT reconcile without verified broker connectivity.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .domain_health.analytics_health`

---

## 11. Duplicate Scheduler Owner

**SYMPTOM:** Same job scheduled by multiple sources (e.g., systemd + Hermes cron)
**HEALTH STATUS:** WARNING in scheduler inventory
**DO NOT DO:**
- Do NOT delete scheduler entries without understanding which is authoritative
- Do NOT disable all schedulers for the same job
**SAFE DIAGNOSTICS:**
```bash
systemctl list-timers --all | grep combine
crontab -l 2>/dev/null | grep combine
```
**RECOVERY STEPS:**
1. Identify which scheduler is the intended authority
2. Disable the non-authoritative scheduler
3. Document the decision in the tech debt register
**ESCALATION / STOP CONDITION:** If both schedulers perform different tasks (not true duplicates), document as INTENTIONAL_REDUNDANCY.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .schedulers`

---

## 12. Disk Full / Low Disk

**SYMPTOM:** Health shows disk WARNING or CRITICAL
**HEALTH STATUS:** disk_health WARNING/CRITICAL
**DO NOT DO:**
- Do NOT delete state files or database files
- Do NOT delete run bundles (evidence)
- Do NOT delete backup files
**SAFE DIAGNOSTICS:**
```bash
df -h /root/prop-desk/strategy_combine
du -sh /root/prop-desk/strategy_combine/* | sort -rh | head -20
du -sh /root/prop-desk/strategy_combine/state/*
du -sh /root/prop-desk/strategy_combine/reports/*
```
**RECOVERY STEPS:**
1. Identify largest directories
2. Check for temporary files that can be cleaned: `find . -name "*.tmp.*" -mtime +7`
3. Check for old backup files: `ls -lt backups/ | tail -20`
4. Archive old run bundles to compressed storage if needed
5. Alert human operator — do NOT auto-delete
**ESCALATION / STOP CONDITION:** If disk usage > 95%, escalate immediately. Do NOT attempt automated cleanup.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .disk_warning`

---

## 13. Service Crash / Restart Loop

**SYMPTOM:** systemd service repeatedly restarting; journal shows crash loop
**HEALTH STATUS:** Component-specific degraded/blocked
**DO NOT DO:**
- Do NOT set Restart=always on a crashing service without fixing the root cause
- Do NOT disable all restart logic
**SAFE DIAGNOSTICS:**
```bash
systemctl status combine-supervisor.service
journalctl -u combine-supervisor.service --no-pager -n 50
systemctl show combine-supervisor.service -p NRestarts
```
**RECOVERY STEPS:**
1. Identify the crash reason from journal logs
2. If it's a known transient issue (e.g., network timeout): let systemd restart
3. If it's a code error: fix the code, then restart
4. If restart count is excessive (> 5 in 10 min): consider pausing the service
**ESCALATION / STOP CONDITION:** Crash loops with > 10 restarts in 1 hour require human intervention.
**POST-RECOVERY VERIFICATION:** `python -m core.system_health --json | jq .components[] | select(.status!="HEALTHY")`

---

## General Notes

### Read-Only Health Check
The health check system (`core/system_health.py`) is strictly READ-ONLY:
- It reads config, state files, and databases (in read-only mode)
- It writes ONLY to `reports/system_health/` (derived reports, not canonical state)
- It does NOT place/cancel orders, mutate registry, change mode, or restart services

### Failure Isolation
Research/knowledge failures do NOT automatically make execution/broker domains unhealthy.
A stale knowledge report does NOT mean broker execution is unsafe.

### Secret Safety
Health reports never contain tokens, API keys, or credentials.
All diagnostic output is redacted before persistence.

### Production Research Readiness
The health check assesses readiness but does NOT start production research.
Assessment is: YES / NO / CONDITIONAL with explicit blockers.
