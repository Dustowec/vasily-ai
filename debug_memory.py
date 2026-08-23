"""Диагностика памяти: что лежит в Cold и как ищется контекст."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.agent import AgentCore
from core.config import Config


async def main():
    config = Config.load()
    config.log_dir = Path("logs")
    agent = AgentCore(config)
    await agent.initialize()

    print("=" * 60)
    print("СОДЕРЖИМОЕ ПАМЯТИ")
    print("=" * 60)

    # 1. Смотрим, что в Cold
    cold = agent.memory._cold
    print(f"\nCold записей: {len(cold)}")
    for key, entry in cold.items():
        print(f"\nКлюч: {key}")
        print(f"  Score: {entry.get('score')}")
        print(f"  Summary: {entry.get('summary', '')[:200]}...")
        print(f"  Created: {entry.get('created_at')}")

    # 2. Смотрим, что в Hot
    hot = agent.memory._hot
    print(f"\nHot записей: {len(hot)}")
    for key, entry in hot.items():
        print(f"\nКлюч: {key}")
        print(f"  Score: {entry.get('score')}")
        print(f"  Value: {str(entry.get('value', ''))[:200]}...")

    # 3. Проверяем build_context на пустой запрос
    print("\n" + "=" * 60)
    print("build_context('')")
    print("=" * 60)
    context = await agent.memory.build_context("")
    print(f"Контекст:\n{context[:500] if context else '(пусто)'}")

    # 4. Проверяем build_context на запрос 'погода'
    print("\n" + "=" * 60)
    print("build_context('погода')")
    print("=" * 60)
    context = await agent.memory.build_context("погода")
    print(f"Контекст:\n{context[:500] if context else '(пусто)'}")

    # 5. Проверяем build_context на запрос 'город'
    print("\n" + "=" * 60)
    print("build_context('город')")
    print("=" * 60)
    context = await agent.memory.build_context("город")
    print(f"Контекст:\n{context[:500] if context else '(пусто)'}")

    await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
