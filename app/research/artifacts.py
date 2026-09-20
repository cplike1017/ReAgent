"""Immutable project-scoped file snapshots. Only sandbox imports are accepted."""
import hashlib
import os
import tempfile
from pathlib import Path, PureWindowsPath

from app.research.errors import ResearchError, ResearchNotFound
from app.research.models import Artifact
from app.research.repository import new_id
from app.session.repository import utc_now

MEDIA_TYPES = {
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
    ".csv": "text/csv", ".json": "application/json", ".jsonl": "application/x-ndjson",
}


class ArtifactStore:
    def __init__(self, root: str, sandbox: str, max_bytes: int):
        self.root = Path(root).resolve()
        self.sandbox = Path(sandbox).resolve()
        self.max_bytes = max_bytes

    def _path(self, project_id: str, name: str) -> Path:
        path = (self.root / project_id / name).resolve()
        if not path.is_relative_to(self.root):
            raise ResearchError("产物路径越界")
        return path

    def import_file(self, project_id: str, relative_path: str) -> Artifact:
        supplied = Path(relative_path)
        if supplied.is_absolute() or PureWindowsPath(relative_path).drive:
            raise ResearchError("仅接受沙箱内相对路径")
        source = (self.sandbox / supplied).resolve()
        if not source.is_relative_to(self.sandbox):
            raise ResearchError("源文件路径越界")
        if not source.is_file():
            raise ResearchNotFound()
        media_type = MEDIA_TYPES.get(source.suffix.lower())
        if media_type is None:
            raise ResearchError("只支持 PDF、UTF-8 文本、Markdown、CSV、JSON 和 JSONL")
        directory = self._path(project_id, ".")
        directory.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(suffix=".part", dir=directory)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
                while chunk := input_file.read(65536):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ResearchError("资料超过大小限制", code="artifact_too_large", status=413)
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            sha256 = digest.hexdigest()
            destination = self._path(project_id, sha256)
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != sha256:
                    raise ResearchError("已有产物校验失败", code="artifact_integrity", status=409)
            else:
                os.replace(temporary, destination)
            return Artifact(
                artifact_id=new_id("artifact"), project_id=project_id,
                name=source.name, media_type=media_type, sha256=sha256,
                size_bytes=size, created_at=utc_now(),
            )
        finally:
            temporary.unlink(missing_ok=True)

    def verified_path(self, artifact: Artifact) -> Path:
        path = self._path(artifact.project_id, artifact.sha256)
        if not path.is_file() or path.stat().st_size != artifact.size_bytes:
            raise ResearchError("产物缺失或大小不一致", code="artifact_integrity", status=409)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(65536):
                digest.update(chunk)
        if digest.hexdigest() != artifact.sha256:
            raise ResearchError("产物哈希校验失败", code="artifact_integrity", status=409)
        return path
