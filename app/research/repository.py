"""SQLite research records; transactions preserve project-scoped references."""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.research.errors import ResearchConflict, ResearchNotFound
from app.research.migrations import apply_migrations
from app.research.models import (
    Artifact, Claim, ClaimCreate, EvidenceSpan, PaperImport, PaperVersion,
    ProjectCreate, ResearchProject,
)
from app.session.repository import sqlite_path_from_url, utc_now

RESOURCES = {
    "artifacts": ("research_artifacts", "artifact_id", Artifact),
    "papers": ("research_paper_versions", "paper_version_id", PaperVersion),
    "evidence": ("research_evidence", "evidence_id", EvidenceSpan),
    "claims": ("research_claims", "claim_id", Claim),
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SQLiteResearchRepository:
    def __init__(self, database_url: str):
        path = sqlite_path_from_url(database_url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            apply_migrations(self._conn)
        except BaseException:
            self._conn.close()
            raise

    @contextmanager
    def _write(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def close(self):
        with self._lock:
            self._conn.close()

    def create_project(self, request: ProjectCreate) -> ResearchProject:
        project = ResearchProject(
            **request.model_dump(), project_id=new_id("project"), created_at=utc_now(),
        )
        with self._write():
            self._conn.execute("INSERT INTO research_projects VALUES (?, ?)",
                               (project.project_id, project.model_dump_json()))
        return project

    def get_project(self, project_id: str) -> ResearchProject:
        with self._lock:
            row = self._conn.execute(
                "SELECT data_json FROM research_projects WHERE project_id=?", (project_id,),
            ).fetchone()
            if row is None:
                raise ResearchNotFound()
            return ResearchProject.model_validate_json(row["data_json"])

    def list_projects(self, limit: int = 50, offset: int = 0) -> list[ResearchProject]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data_json FROM research_projects ORDER BY rowid LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [ResearchProject.model_validate_json(row["data_json"]) for row in rows]

    def _decode(self, kind, row):
        record = RESOURCES[kind][2].model_validate_json(row["data_json"])
        if kind == "papers":
            record.read_pages = [item[0] for item in self._conn.execute(
                "SELECT page FROM research_paper_reads WHERE project_id=? AND paper_version_id=? ORDER BY page",
                (record.project_id, record.paper_version_id),
            )]
            record.read_scope = "selected_pages" if record.read_pages else "metadata"
        return record

    def get(self, project_id: str, kind: str, record_id: str):
        table, key, _ = RESOURCES[kind]
        with self._lock:
            row = self._conn.execute(
                f"SELECT data_json FROM {table} WHERE project_id=? AND {key}=?",
                (project_id, record_id),
            ).fetchone()
            if row is None:
                raise ResearchNotFound()
            return self._decode(kind, row)

    def list_records(self, project_id: str, kind: str, limit: int = 50, offset: int = 0):
        table, _, _ = RESOURCES[kind]
        with self._lock:
            self.get_project(project_id)
            rows = self._conn.execute(
                f"SELECT data_json FROM {table} WHERE project_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
            return [self._decode(kind, row) for row in rows]

    def save_artifact(self, artifact: Artifact) -> Artifact:
        with self._write():
            self.get_project(artifact.project_id)
            row = self._conn.execute(
                "SELECT data_json FROM research_artifacts WHERE project_id=? AND sha256=?",
                (artifact.project_id, artifact.sha256),
            ).fetchone()
            if row is not None:
                return self._decode("artifacts", row)
            self._conn.execute("INSERT INTO research_artifacts VALUES (?, ?, ?, ?)", (
                artifact.artifact_id, artifact.project_id, artifact.sha256, artifact.model_dump_json(),
            ))
        return artifact

    def report_snapshot(self, project_id: str):
        # A read transaction also protects against writers using other SQLite
        # connections. Per-method locking alone cannot keep references coherent.
        with self._lock, self._conn:
            self._conn.execute("BEGIN")
            project = self.get_project(project_id)
            records = {
                kind: self.list_records(project_id, kind, limit=1001)
                for kind in ("papers", "artifacts", "evidence", "claims")
            }
            return project, records

    def import_paper(self, project_id: str, request: PaperImport) -> PaperVersion:
        with self._write():
            self.get_project(project_id)
            if request.artifact_id is not None:
                self.get(project_id, "artifacts", request.artifact_id)
            paper = self._conn.execute(
                "SELECT paper_id FROM research_papers WHERE project_id=? AND source=? AND source_id=?",
                (project_id, request.source, request.source_id),
            ).fetchone()
            paper_id = paper["paper_id"] if paper else new_id("paper")
            if paper is None:
                self._conn.execute("INSERT INTO research_papers VALUES (?, ?, ?, ?)",
                                   (paper_id, project_id, request.source, request.source_id))
            row = self._conn.execute(
                "SELECT data_json FROM research_paper_versions WHERE paper_id=? AND version=?",
                (paper_id, request.version),
            ).fetchone()
            if row is not None:
                current = self._decode("papers", row)
                for field in PaperImport.model_fields:
                    if field not in {"artifact_id", "content_scope"} and getattr(current, field) != getattr(request, field):
                        raise ResearchConflict("相同论文版本的元数据不同，请核对来源版本")
                if request.artifact_id is not None:
                    if current.artifact_id is not None and (
                        current.artifact_id != request.artifact_id or current.content_scope != request.content_scope
                    ):
                        raise ResearchConflict("论文版本已经绑定资料，不允许覆盖")
                    current.artifact_id = request.artifact_id
                    current.content_scope = request.content_scope
                    self._conn.execute(
                        "UPDATE research_paper_versions SET artifact_id=?, data_json=? WHERE paper_version_id=?",
                        (current.artifact_id, current.model_dump_json(), current.paper_version_id),
                    )
                return current
            record = PaperVersion(
                **request.model_dump(), project_id=project_id, paper_id=paper_id,
                paper_version_id=new_id("version"), created_at=utc_now(),
            )
            self._conn.execute("INSERT INTO research_paper_versions VALUES (?, ?, ?, ?, ?, ?)", (
                record.paper_version_id, project_id, paper_id, record.version,
                record.artifact_id, record.model_dump_json(),
            ))
        return record

    def record_read(self, project_id: str, paper_version_id: str, page: int):
        with self._write():
            self.get(project_id, "papers", paper_version_id)
            self._conn.execute("INSERT OR IGNORE INTO research_paper_reads VALUES (?, ?, ?)",
                               (project_id, paper_version_id, page))

    def save_evidence(self, record: EvidenceSpan) -> EvidenceSpan:
        with self._write():
            paper = self.get(record.project_id, "papers", record.paper_version_id)
            if paper.artifact_id != record.artifact_id:
                raise ResearchConflict("证据与论文绑定的资料不一致")
            row = self._conn.execute(
                """SELECT data_json FROM research_evidence
                   WHERE project_id=? AND paper_version_id=? AND page=? AND quote_sha256=?""",
                (record.project_id, record.paper_version_id, record.page, record.quote_sha256),
            ).fetchone()
            if row is not None:
                return self._decode("evidence", row)
            self._conn.execute("INSERT INTO research_evidence VALUES (?, ?, ?, ?, ?, ?)", (
                record.evidence_id, record.project_id, record.paper_version_id,
                record.page, record.quote_sha256, record.model_dump_json(),
            ))
            self._conn.execute("INSERT OR IGNORE INTO research_paper_reads VALUES (?, ?, ?)",
                               (record.project_id, record.paper_version_id, record.page))
        return record

    def create_claim(self, project_id: str, request: ClaimCreate) -> Claim:
        record = Claim(**request.model_dump(), project_id=project_id,
                       claim_id=new_id("claim"), created_at=utc_now())
        with self._write():
            self.get_project(project_id)
            for link in record.evidence_links:
                self.get(project_id, "evidence", link.evidence_id)
            self._conn.execute("INSERT INTO research_claims VALUES (?, ?, ?)",
                               (record.claim_id, project_id, record.model_dump_json()))
            for link in record.evidence_links:
                self._conn.execute("INSERT INTO research_claim_evidence VALUES (?, ?, ?, ?)",
                                   (project_id, record.claim_id, link.evidence_id, link.relation))
        return record
