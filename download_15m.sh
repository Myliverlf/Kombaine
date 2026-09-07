#!/bin/bash
# Докачка 15-минутных свечей для юниверса комбайна (без RI)
set -u
cd /root/prop-desk/futures_lab
LOG=/root/prop-desk/logs/combine_15m_download.log
mkdir -p /root/prop-desk/logs
echo "$(date '+%F %T') старт докачки 15m: BR GAZP LKOH SBER Si" >> "$LOG"
for T in BR GAZP LKOH SBER Si; do
  echo "$(date '+%F %T') -> $T" >> "$LOG"
  /usr/bin/python3 futures_lab.py download --ticker "$T" --days 60 --interval 15m --continuous \
    --out artifacts/tinkoff_futures_data/${T}_60d_15m_continuous.csv >> "$LOG" 2>&1
  echo "$(date '+%F %T') <- $T exit=$?" >> "$LOG"
done
echo "$(date '+%F %T') докачка 15m завершена" >> "$LOG"
