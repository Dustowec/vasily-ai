"""Чистые правила проверки IP. Без зависимостей, кроме ipaddress."""

import ipaddress

CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def forbidden_reason(ip):
    """Вернуть причину блокировки или None.

    Порядок важен: более специфичные категории — первыми,
    чтобы логи несли actionable reason (loopback > private).
    """
    # блок: IPv4-mapped IPv6 — разворачиваем в IPv4 и рекурсивно проверяем.
    # почему: ::ffff:127.0.0.1 должен ловиться как loopback, а не как «просто IPv6».
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        inner = forbidden_reason(ip.ipv4_mapped)
        if inner:
            return f"ipv4_mapped:{inner}"

    # блок: специфичные категории по одной, от узких к широким.
    # почему: 127.0.0.1 на Python 3.14 — и loopback, и private.
    # Хотим в логе «loopback» (точнее), а не «private» (размыто).
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_link_local:
        return "link_local"

    # блок: CGNAT (RFC 6598) явной проверкой.
    # почему: на Python 3.14 is_private для 100.64.0.0/10 = False (проверено в web_scraper).
    # Проверяем ДО is_reserved, чтобы cgnat всегда побеждал.
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT_NETWORK:
        return "cgnat"

    # блок: широкие категории последними.
    # почему: если ничего специфичного не сработало, это финальный барьер.
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"

    return None
