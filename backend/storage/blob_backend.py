"""
二进制对象(原图/附件/STEP/STL/SVG/DXF)存储后端,可插拔:
  - LocalBlobBackend: 落本地磁盘 DATA_DIR/<key>(默认,零依赖,行为与之前一致);
  - S3BlobBackend   : S3/MinIO 对象存储(私有化集群/多节点共享),boto3 懒加载。

为什么需要它: 元数据已可进 SQL(meta_backend),但二进制还钉在单机磁盘 —— 多副本/
多节点部署时无法共享。本层把"二进制读写"与介质解耦。

关键约束: CAD 内核(OCCT/CadQuery)只能往**本地文件路径**写。因此即便用 S3,几何文件
也先写本地工作目录(DATA_DIR 作为缓存),生成后经 sync_dir 上传;读取/对外服务时若本地
缓存缺失再从 S3 拉取(local_path materialize)。即"本地缓存 + 远端对象库"。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional


class BlobBackend:
    """key 形如 "<project_id>/source.png" / "<project_id>/geometry/P1.stl"。"""

    def put_bytes(self, key: str, data: bytes) -> None: ...
    def put_file(self, key: str, src: Path) -> None: ...
    def get_bytes(self, key: str) -> Optional[bytes]: ...
    def local_path(self, key: str) -> Optional[Path]:
        """返回一个可直接读取/FileResponse 的本地路径(必要时从远端拉取);不存在返回 None。"""
        ...
    def exists(self, key: str) -> bool: ...
    def ensure_local_dir(self, prefix: str) -> Path:
        """返回供 CAD 写入的本地工作目录(DATA_DIR/prefix)。"""
        ...
    def sync_dir(self, prefix: str) -> None:
        """把本地工作目录下新产生的文件同步到远端(Local 为空操作)。"""
        ...


# --------------------------------------------------------------------------- #
# 本地磁盘(默认)
# --------------------------------------------------------------------------- #
class LocalBlobBackend(BlobBackend):
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def _p(self, key: str) -> Path:
        return self.data_dir / key

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key: str, src: Path) -> None:
        p = self._p(key)
        if Path(src).resolve() == p.resolve():
            return  # 已在目标位置(CAD 直接写入的情形)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)

    def get_bytes(self, key: str) -> Optional[bytes]:
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def local_path(self, key: str) -> Optional[Path]:
        p = self._p(key)
        return p if p.exists() else None

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def ensure_local_dir(self, prefix: str) -> Path:
        d = self._p(prefix)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sync_dir(self, prefix: str) -> None:
        pass  # 本地即最终存储,无需同步


# --------------------------------------------------------------------------- #
# S3 / MinIO(私有化集群共享存储)
# --------------------------------------------------------------------------- #
class S3BlobBackend(BlobBackend):
    def __init__(self, cache_dir: Path, *, bucket: str, endpoint_url: str = "",
                 access_key: str = "", secret_key: str = "", region: str = "",
                 prefix: str = ""):
        import boto3  # 懒加载: 仅选用 s3 时才需要

        self.cache_dir = cache_dir
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            region_name=region or None,
        )
        # 确保桶存在(MinIO 友好)
        try:
            self.client.head_bucket(Bucket=bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=bucket)
            except Exception:
                pass

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def _cache(self, key: str) -> Path:
        return self.cache_dir / key

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._cache(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)  # 写本地缓存
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def put_file(self, key: str, src: Path) -> None:
        self.client.upload_file(str(src), self.bucket, self._k(key))

    def get_bytes(self, key: str) -> Optional[bytes]:
        p = self.local_path(key)
        return p.read_bytes() if p else None

    def _remote_exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def local_path(self, key: str) -> Optional[Path]:
        p = self._cache(key)
        if p.exists():
            return p
        if self._remote_exists(key):
            p.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, self._k(key), str(p))
            return p
        return None

    def exists(self, key: str) -> bool:
        return self._cache(key).exists() or self._remote_exists(key)

    def ensure_local_dir(self, prefix: str) -> Path:
        d = self._cache(prefix)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def sync_dir(self, prefix: str) -> None:
        base = self._cache(prefix)
        if not base.exists():
            return
        for f in base.rglob("*"):
            if f.is_file():
                key = f.relative_to(self.cache_dir).as_posix()
                self.client.upload_file(str(f), self.bucket, self._k(key))


# --------------------------------------------------------------------------- #
# 工厂(单例)
# --------------------------------------------------------------------------- #
_blob: Optional[BlobBackend] = None


def get_blob_backend() -> BlobBackend:
    global _blob
    if _blob is not None:
        return _blob
    from ..config import (
        BLOB_BACKEND, DATA_DIR, S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT_URL,
        S3_PREFIX, S3_REGION, S3_SECRET_KEY,
    )

    if BLOB_BACKEND.lower() in ("s3", "minio"):
        _blob = S3BlobBackend(
            DATA_DIR, bucket=S3_BUCKET, endpoint_url=S3_ENDPOINT_URL,
            access_key=S3_ACCESS_KEY, secret_key=S3_SECRET_KEY,
            region=S3_REGION, prefix=S3_PREFIX,
        )
    else:
        _blob = LocalBlobBackend(DATA_DIR)
    return _blob
