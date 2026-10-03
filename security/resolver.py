"""SafeResolver: resolve-then-validate внутри aiohttp.TCPConnector."""

import asyncio
import ipaddress
import socket

from aiohttp.abc import AbstractResolver

from core.logging_config import get_logger
from security import allowlist
from security.ip_rules import forbidden_reason

logger = get_logger("security", name="resolver")

# блок: значение по умолчанию для DNS-таймаута.
# почему: 3 секунды — выше любого домашнего резолвера, ниже любого
# пользовательского ожидания. Может быть переопределён в __init__
# (для тестов) или через атрибут модуля.
DNS_TIMEOUT_SECONDS = 3.0


class SafeResolver(AbstractResolver):
    """DNS-резолвер, блокирующий private/loopback/link-local на уровне connect.

    Работает внутри коннектора aiohttp, поэтому проверенный IP и IP,
    к которому реально подключились — один и тот же. Закрывает DNS rebinding.

    Allow-list проверяется ДО forbidden_reason: если хост или IP явно
    разрешён — пропускаем. Это единственный способ ходить на localhost.

    Параметры:
        is_ip_forbidden — callable(ip) -> bool, по умолчанию —
            forbidden_reason(ip) is not None. Позволяет тестам
            подменять проверку, чтобы разрешить локальный stub-сервер.
        dns_timeout — таймаут DNS-резолва в секундах.
    """

    def __init__(self, is_ip_forbidden=None, dns_timeout=None):
        # блок: сохраняем кастомный чекер IP или используем дефолтный.
        # почему: тесты monkeypatch'ат WebScraperTool._is_private_ip —
        # нужно, чтобы резолвер видел подмену.
        self._is_forbidden = (
            is_ip_forbidden
            if is_ip_forbidden is not None
            else (lambda ip: forbidden_reason(ip) is not None)
        )
        # блок: таймаут — параметром, чтобы тесты могли его уменьшить.
        self._dns_timeout = (
            dns_timeout if dns_timeout is not None else DNS_TIMEOUT_SECONDS
        )

    async def resolve(self, host, port=0, family=socket.AF_INET):
        # блок: резолв DNS с таймаутом.
        # почему: зависший DNS без таймаута = зависший запрос.
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM),
                timeout=self._dns_timeout,
            )
        except TimeoutError as e:
            logger.warning("ssrf_dns_timeout", host=host)
            raise OSError(f"DNS timeout for {host}") from e
        except socket.gaierror as e:
            logger.warning("ssrf_dns_failure", host=host, error=str(e))
            raise OSError(f"DNS failure for {host}: {e}") from e

        # блок: один раз выясняем, разрешён ли хост целиком.
        # почему: allowlist.is_host_allowed делает lookup в Config;
        # нет смысла повторять это для каждого IP из результата.
        host_allowed = allowlist.is_host_allowed(host)

        # блок: перебор всех адресов, возвращённых DNS.
        # почему: хост может резолвиться в несколько IP (IPv4+IPv6, CDN).
        # Если хоть один запрещён — блокируем запрос целиком.
        result = []
        for fam, _type, proto, _canon, sockaddr in infos:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            # блок: allow-list пропускает всё.
            # почему: если хост или конкретный IP явно разрешён в конфиге,
            # не проверяем forbidden_reason вообще.
            if host_allowed or allowlist.is_ip_allowed(ip):
                result.append(
                    {
                        "hostname": host,
                        "host": ip_str,
                        "port": port,
                        "family": fam,
                        "proto": proto,
                        "flags": 0,
                    }
                )
                continue

            # блок: кастомная или дефолтная проверка запрещённых IP.
            # почему: forbidden_reason даёт точную причину для лога;
            # если кастомный чекер вернул True без reason — пишем "forbidden".
            if self._is_forbidden(ip):
                reason = forbidden_reason(ip) or "forbidden"
                logger.warning(
                    "ssrf_blocked_at_resolve",
                    host=host,
                    ip=ip_str,
                    reason=reason,
                )
                raise OSError(f"Blocked IP {ip_str} ({reason})")

            result.append(
                {
                    "hostname": host,
                    "host": ip_str,
                    "port": port,
                    "family": fam,
                    "proto": proto,
                    "flags": 0,
                }
            )

        if not result:
            raise OSError(f"No usable addresses for {host}")
        return result

    async def close(self):
        # блок: no-op по контракту AbstractResolver.
        return None
