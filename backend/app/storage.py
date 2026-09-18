"""Object storage: Amazon S3 or the local filesystem.

Key layout (mirrors the spec's suggested S3 structure):
    uploads/{document_id}.pdf
    processed/{document_id}/chunks.json
    processed/{document_id}/topic_map.json
    processed/{document_id}/pages/{n}.png
    teaching-plans/{document_id}/{topic_id}.json
    audio/{document_id}/{reel_id}.mp3
    videos/{document_id}/{reel_id}.mp4
"""
from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Optional

from .config import settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root / "storage"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        self._path(key).write_bytes(data)
        return key

    def put_file(self, key: str, path: Path, content_type: Optional[str] = None) -> str:
        shutil.copyfile(path, self._path(key))
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def get_to_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), path)
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def local_path(self, key: str) -> Path:
        return self._path(key)

    def url(self, key: str) -> str:
        return f"{settings.PUBLIC_BASE_URL}/media/{key}"


class S3Storage:
    def __init__(self, bucket: str, prefix: str, region: str):
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client("s3", region_name=region)

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def put_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=ct)
        return key

    def put_file(self, key: str, path: Path, content_type: Optional[str] = None) -> str:
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.upload_file(str(path), self.bucket, self._k(key), ExtraArgs={"ContentType": ct})
        return key

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def get_to_file(self, key: str, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, self._k(key), str(path))
        return path

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def local_path(self, key: str) -> Path:
        # materialise into the work dir so ffmpeg / pdfium can read it
        p = settings.WORK_DIR / "s3cache" / key
        if not p.exists():
            self.get_to_file(key, p)
        return p

    def url(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._k(key)}, ExpiresIn=6 * 3600
        )


def _build():
    if settings.STORAGE == "s3":
        return S3Storage(settings.S3_BUCKET, settings.S3_PREFIX, settings.AWS_REGION)
    return LocalStorage(settings.DATA_DIR)


storage = _build()


def put_json(key: str, obj: Any) -> str:
    return storage.put_bytes(key, json.dumps(obj, indent=2).encode("utf-8"), "application/json")


def get_json(key: str) -> Any:
    return json.loads(storage.get_bytes(key).decode("utf-8"))


# key helpers ---------------------------------------------------------------
def k_upload(doc_id: str) -> str:
    return f"uploads/{doc_id}.pdf"


def k_chunks(doc_id: str) -> str:
    return f"processed/{doc_id}/chunks.json"


def k_topic_map(doc_id: str) -> str:
    return f"processed/{doc_id}/topic_map.json"


def k_page_image(doc_id: str, page: int) -> str:
    return f"processed/{doc_id}/pages/{page}.png"


def k_plan(doc_id: str, topic_id: str) -> str:
    return f"teaching-plans/{doc_id}/{topic_id}.json"


def k_audio(doc_id: str, reel_id: str) -> str:
    return f"audio/{doc_id}/{reel_id}.mp3"


def k_video(doc_id: str, reel_id: str) -> str:
    return f"videos/{doc_id}/{reel_id}.mp4"


def k_thumb(doc_id: str, reel_id: str) -> str:
    return f"videos/{doc_id}/{reel_id}.jpg"


def k_combined(doc_id: str) -> str:
    return f"videos/{doc_id}/combined.mp4"
