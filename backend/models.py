# backend/models.py
from pydantic import BaseModel, field_validator


class RecommendRequest(BaseModel):
    career: str

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
