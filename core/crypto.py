"""CryptoProvider — задел на будущее.

Сейчас это заглушка (NoOp), которая ничего не шифрует.
При необходимости заменяется на реальную криптографию (Fernet + PBKDF2).
"""


class NoOpCrypto:
    """Заглушка: не шифрует и не расшифровывает данные."""

    def encrypt(self, data: bytes) -> bytes:
        """Возвращает данные без изменений."""
        return data

    def decrypt(self, data: bytes) -> bytes:
        """Возвращает данные без изменений."""
        return data


# Для обратной совместимости: можно использовать как класс, так и экземпляр
DEFAULT_CRYPTO = NoOpCrypto()
