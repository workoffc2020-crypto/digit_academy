"""Encrypts Aadhaar / ID numbers at rest (Fernet). The key lives in <data>/data/secret.key and is included in backups
(otherwise a restored backup could not decrypt its own data). Anyone who has the key AND the database can read the
numbers - so keep backups on trusted media."""
import os

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, key_path: str):
        self.key_path = key_path
        if os.path.isfile(key_path):
            with open(key_path, "rb") as f:
                key = f.read().strip()
        else:
            key = Fernet.generate_key()
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            with open(key_path, "wb") as f:
                f.write(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        self._f = Fernet(key)

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode("utf-8")).decode("ascii") if text else ""

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._f.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""


def mask(number: str, keep: int = 4) -> str:
    n = "".join(ch for ch in (number or "") if ch.isalnum())
    if not n:
        return ""
    return "X" * max(len(n) - keep, 0) + n[-keep:]
