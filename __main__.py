"""Точка входа для python -m vasily_ai."""

import asyncio
import sys
from pathlib import Path


def main():
    """Запуск агента с правильным путём."""
    # Добавляем папку проекта в sys.path
    project_root = Path(__file__).parent
    sys.path.insert(0, str(project_root))

    from core.agent import main as agent_main

    try:
        asyncio.run(agent_main())
    except KeyboardInterrupt:
        print("\nAgent interrupted by user")


if __name__ == "__main__":
    main()
