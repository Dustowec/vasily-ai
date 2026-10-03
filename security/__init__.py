"""Публичный API SSRF-защиты для всех инструментов, ходящих в сеть.

Инструменты импортируют отсюда только две функции:
    preflight_check(url, is_ip_forbidden=None) -> reason | None
    safe_connector(is_ip_forbidden=None)       -> aiohttp.TCPConnector

Внутренности (allowlist, ip_rules, SafeResolver) — не публичны.
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


def safe_connector(is_ip_forbidden=None, **kwargs) -> aiohttp.TCPConnector:
    """Фабрика коннектора с SSRF-защитой и отключённым DNS-кэшем.

    use_dns_cache=False обязателен: иначе aiohttp может закэшировать
    результат до проверки SafeResolver.

    is_ip_forbidden — опциональный callable для подмены IP-проверки
    (нужен тестам, которые разрешают локальный stub-сервер).
    """
    return aiohttp.TCPConnector(
        resolver=SafeResolver(is_ip_forbidden=is_ip_forbidden),
        use_dns_cache=False,
        force_close=True,
        **kwargs,
    )


def preflight_check(url: str, is_ip_forbidden=None) -> str | None:
    """Статическая (без DNS) проверка URL. Возвращает reason или None.

    Порядок: scheme → embedded_credentials → hostname → allow-list →
    localhost → literal IP → port.
    DNS-проверка происходит позже, внутри SafeResolver.

    is_ip_forbidden — опциональный callable(ip) -> bool для подмены
    IP-проверки (тесты с локальным stub-сервером).
    """
    # блок: дефолтный чекер, если кастомный не передан.
    check_ip = (
        is_ip_forbidden
        if is_ip_forbidden is not None
        else (lambda ip: forbidden_reason(ip) is not None)
    )

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

    # блок: allow-list хоста — ДО всех блокировок.
    # почему: если хост явно разрешён (ручной список или backend URL из
    # конфига), пропускаем и localhost, и IP-проверки, и порт-whitelist.
    if allowlist.is_host_allowed(hostname):
        return None

    # блок: literal localhost.
    h = hostname.lower()
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
    # почему: в проде loopback сюда не доходит (заблокирован выше),
    # только через allow-list. А там порт уже пропущен.
    if not ip.is_loopback and port not in ALLOWED_PORTS:
        return f"blocked_port:{port}"

    return None
