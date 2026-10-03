"""Allow-list для SSRF-защиты.

Явно разрешённые хосты/IP, к которым МОЖНО ходить, даже если они
выглядят как private/loopback.

Источник — Config.ssrf_allowed_hosts / Config.ssrf_allowed_ips (ручной список).

Auto-trust бэкендов из конфига УБРАН: он слишком широкий — добавлял
literal-IP 127.0.0.1 в глобальный whitelist, и любой инструмент
(включая web_scraper с URL от LLM) мог ходить на loopback.

Инструменты с доверенным бэкендом (web_search) передают trusted_hosts
явно в preflight_check() / safe_connector() — см. security/__init__.py.
"""

import ipaddress


def _load() -> tuple[set[str], set]:
    """Загружает ручной allow-list из Config."""
    # блок: импорт Config внутри функции.
    # почему: core.config импортирует core.logging_config, который лениво
    # импортирует core.config — верхнеуровневый импорт мог бы дать цикл.
    from core.config import Config

    config = Config.load()

    # блок: нормализация хостов (lowercase + strip).
    # почему: urlparse().hostname уже lowercase, а пользователь может
    # написать "MyNas.local" — надо, чтобы совпало.
    hosts = {h.lower().strip() for h in (config.ssrf_allowed_hosts or []) if h}

    # блок: парсинг IP-строк в объекты ipaddress.
    # почему: сравнение объектов корректнее строк — учтёт
    # "::1" == "0:0:0:0:0:0:0:1".
    ips: set = set()
    for raw in config.ssrf_allowed_ips or []:
        try:
            ips.add(ipaddress.ip_address(raw.strip()))
        except (ValueError, AttributeError):
            # блок: молча пропускаем мусор.
            # почему: одна опечатка в конфиге не должна ронять агент.
            pass

    return hosts, ips


def is_host_allowed(hostname: str) -> bool:
    """True, если hostname явно разрешён в ручном allow-list."""
    hosts, _ = _load()
    return hostname.lower() in hosts


def is_ip_allowed(ip) -> bool:
    """True, если IP явно разрешён в ручном allow-list."""
    _, ips = _load()
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return False
    return ip in ips
