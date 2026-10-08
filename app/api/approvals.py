"""Local single-user review endpoints. Actor is audit data, not authentication."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.tools.approvals import ApprovalConflictError, ApprovalNotFoundError, ApprovalStatus


router = APIRouter(prefix="/api/web/approvals", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    target_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    actor: str = Field(default="local-user", min_length=1, max_length=128)
    reason: str = Field(default="", max_length=2000)


def _service(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    gateway = getattr(runtime, "tool_gateway", None)
    service = getattr(gateway, "approval_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail={"code": "APPROVAL_UNAVAILABLE", "message": "Approval service is unavailable"})
    return service


@router.get("")
def list_approvals(request: Request, session_id: str | None = Query(default=None, max_length=128),
                   turn_id: str | None = Query(default=None, max_length=128),
                   status: ApprovalStatus | None = "pending", limit: int = Query(default=100, ge=1, le=1000)):
    records = _service(request).list_requests(session_id=session_id, turn_id=turn_id, status=status, limit=limit)
    return {"approvals": [record.public_dict() for record in records], "count": len(records)}


@router.get("/{approval_id}")
def get_approval(request: Request, approval_id: str = Path(min_length=1, max_length=128)):
    try:
        return _service(request).get(approval_id).public_dict()
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": "Approval request does not exist"}) from exc


@router.post("/{approval_id}/decision")
def decide_approval(body: ApprovalDecisionRequest, request: Request,
                    approval_id: str = Path(min_length=1, max_length=128)):
    try:
        record = _service(request).decide(approval_id, body.decision, target_hash=body.target_hash,
                                          actor=body.actor, reason=body.reason)
        return record.public_dict()
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": "Approval request does not exist"}) from exc
    except ApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc
