"""Encryption/storage/process boundaries; no production credentials or services."""

import asyncio
import io
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from apps.backups.archive import EncryptArchive, plaintext, validate
from apps.backups.config import BackupConfig, BackupFailure, protected_config
from apps.backups.postgres import PostgresTools
from apps.backups.storage import S3Storage


@pytest.fixture
def backup_config():
    return BackupConfig(
        endpoint="https://s3.test",
        region="us-east-1",
        bucket="ttcp-test",
        access_key="test-access",
        secret_key="test-secret",
        active_key_id="test-v1",
        encryption_keys={"test-v1": "ab" * 32},
        postgres={
            "host": "localhost",
            "database": "test",
            "user": "test",
            "password": "test-pg-secret",
            "sslmode": "disable",
        },
    )


def archive(config, identity, data=b"PGDMP" + b"private-database-value" * 100000):
    file = io.BytesIO()
    writer = EncryptArchive(file, config, identity)
    for pos in range(0, len(data), 65536):
        writer.update(data[pos : pos + 65536])
    writer.finish()
    return file


def test_encrypted_round_trip_key_rotation_bounds_and_identity(backup_config):
    identity = uuid4()
    file = archive(backup_config, identity)
    original = file.getvalue()
    assert b"private-database-value" not in original
    validate(file, backup_config, identity)
    assert b"".join(plaintext(file, backup_config, identity)).startswith(b"PGDMPprivate")
    assert archive(backup_config, identity).getvalue() != original  # Fresh nonce per dump.
    rotated = backup_config.model_copy(update={"active_key_id": "new"})
    validate(file, rotated, identity)  # Retained old key reads older archives.
    with pytest.raises(BackupFailure):
        validate(file, backup_config, uuid4())
    for pos in (0, 9, 40, 70, len(original) - 1):
        damaged = bytearray(original)
        damaged[pos] ^= 1
        with pytest.raises(BackupFailure):
            validate(io.BytesIO(damaged), backup_config, identity)
    for damaged in (original[:-1], original[:-16], original + b"extra"):
        with pytest.raises(BackupFailure):
            validate(io.BytesIO(damaged), backup_config, identity)
    small = backup_config.model_copy(update={"max_plain_bytes": 1024})
    with pytest.raises(BackupFailure):
        validate(file, small, identity)
    with pytest.raises(BackupFailure):
        archive(small, identity)
    with pytest.raises(BackupFailure):
        validate(archive(backup_config, identity, b"not-a-pg-dump"), backup_config, identity)


def test_protected_config_fails_closed_without_echoing_values(tmp_path, backup_config):
    path = tmp_path / "config.json"
    payload = backup_config.model_dump(mode="json")
    payload["secret_key"] = "test-only-secret-must-not-appear"
    payload["encryption_keys"] = {"test-v1": "ab" * 32}
    payload["postgres"]["password"] = "test-pg-secret"
    payload["access_key"] = "test-access"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    assert protected_config(str(path)).key("test-v1") == bytes.fromhex("ab" * 32)
    for endpoint in ("http://s3.test", "https://user:password@s3.test", "https://s3.test/path"):
        payload["endpoint"] = endpoint
        path.write_text(json.dumps(payload))
        with pytest.raises(BackupFailure) as error:
            protected_config(str(path))
        assert str(error.value) == "backup_unavailable"
    if os.name == "posix":
        path.chmod(0o644)
        with pytest.raises(BackupFailure):
            protected_config(str(path))
    with pytest.raises(BackupFailure):
        protected_config(str(tmp_path / "missing"))


class ObjectStore:
    """HTTP peer for adapter tests, with conditional writes and lost replies."""

    def __init__(self):
        self.objects, self.requests = {}, []
        self.lose_reply = False
        self.status = None

    async def handle(self, request):
        body = await request.aread()
        self.requests.append(request)
        assert "AWS4-HMAC-SHA256" in request.headers["authorization"]
        import hashlib

        assert request.headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()
        if self.status:
            return httpx.Response(self.status, content=b"provider-secret-never-log")
        path = request.url.path
        if request.method == "GET":

            class Bytes(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield self.data

            if path not in self.objects:
                return httpx.Response(404)
            stream = Bytes()
            stream.data = self.objects[path]
            return httpx.Response(200, stream=stream)
        assert request.method == "PUT" and request.headers["if-none-match"] == "*"
        if path in self.objects:
            return httpx.Response(412)
        self.objects[path] = body
        if self.lose_reply:
            self.lose_reply = False
            raise httpx.ReadError("provider-secret-never-log")
        return httpx.Response(200)


async def test_s3_conditional_write_readback_signed_payload_and_error_redaction(backup_config):
    peer = ObjectStore()
    storage = S3Storage(backup_config, transport=httpx.MockTransport(peer.handle))
    identity = uuid4()
    try:
        with tempfile.TemporaryFile() as file:
            assert not await storage.download(identity, file)
            source = archive(backup_config, identity)
            await storage.put_if_absent(identity, source)
            await storage.put_if_absent(identity, archive(backup_config, identity))
            assert await storage.download(identity, file)
            validate(file, backup_config, identity)
            assert len(peer.objects) == 1
            assert next(iter(peer.objects.values())) == source.getvalue()
            peer.status = 403
            with pytest.raises(BackupFailure) as error:
                await storage.download(identity, file)
            assert error.value.code == "backup_failed" and not error.value.retryable
            peer.status = 503
            with pytest.raises(BackupFailure) as error:
                await storage.download(identity, file)
            assert error.value.code == "backup_unknown" and error.value.retryable
            peer.status = 307
            with pytest.raises(BackupFailure):
                await storage.download(identity, file)
            assert len({request.url.host for request in peer.requests}) == 1
    finally:
        await storage.close()


async def test_signature_fixed_vector(backup_config):
    storage = S3Storage(backup_config)
    from uuid import UUID

    url, headers = storage.signed(
        "GET",
        UUID(int=0),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        now=datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert url == "https://s3.test/ttcp-test/ttcp/00000000-0000-0000-0000-000000000000.ttcpbk"
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in headers["authorization"]
    assert headers["x-amz-date"] == "20261002T000000Z"
    # Independently cross-checked against botocore 1.43.107 S3SigV4Auth.
    assert headers["authorization"].endswith(
        "Signature=acad0c3cf5dd61c2be7688eed49ed2609491d1a30bbaca18aff9dcf8e891ce36"
    )
    import hashlib

    _, headers = storage.signed(
        "PUT",
        UUID(int=0),
        hashlib.sha256(b"test-ciphertext").hexdigest(),
        {"if-none-match": "*", "content-length": "15", "content-type": "application/octet-stream"},
        now=datetime(2026, 10, 2, tzinfo=UTC),
    )
    assert headers["authorization"].endswith(
        "Signature=a73586d7eae5310c9538c1a2fdd4475a843fd1430dd2086ec483746daaa2137b"
    )
    await storage.close()


async def test_restore_authenticates_entire_archive_before_target_access(
    backup_config, monkeypatch
):
    tools = PostgresTools()
    accesses = []

    async def reject(*args):
        accesses.append(True)
        raise AssertionError("Target must not be accessed")

    monkeypatch.setattr(tools, "empty_target", reject)
    monkeypatch.setattr(tools, "spawn", reject)
    identity = uuid4()
    damaged = archive(backup_config, identity).getvalue()[:-1]
    with pytest.raises(BackupFailure):
        await tools.restore(io.BytesIO(damaged), backup_config, identity, backup_config.postgres)
    assert accesses == []


async def test_dump_interruption_kills_subprocess_without_plaintext_file(
    backup_config, monkeypatch
):
    class Process:
        returncode = None
        killed = False

        async def wait(self):
            self.returncode = -9
            return -9

        def kill(self):
            self.killed = True

        class Output:
            async def read(self, size):
                if process.killed:
                    return b""
                raise asyncio.CancelledError()

        stdout = Output()

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    tools = PostgresTools()
    monkeypatch.setattr(tools, "spawn", spawn)
    with tempfile.TemporaryFile() as file, pytest.raises(asyncio.CancelledError):
        await tools.dump(file, backup_config, uuid4())
    assert process.killed and process.returncode == -9


async def test_cleanup_drains_paused_subprocess_pipe():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys,time; sys.stdout.buffer.write(b'x'*1048576); "
        "sys.stdout.flush(); time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await process.stdout.read(1)
        await asyncio.sleep(0.1)  # Fill the reader and OS pipe before cleanup.
        await asyncio.wait_for(PostgresTools().cleanup(process), timeout=5)
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()
