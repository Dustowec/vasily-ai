import io
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.parse
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# =================================================================
# === ПРИНУДИТЕЛЬНАЯ УСТАНОВКА UTF-8 (только если есть консоль) ===
# =================================================================
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# =================================================================
# === ЛОГИРОВАНИЕ ===
# =================================================================
DEBUG_LOG = "debug_log.txt"

try:
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"=== ЗАПУСК АГЕНТА {datetime.now().strftime('%d.%m.%Y %H:%M:%S')} ===\n")
        f.write(f"Платформа: {sys.platform}\n")
        f.write(f"Python: {sys.version}\n")
        f.write(
            f"Кодировка stdout: {sys.stdout.encoding if hasattr(sys.stdout, 'encoding') else 'unknown'}\n"
        )
        f.write(f"{'='*60}\n")
except Exception as e:
    print(f"НЕ УДАЛОСЬ СОЗДАТЬ ЛОГ-ФАЙЛ: {e}")
    input("Нажмите Enter для выхода...")
    sys.exit(1)


def log_error(error_msg, context=""):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    line = f"{now} - ошибка: [{error_msg}]"
    if context:
        line += f" (контекст: {context})"
    print(f"[ЛОГ] {line}")
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        sys.stderr.write(f"НЕ УДАЛОСЬ ЗАПИСАТЬ В ЛОГ: {e}\n")


def log_info(msg):
    now = datetime.now().strftime("%H:%M:%S")
    line = f"[{now}] {msg}"
    print(line)
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass


# =================================================================
# === ОБРАБОТЧИК ОШИБОК С ОЖИДАНИЕМ ENTER ===
# =================================================================
def thread_error_handler(args):
    log_error(
        f"THREAD {args.thread.name}: {args.exc_type.__name__}: {args.exc_value}",
        "threading.excepthook",
    )


try:
    threading.excepthook = thread_error_handler
except AttributeError:
    pass


def error_handler(exc_type, exc_value, exc_tb):
    try:
        error_msg = f"{exc_type.__name__}: {exc_value}"
        log_error(error_msg, "sys.excepthook")
        print("\n" + "=" * 60)
        print("КРИТИЧЕСКАЯ ОШИБКА! Окно не закроется, пока вы не нажмёте Enter")
        print("=" * 60)
        traceback.print_exception(exc_type, exc_value, exc_tb)
        print("=" * 60)
        print("\nНажмите Enter для выхода...")
        input()
    except Exception as logging_failed:
        sys.stderr.write(f"FATAL: {exc_type.__name__}: {exc_value}\n")
        sys.stderr.write(f"Логирование упало: {logging_failed}\n")
        try:
            input("Press Enter...")
        except:
            pass
    finally:
        sys.exit(1)


sys.excepthook = error_handler

# =================================================================
# === БЕЗОПАСНЫЙ ИМПОРТ pyperclip ===
# =================================================================
PYPERCLIP_OK = False
try:
    import pyperclip

    try:
        pyperclip.paste()
        PYPERCLIP_OK = True
    except Exception as e:
        log_error(f"pyperclip.paste() упал: {type(e).__name__}: {e}", "pyperclip_init")
except ImportError:
    log_error("pyperclip не установлен", "pyperclip_init")

if not PYPERCLIP_OK:
    print("⚠️ pyperclip не установлен или не работает. Буфер будет через Tkinter.")
    print("Для стабильной работы на Windows: pip install pyperclip")


# =================================================================
# === БЕЗОПАСНЫЙ INPUT() ===
# =================================================================
def safe_input(prompt):
    try:
        return input(prompt)
    except EOFError:
        log_error("Закрытие консоли (EOF)", "safe_input")
        print("\n\nВыход...")
        sys.exit(0)
    except KeyboardInterrupt:
        raise


# =================================================================
# === ДОЛГОСРОЧНАЯ ПАМЯТЬ (с очисткой старых записей) ===
# =================================================================
LONG_MEMORY_FILE = "long_memory.txt"
MEMORY_CONTEXT_SIZE = 10
MAX_MEMORY_DAYS = 30


def clean_old_memory():
    if not os.path.exists(LONG_MEMORY_FILE):
        return
    try:
        with open(LONG_MEMORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        cutoff = datetime.now() - timedelta(days=MAX_MEMORY_DAYS)
        new_lines = []
        for line in lines:
            match = re.search(r"\[(\d{2}\.\d{2}\.\d{4})", line)
            if match:
                try:
                    date_str = match.group(1)
                    dt = datetime.strptime(date_str, "%d.%m.%Y")
                    if dt >= cutoff:
                        new_lines.append(line)
                except:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        if len(new_lines) != len(lines):
            with open(LONG_MEMORY_FILE, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            log_info(f"Память очищена: удалено {len(lines)-len(new_lines)} старых записей")
    except Exception as e:
        log_error(f"Ошибка очистки памяти: {e}", "clean_old_memory")


clean_old_memory()


def memory_add(role, content):
    try:
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        role_tag = "USER" if role == "user" else "ASSISTANT"
        line = f"[{now}] {role_tag}: {content}\n"
        with open(LONG_MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "memory_add")


def memory_load_context(max_chars=3000):
    if not os.path.exists(LONG_MEMORY_FILE):
        return ""
    try:
        with open(LONG_MEMORY_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        recent = lines[-MEMORY_CONTEXT_SIZE:]
        context = "".join(recent).strip()
        if len(context) > max_chars:
            parts = []
            total_len = 0
            for line in recent:
                if total_len + len(line) > max_chars:
                    break
                parts.append(line)
                total_len += len(line)
            context = "".join(parts).strip()
        return context
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "memory_load_context")
        return ""


def memory_show():
    if not os.path.exists(LONG_MEMORY_FILE):
        print("Память пуста.")
        return
    with open(LONG_MEMORY_FILE, encoding="utf-8") as f:
        print(f.read())


# =================================================================
# === НАСТРОЙКИ ЯДРА ИИ ===
# =================================================================
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
DB_FILE = "visited_sites.json"
AI_TIMEOUT = 120

_TK_ROOT = None


def get_tk_root():
    global _TK_ROOT
    if _TK_ROOT is None:
        try:
            _TK_ROOT = tk.Tk()
            _TK_ROOT.withdraw()
        except Exception as e:
            log_error(f"{type(e).__name__}: {e}", "get_tk_root")
            _TK_ROOT = None
    return _TK_ROOT


def get_safe_path(folder_name="my_database"):
    possible_paths = [
        os.path.join(os.path.expanduser("~"), "Documents", folder_name),
        os.path.join(os.path.expanduser("~"), "Desktop", folder_name),
        os.path.dirname(os.path.abspath(__file__)),
    ]
    for path in possible_paths:
        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, "test_write.txt")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            return path
        except:
            continue
    return folder_name


LOCAL_DB_DIR = get_safe_path("my_database")

# =================================================================
# === БАЗА ЗНАНИЙ DANBOORU (с резервным копированием) ===
# =================================================================
KNOWLEDGE_BASE_FILE = "danbooru_knowledge_base.json"
BACKUP_DATE_FILE = "last_backup_date.txt"


def backup_knowledge_base():
    if not os.path.exists(KNOWLEDGE_BASE_FILE):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    last_backup = ""
    if os.path.exists(BACKUP_DATE_FILE):
        with open(BACKUP_DATE_FILE) as f:
            last_backup = f.read().strip()
    if last_backup == today:
        return
    backup_name = f"danbooru_knowledge_base_backup_{today}.json"
    try:
        import shutil

        shutil.copy2(KNOWLEDGE_BASE_FILE, backup_name)
        with open(BACKUP_DATE_FILE, "w") as f:
            f.write(today)
        log_info(f"Создана резервная копия базы знаний: {backup_name}")
    except Exception as e:
        log_error(f"Ошибка резервного копирования: {e}", "backup_knowledge_base")


def load_knowledge_base():
    backup_knowledge_base()
    if not os.path.exists(KNOWLEDGE_BASE_FILE):
        return {}
    try:
        with open(KNOWLEDGE_BASE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "load_knowledge_base")
        return {}


def save_knowledge_base_as_txt(knowledge_base):
    try:
        with open("danbooru_knowledge_base.txt", "w", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write("БАЗА ЗНАНИЙ ТЕГОВ DANBOORU\n")
            f.write(f"Всего тегов: {len(knowledge_base)}\n")
            f.write("=" * 60 + "\n\n")
            for tag, explanation in sorted(knowledge_base.items()):
                f.write(f"{tag}:\n")
                f.write(f"  {explanation}\n\n")
        return True
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "save_knowledge_base_as_txt")
        return False


# =================================================================
# === ИМПОРТ СОБСТВЕННЫХ ТЕГОВ ===
# =================================================================
def import_my_tags(knowledge_base):
    my_tags_file = "my_tags_auto.txt"
    if not os.path.exists(my_tags_file):
        return knowledge_base
    log_info("Найден my_tags_auto.txt, обновляю базу...")
    try:
        with open(my_tags_file, encoding="utf-8") as f:
            lines = f.readlines()
        new_tags = {}
        current_category = ""
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "Категория" in line or "категория" in line:
                current_category = line.replace("Категория:", "").replace("Категория —", "").strip()
                continue
            if " — " in line or ": " in line:
                if " — " in line:
                    line = line.replace(" — ", ": ")
                try:
                    tag, explanation = line.split(": ", 1)
                    tag = tag.strip()
                    explanation = explanation.strip()
                    if tag and explanation:
                        new_tags[tag] = (
                            f"[{current_category}] {explanation}"
                            if current_category
                            else explanation
                        )
                except ValueError:
                    continue
        total = len(new_tags)
        if total == 0:
            log_info("Новых тегов не найдено.")
            return knowledge_base
        added = replaced = skipped_identical = 0
        for tag, explanation in new_tags.items():
            if tag in knowledge_base:
                if knowledge_base[tag] == explanation:
                    skipped_identical += 1
                else:
                    knowledge_base[tag] = explanation
                    replaced += 1
            else:
                knowledge_base[tag] = explanation
                added += 1
        if added > 0 or replaced > 0 or skipped_identical > 0:
            with open(KNOWLEDGE_BASE_FILE, "w", encoding="utf-8") as f:
                json.dump(knowledge_base, f, ensure_ascii=False, indent=2)
            save_knowledge_base_as_txt(knowledge_base)
            log_info(
                f"Готово! +{added}, замен {replaced}, пропущено {skipped_identical}. Всего {len(knowledge_base)}."
            )
        else:
            log_info("Изменений не было.")
        return knowledge_base
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "import_my_tags")
        return knowledge_base


KNOWLEDGE_BASE = load_knowledge_base()
if KNOWLEDGE_BASE:
    log_info(f"Загружено {len(KNOWLEDGE_BASE)} тегов из базы")
KNOWLEDGE_BASE = import_my_tags(KNOWLEDGE_BASE)

# =================================================================
# === СТИЛИ ГЕНЕРАЦИИ АРТОВ ===
# =================================================================
STYLES = {
    "1": {
        "label": "РЕАЛИЗМ",
        "name": "realisticVisionV51_v51VAE",
        "prompt_tail": "photorealistic, 8k, sharp focus, detailed skin textures, realistic lighting, DSLR, depth of field, high quality",
        "negative": "cartoon, anime, illustration, painting, blurry, low quality, distorted, deformed",
    },
    "2": {
        "label": "АНИМЕ",
        "name": "ponyDiffusionV6XL_v6StartWithThisOne",
        "prompt_tail": "anime style, vibrant colors, cel shading, detailed eyes, dynamic pose, masterpiece, best quality, highres",
        "negative": "photorealistic, 3d, CGI, blurry, lowres, bad anatomy, ugly, distorted",
    },
    "3": {
        "label": "ФЭНТЕЗИ",
        "name": "realisticVisionV51_v51VAE",
        "prompt_tail": "fantasy art, magical atmosphere, cinematic lighting, ethereal glow, concept art, high detail, 8k",
        "negative": "modern, urban, realistic skin, blurry, low quality",
    },
    "4": {
        "label": "КИБЕРПАНК",
        "name": "realisticVisionV51_v51VAE",
        "prompt_tail": "cyberpunk style, neon lights, futuristic city, rain, blade runner atmosphere, high detail, cinematic",
        "negative": "nature, medieval, bright daylight, blurry, low quality",
    },
}


# =================================================================
# === БУФЕР ОБМЕНА И ПАМЯТЬ САЙТОВ ===
# =================================================================
def load_memory():
    """Загружает память сайтов, удаляет записи старше MAX_MEMORY_DAYS дней."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, encoding="utf-8") as f:
                data = json.load(f)
            cutoff = datetime.now() - timedelta(days=MAX_MEMORY_DAYS)
            new_data = {}
            changed = False
            for key, entries in data.items():
                if isinstance(entries, list):
                    filtered = []
                    for entry in entries:
                        if isinstance(entry, dict) and "timestamp" in entry:
                            try:
                                ts = datetime.fromisoformat(entry["timestamp"])
                                if ts >= cutoff:
                                    filtered.append(entry)
                            except:
                                filtered.append(entry)
                        else:
                            filtered.append(entry)
                    if filtered:
                        new_data[key] = filtered
                        if len(filtered) != len(entries):
                            changed = True
                    else:
                        changed = True
                else:
                    new_data[key] = entries
            if changed:
                save_memory(new_data)
                log_info("Память сайтов очищена от старых записей")
            return new_data
        except Exception as e:
            log_error(f"{type(e).__name__}: {e}", "load_memory")
            return {}
    return {}


def save_memory(memory):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=4)
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "save_memory")


def copy_to_clipboard(text):
    log_info("Копирование в буфер...")
    if PYPERCLIP_OK:
        try:
            pyperclip.copy(text)
            log_info("pyperclip: успешно скопировано")
            return True
        except Exception as e:
            log_error(f"pyperclip.copy() упал: {type(e).__name__}: {e}", "copy_to_clipboard")
    try:
        import subprocess

        process = subprocess.Popen(["clip"], stdin=subprocess.PIPE, text=True)
        process.communicate(text)
        log_info("clip.exe: успешно скопировано")
        return True
    except Exception as e:
        log_error(f"clip.exe упал: {type(e).__name__}: {e}", "copy_to_clipboard")
    try:
        root = get_tk_root()
        if root:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            time.sleep(0.1)
            log_info("Tkinter: успешно скопировано")
            return True
    except Exception as e:
        log_error(f"Tkinter упал: {type(e).__name__}: {e}", "copy_to_clipboard")
    return False


def get_clipboard_text():
    if PYPERCLIP_OK:
        try:
            return pyperclip.paste()
        except Exception as e:
            log_error(f"pyperclip.paste() упал: {type(e).__name__}: {e}", "get_clipboard_text")
    try:
        root = get_tk_root()
        if root:
            return root.clipboard_get()
    except Exception as e:
        log_error(f"Tkinter упал: {type(e).__name__}: {e}", "get_clipboard_text")
    return ""


# =================================================================
# === ВЕБ-ПОИСК ЧЕРЕЗ ПУБЛИЧНЫЙ SEARXNG ===
# =================================================================
def search_internet(query, max_results=3):
    log_info(f"Поиск через публичный SearXNG: '{query}'")
    if not query or len(query.strip()) < 2:
        return []

    searx_instances = [
        "https://searx.be/search",
        "https://searxng.site/search",
        "https://paulgo.io/search",
        "https://priv.au/search",
        "https://search.sapti.me/search",
    ]

    urls = []
    for instance in searx_instances:
        try:
            response = requests.get(
                instance, params={"q": query, "format": "json", "categories": "general"}, timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", []) or data.get("items", [])
                for item in results[:max_results]:
                    url = item.get("url") or item.get("link")
                    if url and url.startswith("http"):
                        urls.append(url)
                if urls:
                    log_info(f"Найдено {len(urls)} ссылок через SearXNG")
                    return urls
        except Exception as e:
            log_error(f"Ошибка SearXNG ({instance}): {e}", "search_internet")
            continue

    log_info("Поиск не дал результатов.")
    return []


# =================================================================
# === ПАРСИНГ САЙТОВ С ВАЛИДАЦИЕЙ URL ===
# =================================================================
def is_valid_url(url):
    if not url:
        return False
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if "javascript:" in url.lower():
        return False
    return True


def scrape_website(url):
    if not is_valid_url(url):
        log_error(f"Невалидный URL: {url}", "scrape_website")
        return None
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for script in soup(["script", "style"]):
            script.extract()
        text = soup.get_text(separator=" ")
        return " ".join(text.split())[:15000]
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", f"scrape_website({url})")
        return None


# =================================================================
# === ВЗАИМОДЕЙСТВИЕ С СЕРВЕРОМ ИИ (LM Studio) ===
# =================================================================
def ask_local_ai(
    prompt,
    system_instruction="You are a helpful assistant.",
    temperature=0.5,
    max_tokens=2048,
    stop=None,
):
    payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stop": stop or ["</s>", "<|im_end|>", "<|im_start|>", "\nUser:", "\nuser:"],
    }
    try:
        start = time.time()
        response = requests.post(LM_STUDIO_URL, json=payload, timeout=AI_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        elapsed = time.time() - start
        log_info(f"LM Studio ответил за {elapsed:.2f} сек")
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("Ошибка:") or "not_found" in content.lower():
                return None
            return content
        return None
    except Exception as e:
        log_error(f"{type(e).__name__}: {e}", "ask_local_ai")
        return None


def process_text_content(content, source_name, memory):
    if len(content.split()) < 10:
        return None
    if source_name not in memory:
        memory[source_name] = []
    context = memory_load_context()
    system_prompt = (
        f"Ты — продвинутый ИИ-аналитик. Контекст из прошлых диалогов:\n{context}\n\n"
        "Сделай ОЧЕНЬ краткую выжимку (3-5 пунктов) на русском языке. Пиши только важные факты."
    )
    user_prompt = f"Проанализируй текст и сделай выжимку:\n\n{content}"
    summary = ask_local_ai(user_prompt, system_prompt, temperature=0.3)
    if summary is None:
        return None
    memory[source_name].append(
        {"summary": summary.strip(), "timestamp": datetime.now().isoformat()}
    )
    save_memory(memory)
    memory_add("assistant", f"[Саммари {source_name}]: {summary.strip()}")
    return summary


# =================================================================
# === ЛОКАЛЬНЫЙ ПОИСК И ГЕНЕРАТОР ТЕГОВ (RAG) ===
# =================================================================
def search_local_files(query):
    if not os.path.isdir(LOCAL_DB_DIR):
        return []
    MAX_FILE_SIZE = 5 * 1024 * 1024
    log_info(f"Поиск в локальных файлах: '{query}'")
    words = [w.lower() for w in re.findall(r"\b\w+\b", query) if len(w) > 2]
    if not words:
        return []
    relevant_chunks = []
    for filename in os.listdir(LOCAL_DB_DIR):
        file_path = os.path.join(LOCAL_DB_DIR, filename)
        if os.path.isfile(file_path):
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                continue
            try:
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read().lower()
                if any(w in content for w in words):
                    relevant_chunks.append(f"Данные из файла {filename}:\n{content[:10000]}")
            except Exception as e:
                log_error(f"{type(e).__name__}: {e}", f"search_local_files({filename})")
    return relevant_chunks


def normalize_tag(tag):
    tag = re.sub(r"[^a-zA-Z0-9_-]", "", tag.strip().lower())
    return tag


def find_relevant_tags(scene, knowledge_base, max_tags=50):
    if not knowledge_base:
        return []
    scene_lower = scene.lower()
    words = set(re.findall(r"\b[a-zа-яё]+\b", scene_lower))
    words = {w for w in words if len(w) > 2}
    scored = []
    for tag, description in knowledge_base.items():
        desc_lower = description.lower()
        tag_parts = tag.replace("_", " ").lower()
        score = 0
        for w in words:
            if re.search(r"\b" + re.escape(w) + r"\b", desc_lower) or re.search(
                r"\b" + re.escape(w) + r"\b", tag_parts
            ):
                score += 1
        if score > 0:
            scored.append((score, tag, description))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(t, d) for score, t, d in scored[:max_tags]]


def clean_response_tags(response):
    if not response:
        return []
    parts = response.split(",")
    cleaned = []
    for part in parts:
        part = part.strip()
        part = re.sub(r"^[\d\-]+\.?\s*", "", part)
        part = re.sub(r"^(tags|теги)\s*:", "", part, flags=re.IGNORECASE)
        if ":" in part:
            part = part.split(":")[0].strip()
        part = normalize_tag(part)
        if part and len(part) > 1:
            cleaned.append(part)
    return cleaned


def generate_danbooru_tags(scene, style, knowledge_base):
    relevant = find_relevant_tags(scene, knowledge_base, max_tags=60)
    system_prompt = "Ты генератор тегов Danbooru. Отвечай ТОЛЬКО тегами через запятую, на английском. НЕ добавляй пояснения, префиксы, суффиксы."
    if relevant:
        tags_list = "\n".join([f"- {tag}: {desc[:120]}" for tag, desc in relevant])
        prompt = f"Ты эксперт по тегам Danbooru. Выбери из списка 15-25 тегов под сцену.\nОписание: {scene}\nСтиль: {style['label']}\nДоступные теги:\n{tags_list}\nВыведи ТОЛЬКО теги через запятую. В конце добавь: {style['prompt_tail']}"
    else:
        prompt = f"Преврати описание в английские теги Danbooru через запятую.\nОписание: {scene}\nСтиль: {style['label']}\nВ конце добавь: {style['prompt_tail']}"
    response = ask_local_ai(
        prompt, system_instruction=system_prompt, temperature=0.1, max_tokens=400
    )
    if response is None:
        return "", "ошибка модели", []
    cleaned_tags = clean_response_tags(response)
    if len(cleaned_tags) < 3:
        raw_tags = [normalize_tag(t) for t in response.split(",") if normalize_tag(t)]
        if raw_tags:
            cleaned_tags = raw_tags
    # Добавляем хвост стиля (программно — защита от того, что модель проигнорировала инструкцию)
    tail = style["prompt_tail"].split(",")
    for t in tail:
        t = t.strip()
        if t and t not in cleaned_tags:
            cleaned_tags.append(t)
    cleaned_response = ", ".join(cleaned_tags)
    source_note = f" (использовано {len(relevant)} тегов из базы)" if relevant else " (база пуста)"
    return cleaned_response, source_note, relevant


# =================================================================
# === ПРЯМОЙ ПАРСЕР DANBOORU (с URL-encoding) ===
# =================================================================
def parse_danbooru_direct(tag_query):
    log_info(f"Прямой парсинг Danbooru по запросу: '{tag_query}'")
    try:
        encoded_query = urllib.parse.quote(tag_query)
        url = f"https://danbooru.donmai.us/tags.json?search[name_matches]={encoded_query}&limit=10"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            tags_data = response.json()
            output = []
            for item in tags_data:
                name = item.get("name")
                category = item.get("category")
                post_count = item.get("post_count")
                output.append(f"Тег: {name} | Категория: {category} | Постов: {post_count}")
            return "\n".join(output) if output else "Тегов не найдено."
        return f"Ошибка {response.status_code}"
    except Exception as e:
        log_error(f"Ошибка парсинга Danbooru: {e}", "parse_danbooru_direct")
        return "Не удалось связаться с Danbooru."


# =================================================================
# === РЕЖИМ АРТ С КОНСОЛИ (ПОЛНАЯ ВЕРСИЯ) ===
# =================================================================
def art_mode():
    log_info("Вход в art_mode")
    start_time = time.time()
    try:
        print("\n" + "=" * 60)
        print("🎨 ГЕНЕРАТОР АРТА (приоритетный режим)")
        print("=" * 60)
        print("\nВыбери стиль:")
        for key, style in STYLES.items():
            print(f"  {key}. {style['label']} - {style['name']}")
        choice = safe_input("\nСтиль (1-4): ").strip()
        if choice not in STYLES:
            choice = "1"
        style = STYLES[choice]
        scene = safe_input("\nОпиши сцену подробно: ").strip()
        if not scene:
            return
        memory_add("user", f"[АРТ] Стиль: {style['label']}. Сцена: {scene}")
        response, source_note, relevant_tags = generate_danbooru_tags(scene, style, KNOWLEDGE_BASE)
        if not response:
            print("⚠️ Модель не дала ответа или вернула ошибку.")
            return
        elapsed = time.time() - start_time
        print("\n" + "=" * 60)
        print(f"🚀 ПРОМПТ ДЛЯ {style['name']}")
        print(response)
        print(f"\n{source_note}")
        print(f"⏱️ Время: {elapsed:.2f} сек")
        print("=" * 60)
        print("\n❌ НЕГАТИВНЫЙ ПРОМПТ:")
        print(style["negative"])
        print("=" * 60)
        copy_to_clipboard(response)
        memory_add("assistant", f"Сгенерирован промпт: {response[:200]}")
        feedback = safe_input("\n💾 Сохранить в golden_prompts.txt? (д/н): ").strip().lower()
        if feedback in ["д", "y", "да", "yes"]:
            try:
                with open("golden_prompts.txt", "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
                    f.write(f"Модель: {style['name']}\n")
                    f.write(f"Стиль: {style['label']}\n")
                    f.write(f"Сцена: {scene}\n")
                    f.write(f"Промпт: {response}\n")
                    f.write(f"Негатив: {style['negative']}\n")
                    f.write(f"Использовано тегов из базы: {len(relevant_tags)}\n")
                    f.write(f"Время: {elapsed:.2f}с\n")
                print("✅ Сохранено в golden_prompts.txt!")
            except Exception as e:
                log_error(f"Ошибка сохранения: {e}", "save_golden_prompt")
                print("⚠️ Ошибка сохранения")
    except Exception as e:
        log_error(f"Ошибка в арт-режиме: {e}", "art_mode")


# =================================================================
# === ТОЧКА СВЯЗИ С ВЕБ-ИНТЕРФЕЙСОМ STREAMLIT ===
# =================================================================
def run_single_prompt(prompt_text, style_key="2"):
    style = STYLES.get(style_key, STYLES["2"])
    try:
        response, source_note, relevant_tags = generate_danbooru_tags(
            prompt_text, style, KNOWLEDGE_BASE
        )
        if response:
            copy_to_clipboard(response)
            memory_add("user", f"[ПРОМПТ] Стиль: {style['label']}. Сцена: {prompt_text}")
            memory_add("assistant", f"Сгенерирован промпт: {response[:200]}")
        else:
            response = "Ошибка генерации промпта"
        return response
    except Exception as e:
        log_error(f"Ошибка в функции run_single_prompt: {e}", "run_single_prompt")
        return f"Ошибка: {e}"


# =================================================================
# === ГЛАВНЫЙ ЦИКЛ КОНСОЛИ ===
# =================================================================
def main():
    try:
        log_info("=== НАЧАЛО MAIN ===")
        memory = load_memory()
        print("=" * 60)
        print("🚜 Василий готов к пахоте, Хозяин!")
        print(f"📚 База тегов: {len(KNOWLEDGE_BASE)}")
        print(f"🧠 Память активна, лог пишется в {DEBUG_LOG}")
        print("=" * 60)
        print("\nКоманды:")
        print("  Enter / арт     → 🎨 генерация арта")
        print("  память          → 🧠 показать историю диалога")
        print("  диск            → 🔍 поиск по локальным файлам")
        print("  данбоору / db   → 🔍 поиск тега на Danbooru")
        print("  любой текст     → 🔍 поиск в интернете")
        print("  выход           → завершить")
        while True:
            user_input = safe_input("\n🎯 Команда: ").strip()
            if not user_input:
                art_mode()
                continue
            cmd = user_input.lower()
            if cmd in ["выход", "exit", "quit", "q"]:
                print("\nДо встречи!")
                break
            if cmd in ["арт", "art", "промпт"]:
                art_mode()
                continue
            if cmd in ["память", "история"]:
                memory_show()
                continue
            if cmd in ["диск", "disk", "file"]:
                local_query = safe_input("Что найти? ").strip()
                if not local_query:
                    continue
                memory_add("user", f"[ДИСК] Запрос: {local_query}")
                files_data = search_local_files(local_query)
                if not files_data:
                    print("Ничего не найдено.")
                    continue
                all_data = "\n\n".join(files_data)
                context = memory_load_context()
                system = f"Ты — координатор базы. Отвечай на русском.\n\nКОНТЕКСТ ИЗ ПРОШЛЫХ ДИАЛОГОВ:\n{context}"
                report = ask_local_ai(
                    f"Ответь по запросу '{local_query}':\n\n{all_data}", system, temperature=0.4
                )
                if report is not None:
                    print(f"\nОТВЕТ:\n{report}")
                    memory_add("assistant", report[:300])
                else:
                    print("⚠️ Модель не дала ответа")
                continue
            if cmd in ["данбоору", "danbooru", "db"]:
                tag_query = safe_input("Какой тег искать? ").strip()
                if tag_query:
                    result = parse_danbooru_direct(tag_query)
                    print(f"\n{result}")
                continue
            # По умолчанию — поиск в интернете
            print("\n🔍 Ищу в интернете...")
            urls = search_internet(user_input)
            if urls:
                print(f"🌐 Найдено сайтов: {len(urls)}. Парсю первый...")
                site_text = scrape_website(urls[0])
                if site_text:
                    res = process_text_content(site_text, urls[0], memory)
                    print(f"\n📄 Результат:\n{res}")
                else:
                    print("❌ Не удалось извлечь текст.")
            else:
                print("❌ Ничего не найдено.")
    except Exception as e:
        log_error(f"Критическая ошибка в main: {e}", "main")
        safe_input("Нажмите Enter для выхода...")


# =================================================================
# === ТОЧКА ВХОДА ===
# =================================================================
if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--prompt":
        p_text = " ".join(sys.argv[2:])
        print(run_single_prompt(p_text))
        sys.exit(0)
    else:
        main()
