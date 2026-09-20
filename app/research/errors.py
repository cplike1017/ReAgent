"""Errors exposed by the local research API."""


class ResearchError(Exception):
    def __init__(self, message: str, *, code: str = "research_invalid", status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


class ResearchNotFound(ResearchError):
    def __init__(self):
        super().__init__("科研记录不存在或不属于该项目", code="research_not_found", status=404)


class ResearchConflict(ResearchError):
    def __init__(self, message: str):
        super().__init__(message, code="research_conflict", status=409)
