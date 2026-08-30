# Vasily AI

[English version below](#english-version)

## 🇷🇺 Русская версия

**Vasily AI** — локальный ИИ-агент с архитектурой ReAct (Reasoning + Acting). Работает через Ollama, поддерживает плагины, имеет градиентно-сессионную память с динамическим охлаждением, структурированное логирование, систему мониторинга (Watchdog) и веб-дашборд.

**Статус:** ✅ Стабильная версия · Готов к использованию · **279/279 тестов проходят**

---

### Возможности

| Область | Описание |
|---------|----------|
| **Ядро** | ReAct-цикл с вызовом инструментов · Асинхронное ядро (asyncio) · Автодискавери плагинов · Скользящее окно диалога на 5 пар |
| **Память** | **Градиентно-сессионная (Gradient Cascade)**: три зоны (TGS, Hot, Cold), динамическое охлаждение, защита `protected` и `shield`, атомарная запись, LLM-компрессия |
| **Умный поиск** | **LLM Query Expansion (0 МБ VRAM)**: `recall_memory` семантически расширяет запрос синонимами через LLM перед поиском, находя факты даже при неточном совпадении слов |
| **Целостность данных** | **Защита от дубликатов**: `remember_fact` архитектурно проверяет память перед записью и блокирует создание дубликатов (сравнение по длине + семантический поиск) |
| **Мониторинг** | Watchdog: фоновый мониторинг LLM, плагинов, памяти и диска · Автоматическое восстановление (до 2 попыток) · Crash-репорты · Уведомления пользователя |
| **Плагины** | Генерация арт-промптов · Веб-поиск (SearXNG) · Веб-скрапинг с SSRF-защитой · Поиск по Danbooru · Чтение локальных файлов (csv, json, txt, md, xlsx, pdf) · Echo (для тестов) |
| **Логирование** | 4 категории (core/interaction/plugins/llm) · 5 уровней алертов · Ротация 72 часа · Санация PII · Отдельный лог для Watchdog |
| **Безопасность** | Crash-репорты (JSON + MD) · SSRF-защита · Защита от path traversal · Санация логов · Атомарная запись памяти |
| **Интерфейс** | Веб-дашборд (Streamlit) · История чата в реальном времени · Статус модулей · Управление памятью · Краш-лог |

---

### Новая архитектура памяти (Gradient Cascade Memory)

Память больше не привязана к календарю — она остывает от действий пользователя.

**Принципы:**
- 1 тик = 1 сообщение (запрос + ответ)
- Остывание замедляется при интенсивной сессии (Floating Decay)
- Три зоны: TGS (защита), Hot (активная), Cold (сжатые саммари)
- Нагрев: +5.0 при `recall`, +10.0 при `remember`
- Защита `protected`: тема не сжимается повторно до мутации
- Команды: `забудь <тема>` и `забудь всё` (с подтверждением)

**Умный поиск (`recall_memory`):**
- Запрос семантически расширяется через LLM (синонимы, связанные понятия)
- Поиск идёт по HOT и COLD зонам, TGS исключена (чтобы не дублировать системный промпт)
- Возвращает топ-5 фактов, отсортированных по рейтингу

**Защита от дубликатов (`remember_fact`):**
- Перед записью проверяет память через `recall_memory` (с семантическим расширением)
- Если найден похожий факт — блокирует запись и возвращает `already_exists`
- Сравнение по длине: если тексты сопоставимы по размеру (>40% совпадения) — считает дубликатом

**Файлы хранения:**
- `data/tgs_memory.json` — защищённые темы
- `data/tg_hot_memory.json` — активные темы
- `data/tg_cold_memory.json` — архив с саммари

---

### Веб-дашборд (Streamlit)

Запустите дашборд командой:

streamlit run ui/dashboard.py
Дашборд предоставляет:

Историю чата в реальном времени

Статус всех модулей (LLM, плагины, память, диск) с цветовыми индикаторами

Отображение текущего RAG-контекста (что будет передано в LLM)

Кнопки управления: «Забыть всё», «Перезапуск агента», «Выход»

Краш-лог — показывает последний отчёт об ошибке

Автоматическое обновление статуса каждые 5 секунд

Быстрый старт
Требования:

Python 3.14+

Ollama с моделью (рекомендуется Qwen 3.5-4B Q6_m)

SearXNG (опционально, для веб-поиска)

Установка:

git clone https://github.com/Dustowec/vasily-ai.git
cd vasily-ai
uv sync --all-extras
Настройка модели:

Скачайте модель в Ollama:

ollama pull qwen3.5:4b
Настройка:

Создай vasily_config.json в корне проекта:

json
{
  "llm_url": "http://localhost:11434",
  "llm_model": "qwen3.5:4b",
  "searxng_url": "http://localhost:8080/search",
  "dev_mode": false,
  "max_react_iterations": 6
}
Или используй переменные окружения:

export VASILY_LLM_MODEL=qwen3.5:4b
export VASILY_DEV_MODE=true
Запуск:

CLI-версия: python -m core.agent

Дашборд: streamlit run ui/dashboard.py

Команды:

text
> status                         — показать состояние агента и памяти
> help                            — справка
> забыть <тема>                  — забыть конкретную тему
> забыть всё                      — запрос на полную очистку
> забыть всё да                   — подтверждение полной очистки
> exit                            — выход (только в CLI)
Плагины
Плагин	Описание
art_generator	Генерирует детальные промпты для Stable Diffusion / Midjourney
web_search	Поиск через SearXNG (поддерживает моки в dev_mode)
web_scraper	Извлекает текст с веб-страниц с SSRF-защитой
danbooru_search	Поиск постов и тегов на Danbooru
local_reader	Чтение файлов из workspace/reading/ (csv, json, txt, md, xlsx, pdf)
echo	Тестовый плагин — возвращает введённое сообщение
Структура проекта
text
vasily_ai/
├── core/               # Ядро
│   ├── agent.py        # AgentCore — оркестрация
│   ├── react_loop.py   # ReAct-цикл
│   ├── config.py       # Конфигурация (файл + ENV + дефолты)
│   ├── plugin_registry.py
│   ├── token_manager.py
│   ├── golden_prompts.py
│   ├── logging_config.py
│   ├── crash_reporter.py
│   ├── health_check.py
│   ├── metrics.py
│   ├── backup.py
│   ├── crypto.py       # Заглушка (NoOp) — задел на будущее
│   ├── scheduler.py    # Периодические задачи
│   └── watchdog.py     # Мониторинг и автовосстановление
├── plugins/            # Автодискавери плагинов
├── memory/             # Память (Gradient Cascade)
│   ├── manager.py      # Основная логика
│   └── llm_compressor.py
├── integrations/       # Внешние сервисы
│   └── ollama_client.py
├── ui/                 # Веб-дашборд
│   └── dashboard.py    # Streamlit-приложение
├── tests/              # Тесты (279 collected, 279 passed)
├── logs/               # Логи (создаётся автоматически)
└── data/               # Данные и память (создаётся автоматически)
Тестирование
pytest tests/ -v
Результат: 279 тестов, 279 проходят.

Ключевые сценарии:

python test_intelligence.py   # P2-2: метрики, проход сценариев
Логи и мониторинг
Логи пишутся в logs/ с ротацией 72 часа:

Файл	Категория
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Вызовы плагинов
plugins.log	Внутренние логи плагинов
llm.log	Запросы и ответы LLM
watchdog.log	События мониторинга (ротация 50 записей)
vasily.log	Все логи в одном файле
Уровни алертов: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH

Crash-репорты сохраняются в logs/crash_reports/YYYY-MM-DD/crash_XXX.json и .md.

Лицензия
MIT

🌐 English Version
Overview
Vasily AI is a local AI agent with a ReAct (Reasoning + Acting) architecture. It runs on Ollama, supports plugins, has gradient-session memory with dynamic cooling, structured logging, a monitoring system (Watchdog), and a web dashboard.

Status: ✅ Stable · Ready for use · 279/279 tests passing

Features
Area	Description
Core	ReAct loop with tool calling · Async asyncio core · Plugin auto-discovery · Sliding window of 5 dialogue pairs
Memory	Gradient Cascade: three zones (TGS, Hot, Cold), dynamic cooling, protected/shield flags, atomic write, LLM compression
Smart Search	LLM Query Expansion (0 MB VRAM): recall_memory semantically expands query with synonyms via LLM before searching, finding facts even with fuzzy matches
Data Integrity	Deduplication: remember_fact checks memory before writing and blocks duplicate creation (length comparison + semantic search)
Monitoring	Watchdog: background monitoring of LLM, plugins, memory, disk · Auto-recovery (up to 2 attempts) · Crash reports · User notifications
Plugins	Art generation · Web search · Web scraping with SSRF protection · Danbooru search · Local file reading (csv, json, txt, md, xlsx, pdf) · Echo
Logging	4 categories · 5 alert levels · 72h rotation · PII sanitization · Separate watchdog log
Security	Crash reports (JSON + MD) · SSRF protection · Path traversal protection · Atomic memory write
Interface	Web dashboard (Streamlit) · Real-time chat history · Module status · Memory management · Crash log
Gradient Cascade Memory
Memory is no longer tied to calendar time — it cools based on user actions.

Principles:

1 tick = 1 message (user + assistant)

Cooling slows under high session load (Floating Decay)

Three zones: TGS (protected), Hot (active), Cold (compressed summaries)

Heating: +5.0 on recall, +10.0 on remember

Protected flag: prevents re-compression until mutation

Commands: forget <topic> and forget all (with confirmation)

Smart Search (recall_memory):

Query is semantically expanded via LLM (synonyms, related concepts)

Search goes through HOT and COLD zones, TGS is excluded (to avoid duplicating system prompt)

Returns top 5 facts sorted by score

Deduplication (remember_fact):

Checks memory via recall_memory (with semantic expansion) before writing

If similar fact is found — blocks write and returns already_exists

Length comparison: if texts are comparable in size (>40% overlap) — treats as duplicate

Storage files:

data/tgs_memory.json — protected topics

data/tg_hot_memory.json — active topics

data/tg_cold_memory.json — archived summaries

Web Dashboard (Streamlit)
Run the dashboard with:

streamlit run ui/dashboard.py
The dashboard provides:

Real-time chat history

Module status (LLM, plugins, memory, disk) with color indicators

Current RAG context (what will be sent to LLM)

Control buttons: «Forget All», «Restart Agent», «Exit»

Crash log — shows the latest error report

Auto-refresh every 5 seconds

Quick Start
Requirements:

Python 3.14+

Ollama with compatible model (recommended: Qwen 3.5-4B Q6_m)

SearXNG (optional, for web search)

Installation:

git clone https://github.com/Dustowec/vasily-ai.git
cd vasily-ai
uv sync --all-extras
Model setup:

Pull the model into Ollama:

ollama pull qwen3.5:4b
Configuration:

Create vasily_config.json in the project root:

json
{
  "llm_url": "http://localhost:11434",
  "llm_model": "qwen3.5:4b",
  "searxng_url": "http://localhost:8080/search",
  "dev_mode": false,
  "max_react_iterations": 6
}
Or use environment variables:

export VASILY_LLM_MODEL=qwen3.5:4b
export VASILY_DEV_MODE=true
Run:

CLI: python -m core.agent

Dashboard: streamlit run ui/dashboard.py

Commands:

text
> status                         — show agent and memory status
> help                            — show help
> forget <topic>                  — forget a specific topic
> forget all                      — request full memory wipe
> forget all yes                  — confirm full memory wipe
> exit                            — exit (CLI only)
Plugins
Plugin	Description
art_generator	Generates detailed prompts for Stable Diffusion / Midjourney
web_search	Searches via SearXNG (mocks in dev_mode)
web_scraper	Extracts page content with SSRF protection
danbooru_search	Searches Danbooru posts and tags
local_reader	Reads files from workspace/reading/ (csv, json, txt, md, xlsx, pdf)
echo	Test plugin — returns input as-is
Project Structure
text
vasily_ai/
├── core/               # Core components
├── plugins/            # Auto-discovered plugins
├── memory/             # Gradient Cascade Memory
├── integrations/       # External services
├── ui/                 # Web dashboard
├── tests/              # Test suite (279 collected, 279 passed)
├── logs/               # Rotated logs (auto-created)
└── data/               # Persistent data (auto-created)
Testing
pytest tests/ -v
Result: 279 tests, 279 passed.

python test_intelligence.py   # P2-2: metrics, scenario passing
Logging
Logs are written to logs/ with 72h rotation:

File	Category
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Core ↔ Plugin calls
plugins.log	Plugin internals
llm.log	LLM requests/responses
watchdog.log	Monitoring events (50-entry rotation)
vasily.log	All logs combined
Alert levels: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH

Crash reports: logs/crash_reports/YYYY-MM-DD/crash_XXX.json and .md

License
MIT
