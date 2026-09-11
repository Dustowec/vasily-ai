# ADR-014: Code Executor — песочница, тулзы записи/чтения, TDD-протокол, универсальная failure-policy

**Статус:** Accepted (draft)
**Дата:** 2026-09-11
**Автор:** Архитектор (User)
**Red team:** не проводился (ожидается)

---

1. Контекст

Пункты 3 и 5 манифеста «Vasily-AI» объединены: Code Executor (тулзы
записи/чтения кода) и Локальный Тест-Конвейер (запуск pytest) образуют
единый рабочий цикл. Без второго первый слеп; без первого второй
бесполезен.

Ключевое переосмысление семантики: **это не «Copilot»**. GitHub Copilot —
парный программист, подсказывает по ходу внутри проекта. Code Executor —
**автономный исполнитель задач в изолированной песочнице**. Читает
инструкцию, пишет код, гоняет тесты, отдаёт результат. Не имеет доступа
к проекту `vasily-ai` и не является самоулучшающимся.

---

2. Границы песочницы (физическая изоляция)

Принцип: **не блоклисты, а отсутствие доступа**. Агент физически не видит
то, до чего не должен дотянуться. Тот же принцип, что в SSRF: белый
список вместо чёрного.

| Путь | Чтение | Запись | Назначение |
|------|--------|--------|------------|
| `workspace/reading/` | ✅ | ❌ | Входные данные (pdf/xlsx/csv/txt/md), кладёт юзер |
| `workspace/wrote/` | ✅ | ✅ | Рабочая зона агента |
| `workspace/wrote/tasks/` | ✅ | ✅ | `task_N.md`, `task_N_done.md` |
| `workspace/wrote/tests/` | ✅ | ✅ | `test_step_N.py`, `test_full.py` |
| Всё вне `workspace/` | ❌ | ❌ | Недоступно |

Нормализация путей обязательна. Path traversal (`../`, симлинки,
абсолютные пути вне `workspace/`) блокируется на уровне каждой тулзы.

---

3. Тулзы


3.1 `view_file(path)`

- Читает **любой текстовый** файл в `workspace/`
- Текстовые расширения: `.py .md .txt .json .yaml .toml .csv`
- Бинарники (pdf/xlsx) **не читает** — сознательная потеря функционала
  `local_reader`
- Формат ответа: `{"status": "success", "path": str, "lines": list[str], "total_lines": int}`
- Лимит 1 МБ
- Path traversal → ошибка
- Failure policy: default (`per_tool / 3 / dush`)

3.2 `write_file(path, content)`

- Пишет **только** в `workspace/wrote/` (включая `tasks/`, `tests/`)
- Если файл существует — создаёт `.bak`, затем перезаписывает
- Возвращает: `{"status": "success", "path": str, "bytes": int, "backup": str|null}`
- Failure policy: default

3.3 `patch_file(path, search_text, replace_text)`

- Работает **только** в `workspace/wrote/`
- **0 совпадений** `search_text` → ошибка
- **1 совпадение** → замена
- **2+ совпадений** → ошибка «неоднозначно, уточни контекст»
- `.bak` перед изменением
- **Жёсткий блок:** `patch_file` для файлов, чьё имя начинается с `test_`
  (проверка в коде тулзы, не в промпте) → ошибка
- Failure policy: default

3.4 `run_terminal_command(command)`

- **Whitelist:** только `pytest`. Больше ничего.
- Валидация аргументов: отклоняются `;`, `&&`, `||`, `|`, `$()`, `` ` ``,
  `>`, `<`, `&`
- Рабочая директория (cwd): `workspace/wrote/`
- **Таймаут: 120 секунд** (жёсткий)
- Возвращает:
  ```python
  {
      "status": "success" | "failed",
      "exit_code": int,        # 0 = успех, ≠0 = провал, -1 = таймаут
      "stdout": str,
      "stderr": str,
      "duration_ms": int,
      "timed_out": bool,
  }

Failure policy: per_args / 5 / stop — ключевое отличие от остальных

4. Универсальная failure-policy

4.1 Атрибуты в BaseTool

Заменяет хардкод CONSECUTIVE_ERROR_DUSH_THRESHOLD = 3 из ReActLoop.
Правило живёт рядом с тулзой, а не в общей куче. ReActLoop не знает
ни про одну конкретную тулзу.

class BaseTool(ABC):
    failure_counting_mode: str = "per_tool"   # per_tool | per_args | disabled
    failure_threshold: int = 3
    failure_action: str = "dush"              # dush | stop

4.2 Семантика счётчиков

Mode	Ключ	Обнуляется
per_tool	имя тулзы	при успехе той же тулзы
per_args	имя + хеш аргументов	только при успехе той же команды с теми же аргументами; смена команды счётчик не трогает
disabled	—	тулза не участвует
Обоснование для per_args: обнуление при смене команды позволило бы
модели «обмануть» счётчик, дёрнув другую команду. Это маскировало бы
упорство, а не наказывало его. Только честный успех той же команды —
сигнал «проблема решена».

4.3 Семантика действий

dush — на пороге вставить user-сообщение в messages, цикл
продолжается. Используется для «подсказать модели сменить подход».

stop — на пороге прервать цикл, return ReActResult(status="failed", answer="...").
Используется, когда повторение бессмысленно и нужна помощь юзера.

4.4 Раскладка по тулзам

Тулза	Mode	Threshold	Action
web_scraper	default	3	dush
web_search	default	3	dush
view_file	default	3	dush
write_file	default	3	dush
patch_file	default	3	dush
run_terminal_command	per_args	5	stop

4.5 Реализация в ReActLoop

Удаляется жёсткий блок душ-порога. Вместо — единая функция:
_check_failure_policy(tool_name: str, args: dict, is_error: bool) -> Action
Функция читает атрибуты у самой тулзы через plugin_registry.get(tool_name).
Никакого знания о конкретных тулзах в ReActLoop.

5. Рабочий протокол Code Executor (для промпта)


Найти задачу: минимальный N среди workspace/wrote/tasks/task_N.md,
игнорируя *_done.md.

Прочитать план через view_file("wrote/tasks/task_N.md").

На каждый шаг:

write_file("tests/test_step_N.py", ...) — тест первым

write_file("step_N.py", ...) — код

run_terminal_command("pytest tests/test_step_N.py -v")

exit_code == 0 → следующий шаг

exit_code != 0 → править код (patch_file для step_N.py)

Тесты менять запрещено. patch_file физически откажет для test_*.py.

5 провалов одной команды подряд → счётчик per_args доходит до 5
→ stop → failed → юзеру: «Застрял на {test} — упал 5 раз, нужна помощь».

Финальный тест: write_file("tests/test_full.py", ...) — проверка
логики и связности всего файла целиком, не построчно.

Приёмка:

run_terminal_command("pytest -v") — прогон всех тестов в wrote/

Все зелёные → переименовать task_N.md → task_N_done.md

Красные → failed, юзеру: «Финальный тест не прошёл».

6. Удаления


plugins/local_reader/ удаляется целиком (тулза + тесты).
Функционал частично покрыт view_file (только текстовые форматы).
Pdf/xlsx/csv — теряем сознательно (были экспериментальными, не
использовались).

Причина: два канала доступа к файлам (local_reader + view_file)
расширяют поверхность атаки и создают семантическую путаницу у модели.
Один инструмент — одна семантика.

7. Реализация


7.1 Новые файлы
plugins/view_file/tool.py — ViewFileTool

plugins/write_file/tool.py — WriteFileTool

plugins/patch_file/tool.py — PatchFileTool

plugins/run_terminal_command/tool.py — RunTerminalCommandTool

Каждый — наследник BaseTool, регистрируется через __all__ в
plugins/<name>/__init__.py.

7.2 Изменения в core/base_tool.py

Добавить три атрибута класса: failure_counting_mode, failure_threshold,
failure_action. Значения по умолчанию сохраняют текущее поведение душ.

7.3 Изменения в core/react_loop.py

Удалить константу CONSECUTIVE_ERROR_DUSH_THRESHOLD.

Удалить текущий жёсткий блок душ-порога.

Добавить _check_failure_policy(tool_name, args, is_error).

Счётчики failures: dict[str, int] — одна переменная вместо
consecutive_errors.

Для действия stop — прерывание цикла с ReActResult(status="failed", ...).

7.4 Удаления

plugins/local_reader/ — целиком.

Старые тесты local_reader — целиком.

7.5 Промпт
В DEFAULT_SYSTEM_PROMPT (или отдельный code_executor prompt type)
добавить рабочий протокол §5. Точные формулировки — на этапе реализации.

8. Тесты
8.1 Unit на failure-policy
per_tool / 3 / dush: 1, 2 ошибки — душа нет; 3 — есть; 4, 5 — без
повтора.

per_tool / 3 / dush: ошибка × 2 → успех → ошибка × 2 = душа нет.

per_args / 5 / stop: 5 провалов одной команды → цикл прерван,
status="failed".

per_args / 5 / stop: 4 провала команды A + 4 провала команды B
(между собой) → стопа нет (разные ключи).

disabled: тулза не считается.

8.2 Unit на тулзы
view_file: успех, path traversal, несуществующий файл, слишком
большой файл.

write_file: успех, .bak, path traversal, путь вне wrote/.

patch_file: 0 совпадений → ошибка, 1 → замена, 2+ → ошибка,
test_*.py → жёсткий блок, .bak.

run_terminal_command: whitelist команд, отклонение ;, отклонение
pipe, таймаут 120с, cwd = wrote/, exit_code возвращается.

8.3 Интеграционные
Мок LLM проходит полный TDD-цикл: тест → код → прогон → зелёный →
следующий шаг → финальный тест → rename task_N.md в task_N_done.md.

Мок LLM пытается patch_file на test_step_1.py → ошибка, но цикл
продолжается.

Мок LLM падает на test_step_1.py 5 раз → stop, failed.

9. Открытые вопросы
Точная формулировка user-сообщения при stop. Черновик: «Стоп.
Команда {command} падает 5 раз подряд. Нужна помощь пользователя.»
Финализируется при реализации.

__init__.py в tests/ для pytest — добавим, если pytest не
найдёт тесты по относительному пути. Уточняется по факту.

Путь к промпту Code Executor — отдельный prompt_type в
GoldenPromptsLibrary или дополнение к default. Решается при
реализации.

10. История ревью
Реализация ещё не начата. Red team — ожидаем ответа ревьювера.
