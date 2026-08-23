Vasily AI
English version below

🇷🇺 Русская версия
Vasily AI — локальный ИИ-агент с архитектурой ReAct (Reasoning + Acting). Работает через Ollama, поддерживает плагины, имеет градиентно-сессионную память с динамическим охлаждением, структурированное логирование и систему crash-репортов.

Статус: ✅ Стабильная версия · Память обновлена до Gradient Cascade · Готов к дашборду

Возможности
Область	Описание
Ядро	ReAct-цикл с вызовом инструментов · Асинхронное ядро (asyncio) · Автодискавери плагинов
Память	Градиентно-сессионная (Gradient Cascade): три зоны (TGS, Hot, Cold), динамическое охлаждение, защита protected и shield, атомарная запись
Плагины	Генерация арт-промптов · Веб-поиск (SearXNG) · Веб-скрапинг с SSRF-защитой · Поиск по Danbooru · Чтение локальных файлов (csv, json, txt, md, xlsx, pdf) · Echo (для тестов)
Логирование	4 категории (core/interaction/plugins/llm) · 5 уровней алертов · Ротация 72 часа · Санация PII
Безопасность	Crash-репорты (JSON + MD) · SSRF-защита · Защита от path traversal · Санация логов · Атомарная запись памяти
Инструменты	TokenManager · MetricsCollector · BackupManager · Health Check · Автозапуск Ollama · Команды управления памятью
Новая архитектура памяти (Gradient Cascade Memory)
Память больше не привязана к календарю — она остывает от действий пользователя.

Принципы:

1 тик = 1 сообщение (запрос + ответ)

Остывание замедляется при интенсивной сессии (Floating Decay)

Три зоны: TGS (защита), Hot (активная), Cold (сжатые саммари)

Нагрев: +5.0 при recall, +10.0 при remember

Защита protected: тема не сжимается повторно до мутации

Команды: забудь <тема> и забудь всё (с подтверждением)

Файлы хранения:

data/tgs_memory.json — защищённые темы

data/tg_hot_memory.json — активные темы

data/tg_cold_memory.json — архив с саммари

Быстрый старт
Требования:

Python 3.14+

Ollama с моделью (рекомендуется vasily-qwen или llama3.2)

SearXNG (опционально, для веб-поиска)

Установка:

bash
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

bash
export VASILY_LLM_MODEL=llama3.2
export VASILY_DEV_MODE=true
Запуск:

bash
python -m vasily_ai
Или двойным кликом по run_agent.bat.

Команды:

text
> привет
> нарисуй самурая под дождём
> найди погоду на завтра
> прочитай файл data/report.json
> статус
> забыть про самурая
> забыть всё
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
├── memory/             # Память (Gradient Cascade)
│   ├── manager.py      # Основная логика
│   └── llm_compressor.py
├── integrations/       # Внешние сервисы
│   └── ollama_client.py
├── tests/              # Тесты (111 passed)
├── logs/               # Логи (создаётся автоматически)
└── data/               # Данные и память (создаётся автоматически)
Тестирование
bash
pytest tests/ -v
Ключевые сценарии:

bash
python test_intelligence.py   # P2-2: метрики, проход сценариев
Логи и мониторинг
Логи пишутся в logs/ с ротацией 72 часа:

Файл	Категория
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Вызовы плагинов
plugins.log	Внутренние логи плагинов
llm.log	Запросы и ответы LLM
vasily.log	Все логи в одном файле
Уровни алертов: STATE, REQUEST, WARNING, CRITICAL_WARNING, CRASH

Crash-репорты сохраняются в logs/crash_reports/YYYY-MM-DD/crash_XXX.json и .md.

Лицензия
MIT

🌐 English Version
Overview
Vasily AI is a local AI agent with a ReAct (Reasoning + Acting) architecture. It runs on Ollama, supports plugins, has gradient-session memory with dynamic cooling, structured logging, and crash reporting.

Status: ✅ Stable · Memory upgraded to Gradient Cascade · Ready for dashboard

Features
Area	Description
Core	ReAct loop with tool calling · Async asyncio core · Plugin auto-discovery
Memory	Gradient Cascade: three zones (TGS, Hot, Cold), dynamic cooling, protected/shield flags, atomic write
Plugins	Art generation · Web search · Web scraping with SSRF protection · Danbooru search · Local file reading (csv, json, txt, md, xlsx, pdf) · Echo
Logging	4 categories · 5 alert levels · 72h rotation · PII sanitization
Security	Crash reports (JSON + MD) · SSRF protection · Path traversal protection · Atomic memory write
Tools	TokenManager · MetricsCollector · BackupManager · Health Check · Ollama auto-start · Memory commands
Gradient Cascade Memory
Memory is no longer tied to calendar time — it cools based on user actions.

Principles:

1 tick = 1 message (user + assistant)

Cooling slows under high session load (Floating Decay)

Three zones: TGS (protected), Hot (active), Cold (compressed summaries)

Heating: +5.0 on recall, +10.0 on remember

Protected flag: prevents re-compression until mutation

Commands: forget <topic> and forget all (with confirmation)

Storage files:

data/tgs_memory.json — protected topics

data/tg_hot_memory.json — active topics

data/tg_cold_memory.json — archived summaries

Quick Start
Requirements:

Python 3.14+

Ollama with compatible model

SearXNG (optional)

Installation:

bash
git clone https://github.com/Dustowec/vasily-ai.git
cd vasily-ai
uv sync --all-extras
Run:

bash
python -m vasily_ai
Or double-click run_agent.bat.

Commands:

text
> hello
> draw a samurai in the rain
> search for weather tomorrow
> read file data/report.json
> status
> forget about samurai
> forget all
> help
> exit
Plugins
Plugin	Description
art_generator	Generates detailed prompts for Stable Diffusion / Midjourney
web_search	Searches via SearXNG (mocks in dev_mode)
web_scraper	Extracts page content with SSRF protection
danbooru_search	Searches Danbooru posts and tags
local_reader	Reads files from data/ and reports/ (csv, json, txt, md, xlsx, pdf)
echo	Test plugin — returns input as-is
Project Structure
text
vasily_ai/
├── core/               # Core components
├── plugins/            # Auto-discovered plugins
├── memory/             # Gradient Cascade Memory
├── integrations/       # External services
├── tests/              # Test suite (111 passed)
├── logs/               # Rotated logs (auto-created)
└── data/               # Persistent data (auto-created)
Testing
bash
pytest tests/ -v
python test_intelligence.py
Logging
Logs are written to logs/ with 72h rotation:

File	Category
core.log	AgentCore, PluginRegistry, ReActLoop
interaction.log	Core ↔ Plugin calls
plugins.log	Plugin internals
llm.log	LLM requests/responses
vasily.log	All logs combined
Crash reports: logs/crash_reports/YYYY-MM-DD/crash_XXX.json and .md

License
MIT
