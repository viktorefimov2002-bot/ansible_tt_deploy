"""Versioned AES-256-GCM envelope; only ciphertext is ever written to a file.

Header and backup UUID are authenticated. A trailing GCM tag authenticates the
whole gzip/custom PostgreSQL archive, including truncation. Restore verifies a
complete first pass before a second pass feeds pg_restore.
"""

import asyncio
import os
import struct
import zlib
from collections.abc import Iterator
from uuid import UUID

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from apps.backups.config import BackupFailure

MAGIC = b"TTCPBK01"
HEADER = struct.Struct("!8s16s32s12s")
CHUNK = 64 * 1024


class EncryptArchive:
    def __init__(self, file, config, backup_id: UUID):
        self.file, self.config = file, config
        nonce = os.urandom(12)
        header = HEADER.pack(
            MAGIC, backup_id.bytes, config.active_key_id.encode().ljust(32, b"\0"), nonce
        )
        self.cipher = Cipher(
            algorithms.AES(config.key(config.active_key_id)), modes.GCM(nonce)
        ).encryptor()
        self.cipher.authenticate_additional_data(header)
        self.compressor = zlib.compressobj(level=6, wbits=31)
        self.plain = 0
        self.write(header)

    def write(self, data):
        if self.file.tell() + len(data) > self.config.max_encrypted_bytes:
            raise BackupFailure()
        self.file.write(data)

    def update(self, data):
        self.plain += len(data)
        if self.plain > self.config.max_plain_bytes:
            raise BackupFailure()
        self.write(self.cipher.update(self.compressor.compress(data)))

    def finish(self):
        self.write(self.cipher.update(self.compressor.flush()))
        self.write(self.cipher.finalize())
        self.write(self.cipher.tag)
        self.file.seek(0)


def plaintext(file, config, backup_id: UUID) -> Iterator[bytes]:
    """Private streaming decoder. Consumers must validate a complete first pass."""
    try:
        file.seek(0, 2)
        size = file.tell()
        if not HEADER.size + 16 < size <= config.max_encrypted_bytes:
            raise ValueError()
        file.seek(0)
        header = file.read(HEADER.size)
        magic, identity, key_id, nonce = HEADER.unpack(header)
        if magic != MAGIC or identity != backup_id.bytes:
            raise ValueError()
        key_id = key_id.rstrip(b"\0").decode("ascii")
        file.seek(-16, 2)
        tag = file.read(16)
        file.seek(HEADER.size)
        cipher = Cipher(algorithms.AES(config.key(key_id)), modes.GCM(nonce, tag)).decryptor()
        cipher.authenticate_additional_data(header)
        decoder = zlib.decompressobj(wbits=31)
        remaining, total, prefix = size - HEADER.size - 16, 0, b""
        while remaining:
            data = file.read(min(CHUNK, remaining))
            if not data:
                raise ValueError()
            remaining -= len(data)
            compressed = cipher.update(data)
            while compressed:
                decoded = decoder.decompress(compressed, CHUNK)
                compressed = decoder.unconsumed_tail
                total += len(decoded)
                prefix = (prefix + decoded)[:5]
                if total > config.max_plain_bytes or decoder.unused_data:
                    raise ValueError()
                if decoded:
                    yield decoded
        cipher.finalize()  # Verifies the tag before validate() can succeed.
        if not decoder.eof or decoder.unused_data or prefix != b"PGDMP":
            raise ValueError()
    except BackupFailure:
        raise
    except Exception:
        raise BackupFailure() from None


def validate(file, config, backup_id):
    for _ in plaintext(file, config, backup_id):
        pass


async def verify_archive(file, config, backup_id):
    for _ in plaintext(file, config, backup_id):
        await asyncio.sleep(0)
