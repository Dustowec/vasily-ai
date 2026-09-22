Vasily AI
Vasily AI — локальный ИИ-агент на архитектуре ReAct (Reasoning + Acting). Работает через Ollama, поддерживает плагины, градиентно-каскадную память с динамическим охлаждением, структурированное логирование, watchdog и автоматические crash-репорты.

Статус: стабильная версия · 319/319 тестов проходят

Возможности
Область	Описание
Ядро	ReAct-цикл с вызовом инструментов · asyncio · автодискавери плагинов · скользящее окно диалога (5 пар)

Память	Gradient Cascade: зоны TGS / Hot / Cold · динамическое охлаждение · флаги no_compress и shield · атомарная запись · LLM-компрессия

Умный поиск	LLM Query Expansion: recall_memory расширяет запрос синонимами через LLM перед поиском (0 МБ VRAM)

Целостность данных	remember_fact проверяет память перед записью и блокирует дубликаты (сравнение по длине + семантический поиск)

Мониторинг	Watchdog: LLM, плагины, память, диск · автовосстановление (до 2 попыток) · статусные иконки

Crash-репорты	Автоматически генерируются при ERROR/CRITICAL в логах · JSON + Markdown · отчёты при фатальных падениях и недоступности LLM

Плагины	Art-промпты · веб-поиск (SearXNG) · веб-скрапинг с SSRF-защитой · Danbooru · чтение локальных файлов · echo

Логирование	4 категории (core/interaction/plugins/llm) · 5 уровней алертов · ротация 72 часа · санация секретов · отдельный watchdog-лог

Безопасность	SSRF-защита · защита от path traversal · санация логов · атомарная запись памяти · crash-репорты

Память (Gradient Cascade)
Память остывает от действий пользователя, а не от календаря.

Принципы:

1 тик = 1 сообщение (запрос + ответ)

Три зоны: TGS (защищённые темы), Hot (активные), Cold (сжатые саммари)

Нагрев: +5 при recall, +10 при remember

Флаг no_compress — запись не сжимается при охлаждении

Флаг shield — защита свежепромотированной записи в TGS

TGS — максимум 10 записей, вытеснение по LRU

Команды: забудь <тема> и забудь всё (с подтверждением)

Умный поиск (recall_memory):

Запрос расширяется через LLM (синонимы, связанные понятия)

Поиск по всем трём зонам, возврат топ-5 по рейтингу

Найденное нагревается через heat_facts

Защита от дубликатов (remember_fact):

Перед записью проверяет память через recall_memory

При обнаружении похожего факта — блокирует запись или обновляет существующий (в зависимости от вердикта LLM: ДУБЛЬ / ДОПОЛНЕНИЕ / ПРОТИВОРЕЧИЕ / НЕТ)

Файлы хранения:

data/tgs_memory.json — защищённые темы

data/tg_hot_memory.json — активные темы

data/tg_cold_memory.json — архив с саммари

Логирование и crash-репорты
Логи пишутся в logs/ с ротацией каждые 72 часа.

Файл	Категория
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Вызовы плагинов
plugins.log	Внутренние логи плагинов
llm.log	Запросы и ответы LLM
watchdog.log	События мониторинга (ротация 50 записей)
vasily.log	Все логи в одном файле
Уровни алертов: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH.

Санация: ключи вида password/token/api_key → [REDACTED]. Поля prompt/query/url на уровне ERROR/CRITICAL заменяются на {length, hash}. Вложенные dict и list проверяются рекурсивно.

Crash-репорты: при появлении записи уровня ERROR или CRITICAL в логгерах core/plugins/llm/interaction автоматически создаётся отчёт. Плюс отчёты генерируются при фатальных падениях и при недоступности LLM после всех попыток восстановления. Сохраняются в logs/crash_reports/YYYY-MM-DD/ в форматах JSON и Markdown.

Watchdog
Фоновый мониторинг раз в 30 секунд: LLM, плагины, память, диск. При сбое — до 2 попыток восстановления. Для LLM — пересоздание клиента, для плагинов — перезагрузка реестра, для памяти — восстановление из .tmp. Состояние отображается иконками в команде status.

Плагины
Плагин	Описание
art_generator	Генерирует детальные промпты для Stable Diffusion / Midjourney
web_search	Поиск через SearXNG (поддерживает моки в dev_mode)
web_scraper	Извлекает текст с веб-страниц с SSRF-защитой
danbooru_search	Поиск постов и тегов на Danbooru
local_reader	Чтение файлов из workspace/reading (csv, json, txt, md, xlsx, pdf)
echo	Тестовый плагин — возвращает введённое сообщение
Структура проекта
text
vasily_ai/
├── core/               # Ядро
│   ├── agent.py        # AgentCore — оркестрация
│   ├── react_loop.py   # ReAct-цикл
│   ├── config.py       # Конфигурация (файл + ENV + дефолты)
│   ├── plugin_registry.py
│   ├── base_tool.py
│   ├── internal_tools.py
│   ├── token_manager.py
│   ├── golden_prompts.py
│   ├── logging_config.py
│   ├── crash_reporter.py
│   ├── health_check.py
│   ├── metrics.py
│   ├── backup.py
│   ├── crypto.py       # Заглушка (NoOp) — задел на будущее
│   └── watchdog.py     # Мониторинг и автовосстановление
├── plugins/            # Автодискавери плагинов
├── memory/             # Память (Gradient Cascade)
│   ├── manager.py
│   └── llm_compressor.py
├── integrations/       # Внешние сервисы
│   └── ollama_client.py
├── tests/              # Тесты (319 collected, 319 passed)
├── logs/               # Логи (создаётся автоматически)
└── data/               # Данные и память (создаётся автоматически)
Тестирование
319 тестов, все проходят.

Быстрый старт
Требования: Python 3.14+, Ollama с моделью (рекомендуется Qwen 3.5-4B Q6_m), опционально SearXNG для веб-поиска.

Установка: через uv sync --all-extras.

Настройка модели: скачать модель в Ollama (ollama pull qwen3.5:4b).

Конфигурация: файл vasily_config.json в корне проекта или переменные окружения с префиксом VASILY_ (например, VASILY_LLM_MODEL, VASILY_DEV_MODE).

Запуск: python -m core.agent.

Команды:

status — состояние агента и памяти

help — справка

забудь <тема> / забыть <тема> — забыть конкретную тему

забудь всё — запрос на полную очистку

забудь всё да — подтверждение

удалить 1, 2 / удалить всех — выбор кандидатов на удаление после забудь

exit — выход

Лицензия
MIT

🌐 English Version
Vasily AI — a local AI agent with a ReAct (Reasoning + Acting) architecture. Runs on Ollama, supports plugins, gradient-cascade memory with dynamic cooling, structured logging, a watchdog, and automatic crash reports.

Status: stable · 319/319 tests passing

Features
Area	Description
Core	ReAct loop with tool calling · asyncio · plugin auto-discovery · sliding window of 5 dialogue pairs
Memory	Gradient Cascade: TGS / Hot / Cold zones · dynamic cooling · no_compress and shield flags · atomic write · LLM compression
Smart Search	LLM Query Expansion: recall_memory expands query with synonyms via LLM before searching (0 MB VRAM)
Data Integrity	remember_fact checks memory before writing and blocks duplicates (length comparison + semantic search)
Monitoring	Watchdog: LLM, plugins, memory, disk · auto-recovery (up to 2 attempts) · status icons
Crash Reports	Auto-generated on ERROR/CRITICAL in logs · JSON + Markdown · also on fatal crashes and LLM unavailability
Plugins	Art prompts · web search (SearXNG) · web scraping with SSRF protection · Danbooru · local file reading · echo
Logging	4 categories (core/interaction/plugins/llm) · 5 alert levels · 72h rotation · secret sanitization · separate watchdog log
Security	SSRF protection · path traversal protection · log sanitization · atomic memory write · crash reports
Gradient Cascade Memory
Memory cools based on user actions, not calendar time.

Principles:

1 tick = 1 message (user + assistant)

Three zones: TGS (protected), Hot (active), Cold (compressed summaries)

Heating: +5 on recall, +10 on remember

no_compress flag — entry is not compressed during cooling

shield flag — protects freshly-promoted TGS entry

TGS holds up to 10 entries, LRU eviction

Commands: forget <topic> and forget all (with confirmation)

Smart Search (recall_memory):

Query expanded via LLM (synonyms, related concepts)

Search across all three zones, top-5 by score

Results are heated via heat_facts

Deduplication (remember_fact):

Checks memory via recall_memory before writing

If a similar fact is found — blocks the write or updates the existing one (based on LLM verdict: DUPLICATE / ADDITION / CONTRADICTION / NO)

Storage files:

data/tgs_memory.json — protected topics

data/tg_hot_memory.json — active topics

data/tg_cold_memory.json — archived summaries

Logging and Crash Reports
Logs are written to logs/ with 72h rotation.

File	Category
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Plugin calls
plugins.log	Plugin internals
llm.log	LLM requests/responses
watchdog.log	Monitoring events (50-entry rotation)
vasily.log	All logs combined
Alert levels: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH.

Sanitization: keys like password/token/api_key → [REDACTED]. prompt/query/url fields at ERROR/CRITICAL are replaced with {length, hash}. Nested dicts and lists are inspected recursively.

Crash reports: any ERROR or CRITICAL in the core/plugins/llm/interaction loggers automatically triggers a report. Reports are also generated on fatal crashes and when the LLM is unavailable after all recovery attempts. Saved to logs/crash_reports/YYYY-MM-DD/ in JSON and Markdown.

Watchdog
Background monitoring every 30 seconds: LLM, plugins, memory, disk. On failure — up to 2 recovery attempts. LLM — client recreation, plugins — registry reload, memory — restore from .tmp. State is shown via icons in the status command.

Plugins
Plugin	Description
art_generator	Generates detailed prompts for Stable Diffusion / Midjourney
web_search	Searches via SearXNG (mocks in dev_mode)
web_scraper	Extracts page content with SSRF protection
danbooru_search	Searches Danbooru posts and tags
local_reader	Reads files from workspace/reading (csv, json, txt, md, xlsx, pdf)
echo	Test plugin — returns input as-is
Project Structure
text
vasily_ai/
├── core/               # Core components
├── plugins/            # Auto-discovered plugins
├── memory/             # Gradient Cascade Memory
├── integrations/       # External services
├── tests/              # Test suite (319 collected, 319 passed)
├── logs/               # Rotated logs (auto-created)
└── data/               # Persistent data (auto-created)
Testing
319 tests, all passing.

Quick Start
Requirements: Python 3.14+, Ollama with a compatible model (recommended: Qwen 3.5-4B Q6_m), optionally SearXNG for web search.

Installation: via uv sync --all-extras.

Model setup: pull the model into Ollama.

Configuration: vasily_config.json in the project root, or environment variables with the VASILY_ prefix (e.g. VASILY_LLM_MODEL, VASILY_DEV_MODE).

Run: python -m core.agent.

Commands:

status — agent and memory status

help — help

forget <topic> — forget a specific topic

forget all — request full wipe

forget all yes — confirm

delete 1, 2 / delete all — pick candidates after forget

exit — exit

License
MIT
