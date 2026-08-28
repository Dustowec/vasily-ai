# ruff: noqa: E402
"""Streamlit дашборд для Vasily AI (с диагностикой web_search)."""

import asyncio
import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import streamlit as st

from core.agent import AgentCore
from core.config import Config
from core.logging_config import setup_logging

st.set_page_config(
    page_title="Vasily AI Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .chat-message-user {
        background-color: #e1f5fe;
        border-radius: 10px;
        padding: 8px 12px;
        margin: 4px 0;
        text-align: right;
        font-size: 0.95rem;
    }
    .chat-message-assistant {
        background-color: #f1f8e9;
        border-radius: 10px;
        padding: 8px 12px;
        margin: 4px 0;
        text-align: left;
        font-size: 0.95rem;
    }
    .status-ok { color: #4caf50; }
    .status-error { color: #f44336; }
    .stButton button { width: 100%; }
    .red-button button {
        background-color: #f44336;
        color: white;
    }
    .red-button button:hover {
        background-color: #d32f2f;
    }
    .css-1v3fvcr h1, .css-1v3fvcr h2, .css-1v3fvcr h3 {
        margin-top: 0.2rem;
        margin-bottom: 0.2rem;
    }
    .css-1d391kg {
        padding-top: 0.2rem;
        padding-bottom: 0.2rem;
    }
    .stTextArea textarea {
        min-height: 80px !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# Инициализация
if "agent" not in st.session_state:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    st.session_state.loop = loop

    log_dir = Path("logs")
    setup_logging(log_dir=log_dir, level="INFO", json_logs=True)

    config = Config.load()
    config.validate()
    agent = AgentCore(config)
    loop.run_until_complete(agent.initialize())

    st.session_state.agent = agent
    st.session_state.messages = []
    st.session_state.initialized = True
    st.session_state.last_refresh = time.time()
    st.session_state.last_status = {}


def send_message(user_input: str):
    if not user_input.strip():
        return
    st.session_state.messages.append({"role": "user", "content": user_input})
    agent = st.session_state.agent
    loop = st.session_state.loop
    try:
        response = loop.run_until_complete(agent.handle_request({"text": user_input}))
    except RuntimeError as e:
        if "closed" in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            st.session_state.loop = loop
            response = loop.run_until_complete(agent.handle_request({"text": user_input}))
        else:
            raise

    if response.get("status") == "success":
        answer = response.get("message", "Нет ответа")
    else:
        answer = f"[Ошибка] {response.get('message', 'Неизвестная ошибка')}"
    st.session_state.messages.append({"role": "assistant", "content": answer})


def get_agent_status(agent, loop):
    """Получить статус модулей напрямую из агента."""
    status = {
        "llm": {"available": False},
        "memory": {"available": False},
        "plugins": {"available": False},
        "web_search": {"available": False},
        "disk": {"available": False},
    }

    # Проверяем LLM
    if agent and agent.llm_client:
        try:
            status["llm"]["available"] = True
        except Exception:
            pass

    # Проверяем память
    if agent and hasattr(agent, "memory"):
        try:
            if agent.memory:
                status["memory"]["available"] = True
        except Exception:
            pass

    # Проверяем плагины (только наличие в реестре)
    if agent and hasattr(agent, "plugin_registry"):
        try:
            if len(agent.plugin_registry) > 0:
                status["plugins"]["available"] = True
        except Exception:
            pass

    # Проверяем web_search (только наличие в реестре, без реального запроса "ping")
    if agent and hasattr(agent, "plugin_registry"):
        try:
            web_search = agent.plugin_registry.get("web_search")
            if web_search is not None:
                status["web_search"]["available"] = True
            else:
                status["web_search"]["available"] = False
                status["web_search"]["error"] = "Plugin not registered"
        except Exception as e:
            status["web_search"]["available"] = False
            status["web_search"]["error"] = str(e)

    # Проверяем диск
    try:
        logs_path = Path("logs")
        if logs_path.exists():
            status["disk"]["available"] = True
    except Exception:
        pass

    return status


def refresh_status():
    """Обновить статус модулей."""
    agent = st.session_state.agent
    loop = st.session_state.loop
    if agent:
        status = get_agent_status(agent, loop)
        st.session_state.last_status = status


now = time.time()
if st.session_state.initialized and (now - st.session_state.last_refresh > 5):
    refresh_status()
    st.session_state.last_refresh = now

# --- LAYOUT ---
col1, col2, col3 = st.columns([0.2, 0.55, 0.25], gap="small")

with col1:
    st.markdown("## 📖 Справка")
    readme_path = Path("README.md")
    if readme_path.exists():
        with open(readme_path, encoding="utf-8") as f:
            readme_content = f.read()
        if "### Команды" in readme_content:
            start = readme_content.find("### Команды")
            end = readme_content.find("###", start + 10)
            if end == -1:
                end = len(readme_content)
            st.markdown(readme_content[start:end])
        else:
            st.markdown("```\n> help\n> status\n> exit\n> забыть <тема>\n> забыть всё\n```")
    else:
        st.info("README.md не найден")

with col2:
    st.markdown("## 💬 История чата")
    chat_container = st.container(height=350)
    with chat_container:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.markdown(
                    f'<div class="chat-message-user">🧑 {msg["content"]}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="chat-message-assistant">🤖 {msg["content"]}</div>',
                    unsafe_allow_html=True,
                )

with col3:
    st.markdown("## 📊 Статус модулей")
    status = st.session_state.get("last_status", {})
    if status:
        llm_ok = status.get("llm", {}).get("available", False)
        plugins_ok = status.get("plugins", {}).get("available", False)
        memory_ok = status.get("memory", {}).get("available", False)
        web_search_ok = status.get("web_search", {}).get("available", False)
        disk_ok = status.get("disk", {}).get("available", False)

        def icon(ok):
            return "🟢" if ok else "🔴"

        st.markdown(f"- **LLM** {icon(llm_ok)}")
        st.markdown(f"- **Плагины** {icon(plugins_ok)}")
        st.markdown(f"- **Память** {icon(memory_ok)}")
        st.markdown(f"- **Поиск** {icon(web_search_ok)}")
        if not web_search_ok and status.get("web_search", {}).get("error"):
            st.caption(f"⚠️ {status['web_search']['error'][:50]}")
        st.markdown(f"- **Диск** {icon(disk_ok)}")
        st.markdown("---")
        st.caption("Легенда: 🟢 работает, 🔴 упал")
    else:
        st.info("Статус не загружен")

# Нижний ярус
st.divider()
col4, col5, col6 = st.columns([0.2, 0.55, 0.25], gap="small")

with col4:
    st.markdown("## ⚙️ Управление")
    if st.button("🗑️ Забыть всё", help="Очистить всю память (ротация)"):
        agent = st.session_state.agent
        if agent and hasattr(agent, "memory"):
            loop = st.session_state.loop
            try:
                result = loop.run_until_complete(agent.memory.forget_all(confirm=True))
            except RuntimeError as e:
                if "closed" in str(e):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    st.session_state.loop = loop
                    result = loop.run_until_complete(agent.memory.forget_all(confirm=True))
                else:
                    raise
            if result:
                st.success("Память очищена")
                st.rerun()
            else:
                st.error("Ошибка очистки")

    if st.button("🔄 Перезапуск агента"):
        agent = st.session_state.agent
        loop = st.session_state.loop
        if agent:
            try:
                loop.run_until_complete(agent.shutdown())
                loop.run_until_complete(agent.initialize())
            except RuntimeError as e:
                if "closed" in str(e):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    st.session_state.loop = loop
                    loop.run_until_complete(agent.shutdown())
                    loop.run_until_complete(agent.initialize())
                else:
                    raise
            st.success("Агент перезапущен")
            st.rerun()

    st.markdown('<div class="red-button">', unsafe_allow_html=True)
    if st.button("⏻ Выход", help="Завершить работу агента и дашборда"):
        agent = st.session_state.agent
        loop = st.session_state.loop
        if agent:
            try:
                loop.run_until_complete(agent.shutdown())
            except RuntimeError:
                pass
        st.success("Агент остановлен. Дашборд закрывается...")
        time.sleep(0.5)
        os._exit(0)
    st.markdown("</div>", unsafe_allow_html=True)

with col5:
    st.markdown("## 📝 Ввод запроса")
    user_input = st.chat_input("Введите сообщение...")
    if user_input:
        send_message(user_input)
        refresh_status()
        st.rerun()

with col6:
    st.markdown("## 💥 Краш-лог")
    crash_dir = Path("logs/crash_reports")
    crash_info = None
    if crash_dir.exists():
        reports = list(crash_dir.glob("**/crash_*.md"))
        if reports:
            latest = max(reports, key=lambda p: p.stat().st_mtime)
            try:
                with open(latest, encoding="utf-8") as f:
                    content = f.read()
                    lines = content.split("\n")
                    summary = []
                    in_summary = False
                    for line in lines:
                        if "Error Summary" in line:
                            in_summary = True
                            continue
                        if in_summary and line.strip().startswith("-"):
                            summary.append(line.strip())
                        if in_summary and line.strip().startswith("##"):
                            break
                    crash_info = {
                        "file": latest.name,
                        "summary": "\n".join(summary) if summary else "Ошибка неизвестна",
                    }
            except Exception:
                crash_info = {"file": latest.name, "summary": "Не удалось прочитать"}
    if crash_info:
        st.warning(f"Сбой: {crash_info['file']}")
        st.text_area(
            "Краш-лог",
            crash_info["summary"],
            height=70,
            key="crash_display",
            label_visibility="collapsed",
        )
    else:
        st.info("Сбоев нет")
