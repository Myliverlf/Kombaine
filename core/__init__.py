"""КОМБАЙН v1 — ядро автономного портфельного контура (live).

Модули:
  config    — запуск/валидация лимитов
  risk      — детерминированный риск-менеджер (решения APPROVED/VETO)
  registry  — слоты портфеля + live-статистика
  waitlist  — лист ожидания стратегий (TTL, retest)
  regime    — детектор режима рынка (ADX/EMA/волатильность)
  analytics — SQLite хранилище трейдов и факторов
  engine    — сигнальный цикл + исполнение ордеров

См. /root/prop-desk/strategy_combine/ARCHITECTURE.md
"""
