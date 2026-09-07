# Unit Validation — Iteration 13B

## Files Created

| File | Path |
|------|------|
| Timer | /etc/systemd/system/combine-research-daily.timer |
| Service | /etc/systemd/system/combine-research-daily.service |
| Wrapper | /root/prop-desk/strategy_combine/scripts/canonical_daily_research.sh |

## Timer Unit Content

```ini
[Unit]
Description=Combine canonical daily research timer

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
```

## Service Unit Content

```ini
[Unit]
Description=Combine: canonical daily research pipeline (Iteration 13B)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /root/prop-desk/strategy_combine/scripts/canonical_daily_research.sh
WorkingDirectory=/root/prop-desk/strategy_combine
StandardOutput=append:/root/prop-desk/logs/combine_research_daily.log
StandardError=append:/root/prop-desk/logs/combine_research_daily.log

[Install]
WantedBy=multi-user.target
```

## Validation Results

| Check | Result |
|-------|--------|
| systemd-analyze verify service | ✅ EXIT 0 |
| systemd-analyze verify timer | ✅ EXIT 0 |
| daemon-reload | ✅ EXIT 0 |

## Schedule

| Property | Value |
|----------|-------|
| OnCalendar | *-*-* 06:00:00 |
| Timezone | CEST (system local, UTC+2) |
| Persistent | true |
| AccuracySec | 1min |
| WorkingDirectory | /root/prop-desk/strategy_combine |
| User | root |
| Timeout | systemd default (90s) |
