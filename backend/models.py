# backend/models.py
from pydantic import BaseModel, field_validator


class RecommendRequest(BaseModel):
    career: str
    seed: int | None = None

    @field_validator("career")
    @classmethod
    def career_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("career must not be empty")
        return v.strip()


class ReasonPoint(BaseModel):
    term: str
    detail: str


class Reason(BaseModel):
    lead: str
    points: list[ReasonPoint]


class CourseCard(BaseModel):
    course_id: str
    name: str
    department: str
    teacher: str
    credits: float
    reason: Reason
    syllabus_url: str


class CourseGroups(BaseModel):
    core: list[CourseCard]
    supporting: list[CourseCard]
    extended: list[CourseCard]


class RecommendResponse(BaseModel):
    career: str
    groups: CourseGroups
    latency_ms: int
    seed: int = 0
    notice: str | None = None  # 清單外職涯：說明推薦依據可轉移能力


class NoMatchResponse(BaseModel):
    career: str
    no_match: bool = True
    message: str


# ===== Q&A mode =====

class QaRequest(BaseModel):
    question: str
    session_id: str | None = None

    @field_validator("question")
    @classmethod
    def question_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be empty")
        return v.strip()


class Citation(BaseModel):
    course_id: str
    name: str
    department: str
    teacher: str
    syllabus_url: str


class QaResponse(BaseModel):
    session_id: str
    turn_number: int
    answer: str
    citations: list[Citation]
    followup_suggestions: list[str]
    latency_ms: int
