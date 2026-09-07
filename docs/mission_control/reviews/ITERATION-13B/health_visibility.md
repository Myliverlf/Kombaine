# Health Visibility — Iteration 13B

## System Health Scheduler Component

After adding the `scheduler` component to `system_health.py`:

```json
{
  "component_id": "scheduler",
  "domain": "SCHEDULER",
  "status": "HEALTHY",
  "message": "Canonical daily research timer active",
  "evidence": {
    "timer_active": true,
    "timer_unit": "combine-research-daily.timer",
    "service_unit": "combine-research-daily.service",
    "NextElapseUSecRealtime": "Sun 2026-08-30 06:00:00 CEST",
    "Persistent": "yes",
    "last_pipeline_run_id": "912a2975-d012-4b70-92f4-b94e46776561",
    "last_run_status": "COMPLETED",
    "pipeline_lock_exists": false
  }
}
```

## Visibility Achieved

| Field | Visible |
|-------|---------|
| scheduler enabled | ✅ (timer_active) |
| scheduler active | ✅ |
| next trigger | ✅ NextElapseUSecRealtime |
| single owner | ✅ (only combine-research-daily.timer) |
| last pipeline run id | ✅ |
| last launch source | ✅ SYSTEMD (from timer) |
| last success/failure | ✅ COMPLETED |
| pipeline freshness | ✅ (via research_run_contract) |
| lock state | ✅ pipeline_lock_exists |
