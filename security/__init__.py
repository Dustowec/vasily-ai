"""Публичный API SSRF-защиты для всех инструментов, ходящих в сеть.

Инструменты импортируют отсюда только две функции:
    preflight_check(url, trusted_hosts=None, is_ip_forbidden=None) -> reason | None
    safe_connector(trusted_hosts=None, is_ip_forbidden=None, **kw) -> aiohttp.TCPConnector

trusted_hosts — опциональный список хостов, которые инструмент считает
доверенными (например, web_search передаёт сюда хост своего бэкенда).
Это НЕ то же самое, что Config.ssrf_allowed_hosts: тот список глобальный
и применяется ко всем инструментам.
"""

import ipaddress
from urllib.parse import urlparse

import aiohttp

from security import allowlist
from security.ip_rules import forbidden_reason
from security.resolver import SafeResolver

# блок: общие константы валидации URL.
ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = {80, 443, 8080, 8443}
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048


def safe_connector(
    trusted_hosts=None, is_ip_forbidden=None, **kwargs
) -> aiohttp.TCPConnector:
    """Фабрика коннектора с SSRF-защитой и отключённым DNS-кэшем.

    use_dns_cache=False обязателен: иначе aiohttp может закэшировать
    результат до проверки SafeResolver.

    trusted_hosts — список hostname'ов, доверенных для данного инструмента.
    is_ip_forbidden — callable для подмены IP-проверки (для тестов).
    """
    return aiohttp.TCPConnector(
        resolver=SafeResolver(
            trusted_hosts=trusted_hosts,
            is_ip_forbidden=is_ip_forbidden,
        ),
        use_dns_cache=False,
        force_close=True,
        **kwargs,
    )


def preflight_check(
    url: str,
    trusted_hosts=None,
    is_ip_forbidden=None,
) -> str | None:
    """Статическая (без DNS) проверка URL. Возвращает reason или None.

    Порядок: scheme → embedded_credentials → hostname → allow-list
    (ручной + trusted_hosts) → localhost → literal IP → port.
    DNS-проверка происходит позже, внутри SafeResolver.

    trusted_hosts — список hostname'ов, доверенных для вызывающего
    инструмента (web_search передаёт хост своего бэкенда).
    is_ip_forbidden — callable для подмены IP-проверки (для тестов).
    """
    # блок: дефолтный чекер, если кастомный не передан.
    check_ip = (
        is_ip_forbidden
        if is_ip_forbidden is not None
        else (lambda ip: forbidden_reason(ip) is not None)
    )

    # блок: нормализуем trusted_hosts в set[str] lowercase один раз.
    # почему: иначе проверка внутри горячего цикла будет O(N).
    trusted_set = {h.lower() for h in (trusted_hosts or []) if h}

    # блок: парсинг URL. Ошибка парсинга = блок.
    try:
        parsed = urlparse(url)
    except Exception:
        return "parse_error"

    # блок: scheme-whitelist. Первым — от него зависит DEFAULT_PORTS ниже.
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return f"blocked_scheme:{parsed.scheme}"

    # блок: embedded_credentials из DSH.
    # почему: http://user:pass@host/ утекает креды в логи и в DNS.
    if parsed.username or parsed.password:
        return "embedded_credentials"

    hostname = parsed.hostname
    if not hostname:
        return "no_hostname"

    # блок: порт. Если явно не указан — берём дефолт по схеме.
    try:
        port = parsed.port
    except ValueError:
        return "invalid_port"
    if port is None:
        port = DEFAULT_PORTS[parsed.scheme.lower()]

    h = hostname.lower()

    # блок: trusted_hosts от инструмента — пропускаем всё для этого хоста.
    # почему: web_search знает, что его бэкенд из конфига доверенный.
    # web_scraper не передаёт trusted_hosts, поэтому loopback для него
    # остаётся запрещённым.
    if h in trusted_set:
        return None

    # блок: ручной allowlist из Config — тоже пропускает всё.
    # почему: это осознанное решение пользователя, глобально доверенное.
    if allowlist.is_host_allowed(hostname):
        return None

    # блок: literal localhost.
    if h in ("localhost", "localhost.localdomain"):
        return "localhost"

    # блок: попытка распарсить hostname как literal IP.
    lit = h.strip("[]")
    try:
        ip = ipaddress.ip_address(lit)
    except ValueError:
        # блок: это hostname, не IP.
        # почему: без DNS мы не знаем, куда он резолвится — порт проверяем
        # безусловно. IP-проверка будет внутри SafeResolver.
        if port not in ALLOWED_PORTS:
            return f"blocked_port:{port}"
        return None

    # блок: это literal IP. Порядок: allow-list IP → IP-проверка → порт.
    if allowlist.is_ip_allowed(ip):
        return None

    if check_ip(ip):
        reason = forbidden_reason(ip) or "forbidden"
        return f"literal_ip:{reason}:{ip}"

    # блок: loopback пропускает порт-whitelist (для тестовых stub-серверов).
    if not ip.is_loopback and port not in ALLOWED_PORTS:
        return f"blocked_port:{port}"

    return None
