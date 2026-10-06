"""Фикс дедупа user_fact (HANDOFF §7.1).

Живой баг: remember_fact использует recall_memory для дедупа, а тот
находит dialogue_summary по общим словам («зовут», «пользователь»).
LLM видит похожий текст, говорит «ДУБЛЬ», и user_fact НЕ создаётся.

Фикс двухслойный:
  1. Дедуп ищет только в каналах user_fact / task_state
     (recall_memory(..., categories=DEDUP_CATEGORIES)).
  2. Вторая линия защиты: если по какой-то причине из дедупа
     вернулся чужой ключ (dialogue_summary:...) — не трогаем его,
     пишем warning, создаём новый user_fact.

Оба слоя проверяются здесь. Красные до реализации.
"""

import pytest

from core.internal_tools import RememberFactTool
from memory.manager import GradientMemory


@pytest.fixture
def m(tmp_path) -> GradientMemory:
    """Изолированный менеджер: все файлы памяти — во временной папке."""
    return GradientMemory(data_dir=str(tmp_path / "data"))


def _make_entry(score: float, value, **overrides) -> dict:
    """Минимальный корректный entry для прямой вставки в зону."""
    base = {
        "value": value,
        "score": score,
        "is_cold": False,
        "no_compress": False,
        "shield": False,
        "summary": None,
        "changed_since_revival": False,
        "last_heat_tick": -1,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "created_tick": 0,
    }
    base.update(overrides)
    return base


class DedupFakeLLM:
    """Минимальный mock OllamaClient для RememberFactTool.

    Отвечает по-разному на два типа промптов:
      - 'Сравни два утверждения' (_similarity_verdict) → verdict;
      - 'Объедини факт B' (_merge_facts)              → merged.

    Различитель — литералы из шаблонов промптов в internal_tools.py.
    """

    def __init__(self, verdict: str = "НЕТ", merged: str = "объединённый факт"):
        self.verdict = verdict
        self.merged = merged
        self.calls: list[str] = []

    async def generate(self, prompt: str, **kwargs) -> dict:
        self.calls.append(prompt)
        if "Сравни два утверждения" in prompt:
            return {"response": self.verdict}
        if "Объедини факт B" in prompt:
            return {"response": self.merged}
        return {"response": ""}


# ==================== главный красный тест (§7.1) ====================


async def test_remember_fact_not_blocked_by_dialogue_summary(m):
    """Живой сценарий из HANDOFF §7.1.

    Память: dialogue_summary со словами «его зовут Вася».
    Команда: «запомни, меня зовут Вася».
    Ожидание: user_fact СОЗДАН, dialogue_summary НЕ тронут.

    Раньше: recall_memory находил dialogue_summary → LLM возвращал
    «ДУБЛЬ» → user_fact не создавался. Тест красный до фикса.
    """
    # блок: подкладываем dialogue_summary с похожими словами.
    # почему: имитируем реальную память, где саммари диалога содержит
    # «зовут Вася» — это триггер ложного срабатывания.
    m._hot["dialogue_summary:old"] = _make_entry(
        25.0, {"summary": "Пользователь представился, его зовут Вася"}
    )

    # блок: FakeLLM всегда отвечает «ДУБЛЬ».
    # почему: реальный LLM именно так и реагировал на похожие тексты.
    # Детерминированно воспроизводим баг.
    fake = DedupFakeLLM(verdict="ДУБЛЬ")
    tool = RememberFactTool(memory_manager=m, llm_client=fake)

    result = await tool._execute(fact="меня зовут Вася")

    # блок: факт должен быть СОХРАНЁН.
    # почему: dialogue_summary — это конспект диалога, а не факт о
    # пользователе. Явная команда «запомни» не должна им блокироваться.
    assert result["status"] == "success", f"Ожидался success, получено: {result}"
    assert result.get("key", "").startswith("user_fact:")

    # блок: проверяем на уровне хранилища, что user_fact реально создан.
    # почему: status=success недостаточно — факт мог не дойти до _hot
    # по другой причине. Смотрим на то, что лежит в памяти.
    user_facts = [k for k in m._hot if k.startswith("user_fact:")]
    assert len(user_facts) == 1, f"Ожидался 1 user_fact, есть: {user_facts}"

    # блок: dialogue_summary НЕ тронут.
    # почему: это чужая категория; RememberFactTool не имеет права
    # перезаписывать её значение даже при вердикте ДУБЛЬ/ДОПОЛНЕНИЕ.
    assert m._hot["dialogue_summary:old"]["value"] == {
        "summary": "Пользователь представился, его зовут Вася"
    }


# ==================== вторая линия защиты ====================


async def test_second_line_blocks_foreign_key_overwrite(m, monkeypatch):
    """Вторая линия: даже если из дедупа вернулся чужой ключ —
    не перезаписываем его, создаём новый user_fact.

    Слой 1 (фильтр категорий) в норме не пустит dialogue_summary в
    дедуп. Но если что-то рассогласуется (опечатка, будущий рефактор),
    слой 2 ловит это и защищает чужие записи от перезаписи.
    """
    # блок: dialogue_summary в памяти, чтобы было что «защищать».
    m._hot["dialogue_summary:evil"] = _make_entry(25.0, {"summary": "старое саммари"})

    # блок: monkeypatch на recall_memory, чтобы СИМУЛИРОВАТЬ рассинхрон.
    # почему: в норме (после фикса) recall_memory с categories не вернёт
    # dialogue_summary. Здесь мы заставляем его вернуть — это и есть
    # проверка второй линии.
    async def fake_recall(query, external=True, categories=None):
        return {
            "found": True,
            "facts": [
                {
                    "key": "dialogue_summary:evil",
                    "zone": "hot",
                    "score": 25.0,
                    "value": {"summary": "старое саммари"},
                    "summary": "",
                }
            ],
        }

    monkeypatch.setattr(m, "recall_memory", fake_recall)

    fake = DedupFakeLLM(verdict="ДОПОЛНЕНИЕ", merged="что-то другое")
    tool = RememberFactTool(memory_manager=m, llm_client=fake)

    result = await tool._execute(fact="меня зовут Вася")

    # блок: чужой ключ не тронут.
    # почему: именно это проверяет вторая линия. Ошибка — если бы
    # remember_user_fact("dialogue_summary:evil", ...) перезаписал value.
    assert m._hot["dialogue_summary:evil"]["value"] == {"summary": "старое саммари"}

    # блок: вместо перезаписи создан НОВЫЙ user_fact.
    # почему: пользователь дал явную команду «запомни» — она должна
    # быть выполнена, даже если слой 1 дал сбой.
    assert result["status"] == "success"
    user_facts = [k for k in m._hot if k.startswith("user_fact:")]
    assert len(user_facts) == 1, f"Ожидался 1 user_fact, есть: {user_facts}"


# ==================== регрессионный guard ====================


async def test_real_user_fact_dedup_still_works(m):
    """Дедуп настоящих user_fact остаётся рабочим.

    Регрессионный guard: фикс §7.1 не должен выключить дедуп для
    легитимных случаев. Если тот же факт уже сохранён и LLM говорит
    «ДУБЛЬ» — возвращаем already_exists, новый user_fact не создаём.
    """
    await m.remember_user_fact("user_fact:existing", "меня зовут Вася")

    # блок: сдвиг тика, чтобы последующий heat не подавлялся как
    # «тот же тик» (см. _apply_heat: last_heat_tick == total_ticks).
    m._total_ticks += 1

    fake = DedupFakeLLM(verdict="ДУБЛЬ")
    tool = RememberFactTool(memory_manager=m, llm_client=fake)

    result = await tool._execute(fact="меня зовут Вася")

    assert result["status"] == "already_exists"

    # блок: второй user_fact НЕ создан.
    # почему: смысл дедупа — не плодить дубликаты. Регресс здесь
    # означал бы, что фикс сломал основную функцию инструмента.
    user_facts = [k for k in m._hot if k.startswith("user_fact:")]
    assert user_facts == ["user_fact:existing"]
