Vasily AI
Vasily AI — локальный -агент на архитектуре ReAct (Reasoning + Acting). аботает через Ollama, поддерживает плагины, градиентно-каскадную память с динамическим охлаждением, структурированное логирование, watchdog и автоматические crash-репорты.

Статус: стабильная версия · 449/449 тестов проходят

---

озможности

| бласть | писание |
|---|---|
| Ядро | ReAct-цикл с вызовом инструментов · asyncio · автодискавери плагинов · скользящее окно диалога (5 пар) |
| амять | Gradient Cascade: зоны TGS / Hot / Cold · динамическое охлаждение · LLM-ранжирование кандидатов (ranking.py) · admission gate на секреты (admission.py) · токенизация + Snowball-стемминг вместо substring (tokenizer.py) · LLM-компрессия из HOT в COLD (llm_compressor.py) |
| ктивные задачи | ADR-017: канал `task_state:` для длительных задач · заморозка охлаждения пока `status="in_progress"` · amnesty при `forget_all` как у `user_fact` · инструмент LongTaskTool (create / checkpoint / step_done / complete / cancel / list / get / help) |
| нструменты | review_file, write_file, patch_file, list_files (internal tools) · recall_memory, remember_fact, long_task (через MemoryManager) · code_execution (отклонён в ADR-014) |
| мный поиск | LLM Query Expansion: `recall_memory` расширяет запрос синонимами через LLM перед поиском (0  VRAM). андидаты ранжируются LLM через memory/ranking.py (30 → 3 показанных). оисковый ключ — токен+стемм вместо substring (memory/tokenizer.py) |
| елостность данных | `remember_fact` проверяет память перед записью и блокирует дубликаты (сравнение по длине + семантический поиск через LLM). Admission gate (memory/admission.py) фильтрует чувствительные данные при записи |
| ониторинг | Watchdog: LLM, плагины, память, диск · автовосстановление (до 2 попыток) · статусные иконки · MetricsCollector: requests_total, tool_calls_blocked, tokens_used · BackupManager: byte-level бэкапы данных |
| Crash-репорты | втоматически генерируются при ERROR/CRITICAL в логах · JSON + Markdown · отчёты при фатальных падениях и недоступности LLM |
| лагины | Art-промпты · веб-поиск (SearXNG) · веб-скрапинг с SSRF-защитой · Danbooru · чтение локальных файлов · echo |
| огирование | 4 категории (core/interaction/plugins/llm) · 5 уровней алертов · ротация 72 часа · санация секретов · отдельный watchdog-лог |
| езопасность | SSRF-защита · защита от path traversal · санация логов · атомарная запись памяти · admission gate на секреты · crash-репорты |

---

## амять (Gradient Cascade)

амять остывает от действий пользователя, а не от календаря.

ринципы:

- 1 тик = 1 сообщение (запрос + ответ)
- Три зоны: TGS (защищённые темы), Hot (активные), Cold (сжатые саммари)
- агрев: +5 при recall, +10 при remember
- лаг no_compress — запись не сжимается при охлаждении
- лаг shield — защита свежепромотированной записи в TGS
- TGS — максимум 10 записей, вытеснение по LRU
- оманды: забудь <тема> и забудь всё (с подтверждением)

мный поиск (recall_memory):

- апрос расширяется через LLM (синонимы, связанные понятия)
- оиск по всем трём зонам
- андидаты ранжируются LLM через ranking.py (до 30 кандидатов, топ-3 показываются)
- айденное нагревается через heat_facts

ктивные задачи (task_state:)

- Создаются через `long_task create key=<ключ> goal=<цель>`
- меют поле `status: in_progress | done | cancelled`
- ока `in_progress`: замораживаются от всех стадий охлаждения (decay пропускает)
- ри forget_all получают amnesty 10 тиков (как user_fact)
- еханизм: new_entry в HOT зоне со score=40, no_compress=True, status=in_progress
- Agent видит top-3 активных задач в system prompt перед каждым запросом

ополнительные модули памяти:

- **admission.py** — gate на секреты (фильтр паролей, токенов, email при записи)
- **tokenizer.py** — токенизация + Snowball-стемминг вместо substring-поиска
- **ranking.py** — LLM-ранжирование кандидатов по релевантности
- **llm_compressor.py** — LLM-компрессия записей при HOT→COLD миграции

ащита от дубликатов (remember_fact):

- еред записью проверяет память через recall_memory
- ри обнаружении похожего факта — блокирует запись или обновляет существующий (вердикт LLM: Ь /  / Т / Т)

айлы хранения:

- data/tgs_memory.json — защищённые темы
- data/tg_hot_memory.json — активные темы
- data/tg_cold_memory.json — архив с саммари

---

## огирование и crash-репорты

оги пишутся в logs/ с ротацией каждые 72 часа.

| айл | атегория |
|---|---|
| core.log | AgentCore, PluginRegistry, ReActLoop |
| interaction.log | ызовы плагинов |
| plugins.log | нутренние логи плагинов |
| llm.log | апросы и ответы LLM |
| watchdog.log | События мониторинга (ротация 50 записей) |
| vasily.log | се логи в одном файле |

ровни алертов: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH.

Санация: ключи вида password/token/api_key → [REDACTED]. оля prompt/query/url на уровне ERROR/CRITICAL заменяются на {length, hash}. ложенные dict и list проверяются рекурсивно.

Crash-репорты: при появлении записи уровня ERROR или CRITICAL в логгерах core/plugins/llm/interaction автоматически создаётся отчёт. люс отчёты генерируются при фатальных падениях и при недоступности LLM после всех попыток восстановления. Сохраняются в logs/crash_reports/YYYY-MM-DD/ в форматах JSON и Markdown.

---

## Watchdog

оновый мониторинг раз в 30 секунд: LLM, плагины, память, диск. ри сбое — до 2 попыток восстановления. ля LLM — пересоздание клиента, для плагинов — перезагрузка registry, для памяти — восстановление из .tmp. Статус показывается через иконки в команде status.

---

## ктивные задачи (ADR-017)

анал `task_state:` для управления длительными задачами агента.

### онцепция

лительная задача («напиши код автономно», «проведи исследование») хранится в памяти как entry с полем `status`. ока `status="in_progress"` — задача не остывает ни на одной стадии TGS/HOT/COLD.

### LongTaskTool

нструмент для явного управления задачами:

| ействие | писание |
|---|---|
| `create key=<ключ> goal=<цель> steps=[...]` | Создать задачу (HOT, score=40, no_compress=True) |
| `checkpoint key=<ключ> index=N desc='...'` | обавить чекпоинт |
| `step_done key=<ключ> step_index=N` | тметить шаг выполненным |
| `complete key=<ключ>` | авершить задачу (status=done) |
| `cancel key=<ключ>` | тменить задачу (status=cancelled) |
| `list` | Список всех задач (все зоны, до 50) |
| `get key=<ключ>` | олучить одну задачу |
| `help` | Справка по командам |

### аморозка охлаждения

етоды `decay()` и `_tgs_decay_demote()` проверяют `entry.get("status") == "in_progress"`. сли да — entry пропускается (continue), без изменения score.

### Amnesty

ри `forget_all` активные задачи обрабатываются функцией `_immune()`, которая возвращает True для task_state со status=in_progress. ни защищены age-independent защитой, аналогично user_fact.

### Injection в system prompt

гент автоматически получает список top-3 активных задач в начале каждого ReAct-цикла:

```
## ТЫ
- research_spring_2026: ровести обзор архитектуры ... (чекпоинты: 2, шаги: 1/4)
- refactor_module_x: ефакторинг модуля X ... (чекпоинты: 0, шаги: 2/3)

ыполняйте задачи по очереди. е создавайте новые, если есть незавершённые.
```

---

## лагины

| лагин | писание |
|---|---|
| art_generator | енерирует подробные промпты для Stable Diffusion / Midjourney |
| web_search | оиск через SearXNG (mocks в dev_mode) |
| web_scraper | звлечение контента страниц с SSRF-защитой |
| danbooru_search | оиск постов и тегов Danbooru |
| local_reader | тение файлов из workspace/reading (csv, json, txt, md, xlsx, pdf) |
| echo | Тестовый плагин — возвращает ввод как есть |

---

## Структура проекта

```
vasily_ai/
├── core/               # сновные компоненты
│   ├── agent.py        # ркестратор агента
│   ├── react_loop.py   # ReAct-цикл (inject active tasks)
│   ├── internal_tools.py # Internal tools + LongTaskTool
│   ├── config.py       # Config: env + file + defaults
│   ├── watchdog.py     # Background monitoring
│   ├── metrics.py      # MetricsCollector
│   ├── backup.py       # BackupManager
│   ├── crypto.py       # CryptoProvider (stub NoOp)
│   ├── service_launcher.py # Service integration
│   ├── health_check.py # Health check reporter
│   ├── token_manager.py # Token usage tracking
│   ├── plugin_registry.py # Auto-discovery
│   └── golden_prompts.py # Prompt templates
├── memory/             # Gradient Cascade Memory
│   ├── manager.py      # ентр памяти (TGS/HOT/COLD)
│   ├── admission.py    # Gate на секреты
│   ├── tokenizer.py    # Токенизация + Snowball
│   ├── ranking.py      # LLM-ранжирование
│   └── llm_compressor.py # LLM-компрессия
├── integrations/       # нешние сервисы
│   └── ollama_client.py # LLM client
├── plugins/            # втодискавери плагины
│   ├── art_generator/
│   ├── danbooru/
│   ├── echo/
│   ├── local_reader/
│   ├── web_scraper/
│   └── web_search/
├── tests/              # Тесты (449 собрано, 449 прошли)
├── docs/adr/           # рхитектурные решения
├── logs/               # отированные логи (авто)
└── data/               # ерсистентные данные (авто)
```

---

## Тесты

```
pytest tests/ --tb=line -q
# 449 passed in ~12s
```

се тесты находятся в `tests/`. лючевые группы:

- `test_task_state_*.py` — заморозка охлаждения, amnesty, создание задач (15 тестов)
- `test_long_task_tool.py` — все 8 действий инструмента (9 тестов)
- `test_recall_categories.py` — фильтр каналов (5 тестов)
- `test_user_fact_dedup.py` — дедупликация user_fact (3 теста)
- `test_react_*.py` — ReAct-цикл, ошибки, лимиты (10 тестов)
- `test_watchdog_*.py` — мониторинг, восстановление (36 тестов)
- `test_web_scraper_*.py` — SSRF-защита, hardening (20 тестов)
- `test_plugin_*.py` — плагины, валидация (30 тестов)

---

## ыстрый старт

Требования: Python 3.14+, Ollama с совместимой моделью (рекомендуется Qwen 3.5-4B Q6_m), опционально SearXNG для веб-поиска.

становка: `uv sync --all-extras`

астройка модели: pull модели в Ollama.

онфигурация: `vasily_config.json` в корне проекта, или environment variables с префиксом VASILY_ (например, VASILY_LLM_MODEL, VASILY_DEV_MODE).

апуск: `python -m core.agent`

оманды:

- status — статус агента и памяти
- help — справка
- forget <тема> — забыть конкретную тему
- forget all — запрос полного сброса
- forget all yes — подтвердить
- delete 1, 2 / delete all — выбрать кандидаты после forget
- exit — выход

---

## ADR (Architecture Decision Records)

| ID | азвание | Статус |
|---|---|---|
| 001 | ыбор asyncio | Implemented |
| 002 | лагин-архитектура | Implemented |
| 003 | Ollama вместо LMStudio | Implemented |
| 004 | Structured logging | Implemented |
| 005 | ReAct pattern | Implemented |
| 006 | Code protection strategy | Implemented |
| 007 | Internal timers | Implemented |
| 008 | тложить шифрование | Implemented |
| 009 | Gradient cascade memory | Implemented |
| 010 | Watchdog monitoring | Implemented |
| 011 | Context optimization, 4B model | Implemented |
| 012 | Memory boundaries and dedup | Implemented |
| 013 | Memory actualization | Implemented |
| 014 | ~~Code Executor~~ | **Rejected** |
| 015 | Retrieval hardening & admission gate | Implemented |
| 016 | JSON tool-call fallback | Implemented |
| 017 | task_state: active tasks channel | **Implemented** |

---

## License

MIT
