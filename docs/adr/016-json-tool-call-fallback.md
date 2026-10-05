# ADR-016: JSON tool-call fallback — модели без native tools

Статус: Accepted
Дата: 2026-10-05
Суперсид: ADR-013 (retrieval), ADR-015 (retrieval hardening)
Автор: Архитектор (User)
Red team: TDD-цикл, 31 красный тест до реализации (2 шага)

## 1. Контекст

Изначальное видение проекта: **агент не должен знать, какая модель
крутится в Ollama** — 1.5B или 32B, лишь бы присылала корректный JSON.
Ollama — просто генератор текста, capabilities модели — деталь
реализации, не архитектуры.

На практике это видение было неосознанно нарушено: ReActLoop жёстко
опирался на native tool calling Ollama. Попытка использовать
Gemma 3n E4B (abliterated, Q4_K_M, 4.2 ГБ) как основную модель
выявила:

- Ollama 0.35.1 не поддерживает tools для семейства gemma3n:
  `capabilities = {completion}`, tools отсутствует;
- при передаче `tools=[...]` в `/api/chat` Ollama отвечает
  HTTP 400 `"does not support tools"`;
- официальная `gemma3n:e4b` от Google имеет **те же** capabilities —
  это ограничение runtime, не нашей обвязки;
- даже если бы tools прошли, ReActLoop добавляет в историю после
  первой итерации `role: "tool"` и `assistant.tool_calls` — Ollama
  видит native-конструкции и пытается построить parser по TEMPLATE
  модели → HTTP 400 `"Unable to generate parser for this template"`.

Дополнительно: smoke-тест на Qwen3.5-4B показал `Similarity verdict
timed out` на 10 секунд — reasoning-модель не укладывалась в timeout
внутренних вызовов памяти.

Документ фиксирует решение по JSON-fallback, порядок внедрения
(TDD) и открытые вопросы, выявленные smoke-тестом.

## 2. Архитектурный принцип

**ReActLoop не знает, какая модель отвечает и откуда взялись
tool_calls.**

Клиент (`OllamaClient`) сам определяет режим при первом вызове
`chat()` с `tools=[...]`:

- **native** — передаёт `tools=` в payload Ollama, читает
  `message.tool_calls`;
- **fallback** — вставляет схему инструментов в system prompt,
  парсит `<tool_call>{...}</tool_call>` из `content`, синтезирует
  `message.tool_calls` в формате Ollama.

Оба режима возвращают **один и тот же формат**. `ReActLoop` не
меняется вообще — ему всё равно, какая модель отвечает и как были
получены вызовы.

## 3. Автодетект capabilities

При первом `chat()` с непустым `tools`:

1. `GET /api/tags`, читаем `capabilities` модели. Сравнение по имени
   без тега (`:latest` отбрасывается — в `/api/tags` имя с тегом, в
   конфиге без).
2. Если `"tools"` в `capabilities` → **native**.
3. Иначе → **fallback**.
4. Модель не найдена / ошибка сети → **fallback** (безопасный
   дефолт: текстовый fallback работает с любой моделью, native — нет).
5. Результат кэшируется в `self._tools_supported: bool | None`.
   Повторные вызовы не дёргают API.

`chat()` без `tools` (пустой список или None) автодетект **не
запускает** — используется для финального ответа в ReActLoop и
внутренних вызовов памяти.

## 4. Fallback: механизм

### 4.1. Формат вызова

`<tool_call>...</tool_call>` с JSON внутри:

```
<tool_call>{"name": "web_search", "arguments": {"query": "test"}}</tool_call>
```

Почему `<tool_call>`: паттерн встречается в трейнах Llama, Mistral,
Gemma — модель интуитивно понимает формат. Плюс парсер ловит «голый»
JSON без тега — двойная страховка.

### 4.2. Парсер `_parse_tool_calls_from_text`

Приоритет:

1. Снимаем markdown-обёртку ` ```json ... ``` ` — она ломает
   сбалансированный скан.
2. Если есть блоки `<tool_call>` — работаем **только** с их
   содержимым (не тащим случайный JSON из рассуждений).
3. Если тегов нет — ищем «голые» сбалансированные `{...}` блоки
   (сканер по глубине вложенности).
4. `arguments` принимает как `dict` или как JSON-строку (модели
   вольны присылать и так, и так).
5. Битый JSON → пустой список (безопасный дефолт: ничего не
   выполняем).

### 4.3. Инструкция для system prompt `_build_tools_instruction`

Собирает markdown-текст:

- перечисляет инструменты (имя + description);
- для каждого — параметры с типами и маркером `(required)`;
- даёт пример формата `<tool_call>{...}</tool_call>`;
- явное указание: «если инструмент не нужен — ответь обычным
  текстом, без тегов».

`_inject_tools_instruction` вставляет этот текст в существующий
system message (или создаёт новый, если system нет). Возвращает
копию — исходный список не мутируется (ReActLoop держит его между
итерациями).

### 4.4. Синтез результата `_apply_fallback_to_result`

- Парсит `content` через `_parse_tool_calls_from_text`.
- Если вызовы найдены — кладёт их в `message.tool_calls` в формате
  Ollama (`[{"function": {"name": ..., "arguments": {...}}}]`).
- **Вырезает** теги `<tool_call>` из `content`: иначе ReActLoop
  положит их в историю, следующая итерация может «подсмотреть» и
  повторить.

## 5. P0-фикс: нормализация истории в fallback

### 5.1. Проблема

После первой итерации ReActLoop добавляет в messages:

- `{"role": "tool", "content": "..."}` — результат инструмента;
- `{"role": "assistant", "content": "...", "tool_calls": [...]}` —
  ответ LLM с вызовами.

Это native-конструкции. Ollama в fallback видит их и падает с
HTTP 400 `"Unable to generate parser for this template"` — потому
что пытается построить native-parser по TEMPLATE модели, которого
у gemma3n нет.

### 5.2. Решение `_normalize_history_for_fallback`

Применяется **только в fallback** перед отправкой в Ollama:

- `assistant.tool_calls` → `assistant` с `content`, содержащим
  `<tool_call>{...}</tool_call>` блоки;
- `role: "tool"` → `role: "user"` с префиксом
  `[Результат инструмента]: ` в content;
- обычные `system` / `user` / `assistant` без `tool_calls` —
  не трогаются.

Возвращает **новый список** (shallow copy верхнего уровня).
Исходные `messages` не мутируются — ReActLoop продолжает работать
с оригиналом, история не портится.

Native-режим **не затронут**: нормализация применяется только в
fallback-ветке `chat()`.

## 6. Таймауты x2 в internal_tools.py

Smoke-тест на Qwen3.5-4B показал `Similarity verdict timed out` на
10 секунд. Reasoning-модель не укладывалась. Все внутренние вызовы
LLM в `RememberFactTool` получили x2 таймаут:

| Метод | Было | Стало |
|---|---|---|
| `_expand_query` | 5.0s | 10.0s |
| `_similarity_verdict` | 10.0s | 20.0s |
| `_merge_facts` | 15.0s | 30.0s |

Это не архитектурное решение, а эмпирическая калибровка под
медленные reasoning-модели. Для быстрых моделей таймауты срабатывают
редко — расширение безопасно.

## 7. Вне ADR: инфраструктура

Между ADR-015 и ADR-016 также сделано (не архитектурные решения,
но зафиксировать стоит):

- Восстановлен `pyproject.toml` из истории (коммит `07edd02`).
  Актуализирован под текущее состояние проекта: только реальные
  runtime-зависимости (`aiohttp`, `bs4`, `structlog`, `snowballstemmer`,
  `PyQt6`, `qasync`, `openpyxl`, `PyPDF2`) и dev-группа (`pytest`,
  `pytest-asyncio`, `pytest-cov`, `black`, `ruff`, `pre-commit`).
- `uv.lock` пересобран: **48 пакетов вместо 87**, −1103 строк.
  Удалено 39 мёртвых пакетов: `streamlit` + вся свита (`altair`,
  `pydeck`, `pyarrow`, `protobuf`, `uvicorn`, `starlette`, `Jinja2`),
  `pandas`, `numpy`, `requests`, `psutil`, `GPUtil`, `watchdog`,
  `pyperclip`, `websockets` и др. Ни один не импортировался в коде.
- `[tool.pytest.ini_options]` перенесён в `pyproject.toml`, файл
  `pytest.ini` удалён.
- `requirements.txt` удалён — uv управляет всем.
- README обновлён: 319 → 376 тестов.
- Рабочие дампы `git_history_with_changes.txt` (3.66 МБ) и
  `test_results.txt` убраны из репозитория, добавлены в `.gitignore`.
- Закрыт открытый вопрос ADR-015 §10 (`pyproject.toml`).

## 8. Порядок внедрения (TDD)

### Шаг 1 (feat): JSON-fallback для tool calling

Тесты: `tests/test_json_tool_calls.py` (23 кейса).

Красный показал:
- 21 `AttributeError` на отсутствующие методы (`_parse_tool_calls_from_text`,
  `_build_tools_instruction`, `_detect_tools_support`);
- 3 честных падения на `chat()` — `tools` попадает в payload
  fallback-запроса, `tool_calls` не синтезируются, теги не вырезаются;
- 2 passed — native-путь не сломан.

### Шаг 2 (fix, P0): нормализация истории

Тесты: `tests/test_json_fallback_history.py` (8 кейсов).

Красный показал:
- 7 `AttributeError` на `_normalize_history_for_fallback`;
- 1 честное падение — `role: "tool"` попадает в payload fallback;
- 1 passed — native-путь не затронут.

**Итог: 407 passed.**

## 9. Осознанные отказы

**Патч TEMPLATE для gemma3n.** Отменено: capabilities определяются
runtime по family (`gemma3n`), а не по TEMPLATE. Обходной путь
через `Modelfile` не заработал бы.

**Гибрид Qwen+tools / Gemma+chat.** Отменено: сложность диспетчера
не оправдана. JSON-fallback решает задачу проще и универсальнее.

**Отказ от tools вообще** (только текстовые ответы). Отменено:
ломает всю ReAct-архитектуру, лишает агента инструментов.

## 10. Открытые вопросы

Выявлены в ходе smoke-теста на Gemma 3n. **Не решены**, требуют
ADR-017.

### 10.1. Путаница каналов recall_memory / remember_fact

`RememberFactTool._execute` для дедупа вызывает `recall_memory`.
Тот ищет во всех зонах, включая `dialogue_summary`. LLM видит
`found: true` (совпадение по общим словам «пользователь», «зовут»),
решает «уже знаю» и **не записывает факт**.

Результат: `user_fact` не создаётся, но LLM отчитывается
«запомнил». Это функциональная ошибка — воспроизведена вживую:

```
> Запомни, меня зовут Вася
Calling plugin args_preview="{'query': 'меня зовут', 'limit': 3}" tool=recall_memory
Tool succeeded result_preview="{'found': True, 'facts': [{'key': 'dialogue_summary:1788887763-5c27e8'...
Отлично, я запомнил, что тебя зовут Вася.
```

В памяти нет `user_fact:xxx` — только старые `dialogue_summary`
со словом «пользователь»/«зовут». **Ложное срабатывание дедупа.**

### 10.2. Автономные задачи не имеют канала памяти

Текущая архитектура:

| Канал | Пишется | Семантика |
|---|---|---|
| `user_fact:*` | По команде «запомни» | Явные факты о пользователе |
| `dialogue_summary:*` | Автоматически после 5 пар | Конспект диалога |
| `task_state:*` | **Отсутствует** | — |

Следствие: если пользователь ставит долгую задачу («напиши код
автономно, декомпозиция, горизонт 2 дня, чек-поинты»), агент
**не может запомнить её состояние**. Всё выпадет в
`dialogue_summary` как сырой конспект, без структуры. В следующем
запросе агент не помнит, что задача в процессе.

**Предложение (обсуждено, не реализовано):**
- новый канал `task_state:*` с семантикой «состояние активной задачи»;
- не остывает пока `status: in_progress`;
- отдельный инструмент `long_task` для явного планирования;
- автоизвлечение задач при компрессии диалога.

Требует ADR-017.

### 10.3. `dialogue_summary` в `recall_memory`

Открытый вопрос: должны ли `dialogue_summary` возвращаться из
`recall_memory` как «найденные факты»?

- **Аргумент за:** это единственный источник долгосрочного
  контекста. Для вопроса «о чём мы говорили 2 дня назад» — лучше
  саммари, чем ничего.
- **Аргумент против:** они дают ложные совпадения при дедупе
  (см. §10.1), потому что содержат общие слова.

Одно из решений: `recall_memory` может принимать параметр
`categories=["user_fact", "task_state"]` и исключать
`dialogue_summary` по умолчанию; отдельный инструмент для поиска
по саммари.

Не решено, требует ADR-017.

## История ревью:

Р1: TDD-цикл, 31 красный тест, две итерации (JSON-fallback, P0-фикс).
    Smoke-тест на Gemma 3n выявил открытые вопросы §10.
