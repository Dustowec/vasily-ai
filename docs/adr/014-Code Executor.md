ADR-014: Code Executor — песочница, тулзы, TDD-протокол, failure-policy
Статус: Accepted (draft accepted to implementation)
Дата: 2026-09-12
Суперсиде: ADR-012 (BaseTool, PluginRegistry), ADR-013 (Gradient Cascade Memory)
Автор: Архитектор (User)
Red team: 6 раундов. Раунды 1–3 — текст. Раунды 4–6 — PoC-эмпирика. Все P0/P1 закрыты. P2 — отложены осознанно в §11.

1. Контекст
Пункты 3 и 5 манифеста «Vasily-AI» объединены: Code Executor (тулзы записи/чтения кода) и Локальный Тест-Конвейер (запуск pytest) образуют единый рабочий цикл.

Семантическое уточнение. Это не «Copilot» в смысле GitHub (парный программист внутри проекта). Code Executor — автономный исполнитель задач в изолированной песочнице. Читает инструкцию, пишет код, гоняет тесты, отдаёт результат. Не имеет доступа к проекту vasily-ai.

Разграничение «sandbox». Термин означает две разные вещи:

Sandbox-1: тулзы агента. view_file, write_file, patch_file, run_terminal_command, finalize_task, list_files ограничены workspace/. Агент не может читать/писать вне через эти тулзы. Реализовано в §2, §2.1, §2.2.

Sandbox-2: процесс pytest. Процесс, запускаемый run_terminal_command, не изолирован от ОС. -I и -c защищают от shadowing модулей и подмены конфига, но не ограничивают доступ к файловой системе, сети, IPC, /proc, окружению родителя. Тест может open("/etc/passwd"), subprocess.run(["curl", ...]), читать /proc/self/environ. rlimits ограничивают ресурсы, но не доступ.

Настоящая изоляция процесса (namespaces, seccomp, bwrap/firejail/AppContainer) — вне scope ADR-014, требует ADR-016.

Урок шести раундов ревью. Спецификация должна говорить что истинно, не как реализовано. Псевдокод в спеке читается как обязательство и ломается на первом несогласии с реальностью (os.openat в Python — не существует). В редакции 7 псевдокод удалён, оставлены нормативные утверждения.

2. Границы песочницы
Принцип: не блоклисты, а отсутствие доступа. Агент физически не видит то, до чего не должен дотянуться.

Путь	Чтение	Запись	Назначение
workspace/reading/	✅	❌	Входные данные, кладёт юзер
workspace/wrote/	✅	✅	Рабочая зона агента
workspace/wrote/tasks/	✅	❌ (только юзер + finalize_task)	task_N.md, task_N_done.md
workspace/wrote/plans/	✅	✅	План от агента (plan_N.md)
workspace/wrote/tests/	✅	✅	test_step_N.py, test_full.py
Всё вне workspace/	❌	❌	Недоступно
Разделение tasks/ и plans/. Задачи от юзера лежат в tasks/ — агент их читает, но не пишет (исключение: finalize_task для переименования). Планы, которые агент генерирует сам, лежат в plans/. Это разделяет доверие: tasks/ — вход от юзера, plans/ — рабочие данные агента.

2.1. Резолв пути (И-1)
Для любого входного пути p и корня root (для view_file — workspace/, для остальных — workspace/wrote/, для finalize_task — workspace/wrote/tasks/):

Шаг 0. Абсолютные пути (os.path.isabs(p)) отвергаются до склейки.

Шаг 1. Null-byte ("\x00" in p) отвергается до склейки.

Шаг 2. Юникод-нормализация NFC.

Шаг 3. Разделители: на Windows \ приводится к /. На POSIX присутствие \ в пути → путь отвергается (path_forbidden). Обоснование: на POSIX \ — легитимный символ имени, но настолько редкий, что присутствие в пути от LLM с высокой вероятностью означает Windows-вектор.

Шаг 4. full = os.path.join(root, p).

Шаг 5. real = os.path.realpath(full), root_real = os.path.realpath(root).

Шаг 6. os.path.commonpath([real, root_real]) == root_real, иначе path_forbidden.

Применяется к файлам и директориям. Различия:

Нормализация, отвержение абсолютных, null-byte, \ на POSIX, NFC — одинаково.

Префикс-проверка (commonpath) — одинакова.

Открытие: для файла — os.open с флагами по назначению; для директории — обход через os.scandir от дескриптора, открытого с O_DIRECTORY | O_NOFOLLOW.

Симлинки: отвергаются на входе в директорию (как для файла).

Применяется к list_files, view_file (отвергает директории явно, §3.1), всем будущим тулзам с путями к директориям.

Защита от TOCTOU (нормативное утверждение).

На POSIX: файл открывается пошагово от заранее открытого дескриптора корня с O_NOFOLLOW на каждом компоненте. Реализация использует os.open(path, flags, dir_fd=...) с одним компонентом в path на каждом шаге. Компонент .. или . в path не допускается (ассерт перед вызовом). Флаги открытия зависят от тулзы: для чтения — O_RDONLY, для записи — O_WRONLY | O_CREAT | O_TRUNC.

На Windows: полная защита от TOCTOU на промежуточных компонентах недостижима средствами stdlib. Используется realpath + os.open без O_NOFOLLOW. Риск — §11.1.

Источник истины о различиях POSIX/Windows — §11.

2.2. Запрет имён (И-3)
Запрещённые имена (сравнение casefold, на любой глубине wrote/):

*_test.py

test.py (ровно это имя)

conftest.py

__init__.py

pytest.ini, pyproject.toml, setup.cfg, tox.ini

pytest, _pytest, py, pluggy, iniconfig, packaging, sitecustomize, usercustomize — и .py-файлы, и одноимённые директории

Top-level имена модулей — на любой глубине wrote/, не только в корне. Обоснование: -I закрывает shadowing через sys.path, но агент может запутаться, если увидит wrote/subdir/pytest.py в list_files. Запрет — простой, дешёвый, снижает когнитивную нагрузку.

test.py (ровно это имя, без суффикса) — запрещён. Конфликт со stdlib-пакетом test. Pytest его соберёт (test_*.py с * = пустая строка), поведение неопределённое. Явный запрет лучше явного разрешения с оговоркой.

__init__.py в wrote/ — запрещён. Обоснование: изменение семантики импорта pytest (pytest считает tests/ пакетом), classname в junit-xml становится другим, парсер конвертирует . в /, что даст неверный путь. Кроме того, __init__.py может содержать произвольный код, исполняемый при импорте тестов.

test_* — особый случай:

Создание разрешено, если файла нет.

Перезапись (write_file на существующий) запрещена.

Патч (patch_file) запрещён всегда.

Точка в имени test_* (кроме .py) запрещена — pytest отвергает такие имена (PoC: ModuleNotFoundError).

Проверка: до и после резолва, по realpath. Сравнение casefold, платформо-независимо. Симлинк, ведущий на запрещённое имя, отвергается.

Симметрично для write_file и patch_file.

Top-level имена — вторая линия. Список неполный и растёт с зависимостями pytest. Основная защита — -I (§3.4). PoC показал: осиротевший .pyc не импортируется, поэтому приоритет — средний.

3. Тулзы
3.1. view_file(path)
Читает любой текстовый файл в workspace/.

Текстовые расширения: .py .md .txt .json .yaml .toml .csv.

Бинарники (pdf/xlsx) — не читает. Сознательная потеря функционала local_reader.

Резолв по §2.1 с root = workspace/.

Директории отвергаются (is_directory).

Формат ответа:
{
    "status": "success",
    "path": str,
    "lines": list[str],
    "total_lines": int,
}

Лимит 1 МБ.

Failure policy: default (per_tool / 3 / dush).

3.2. write_file(path, content)
Пишет только в workspace/wrote/ (включая plans/, tests/). В tasks/ — запрещено (И-6).

Резолв по §2.1 с root = workspace/wrote/.

Запрет имён (И-3) — симметрично с patch_file, по realpath.

Если файл существует — .bak. Ротация: не более 3 последних .bak на файл; старые удаляются.

Возвращает:
{"status": "success", "path": str, "bytes": int, "backup": str | None}

3.3. patch_file(path, search_text, replace_text)
Только workspace/wrote/ (исключая tasks/).

Резолв по §2.1 с root = workspace/wrote/.

0 совпадений search_text → patch_not_found.

1 совпадение → замена.

2+ совпадений → patch_ambiguous.

Запрет имён (И-3) — симметрично с write_file, по realpath.

.bak с ротацией.

Возвращает:
{"status": "success", "path": str, "replaced": 1, "bytes_delta": int, "backup": str}

3.4. run_terminal_command(args: list[str])
Интерфейс — список аргументов. Строки команд нет. Никакого парсинга.

Первый элемент — pytest (отбрасывается). Args — без pytest. Tool сам формирует вызов. Если pytest передан — invalid_argument.

Структурный вызов:
[sys.executable, "-B", str(sandbox_runner), "-I", "-m", "pytest", *hardcoded, *agent_args]

Жёстко зашитые аргументы (все пути абсолютные, через os.path.realpath):
-I
-c <abs path to tool-owned empty ini вне wrote/>
--rootdir=<abs wrote/>
--confcutdir=<abs wrote/>
-p no:cacheprovider
--basetemp=<abs tempdir вне wrote/>
--junit-xml=<abs tempdir>/results.xml

Обоснование каждого:

-B — don't write bytecode. Предотвращает создание core/__pycache__/_sandbox_runner.cpython-314.pyc (Н-1 раунда 6). Без -B runner оставляет байткод в core/, вне cleanup.

-I — флаг Python, убирает cwd и PYTHONPATH из sys.path[0]. Закрывает P0-1 (shadowing pytest.py, _pytest.py, sitecustomize.py). Подтверждено PoC.

-c <temp-ini> — единственный способ отключить discovery ini-файлов. Закрывает P0-2. Подтверждено PoC: --rootdir и --confcutdir не отключают discovery, родительский pytest.ini читается.

--junit-xml — закрывает P0-3 (парсинг).

--rootdir=<abs> — абсолютный путь. Раньше был относительный, резолвился в wrote/wrote/. Закрывает P1-1.

--confcutdir=<abs> — блокирует загрузку conftest.py выше wrote/.

-p no:cacheprovider — встроенный плагин, автозагрузка его не выключает.

Env сабпроцесса (per-platform PATH):
def _build_safe_env(wrote_dir: str) -> dict[str, str]:
    if os.name == "nt":
        path = r"C:\Windows\System32;C:\Windows"
    else:
        path = os.defpath
    return {
        "PATH": path,
        "PYTHONPATH": "",
        "HOME": wrote_dir,
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }

Всё остальное вычищается. PYTHONSTARTUP, PYTHONINSPECT, PYTHONWARNINGS, PYTEST_ADDOPTS из родителя — не наследуются.

Оговорка: PATH = os.defpath (POSIX) и C:\Windows\... (Windows) не гарантирует доступность бинарей вне стандартных путей (Homebrew на macOS, /usr/local/bin). Тесты, требующие внешних инструментов, должны использовать абсолютные пути. Это компромисс между изоляцией и работоспособностью (§11.9).

Белый список флагов агента:
-v, -q, -x, -s, --tb=short, --tb=long, --tb=line, --tb=no,
--disable-warnings

Флаги с аргументом: -k <expr>, -m <expr>, --maxfail=N.

<expr> для -k/-m: без --, без /, без .., без \, длина ≤ 200.

N для --maxfail: целое, 1 ≤ N ≤ 100. Иначе invalid_argument до запуска.

Запрещённые флаги (defense in depth):
-p, --pyargs, --rootdir, --basetemp, --confcutdir, -c,
--import-mode, -o, --override-ini, --junitxml, --html,
--log-file, --cov-report, --capture=fd, --capture=sys

Запрещённые формы позиционных аргументов:

Начинается с / или C:\ → ошибка.

Содержит компонент .. (/../, \..\, равен .., начинается с ../) → ошибка.

Содержит : кроме Windows drive (C:) → ошибка (ADS на Windows).

. как часть имени — разрешено.

rlimits:

Через core/_sandbox_runner.py — вспомогательный скрипт, устанавливающий RLIMIT_AS=512MB, RLIMIT_CPU=60s, RLIMIT_NPROC=64, RLIMIT_FSIZE=64MB, затем os.execv(sys.executable, [...pytest args...]). Избегаем preexec_fn (небезопасен в многопоточном asyncio).

Конфигурируемо через Config: sandbox_memory_limit_mb, sandbox_cpu_limit_sec.

Оговорки (см. §11.8):

RLIMIT_AS — виртуальная память, не RSS. Python + numpy легко перепрыгнут 512 МБ.

RLIMIT_CPU=60s — процессорное время, не wall-time.

RLIMIT_NPROC — лимит на real user ID, а не на процесс. Если у юзера уже 50 процессов, сабпроцесс создаст 14, не 64. Частичная защита.

Тяжёлые тесты (память > 512 МБ, CPU > 60с) не поддерживаются by design (§11.8).

Таймаут: 120 секунд wall-time.

Process group kill:
except asyncio.TimeoutError:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    await proc.wait()

Оба вызова — getpgid и killpg — обёрнуты. getpgid бросает ProcessLookupError первым, если процесс уже умер.

Cleanup __pycache__ и .pytest_cache — родительским процессом:

До create_subprocess_exec: удалить wrote/**/__pycache__/, wrote/.pytest_cache/.

После прогона: то же.

Не _sandbox_runner.py — он стартует уже внутри cwd = wrote/, и к этому моменту Python может создать __pycache__ для самого runner'а.

Порядок:

Cleanup wrote/**/__pycache__/, wrote/.pytest_cache/ — родителем.

Запуск pytest.

Парсинг XML.

Cleanup tempdir (finally).

Cleanup wrote/**/__pycache__/, wrote/.pytest_cache/ — родителем, после прогона.

Один tempdir. И --basetemp, и --junit-xml указывают в один tempfile.mkdtemp(prefix="pytest-"). Не два.

Порядок cleanup tempdir:

Запуск pytest с --junit-xml=<tempdir>/results.xml.

Ожидание (wait_for, таймаут 120с).

Чтение XML (если timed_out=False и collection_error=False).

Парсинг XML в failed_tests / passed_tests.

finally: shutil.rmtree(tempdir, ignore_errors=True).

rmtree — после парсинга. При таймауте или collection_error — XML не читается, rmtree всё равно срабатывает. При исключении парсинга (ElementTree.ParseError) — finally тоже срабатывает.

Санитизация stdout/stderr:

Вырезаются:

\x00–\x08, \x0b–\x1f, \x7f

\u200B–\u200F (ZWSP, LRM, RLM)

\u202A–\u202E (bidi embedding/override)

\u2066–\u2069 (bidi isolates)

\uFEFF (BOM)

Кроме \n и \t. Capped at 64 КБ.

Ответ:
{
    "status": "success" | "failed",
    "exit_code": int,
    "stdout": str,
    "stderr": str,
    "duration_ms": int,
    "timed_out": bool,
    "failed_tests": list[str],
    "passed_tests": list[str],
    "collection_error": bool,
    "internal_error": bool,
    "interrupted": bool,
}

Failure policy: per_test / 5 / stop.

4. Failure-policy
4.1. Атрибуты BaseTool
class BaseTool(ABC):
    failure_counting_mode: str = "per_tool"   # per_tool | per_args | per_test | disabled
    failure_threshold: int = 3
    failure_action: str = "dush"              # dush | stop

4.2. Семантика счётчиков
Mode	Ключ	Обнуляется
per_tool	имя тулзы	при успехе той же тулзы
per_args	имя + нормализованный хеш args	при успехе той же команды
per_test	ID теста	при успехе того же теста
disabled	—	не участвует
Нормализация per_args — best effort. strip, re.sub(r"\s+", " ", s), рекурсивно для списков/словарей. Не гарантирует семантическую эквивалентность. pytest -k "foo" и pytest -k 'foo' — разные ключи. Ослабление осознанное (§11.7).

per_test для run_terminal_command:

Ключ — ID теста из failed_tests.

pytest -v с 5 разными упавшими тестами → 5 счётчиков по 1, стопа нет.

Один тест падает 5 раз → счётчик 5, стоп.

Провал без failed_tests (ImportError до коллекции, pytest не стартовал): ключ <command>:collection_error с инкрементом на 1. Синтетический ключ, не зависит от косметики команды.

Разделение синтетических ключей:

<command>:collection_error — pytest не собрал тесты (exit 3, 4, 5, или errors > 0).

<command>:internal_error — exit code вне диапазона 0–5 (SIGSEGV, SIGKILL, нестандартное значение).

Обоснование: падение интерпретатора и отсутствие тестов — разные проблемы, счётчики не смешиваются.

Успех команды (exit_code == 0) обнуляет счётчики для тестов в passed_tests. Тесты, не запускавшиеся в этом прогоне (-k фильтр, -x ранний выход), не сбрасываются.

4.3. Парсинг junit-xml
Парсинг через xml.etree.ElementTree:
collection_error = False
internal_error = False
interrupted = False

if exit_code == 2:
    collection_error = True
    interrupted = True
elif exit_code in (3, 4, 5):
    collection_error = True
elif exit_code < 0 or exit_code > 5:
    internal_error = True
    collection_error = True

if not timed_out and not collection_error and not internal_error:
    for ts in root.findall("testsuite"):
        if int(ts.get("errors", "0")) > 0:
            # Обрыв парсинга: очищаем частично набранные списки
            collection_error = True
            failed_tests.clear()
            passed_tests.clear()
            break
        for tc in ts.findall("testcase"):
            classname = tc.get("classname", "")
            name = tc.get("name", "")
            if not classname:
                continue
            path = classname.replace(".", "/") + ".py"
            if not (abs_wrote / path).exists():
                continue
            test_id = f"{path}::{name}"
            if tc.find("failure") is not None or tc.find("error") is not None:
                failed_tests.append(test_id)
            elif tc.find("skipped") is None:
                passed_tests.append(test_id)

Правила:

Статус — в дочерних элементах (<failure>, <error>, <skipped>), не в атрибуте @status. PoC подтвердил.

classname == "" → пропускать.

При errors > 0 в <testsuite> — парсинг прерывается немедленно: collection_error = True, частичные списки очищаются, синтетический ключ <command>:collection_error инкрементируется. XML-остаток не парсится. Обоснование: errors > 0 означает, что pytest не смог собрать тесты. Продолжать парсинг — смешивать семантики.

При timed_out=True — XML не парсить.

При collection_error=True — XML не парсить.

При internal_error=True — XML не парсить.

classname конвертируется в путь заменой . на / + .py. Если файла нет — пропускать.

XFAIL / XPASS: не поддерживаются. Тесты с @pytest.mark.xfail / @pytest.mark.skip могут дать некорректные счётчики. Рекомендация: агент не генерирует такие тесты (§11.14).

Таблица exit codes:

exit_code	Значение	collection_error	internal_error	interrupted
0	success	False	False	False
1	tests failed	False	False	False
2	interrupted	True	False	True
3	internal error	True	False	False
4	usage error	True	False	False
5	no tests collected	True	False	False
вне 0–5	internal	True	True	False
-1	(наш timeout)	False	False	False (обрабатывается timed_out)
4.4. Действия
dush — user-сообщение, цикл продолжается. Лимит max_dush_count=3, длина max_dush_message_len=2048 символов.

stop — цикл прерывается, ReActResult(status="failed", answer="...").

4.5. Определение is_error (И-5)
Ошибка, если status in {"error", "failed"} или "error" in result верхнего уровня, или выброшено исключение в _execute.

4.6. Раскладка по тулзам
Тулза	Mode	Threshold	Action
web_scraper	default	3	dush
web_search	default	3	dush
view_file	default	3	dush
write_file	default	3	dush
patch_file	default	3	dush
finalize_task	default	3	dush
run_terminal_command	per_test	5	stop
4.7. Новая тулза finalize_task(N: int)
Единственная тулза с записью в wrote/tasks/. Исключение из И-6.

Только os.rename. Никакого write_file, patch_file.

Regex: ^task_([1-9]\d*)\.md$. Ведущие нули запрещены. task_0.md запрещён.

N — целое, N ≥ 1. Иначе invalid_argument.

Резолв по §2.1 с root = workspace/wrote/tasks/. Проверка на симлинки обязательна.

Если task_N_done.md существует — already_finalized, is_error=False.

Если ни task_N.md, ни task_N_done.md не существует → not_found, is_error=True.

.bak не создаётся.

И-7 для неё не применим формально (path конструируется внутри), но резолв через §2.1 обязателен.

Failure policy: per_tool / 3 / dush.

4.8. Состояние при stop
messages — не сбрасываются, доступны юзеру через result.steps.

task_N.md — не переименовывается (задача не решена).

.bak-файлы — остаются.

plans/plan_N.md — остаётся как черновик.

Последний stdout/stderr от run_terminal_command — в answer в читаемом виде.

5. Рабочий протокол Code Executor
Discovery задачи: list_files(path="wrote/tasks/") — расширение существующей list_files. Возвращает список. Агент фильтрует regex ^task_(\d+)\.md$, игнорирует *_done.md, минимальный N — числовое сравнение (не лексическое).

Прочитать план: view_file("wrote/tasks/task_N.md") — полный путь от workspace/.

При необходимости — write_file("wrote/plans/plan_N.md", ...) с разбивкой на шаги.

На каждый шаг:

write_file("wrote/tests/test_step_N.py", ...) — тест первым

write_file("wrote/step_N.py", ...) — код

run_terminal_command(args=["tests/test_step_N.py", "-v"]) — args как список, БЕЗ pytest, путь относителен wrote/

exit_code == 0 → следующий шаг

exit_code != 0 → patch_file для кода

Тесты менять запрещено. write_file и patch_file симметрично блокируют.

5 провалов одного теста → per_test счётчик = 5 → stop → failed → юзеру: «Застрял на {test_id} — упал 5 раз, нужна помощь». Последний stdout приложен.

Финальный тест: write_file("wrote/tests/test_full.py", ...) — проверка логики и связности всего файла целиком.

Приёмка: run_terminal_command(args=["-v"]) — все тесты в wrote/.

Все зелёные → finalize_task(N).

Красные → failed, юзеру: «Финальный тест не прошёл».

6. Удаления
plugins/local_reader/ — целиком. Функционал частично покрыт view_file (только текстовые форматы). Pdf/xlsx/csv — теряются сознательно.

Причина: два канала доступа к файлам расширяют поверхность атаки и создают семантическую путаницу у модели.

7. Реализация
7.1. Новые файлы
plugins/view_file/tool.py

plugins/write_file/tool.py

plugins/patch_file/tool.py

plugins/run_terminal_command/tool.py

plugins/finalize_task/tool.py

core/_sandbox_runner.py

Каждый — наследник BaseTool, регистрируется через __all__ в plugins/<name>/__init__.py.

7.2. core/base_tool.py
4 атрибута: failure_counting_mode, failure_threshold, failure_action.

Метод _check_path_allowed(path, mode) -> Path — И-1, И-3.

Утилита _resolve_and_validate_path(root, p) -> Path.

7.3. core/react_loop.py
Удалить CONSECUTIVE_ERROR_DUSH_THRESHOLD.

Заменить жёсткий блок на _check_failure_policy(tool_name, args, result) -> Action.

Счётчики failures: dict[str, int], dush_count: int.

Обработка stop, max_dush_count, max_dush_message_len.

7.4. core/internal_tools.py
Расширение list_files на wrote/tasks/, wrote/plans/, wrote/tests/.

7.5. Удаления
plugins/local_reader/ целиком.

Тесты local_reader целиком.

7.6. Конфигурация (Config)
max_dush_count (default 3)

max_dush_message_len (default 2048)

sandbox_memory_limit_mb (default 512)

sandbox_cpu_limit_sec (default 60)

8. Тесты
8.1. Path traversal (И-1)
Векторы для view_file / write_file / patch_file: ../, ..\\, ..／ (fullwidth), абсолютный, C:\, \\?\, ADS, null-byte, симлинк наружу, NFD/NFC.

На POSIX дополнительно: симлинк на промежуточном компоненте (wrote/a → /etc, обращение к a/passwd). \ на POSIX отвергается.

Для list_files: test_directory_traversal_*, test_scandir_symlink_escape_*.

Ожидание: status="error", error_type="path_forbidden".

8.2. Запрет имён (И-3)
Симметрично для write_file и patch_file, включая случай симлинка:

test_foo.py, TEST_foo.py, foo_test.py

test.py (ровно это имя)

conftest.py, Conftest.py

__init__.py (на любой глубине)

pytest.ini, pyproject.toml, setup.cfg, tox.ini

top-level модули (pytest.py, _pytest/ и т.д.) на любой глубине

wrote/foo.py → wrote/tests/test_step_1.py (симлинк)

Особые:

test_test_star_create_allowed_* — создание test_foo.py разрешено, если файла нет

test_test_star_overwrite_forbidden_* — write_file на существующий test_foo.py отвергается

test_test_star_dot_in_name_forbidden_* — test.foo.py (кроме .py) отвергается

8.3. run_terminal_command (И-2)
Разрешённые флаги: проходят.

Запрещённые: отвергаются до запуска.

--confcutdir=wrote/ + --rootdir=wrote/ + -c <temp-ini> — жёстко зашиты.

test_pytest_py_shadowing_* — положить wrote/pytest.py, убедиться что не перехватывает (PoC: -I).

test_pytest_ini_not_read_* — положить pytest.ini в workspace/ и wrote/, проверить configfile: (PoC).

test_rootdir_absolute_* — wrote/wrote/ не появляется.

test_sandbox_runner_no_bytecode_* — после запуска core/__pycache__/_sandbox_runner*.pyc не существует.

test_env_not_inherited_* — os.environ["PATH"] = "/tmp/evil" в родителе → сабпроцесс не наследует.

test_timeout_kills_group_* — дочерний процесс pytest отсутствует.

test_fork_bomb_limited_* — падает по RLIMIT_NPROC.

test_exit_code_sigsegv_* — internal_error=True, collection_error=True.

test_exit_code_sigkill_* — то же.

test_maxfail_validation_* — --maxfail=abc, -1, 0, 101 → invalid_argument.

test_sanitization_* — \u202E, \u200B, \uFEFF вырезаны.

test_tempdir_cleanup_* — после прогона tempdir не существует. При TimeoutError — тоже.

8.4. Парсинг junit-xml (И-7)
test_junit_xml_parsing_* — 1 passed, 1 failed → правильные списки.

test_classname_conversion_* — tests.unit.test_deep → tests/unit/test_deep.py.

test_skip_missing_file_* — classname не соответствует файлу → пропускается.

test_errors_signal_collection_error_* — <testsuite errors="1"> → collection_error=True, списки очищены.

test_collection_error_clears_lists_* — частично набранные списки очищаются.

test_xfail_not_counted_* — xfail не влияет на счётчики.

8.5. Failure-policy
per_tool: 1, 2 ошибки — нет душа; 3 — есть; 4, 5 — без повтора.

per_tool: ошибка × 2 → успех → ошибка × 2 = нет душа.

per_test: 5 провалов одного теста → failed.

per_test: 5 разных тестов по 1 провалу → нет стопа.

per_args нормализация: pytest t1.py -v и pytest t1.py -v — один ключ. Best effort.

disabled: тулза не считается.

is_error: все 4 случая считаются ошибкой; success — не считается.

max_dush_count: 3 душа → 4-й = stop.

max_dush_message_len: dush > 2048 символов отвергается.

8.6. finalize_task
test_finalize_leading_zeros_* — task_001.md отвергается.

test_finalize_not_found_* — not_found, is_error=True.

test_finalize_already_* — already_finalized, is_error=False.

test_finalize_symlink_* — симлинк отвергается.

8.7. И-7 — единая точка проверки пути
test_enumeration_path_check_* — параметризованный тест по всем тулзам ADR-014 с параметром path: подать "../../etc/passwd", ожидать path_forbidden.

test_finalize_path_constructed_internally_* — finalize_task резолвит путь через §2.1, _check_path_allowed не вызывается.

8.8. Интеграционные
Полный TDD-цикл с мок-LLM: тест → код → прогон → зелёный → следующий → финальный → finalize_task.

Мок-LLM пытается patch_file на test_step_1.py → name_forbidden.

Мок-LLM пытается write_file на существующий test_step_1.py → name_forbidden.

Мок-LLM пытается write_file на симлинк, ведущий в tests/test_*.py → name_forbidden.

Мок-LLM падает на одном тесте 5 раз → stop, failed, answer содержит test_id и stdout.

Мок-LLM пытается write_file в tasks/ → path_forbidden.

9. Открытые вопросы
Формулировки user-сообщений (dush, stop).

Белый список флагов pytest — расширяется по мере использования.

__init__.py в tests/ — запрещён (решено в §2.2).

Путь к промпту Code Executor — отдельный prompt_type или дополнение к default.

10. Инварианты безопасности
И-1 (path traversal). POSIX: пошаговое открытие от dirfd корня, O_NOFOLLOW на каждом компоненте, ../. в компоненте отвергается. Windows: realpath + open. TOCTOU на Windows — §11.1. Применяется к файлам и директориям (§2.1). Абсолютные пути и null-byte отвергаются до склейки.

Тесты: test_path_traversal_*, test_symlink_escape_*, test_absolute_path_*, test_intermediate_symlink_* (POSIX), test_directory_traversal_*, test_scandir_symlink_escape_*.

И-2 (изоляция команды). run_terminal_command исполняет только [sys.executable, "-B", str(sandbox_runner), "-I", "-m", "pytest", *hardcoded, *agent_args]. Аргументы с абсолютными путями и ..-компонентами отвергаются до запуска. -c, --rootdir, --confcutdir, -p no:cacheprovider, --basetemp, --junit-xml — зашиты жёстко. Env — фиксированный словарь, per-platform PATH, не наследуется. Таймаут 120с + process group kill. rlimits через _sandbox_runner.py. Тяжёлые тесты не поддерживаются by design.

Тесты: test_run_terminal_*, test_pytest_py_shadowing_*, test_pytest_ini_not_read_*, test_rootdir_absolute_*, test_env_not_inherited_*, test_timeout_kills_group_*, test_fork_bomb_limited_*, test_sandbox_runner_no_bytecode_*.

И-3 (запрет имён). Запрещённые имена (casefold, на любой глубине wrote/) никогда не создаются и не изменяются. test_* — создание разрешено, если файла нет; перезапись и патч запрещены. Точка в имени test_* (кроме .py) запрещена. Top-level имена модулей — на любой глубине. __init__.py — запрещён. Проверка до и после резолва, по realpath. Симлинк, ведущий на запрещённое имя, отвергается. Симметрично для write_file и patch_file.

Тесты: test_write_forbidden_*, test_patch_forbidden_*, test_name_forbidden_after_resolve_*, test_symlink_to_forbidden_name_*, test_test_star_create_allowed_*, test_test_star_overwrite_forbidden_*, test_test_star_dot_in_name_forbidden_*.

И-4 (лимиты душей). Суммарное число dush за цикл ≤ max_dush_count (default 3). Длина каждого dush-сообщения ≤ max_dush_message_len (default 2048 символов). На превышении — принудительный stop.

Тесты: test_dush_limit_*, test_dush_message_len_*.

И-5 (определение ошибки). §4.5.

Тесты: test_is_error_*.

И-6 (разделение tasks/ и plans/). tasks/ — запись только юзером и finalize_task. write_file и patch_file в tasks/ возвращают path_forbidden. plans/ — запись агентом и юзером.

Тесты: test_write_to_tasks_forbidden_*.

И-7 (единая точка проверки пути). Любая тулза, принимающая или конструирующая путь, проходит через резолв §2.1. Для тулз, принимающих путь от агента (view_file, write_file, patch_file, list_files), дополнительно вызывается BaseTool._check_path_allowed. Для тулз, конструирующих путь внутри (finalize_task), — только резолв §2.1, без _check_path_allowed.

Scope: только тулзы, зарегистрированные в ADR-014. web_scraper, web_search — вне scope И-7, у них своя SSRF-валидация.

Тесты: test_enumeration_path_check_*, test_finalize_path_constructed_internally_*.

И-8 (парсинг junit-xml). Статус теста — в дочерних элементах (<failure>, <error>, <skipped>), не в атрибуте @status. При errors > 0 парсинг прерывается, списки очищаются. XML не парсится при timed_out, collection_error, internal_error.

Тесты: test_junit_xml_parsing_*, test_classname_conversion_*, test_errors_signal_collection_error_*, test_collection_error_clears_lists_*.

11. Known limitations (единственный источник истины)
§11.1 TOCTOU на Windows. Стандартная библиотека не даёт openat/O_NOFOLLOW. Риск между realpath и open на промежуточных компонентах. Для локального агента в одном процессе — низкий. Принято осознанно.

§11.2 Промпт-инъекция через содержимое файлов. reading/ (вход от юзера) и plans/ (генерируется агентом, но текст может содержать инструкции). Граница «данные vs инструкции» — не операционализирована. ADR-015.

§11.3 Промпт-инъекция через stdout pytest. Control-символы вырезаются, но текст остаётся. ADR-015.

§11.4 Промпт-инъекция через имена файлов. И-3 блокирует известный набор, но Ignore previous instructions.txt проходит. ADR-015.

§11.5 Сеть процесса pytest. Не блокируется. ADR-015.

§11.6 Ротация .bak. 3 последних на файл, история теряется. ADR-015.

§11.7 per_args — best effort. Косметические вариации команды обнуляют счётчик. Для run_terminal_command заменено на per_test. Для остальных тулз per_args не используется.

§11.8 rlimits — частичная защита. RLIMIT_NPROC зависит от загрузки юзера. Не поддерживаются by design:

Тесты с ML-инференсом (numpy, torch, transformers) — превысят 512 МБ виртуальной памяти.

Тесты компиляции (Cython, numba JIT) — превысят 60с CPU.

Pandas на больших датасетах — превысят RLIMIT_AS.

rlimits конфигурируемы в Config (sandbox_memory_limit, sandbox_cpu_limit).

§11.9 PATH = os.defpath. Бинари вне стандартных путей (Homebrew на macOS, /usr/local/bin на Linux) недоступны. Тесты с внешними инструментами — на свой риск.

§11.10 ADS (file.txt:stream). На Windows realpath не отрезает :stream. Решение — отвергать : в имени файла, кроме позиции X: в начале (Windows drive). На POSIX : — легитимный символ, но отвергается для консистентности.

§11.11 Arbitrary code execution процесса. Процесс, запускаемый через run_terminal_command, исполняет произвольный Python-код. -I и -c защищают от shadowing модулей и подмены конфига pytest, но не ограничивают доступ к файловой системе, сети, IPC, /proc, окружению родителя. rlimits — про ресурсы, не про доступ. Настоящая изоляция (namespaces, seccomp, bwrap/firejail/AppContainer) — ADR-016.

§11.12 exit_code=2 (interrupted). В таблице отнесён к collection_error=True + interrupted=True. XML не парсится. В текущей конфигурации практически не наблюдается (killpg даёт -9, Ctrl+C не доходит). Правило — на случай будущих изменений сигналов.

§11.13 Top-level имена модулей в И-3. Неполный список, растёт с зависимостями pytest. Основная защита — -I. PoC: осиротевший .pyc не импортируется, угроза низкая.

§11.14 XFAIL / XPASS. Не поддерживаются. Тесты с @pytest.mark.xfail / @pytest.mark.skip могут дать некорректные счётчики per_test. Рекомендация: агент не генерирует такие тесты.

§11.15 TOCTOU на os.rename в finalize_task. Между проверкой task_N_done.md и os.rename — окно. Для локального агента в одном процессе риск низкий. Принято осознанно.

§11.16 list_files обход директорий и симлинки. os.scandir с O_NOFOLLOW на входе — защита от симлинков на саму директорию. Симлинки внутри директории не проверяются — list_files их покажет. Обоснование: список имён не даёт доступа к содержимому, доступ даёт view_file. Осознанное решение.

§11.17 Конкурентные run_terminal_command. Cleanup __pycache__, tempdir и .pytest_cache предполагает один активный subprocess. Если ReAct-цикл станет многопоточным — race condition. Сейчас не актуально.

12. История ревью
Раунд 1 (2026-09-12, текст): 8 групп находок. P0-1 (path traversal не операционализирован), P0-2 (run_terminal_command whitelist по символам ломается об pytest), P0-3 (patch_file запрет test_* обходится), P0-4 (view_file симлинки/TOCTOU), P1-1…P1-7.

Раунд 2 (текст): мета о ложных гарантиях. И-1, И-2, И-3 переформулированы.

Раунд 3 (текст): A-1 (И-1 ложная гарантия), A-2 (И-3 асимметричен), A-3 (--confcutdir не зашит), A-4 (--basetemp внутри wrote/), B-2 (per_args ловушка), C-1 (кто пишет task_N.md), D-1 (PATH наследуется).

Раунд 4 (PoC): P0-1 shadowing модулей (подтверждён PoC, закрыт -I), P0-2 родительский pytest.ini (подтверждён PoC дважды, рецидив раунда 3, закрыт -c <temp-ini>), P0-3 парсинг passed_tests (подтверждён PoC, закрыт junit-xml), P0-4 нет discovery (list_files). P1-1…P1-8. М-1 (sandbox разграничен).

Раунд 5 (PoC + diff): P0-A парсер XML (статус в дочерних элементах, подтверждён PoC), P0-C И-3 vs §5 (конфликт test_*, исправлен вариант A), Н-6 top-level имена (пропущен в раунде 5, применён), PoC про __pycache__ (осиротевший .pyc не импортируется).

Раунд 6 (diff + PoC): P0-1 collection_error внутри цикла (семантика обрыва), Н-1 _sandbox_runner.py байткод в core/ (закрыт -B). P1: __init__.py, top-level глубина, cleanup родителем, exit codes вне 0–5, XFAIL/XPASS, N leading zeros, not_found, И-7 шире, list_files директории.

12.1. Ревью-чеклист (редакция 7, закрытие раунда 6)
ID	Severity	Источник	Статус	Ссылка
Diff3-1 (обрыв парсинга XML)	P0	раунд 6	applied	§4.3
Н-1 (__pycache__ runner)	P0	раунд 6	applied	§3.4
Diff1-1 (__init__.py в И-3)	P1	раунд 6	applied	§2.2
Diff1-2 (test.py)	P1	раунд 6	applied	§2.2
Diff1-3 (top-level глубина)	P1	раунд 6	applied	§2.2
Diff2-2 (cleanup родителем)	P1	раунд 6	applied	§3.4
Diff2-3 (конкурентные)	P1	раунд 6	deferred	§11.17
Diff3-2 (exit codes вне 0–5)	P1	раунд 6	applied	§4.2
Diff3-3 (XFAIL/XPASS)	P1	раунд 6	deferred	§11.14
Diff4-1 (N leading zeros)	P1	раунд 6	applied	§4.7
Diff4-2 (not_found)	P1	раунд 6	applied	§4.7
Н-2 (И-7 шире)	P1	раунд 6	applied	§10
Н-3 (директории в §2.1)	P1	раунд 6	applied	§2.1
Diff4-3 (TOCTOU rename)	P2	раунд 6	deferred	§11.15
Diff5-1 (url в И-7)	P2	раунд 6	applied	§10
Н-4 (rglob симлинки)	P2	раунд 6	deferred	§11.16
П-1 (§12 не чеклист)	процессное	раунд 6	applied	§12.1
П-2 (LLM-кодер не определён)	процессное	раунд 6	applied	docs/review-process.md
П-3 (пример из правила 7)	процессное	раунд 6	applied	docs/review-process.md
П-4 (PoC-артефакты)	процессное	раунд 6	applied	docs/review-process.md
13. Ссылки

ADR-015 (планируется): Границы доверия, промпт-инъекции, сеть

ADR-016 (планируется): Изоляция процесса (namespaces, seccomp, bwrap)

docs/review-process.md

docs/poc/ — артефакты эмпирических проверок

Манифест «Vasily-AI», пункты 3 и 5
