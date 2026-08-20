Vasily AI
English version below

🇷🇺 Русская версия
Vasily AI — локальный ИИ-агент с архитектурой ReAct (Reasoning + Acting). Работает через Ollama, поддерживает плагины, имеет двухуровневую память, структурированное логирование и систему crash-репортов.

Статус: ✅ Стабильная версия · Готов к интеграции с дашбордом

Возможности
Область	Описание
Ядро	ReAct-цикл с вызовом инструментов · Асинхронное ядро (asyncio) · Автодискавери плагинов
Плагины	Генерация арт-промптов · Веб-поиск (SearXNG) · Веб-скрапинг с SSRF-защитой · Поиск по Danbooru · Чтение локальных файлов (csv, json, txt, md, xlsx, pdf) · Echo (для тестов)
Память	Двухуровневая (Hot/Cold) · LLM-компрессия · Автоматический сброс диалога через 30 минут
Логирование	4 категории (core/interaction/plugins/llm) · 5 уровней алертов · Ротация 72 часа · Санация PII
Безопасность	Crash-репорты (JSON + MD) · SSRF-защита · Защита от path traversal · Санация логов
Инструменты	TokenManager · MetricsCollector · BackupManager · Health Check · Автозапуск Ollama
Быстрый старт
Требования:

Python 3.14+

Ollama с моделью (рекомендуется vasily-qwen или llama3.2)

SearXNG (опционально, для веб-поиска)

Установка:

git clone https://github.com/Dustowec/vasily-ai.git
cd vasily-ai
uv sync --all-extras
Настройка:

Создай vasily_config.json в корне проекта:

json
{
  "llm_url": "http://localhost:11434",
  "llm_model": "vasily-qwen",
  "searxng_url": "http://localhost:8080/search",
  "dev_mode": false,
  "max_react_iterations": 6
}
Или используй переменные окружения:

export VASILY_LLM_MODEL=llama3.2
export VASILY_DEV_MODE=true
Запуск:

python -m vasily_ai
Команды CLI:

text
> Нарисуй самурая под дождём
> Поищи информацию про Stable Diffusion
> Прочитай файл data/report.json
> status
> help
> exit
Плагины
Плагин	Описание
art_generator	Генерирует детальные промпты для Stable Diffusion / Midjourney
web_search	Поиск через SearXNG (поддерживает моки в dev_mode)
web_scraper	Извлекает текст с веб-страниц с SSRF-защитой
danbooru_search	Поиск постов и тегов на Danbooru
local_reader	Чтение файлов из data/ и reports/ (csv, json, txt, md, xlsx, pdf)
echo	Тестовый плагин — возвращает введённое сообщение
Плагины загружаются автоматически. Достаточно добавить новый плагин в папку plugins/ — он станет доступен агенту.

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
│   └── scheduler.py    # Периодические задачи
├── plugins/            # Автодискавери плагинов
│   ├── art_generator/
│   ├── web_search/
│   ├── web_scraper/
│   ├── danbooru/
│   ├── local_reader/
│   └── echo/
├── memory/             # Память
│   ├── manager.py
│   ├── long_term.py
│   └── llm_compressor.py
├── integrations/       # Внешние сервисы
│   └── ollama_client.py
├── tests/              # Тесты (111 passed)
├── logs/               # Логи (создаётся автоматически)
└── data/               # Данные (создаётся автоматически)
Тестирование
pytest tests/ -v
Ключевые сценарии:

python test_intelligence.py   # P2-2: метрики, проход сценариев, точность инструментов
python test_react_loop.py     # ReAct-цикл с реальной LLM и плагинами
python test_ollama_client.py  # LLM-клиент с ретраями и crash-репортами
Логи и мониторинг
Логи пишутся в logs/ с ротацией каждые 24 часа и хранением 3 дня:

Файл	Категория
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Вызовы плагинов из ядра
plugins.log	Внутренние логи плагинов
llm.log	Запросы и ответы LLM
vasily.log	Все логи в одном файле
Уровни алертов: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH

Crash-репорты сохраняются в logs/crash_reports/YYYY-MM-DD/crash_XXX.json и .md.

Лицензия
MIT

🌐 English Version
Overview
Vasily AI is a local AI agent with a ReAct (Reasoning + Acting) architecture. It runs on Ollama, supports plugins, has two-tier memory, structured logging, and crash reporting.

Status: ✅ Stable · Ready for dashboard integration

Features
Area	Description
Core	ReAct loop with tool calling · Async asyncio core · Plugin auto-discovery
Plugins	Art prompt generation · Web search (SearXNG) · Web scraping with SSRF protection · Danbooru search · Local file reading (csv, json, txt, md, xlsx, pdf) · Echo (testing)
Memory	Two-tier (Hot/Cold) · LLM-powered compression · Dialogue auto-reset every 30 min
Logging	4 categories (core/interaction/plugins/llm) · 5 alert levels · 72h rotation · PII sanitization
Security	Crash reports (JSON + MD) · SSRF protection · Path traversal protection · Log sanitization
Tools	TokenManager · MetricsCollector · BackupManager · Health Check · Ollama auto-start
Quick Start
Requirements:

Python 3.14+

Ollama with a compatible model (vasily-qwen or llama3.2)

SearXNG (optional, for web search)

Installation:

git clone https://github.com/Dustowec/vasily-ai.git
cd vasily-ai
uv sync --all-extras
Configuration:

Create vasily_config.json in the project root:

json
{
  "llm_url": "http://localhost:11434",
  "llm_model": "vasily-qwen",
  "searxng_url": "http://localhost:8080/search",
  "dev_mode": false,
  "max_react_iterations": 6
}
Or use environment variables:

export VASILY_LLM_MODEL=llama3.2
export VASILY_DEV_MODE=true
Run:

python -m vasily_ai
CLI Commands:

text
> Draw a samurai in the rain
> Search for information about Stable Diffusion
> Read file data/report.json
> status
> help
> exit
Plugins
Plugin	Description
art_generator	Generates detailed prompts for Stable Diffusion / Midjourney
web_search	Searches via SearXNG (supports mocks in dev_mode)
web_scraper	Extracts page content with SSRF protection
danbooru_search	Searches Danbooru posts and tags
local_reader	Reads files from data/ and reports/ (csv, json, txt, md, xlsx, pdf)
echo	Test plugin — returns input as-is
Plugins are auto-discovered. Just drop a new plugin into plugins/ and it will be available.

Project Structure
text
vasily_ai/
├── core/               # Core components
├── plugins/            # Auto-discovered plugins
├── memory/             # Memory subsystem
├── integrations/       # External services
├── tests/              # Test suite (111 passed)
├── logs/               # Rotated logs (auto-created)
└── data/               # Persistent data (auto-created)
Testing
pytest tests/ -v
Key tests:

python test_intelligence.py   # P2-2 metrics: scenario pass rate, tool accuracy
python test_react_loop.py     # ReAct loop with real LLM and plugins
python test_ollama_client.py  # LLM client with retries and crash reports
Logging & Monitoring
Logs are written to logs/ with 24‑hour rotation and 3‑day retention:

File	Category
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Core ↔ Plugin calls
plugins.log	Plugin internals
llm.log	LLM requests/responses
vasily.log	All logs combined
Alert levels: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH

Crash reports are saved to logs/crash_reports/YYYY-MM-DD/crash_XXX.json and .md.

License
MIT
