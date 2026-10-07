# Vasily AI

Vasily AI — локальный ИИ-агент на архитектуре ReAct (Reasoning + Acting). Работает через Ollama, поддерживает плагины, градиентно-каскадную память с динамическим охлаждением, структурированное логирование, watchdog и автоматические crash-репорты.

**Статус:** стабильная версия · **449 / 449 тестов проходят**

---

## Возможности

| Область | Описание |
|---|---|
| Ядро | ReAct-цикл с вызовом инструментов · asyncio · автодискавери плагинов · скользящее окно диалога (5 пар) |
| Память | Gradient Cascade: зоны TGS / Hot / Cold · динамическое охлаждение · LLM-ранжирование кандидатов (`ranking.py`) · admission gate на секреты (`admission.py`) · токенизация + Snowball-стемминг вместо substring (`tokenizer.py`) · LLM-компрессия из HOT в COLD (`llm_compressor.py`) |
| Активные задачи | ADR-017: канал `task_state:` для длительных задач · заморозка охлаждения пока `status="in_progress"` · amnesty при `forget_all` как у `user_fact` · инструмент `LongTaskTool` (create / checkpoint / step_done / complete / cancel / list / get / help) |
| Инструменты | `review_file`, `write_file`, `patch_file`, `list_files` (internal tools) · `recall_memory`, `remember_fact`, `long_task` (через MemoryManager) · `code_execution` (отклонён в ADR-014) |
| Умный поиск | LLM Query Expansion: `recall_memory` расширяет запрос синонимами через LLM перед поиском (0 МБ VRAM). Кандидаты ранжируются LLM через `memory/ranking.py` (30 → 3 показанных). Поисковый ключ — токен+стемм вместо substring (`memory/tokenizer.py`) |
| Целостность данных | `remember_fact` проверяет память перед записью и блокирует дубликаты (сравнение по длине + семантический поиск через LLM). Admission gate (`memory/admission.py`) фильтрует чувствительные данные при записи |
| Мониторинг | Watchdog: LLM, плагины, память, диск · автовосстановление (до 2 попыток) · статусные иконки · MetricsCollector: `requests_total`, `tool_calls_blocked`, `tokens_used` · BackupManager: byte-level бэкапы данных |
| Crash-репорты | Автоматически генерируются при ERROR/CRITICAL в логах · JSON + Markdown · отчёты при фатальных падениях и недоступности LLM |
| Плагины | Art-промпты · веб-поиск (SearXNG) · веб-скрапинг с SSRF-защитой · Danbooru · чтение локальных файлов · echo |
| Логирование | 4 категории (core / interaction / plugins / llm) · 5 уровней алертов · ротация 72 часа · санация секретов · отдельный watchdog-лог |
| Безопасность | SSRF-защита · защита от path traversal · санация логов · атомарная запись памяти · admission gate на секреты · crash-репорты |

---

## Память (Gradient Cascade)

Память остывает от действий пользователя, а не от календаря.

**Принципы:**

- 1 тик = 1 сообщение (запрос + ответ)
- Три зоны: **TGS** (защищённые темы), **Hot** (активные), **Cold** (сжатые саммари)
- Нагрев: `+5` при recall, `+10` при remember
- Флаг `no_compress` — запись не сжимается при охлаждении
- Флаг `shield` — защита свежепромотированной записи в TGS
- TGS — максимум 10 записей, вытеснение по LRU
- Команды: `забудь <тема>` и `забудь всё` (с подтверждением)

**Умный поиск (`recall_memory`):**

- запрос расширяется через LLM (синонимы, связанные понятия)
- поиск по всем трём зонам
- кандидаты ранжируются LLM через `ranking.py` (до 30 кандидатов, топ-3 показываются)
- найденное нагревается через `heat_facts`

**Активные задачи (`task_state:`):**

- Создаются через `long_task create key=<ключ> goal=<цель>`
- Имеют поле `status: in_progress | done | cancelled`
- Пока `in_progress`: замораживаются от всех стадий охлаждения (`decay` пропускает)
- При `forget_all` получают amnesty 10 тиков (как `user_fact`)
- Механизм: `new_entry` в HOT зоне со `score=40`, `no_compress=True`, `status=in_progress`
- Агент видит top-3 активных задач в system prompt перед каждым запросом

**Дополнительные модули памяти:**

- **`admission.py`** — gate на секреты (фильтр паролей, токенов, email при записи)
- **`tokenizer.py`** — токенизация + Snowball-стемминг вместо substring-поиска
- **`ranking.py`** — LLM-ранжирование кандидатов по релевантности
- **`llm_compressor.py`** — LLM-компрессия записей при HOT → COLD миграции

**Защита от дубликатов (`remember_fact`):**

- Перед записью проверяет память через `recall_memory`
- При обнаружении похожего факта — блокирует запись или обновляет существующий (вердикт LLM: `ДУБЛЬ` / `ДОПОЛНЕНИЕ` / `ПРОТИВОРЕЧИЕ` / `НЕТ`)

**Файлы хранения:**

- `data/tgs_memory.json` — защищённые темы
- `data/tg_hot_memory.json` — активные темы
- `data/tg_cold_memory.json` — архив с саммари

---

## Логирование и crash-репорты

Логи пишутся в `logs/` с ротацией каждые 72 часа.

| Файл | Категория |
|---|---|
| `core.log` | AgentCore, PluginRegistry, ReActLoop |
| `interaction.log` | вызовы плагинов |
| `plugins.log` | внутренние логи плагинов |
| `llm.log` | запросы и ответы LLM |
| `watchdog.log` | события мониторинга (ротация 50 записей) |
| `vasily.log` | все логи в одном файле |

**Уровни алертов:** `STATE`, `REQUEST`, `WARNING`, `CRITICAL_WARNING`, `CRASH`.

**Санация:** ключи вида `password` / `token` / `api_key` → `[REDACTED]`. Поля `prompt` / `query` / `url` на уровне ERROR / CRITICAL заменяются на `{length, hash}`. Вложенные `dict` и `list` проверяются рекурсивно.

**Crash-репорты:** при появлении записи уровня ERROR или CRITICAL в логгерах `core` / `plugins` / `llm` / `interaction` автоматически создаётся отчёт. Плюс отчёты генерируются при фатальных падениях и при недоступности LLM после всех попыток восстановления. Сохраняются в `logs/crash_reports/YYYY-MM-DD/` в форматах JSON и Markdown.

---

## Watchdog

Фоновый мониторинг раз в 30 секунд: LLM, плагины, память, диск. При сбое — до 2 попыток восстановления. Для LLM — пересоздание клиента, для плагинов — перезагрузка registry, для памяти — восстановление из `.tmp`. Статус показывается через иконки в команде `status`.

---

## Активные задачи (ADR-017)

Канал `task_state:` для управления длительными задачами агента.

### Концепция

Длительная задача («напиши код автономно», «проведи исследование») хранится в памяти как entry с полем `status`. Пока `status="in_progress"` — задача не остывает ни на одной стадии TGS / HOT / COLD.

### LongTaskTool

Инструмент для явного управления задачами:

| Действие | Описание |
|---|---|
| `create key=<ключ> goal=<цель> steps=[...]` | Создать задачу (HOT, `score=40`, `no_compress=True`) |
| `checkpoint key=<ключ> index=N desc='...'` | Добавить чекпоинт |
| `step_done key=<ключ> step_index=N` | Отметить шаг выполненным |
| `complete key=<ключ>` | Завершить задачу (`status=done`) |
| `cancel key=<ключ>` | Отменить задачу (`status=cancelled`) |
| `list` | Список всех задач (все зоны, до 50) |
| `get key=<ключ>` | Получить одну задачу |
| `help` | Справка по командам |

### Заморозка охлаждения

Методы `decay()` и `_tgs_decay_demote()` проверяют `entry.get("status") == "in_progress"`. Если да — entry пропускается (`continue`), без изменения `score`.

### Amnesty

При `forget_all` активные задачи обрабатываются функцией `_immune()`, которая возвращает `True` для `task_state` со `status=in_progress`. Они защищены age-independent защитой, аналогично `user_fact`.

### Injection в system prompt

Агент автоматически получает список top-3 активных задач в начале каждого ReAct-цикла:

```
## ТВОИ АКТИВНЫЕ ЗАДАЧИ
- research_spring_2026: провести обзор архитектуры ... (чекпоинты: 2, шаги: 1/4)
- refactor_module_x: рефакторинг модуля X ... (чекпоинты: 0, шаги: 2/3)

Выполняй задачи по очереди. Не создавай новые, если есть незавершённые.
```

---

## Плагины

| Плагин | Описание |
|---|---|
| `art_generator` | генерирует подробные промпты для Stable Diffusion / Midjourney |
| `web_search` | поиск через SearXNG (mocks в `dev_mode`) |
| `web_scraper` | извлечение контента страниц с SSRF-защитой |
| `danbooru_search` | поиск постов и тегов Danbooru |
| `local_reader` | чтение файлов из `workspace/reading` (csv, json, txt, md, xlsx, pdf) |

---

## Структура проекта

```
vasily_ai/
├── core/                     # Основные компоненты
│   ├── agent.py              # Оркестратор агента
│   ├── react_loop.py         # ReAct-цикл (inject active tasks)
│   ├── internal_tools.py     # Internal tools + LongTaskTool
│   ├── config.py             # Config: env + file + defaults
│   ├── watchdog.py           # Background monitoring
│   ├── metrics.py            # MetricsCollector
│   ├── backup.py             # BackupManager
│   ├── crypto.py             # CryptoProvider (заглушка NoOp)
│   ├── service_launcher.py   # Service integration
│   ├── health_check.py       # Health check reporter
│   ├── token_manager.py      # Token usage tracking
│   ├── plugin_registry.py    # Auto-discovery
│   └── golden_prompts.py     # Prompt templates
├── memory/                   # Gradient Cascade Memory
│   ├── manager.py            # Центр памяти (TGS / HOT / COLD)
│   ├── admission.py          # Gate на секреты
│   ├── tokenizer.py          # Токенизация + Snowball
│   ├── ranking.py            # LLM-ранжирование
│   └── llm_compressor.py     # LLM-компрессия
├── integrations/             # Внешние сервисы
│   └── ollama_client.py      # LLM client (native + JSON-fallback)
├── plugins/                  # Автодискавери плагины
│   ├── art_generator/
│   ├── danbooru/
│   ├── echo/
│   ├── local_reader/
│   ├── web_scraper/
│   └── web_search/
├── security/                 # SSRF-периметр
│   ├── __init__.py           # preflight_check + safe_connector
│   ├── ip_rules.py           # forbidden_reason(ip)
│   ├── resolver.py           # SafeResolver
│   └── allowlist.py          # Ручной allow-list
├── tests/                    # Тесты (449 собрано, 449 прошли)
├── docs/adr/                 # Архитектурные решения
├── logs/                     # Логи (авто)
└── data/                     # Персистентные данные (авто)
```

---

## Тесты

```powershell
pytest tests/ --tb=line -q
# 449 passed in ~12s
```

Все тесты находятся в `tests/`. Ключевые группы:

- `test_task_state_*.py` — заморозка охлаждения, amnesty, создание задач (15 тестов)
- `test_long_task_tool.py` — все 8 действий инструмента (9 тестов)
- `test_recall_categories.py` — фильтр каналов (5 тестов)
- `test_user_fact_dedup.py` — дедупликация `user_fact` (3 теста)
- `test_react_*.py` — ReAct-цикл, ошибки, лимиты (10 тестов)
- `test_watchdog_*.py` — мониторинг, восстановление (36 тестов)
- `test_web_scraper_*.py` — SSRF-защита, hardening (20 тестов)
- `test_plugin_*.py` — плагины, валидация (30 тестов)
- `test_json_tool_calls.py`, `test_json_fallback_history.py` — JSON-fallback (31 тест, ADR-016)
- `test_admission.py`, `test_tokenizer.py`, `test_llm_ranking.py` — memory hardening (49 тестов, ADR-015)

---

## Быстрый старт

**Требования:**
- Python **3.14+**
- [Ollama](https://ollama.com/) с совместимой моделью. Рекомендуется **Qwen 3.5-4B** (нативная поддержка tools) или **Gemma 3n E4B** (JSON-fallback, ADR-016)
- Опционально [SearXNG](https://docs.searxng.org/) для веб-поиска (локально в Docker на `127.0.0.1:8888`)

**Установка:**

```powershell
uv sync --all-extras
```

**Настройка модели:** скачать модель в Ollama (`ollama pull <model>` или `ollama create <name> -f Modelfile` для локального GGUF).

**Конфигурация:** `vasily_config.json` в корне проекта. Любое поле переопределяется через env-переменную `VASILY_<FIELD>`.

**Запуск:**

```powershell
python __main__.py
```

**Команды CLI:**

| Команда | Действие |
|---|---|
| `status` | статус агента и памяти |
| `help` | справка |
| `забудь <тема>` / `забыть <тема>` | забыть конкретную тему |
| `забудь всё` | запрос каскадной ротации памяти |
| `забудь всё да` | подтвердить ротацию |
| `удалить 1, 2` / `удалить всех` | выбрать кандидатов после `забудь` |
| `exit` | выход |

---

## ADR (Architecture Decision Records)

| ID | Название | Статус |
|---|---|---|
| 001 | Выбор asyncio | Implemented |
| 002 | Плагин-архитектура | Implemented |
| 003 | Ollama вместо LMStudio | Implemented |
| 004 | Structured logging | Implemented |
| 005 | ReAct pattern | Implemented |
| 006 | Code protection strategy | Implemented |
| 007 | Internal timers | Implemented |
| 008 | Отложить шифрование | Implemented |
| 009 | Gradient cascade memory | Implemented |
| 010 | Watchdog monitoring | Implemented |
| 011 | Context optimization, 4B model | Implemented |
| 012 | Memory boundaries and dedup | Implemented |
| 013 | Memory actualization | Implemented |
| 014 | ~~Code Executor~~ | **Rejected** |
| 015 | Retrieval hardening & admission gate | Implemented |
| 016 | JSON tool-call fallback | Implemented |
| 017 | `task_state:` active tasks channel | **Implemented** |

---

## License

MIT
