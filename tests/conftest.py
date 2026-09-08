"""Общая конфигурация тестов.

Глушит косметический шум "--- Logging error --- I/O operation on
closed file": setup_logging() внутри тестов вешает консольный хендлер
на захваченный pytest'ом поток; pytest закрывает поток в конце теста,
а хендлер остаётся до конца сессии и спотыкается об закрытое.
Лечение: ни один logging-хендлер не переживает границу теста.
"""

import logging

import pytest


def _purge_handlers() -> None:
    root = logging.getLogger()
    named = [logging.getLogger(n) for n in logging.root.manager.loggerDict]
    for lg in [root, *named]:
        for h in list(lg.handlers):
            lg.removeHandler(h)
    root.addHandler(logging.NullHandler())


def pytest_configure(config) -> None:
    _purge_handlers()


@pytest.fixture(autouse=True)
def _clean_logging_boundary():
    """Срабатывает после КАЖДОГО теста: смывает хендлеры журнала."""
    yield
    _purge_handlers()
