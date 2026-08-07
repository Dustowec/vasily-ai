# ADR-003: Отказ от LM Studio в пользу Ollama для автоподъёма

## Статус
Принято

## Контекст
Агент должен работать 24/7 и автоматически восстанавливаться после сбоев. Для этого нужно:
- Автоматически запускать LLM-сервер при старте системы
- Перезапускать его при падении
- Контролировать использование VRAM/RAM
- Переключаться между моделями

**Проблема с LM Studio:**
1. Не имеет нормального CLI для headless-режима (без GUI)
2. Сложно автоматизировать запуск с нужными параметрами
3. Нет встроенного watchdog (автоматического перезапуска)
4. Требует ручного выбора модели через GUI

## Решение
Использовать **Ollama** вместо LM Studio для локальных LLM.

**Преимущества Ollama:**
1. **CLI-first подход:** Все операции через командную строку
2. **Docker-поддержка:** Легко упаковать в контейнер
3. **Systemd/Taskscheduler:** Нативная поддержка автозапуска
4. **REST API:** Единый интерфейс для всех моделей
5. **Модельный хаб:** ollama pull llama3.2 — одна команда для скачивания

**Пример использования:**
\\\ash
# Запуск сервера
ollama serve

# Скачивание модели
ollama pull llama3.2

# Запрос к модели
curl http://localhost:11434/api/generate -d '{
  "model": "llama3.2",
  "prompt": "Привет!"
}'
\\\

**Интеграция с агентом:**
\\\python
class OllamaClient:
    def __init__(self, model: str = "llama3.2"):
        self.base_url = "http://localhost:11434"
        self.model = model

    async def chat(self, messages: list) -> str:
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages}
            )
            return (await response.json())["message"]["content"]
\\\

## Последствия

### Положительные:
- ✅ Легко автоматизировать запуск и перезапуск
- ✅ Можно управлять через systemd (Linux) или Task Scheduler (Windows)
- ✅ Единый API для всех моделей (Llama, Mistral, Qwen)
- ✅ Меньше потребления ресурсов (нет GUI)
- ✅ Возможность запуска в Docker-контейнере

### Отрицательные:
- ❌ Нет GUI для ручного управления (но нам и не нужен)
- ❌ Меньше настроек квантизации (но достаточно для большинства задач)
- ❌ Нужно переписать интеграцию с LM Studio

### Миграция:
1. Установить Ollama: winget install Ollama.Ollama
2. Скачать модели: ollama pull llama3.2, ollama pull qwen2.5:14b
3. Переписать LMStudioClient → OllamaClient
4. Настроить автозапуск через Task Scheduler (Windows) или systemd (Linux)

## Ссылки
- [Ollama GitHub](https://github.com/ollama/ollama)
- [Ollama API документация](https://github.com/ollama/ollama/blob/main/docs/api.md)
