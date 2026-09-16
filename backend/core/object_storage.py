"""Minimal asynchronous S3-compatible storage client for local MinIO.

The project already depends on httpx but intentionally does not add an S3 SDK.
Only the operations needed by resume processing are implemented here.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from backend.config import Settings, get_settings


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        endpoint = self.settings.minio_endpoint.rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ObjectStorageError("MINIO_ENDPOINT 必须是有效的 HTTP(S) 地址")
        self.endpoint = endpoint
        self.host = parsed.netloc
        self.transport = transport

    @staticmethod
    def _sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    def _headers(self, method: str, path: str, payload: bytes, now: datetime) -> dict[str, str]:
        payload_hash = hashlib.sha256(payload).hexdigest()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        canonical_headers = (
            f"host:{self.host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [method, path, "", canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{self.settings.minio_region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        date_key = self._sign(
            ("AWS4" + self.settings.minio_secret_key).encode("utf-8"), date_stamp
        )
        region_key = self._sign(date_key, self.settings.minio_region)
        service_key = self._sign(region_key, "s3")
        signing_key = self._sign(service_key, "aws4_request")
        signature = hmac.new(
            signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.settings.minio_access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return {
            "Host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": authorization,
        }

    async def _request(self, method: str, path: str, payload: bytes = b"") -> httpx.Response:
        now = datetime.now(timezone.utc)
        headers = self._headers(method, path, payload, now)
        async with httpx.AsyncClient(
            timeout=30.0, transport=self.transport
        ) as client:
            return await client.request(
                method, f"{self.endpoint}{path}", headers=headers, content=payload
            )

    def _bucket_path(self) -> str:
        return "/" + quote(self.settings.minio_bucket, safe="")

    def _object_path(self, object_key: str) -> str:
        key = object_key.strip("/")
        if not key:
            raise ObjectStorageError("对象路径不能为空")
        return f"{self._bucket_path()}/{quote(key, safe='/')}"

    async def ensure_bucket(self) -> None:
        path = self._bucket_path()
        response = await self._request("HEAD", path)
        if response.status_code == 200:
            return
        if response.status_code != 404:
            raise ObjectStorageError(f"检查对象存储桶失败（HTTP {response.status_code}）")
        response = await self._request("PUT", path)
        if response.status_code not in {200, 204, 409}:
            raise ObjectStorageError(f"创建对象存储桶失败（HTTP {response.status_code}）")

    async def put_file(self, object_key: str, local_path: str) -> None:
        await self.ensure_bucket()
        payload = Path(local_path).read_bytes()
        response = await self._request("PUT", self._object_path(object_key), payload)
        if response.status_code not in {200, 201, 204}:
            raise ObjectStorageError(f"上传对象失败（HTTP {response.status_code}）")

    async def get_file(self, object_key: str, local_path: str) -> None:
        response = await self._request("GET", self._object_path(object_key))
        if response.status_code != 200:
            raise ObjectStorageError(f"下载对象失败（HTTP {response.status_code}）")
        Path(local_path).write_bytes(response.content)

    async def delete(self, object_key: str) -> None:
        response = await self._request("DELETE", self._object_path(object_key))
        if response.status_code not in {200, 204, 404}:
            raise ObjectStorageError(f"删除对象失败（HTTP {response.status_code}）")


def get_object_storage() -> ObjectStorageClient:
    return ObjectStorageClient()
