# ADR-004: Структурированные логи через structlog

## Статус
Принято

## Контекст
Текущая система логирования использует print() и кастомные функции log_info(), log_error().

**Проблемы:**
1. Логи — это просто текст, их сложно фильтровать
2. Нет структурированных данных (timestamp, module, function, request_id)
3. Сложно анализировать логи программно
4. Нельзя легко переключить уровень детализации (DEBUG/INFO/ERROR)

**Пример плохого лога:**
\\\
[12:34:56] Ошибка: Connection timeout (контекст: scrape_website)
\\\

## Решение
Использовать **structlog** для структурированного логирования.

**Пример хорошего лога:**
\\\json
{
  "timestamp": "2026-08-07T12:34:56.789Z",
  "level": "error",
  "module": "plugins.web_scraper",
  "function": "scrape_website",
  "request_id": "abc-123-def",
  "message": "Connection timeout",
  "url": "https://example.com",
  "timeout": 10
}
\\\

**Технический стек:**
- structlog — библиотека для структурированных логов
- JSON-формат для машинного чтения
- Colorized output для консоли
- Rotating file handler для файлов

**Интеграция:**
\\\python
import structlog

logger = structlog.get_logger()

async def scrape_website(url: str):
    logger.info("scraping_started", url=url)
    try:
        response = await aiohttp.get(url)
        logger.info("scraping_success", url=url, status=response.status)
    except Exception as e:
        logger.error("scraping_failed", url=url, error=str(e))
\\\

## Последствия

### Положительные:
- ✅ Логи можно фильтровать через jq или другие инструменты
- ✅ Каждый лог имеет контекст (timestamp, module, request_id)
- ✅ Можно легко переключить уровень детализации
- ✅ Логи готовы для отправки в ELK/Splunk/Grafana Loki
- ✅ Автоматический injection контекста (не нужно передавать request_id вручную)

### Отрицательные:
- ❌ Логи в JSON сложнее читать глазами (но есть colorized output)
- ❌ Дополнительная зависимость (structlog)
- ❌ Нужно переписать все вызовы log_info() → logger.info()

### Форматы вывода:
1. **Консоль:** Colorized, читаемый для человека
2. **Файл:** JSON для машинного анализа
3. **Remote:** Отправка в систему мониторинга (опционально)

## Ссылки
- [structlog документация](https://www.structlog.org/)
- [12-Factor App: Logs](https://12factor.net/logs)
