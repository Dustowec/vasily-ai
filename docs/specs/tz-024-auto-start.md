# ТЗ-024: Автоподъём сервисов

Дата: 2026-08-11
Статус: Draft
Фаза: 3

## Цель

Если Ollama не запущен, агент должен автоматически запустить его
и дождаться готовности вместо немедленного падения.

## Минимальный scope

1. Новый модуль core/service_launcher.py.
2. Функция ensure_ollama_running(config) -> bool.
3. Логика:
   - health check Ollama (GET /api/tags).
   - если недоступен и config.llm_auto_start == True:
     запустить процесс "ollama serve" через subprocess.
   - ждать до N секунд (config.llm_auto_start_timeout),
     пока health check не пройдёт.
   - вернуть True если сервис доступен, False если нет.
4. Вызывается в AgentCore.initialize() перед health_check().
5. Если llm_auto_start == False — поведение не меняется.

## Вне минимального scope

- Автоподъём SearXNG и других внешних сервисов.
- Управление systemd / Windows Services.
- Автоматическая установка Ollama.

## Acceptance criteria

1. core/service_launcher.py существует.
2. ensure_ollama_running возвращает True когда Ollama уже запущен.
3. ensure_ollama_running возвращает False когда Ollama недоступен
   и llm_auto_start == False.
4. AgentCore.initialize() вызывает ensure_ollama_running.
5. Существующие тесты остаются зелёными.
