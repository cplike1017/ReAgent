"""Local-user research foundation API. Project scoping is not authentication."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app.research.errors import ResearchError
from app.research.models import ArtifactImport, ClaimCreate, EvidenceCreate, PaperImport, ProjectCreate
from app.research.service import ResearchService

router = APIRouter(prefix="/api/research", tags=["research"])
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=2**63 - 1)]
Resource = Literal["artifacts", "papers", "evidence", "claims"]


def get_service(request: Request) -> ResearchService:
    service = getattr(request.app.state, "research_service", None)
    if service is None:
        raise HTTPException(503, detail={
            "code": "research_disabled", "message": "请配置 RESEARCH_ENABLED=true 并重启服务",
        })
    return service


Service = Annotated[ResearchService, Depends(get_service)]


async def research_error_handler(request: Request, error: ResearchError):
    return JSONResponse(status_code=error.status, content={
        "detail": {"code": error.code, "message": str(error)},
    })


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, service: Service):
    return service.repository.create_project(body)


@router.get("/projects")
def list_projects(service: Service, limit: Limit = 50, offset: Offset = 0):
    return {"items": service.repository.list_projects(limit, offset)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, service: Service):
    return service.repository.get_project(project_id)


@router.post("/projects/{project_id}/artifacts/import")
def import_artifact(project_id: str, body: ArtifactImport, service: Service):
    return service.import_artifact(project_id, body.path)


@router.get("/projects/{project_id}/artifacts/{artifact_id}/content")
def artifact_content(project_id: str, artifact_id: str, service: Service):
    artifact = service.repository.get(project_id, "artifacts", artifact_id)
    path = service.artifacts.verified_path(artifact)
    return FileResponse(path, media_type=artifact.media_type, filename=artifact.name,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/projects/{project_id}/papers/import")
def import_paper(project_id: str, body: PaperImport, service: Service):
    return service.repository.import_paper(project_id, body)


@router.get("/projects/{project_id}/papers/{paper_version_id}/text")
def read_page(project_id: str, paper_version_id: str, service: Service,
              page: Annotated[int, Query(ge=1)] = 1,
              offset: Offset = 0, limit: Annotated[int, Query(ge=1, le=20000)] = 20000):
    return service.read_page(project_id, paper_version_id, page, offset, limit)


@router.post("/projects/{project_id}/evidence", status_code=201)
def create_evidence(project_id: str, body: EvidenceCreate, service: Service):
    return service.create_evidence(project_id, body)


@router.post("/projects/{project_id}/claims", status_code=201)
def create_claim(project_id: str, body: ClaimCreate, service: Service):
    return service.repository.create_claim(project_id, body)


@router.get("/projects/{project_id}/report")
def export_report(project_id: str, service: Service):
    return Response(service.export_report(project_id), media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="research-report.md"'})


@router.get("/projects/{project_id}/{resource}")
def list_records(project_id: str, resource: Resource, service: Service,
                 limit: Limit = 50, offset: Offset = 0):
    return {"items": service.repository.list_records(project_id, resource, limit, offset)}


@router.get("/projects/{project_id}/{resource}/{record_id}")
def get_record(project_id: str, resource: Resource, record_id: str, service: Service):
    return service.repository.get(project_id, resource, record_id)
