"""Versioned, additive migrations in one SQLite transaction.

This module never uses user_version (shared with other application modules)
or executescript (which may implicitly commit an existing transaction).
"""
import sqlite3

MIGRATIONS = [
    (1, (
        """CREATE TABLE research_projects (
            project_id TEXT PRIMARY KEY, data_json TEXT NOT NULL)""",
        """CREATE TABLE research_artifacts (
            artifact_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES research_projects(project_id),
            sha256 TEXT NOT NULL, data_json TEXT NOT NULL,
            UNIQUE(project_id, artifact_id), UNIQUE(project_id, sha256))""",
        """CREATE TABLE research_papers (
            paper_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES research_projects(project_id),
            source TEXT NOT NULL, source_id TEXT NOT NULL,
            UNIQUE(project_id, paper_id), UNIQUE(project_id, source, source_id))""",
        """CREATE TABLE research_paper_versions (
            paper_version_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            paper_id TEXT NOT NULL, version TEXT NOT NULL, artifact_id TEXT,
            data_json TEXT NOT NULL,
            UNIQUE(project_id, paper_version_id), UNIQUE(paper_id, version),
            FOREIGN KEY(project_id, paper_id) REFERENCES research_papers(project_id, paper_id),
            FOREIGN KEY(project_id, artifact_id) REFERENCES research_artifacts(project_id, artifact_id))""",
        """CREATE TABLE research_paper_reads (
            project_id TEXT NOT NULL, paper_version_id TEXT NOT NULL, page INTEGER NOT NULL CHECK(page > 0),
            PRIMARY KEY(project_id, paper_version_id, page),
            FOREIGN KEY(project_id, paper_version_id) REFERENCES research_paper_versions(project_id, paper_version_id))""",
        """CREATE TABLE research_evidence (
            evidence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            paper_version_id TEXT NOT NULL, page INTEGER NOT NULL CHECK(page > 0),
            quote_sha256 TEXT NOT NULL, data_json TEXT NOT NULL,
            UNIQUE(project_id, evidence_id),
            UNIQUE(project_id, paper_version_id, page, quote_sha256),
            FOREIGN KEY(project_id, paper_version_id) REFERENCES research_paper_versions(project_id, paper_version_id))""",
        """CREATE TABLE research_claims (
            claim_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES research_projects(project_id),
            data_json TEXT NOT NULL, UNIQUE(project_id, claim_id))""",
        """CREATE TABLE research_claim_evidence (
            project_id TEXT NOT NULL, claim_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation IN ('supports','refutes','background')),
            PRIMARY KEY(project_id, claim_id, evidence_id, relation),
            FOREIGN KEY(project_id, claim_id) REFERENCES research_claims(project_id, claim_id),
            FOREIGN KEY(project_id, evidence_id) REFERENCES research_evidence(project_id, evidence_id))""",
    )),
    (2, (
        """CREATE TABLE research_queries (
            query_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES research_projects(project_id),
            created_at TEXT NOT NULL, data_json TEXT NOT NULL,
            UNIQUE(project_id, query_id))""",
        """CREATE TABLE research_search_embeddings (
            project_id TEXT NOT NULL REFERENCES research_projects(project_id),
            resource TEXT NOT NULL CHECK(resource IN ('papers','evidence','claims')),
            record_id TEXT NOT NULL, model TEXT NOT NULL,
            text_sha256 TEXT NOT NULL, vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(project_id, resource, record_id, model))""",
        """CREATE INDEX research_queries_project_created
            ON research_queries(project_id, created_at, query_id)""",
    )),
]


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS research_schema_migrations (version INTEGER PRIMARY KEY)"
        )
        applied = [row[0] for row in connection.execute(
            "SELECT version FROM research_schema_migrations ORDER BY version"
        )]
        expected = [version for version, _ in MIGRATIONS]
        if applied != expected[:len(applied)]:
            raise ValueError("不支持的科研数据库迁移版本，拒绝修改")
        for version, statements in MIGRATIONS[len(applied):]:
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO research_schema_migrations VALUES (?)", (version,))
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
