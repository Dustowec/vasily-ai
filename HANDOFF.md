Vasily AI — HANDOFF (передача контекста в новую сессию)
Обновлено: 2026-08-11, конец сессии. Новой сессии: читать ПЕРВЫМ.

1. Суть проекта
Vasily AI — локальный ИИ-агент (Windows, CLI), Python 3.14.
LLM: Ollama, модель vasily-qwen (integrations/ollama_client.py)
Архитектура: плагины (plugins/*), ReAct-цикл (core/react_loop.py),
память hot/cold (memory/manager.py), внутренний планировщик (core/scheduler.py)
Логи: structlog, 4 журнала, санация (core/logging_config.py)
Тесты: pytest (tests/, asyncio_mode=auto); функциональный гейт: test_intelligence.py
Гигиена: pre-commit (black, ruff); ветка develop; remote: github.com/Dustowec/vasily-ai

2. Роли и процесс
Пользователь — начинающий: только копируемые команды в PowerShell.
Предпочитает создание файлов через notepad, а не heredoc.
Ассистент — техлид: нумерованные шаги; если правки сложны — полные файлы кодом.
Дисциплина: TDD (red-green), коммит на логический блок, пользователь шлёт выводы.
Маркер «готов» или вывод команды закрывает шаг.

3. Текущее состояние (2026-08-11)
Фазы 0–2.5 закрыты (стабилизация завершена).
ТЗ-017.6 (hardening входов) закрыт; ревью DeepSeek отработано полностью.
ФАЗА 3 ЗАКРЫТА ПОЛНОСТЬЮ:
- G-1 + Warm-память: диалог сохраняется в dialogue:last,
  сброс выполняется задачей dialogue_reset через PeriodicScheduler (30 мин).
- ТЗ-022: PeriodicScheduler по ADR-005, компрессия памяти через планировщик.
- ТЗ-023: MetricsCollector в core/metrics.py, интегрирован в AgentCore.
- ТЗ-024: автоподъём Ollama через core/service_launcher.py
  (llm_auto_start + llm_auto_start_timeout в конфиге).
- ТЗ-025: байтовый BackupManager в core/backup.py, агностичный к шифрованию.
- ТЗ-026: CrashAnalyzer в core/crash_analyzer.py, читает crash_*.json
  из logs/crash_reports и запрашивает анализ через локальный LLM.

Тесты: 111 passed. ТЗ-021: 5/5 PASS, ACCEPTANCE: PASSED. Покрытие ~73%.
Репозиторий: develop запушен, working tree clean.

4. Утверждённые решения (см. docs/)
docs/adr/ADR-005-internal-timers.md: периодические таймеры — внутренние
asyncio-задачи, не зависят от внешних вызовов.
docs/specs/phase3-g1-warm-memory.md: G-1 и Warm-память через планировщик.
docs/specs/tz-023-monitoring.md: метрики.
docs/specs/tz-024-auto-start.md: автоподъём Ollama.
docs/specs/tz-025-backups.md: байтовые бэкапы.
docs/specs/tz-026-crash-analysis.md: анализ crash-отчётов.
docs/specs/phase4-data-security.md: CryptoProvider, Fernet, PBKDF2, KEK/DEK,
ротация пароля; реализация в Фазе 4.

5. Очередь Фазы 4
Фаза 4 — Data Security:
- CryptoProvider (Fernet + PBKDF2).
- KEK/DEK схема.
- Ротация пароля.
- Перевод бэкапов на шифрованные копии.
- Аутентификация (CLI).

6. Реестр пробелов (не терять)
Остаточное покрытие: logging_config 42%, crash_reporter 70%, llm_compressor 60% — опционально.
Счётчик подряд идущих ошибок в ReAct — опционально.
DI в тестах вместо monkeypatch — отложено.
Warm-память: сброс только через планировщик, не через run().

7. Ключевые команды
python -m pytest -v
python test_intelligence.py
git add <файлы>; git commit -m "..."; git push
