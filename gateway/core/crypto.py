"""Шифрование значений Mapping Store (раздел 8 ТЗ) на чистой stdlib.

Ядро шлюза не тянет внешних зависимостей (важно для on-prem/air-gapped
инсталляций), поэтому вместо AES используется классическая конструкция
из криптографических примитивов stdlib:

  * потоковый шифр: keystream = HMAC-SHA256(enc_key, nonce || counter)
    в режиме счётчика (PRF-CTR) — стандартная схема построения потокового
    шифра из PRF;
  * целостность: encrypt-then-MAC, tag = HMAC-SHA256(mac_key, nonce || ct);
  * ключи шифрования и MAC разведены через доменную сепарацию от master-key;
  * nonce — 16 случайных байт (os.urandom) на каждое шифрование.

Данные эфемерны (TTL ~минуты, только в памяти процесса), защищаемая
модель угроз — случайное попадание значений в дампы/своп, а не
долговременное хранение.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

NONCE_SIZE = 16
TAG_SIZE = 32


def generate_key() -> str:
    """Сгенерировать master-key (base64, 32 байта) — формат AIGATE_MAPPING_ENCRYPTION_KEY."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


class SecretBox:
    def __init__(self, key: str | bytes = ""):
        if not key:
            master = os.urandom(32)
        elif isinstance(key, bytes):
            master = key
        else:
            try:
                master = base64.urlsafe_b64decode(key.encode())
            except Exception as e:
                raise ValueError("Ключ должен быть в base64 (см. generate_key)") from e
        if len(master) < 16:
            raise ValueError("Ключ слишком короткий: нужно >= 16 байт")
        # доменная сепарация: независимые ключи шифрования и аутентификации
        self._enc_key = hashlib.sha256(master + b"|aigate-enc").digest()
        self._mac_key = hashlib.sha256(master + b"|aigate-mac").digest()

    def _keystream_xor(self, nonce: bytes, data: bytes) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < len(data):
            block = hmac.new(
                self._enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256
            ).digest()
            out.extend(block)
            counter += 1
        return bytes(a ^ b for a, b in zip(data, out))

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = os.urandom(NONCE_SIZE)
        ciphertext = self._keystream_xor(nonce, plaintext)
        tag = hmac.new(self._mac_key, nonce + ciphertext, hashlib.sha256).digest()
        return nonce + ciphertext + tag

    def decrypt(self, blob: bytes) -> bytes:
        if len(blob) < NONCE_SIZE + TAG_SIZE:
            raise ValueError("Повреждённый шифртекст")
        nonce = blob[:NONCE_SIZE]
        ciphertext = blob[NONCE_SIZE:-TAG_SIZE]
        tag = blob[-TAG_SIZE:]
        expected = hmac.new(self._mac_key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("Проверка целостности не пройдена")
        return self._keystream_xor(nonce, ciphertext)
