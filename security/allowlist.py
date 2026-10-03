"""Allow-list для SSRF-защиты.

Явно разрешённые хосты/IP, к которым МОЖНО ходить, даже если они
выглядят как private/loopback.

Источники (объединяются):
1. Config.ssrf_allowed_hosts / Config.ssrf_allowed_ips — ручной список.
2. Config.searxng_url, Config.danbooru_url — URL бэкендов, объявленные
   в конфиге. Они доверенные по определению: если пользователь прописал
   "http://192.168.1.50:8888/" — значит он хочет туда ходить.

Кэша нет: Config.load() вызывается на каждый чек. Это даёт корректную
изоляцию тестов (monkeypatch на Config.load виден сразу) и стоит
микросекунды на локальном агенте.
"""

import ipaddress
from urllib.parse import urlparse


def _load() -> tuple[set[str], set]:
    """Загружает allow-list из Config."""
    # блок: импорт Config внутри функции.
    # почему: core.config импортирует core.logging_config, а тот в свою
    # очередь лениво импортирует core.config. Верхнеуровневый импорт
    # мог бы дать цикл при нетипичном порядке загрузки.
    from core.config import Config

    config = Config.load()

    # блок: ручные хосты из конфига (lowercase + strip).
    # почему: urlparse().hostname уже lowercase, а пользователь может
    # написать "MyNas.local" — надо, чтобы совпало.
    hosts = {h.lower().strip() for h in (config.ssrf_allowed_hosts or []) if h}

    # блок: auto-trust URL-ов бэкендов, объявленных в конфиге.
    # почему: если searxng_url указывает на 127.0.0.1 или NAS — это
    # осознанное решение пользователя, а не URL от LLM. Прогонять его
    # через SSRF-фильтр бессмысленно, а инструмент ломает.
    for backend_url in (config.searxng_url, config.danbooru_url):
        if not backend_url:
            continue
        try:
            h = urlparse(backend_url).hostname
            if h:
                hosts.add(h.lower())
        except Exception:
            # блок: молча пропускаем мусор в URL.
            # почему: одна кривая строка в конфиге не должна ронять агент.
            pass

    # блок: парсинг IP-строк в объекты ipaddress.
    # почему: сравнение объектов корректнее строк — учтёт
    # "::1" == "0:0:0:0:0:0:0:1".
    ips: set = set()
    for raw in config.ssrf_allowed_ips or []:
        try:
            ips.add(ipaddress.ip_address(raw.strip()))
        except (ValueError, AttributeError):
            pass

    return hosts, ips


def is_host_allowed(hostname: str) -> bool:
    """True, если hostname явно разрешён (ручной список или бэкенд из конфига)."""
    hosts, _ = _load()
    return hostname.lower() in hosts


def is_ip_allowed(ip) -> bool:
    """True, если IP явно разрешён в конфиге (принимает str или ipaddress)."""
    _, ips = _load()
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return False
    return ip in ips
