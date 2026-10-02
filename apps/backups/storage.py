"""Narrow S3-compatible adapter: fixed UUID objects, conditional writes, no list/delete.

SigV4 signed payloads over TLS. HTTP bodies/errors never enter application logs.
"""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from apps.backups.archive import CHUNK
from apps.backups.config import BackupFailure


class BackupStorage(Protocol):
    async def download(self, backup_id: UUID, file) -> bool: ...

    async def put_if_absent(self, backup_id: UUID, file) -> None: ...


def digest(file):
    file.seek(0)
    sha = hashlib.sha256()
    while data := file.read(CHUNK):
        sha.update(data)
    file.seek(0)
    return sha.hexdigest()


class S3Storage:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    def signed(self, method, backup_id, payload_hash, extra=None, now=None):
        c = self.config
        now = now or datetime.now(UTC)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = f"/{c.bucket}/{c.prefix}/{backup_id}.ttcpbk"
        uri = quote(path, safe="/-_.~")
        headers = {
            "host": urlsplit(c.endpoint).netloc,
            "x-amz-date": stamp,
            "x-amz-content-sha256": payload_hash,
            **(extra or {}),
        }
        if c.session_token:
            headers["x-amz-security-token"] = c.session_token.get_secret_value()
        names = ";".join(sorted(headers))
        canonical = "\n".join(
            [
                method,
                uri,
                "",
                "".join(f"{k}:{' '.join(headers[k].split())}\n" for k in sorted(headers)),
                names,
                payload_hash,
            ]
        )
        scope = f"{day}/{c.region}/s3/aws4_request"
        message = (
            f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        )
        key = ("AWS4" + c.secret_key.get_secret_value()).encode()
        for part in (day, c.region, "s3", "aws4_request"):
            key = hmac.digest(key, part.encode(), "sha256")
        signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={c.access_key.get_secret_value()}/{scope}, "
            f"SignedHeaders={names}, Signature={signature}"
        )
        return c.endpoint + uri, headers

    async def download(self, backup_id, file):
        url, headers = self.signed("GET", backup_id, hashlib.sha256(b"").hexdigest())
        try:
            async with self.client.stream("GET", url, headers=headers) as response:
                if response.status_code == 404:
                    return False
                self.check(response.status_code)
                file.seek(0)
                file.truncate()
                size = 0
                async for data in response.aiter_raw(CHUNK):
                    size += len(data)
                    if size > self.config.max_encrypted_bytes:
                        raise BackupFailure()
                    file.write(data)
                file.seek(0)
                return True
        except httpx.HTTPError:
            raise BackupFailure("backup_unknown", retryable=True) from None

    async def put_if_absent(self, backup_id, file):
        sha = digest(file)
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        url, headers = self.signed(
            "PUT",
            backup_id,
            sha,
            {
                "if-none-match": "*",
                "content-length": str(size),
                "content-type": "application/octet-stream",
            },
        )

        async def body():
            while data := file.read(CHUNK):
                yield data
                await asyncio.sleep(0)

        try:
            # stream prevents unbounded provider error bodies from being buffered.
            async with self.client.stream("PUT", url, headers=headers, content=body()) as response:
                if response.status_code == 412:
                    return  # Caller downloads/authenticates the winner.
                self.check(response.status_code)
        except httpx.HTTPError:
            raise BackupFailure("backup_unknown", retryable=True) from None

    @staticmethod
    def check(status):
        if 200 <= status < 300:
            return
        if status in (408, 409, 429) or status >= 500:
            raise BackupFailure("backup_unknown", retryable=True)
        raise BackupFailure()
