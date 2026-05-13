from typing import Any, Dict, List

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    analyst_id: str | None = Field(default=None, examples=["product_competitive_analyst"])
    scenario_id: str | None = Field(default=None, examples=["market_trend_analysis"])
    query: str = Field(..., examples=["请对某产品开展系统性的竞争分析"])

    @model_validator(mode="after")
    def require_entry_id(self) -> "ChatRequest":
        if not (self.analyst_id or self.scenario_id):
            raise ValueError("analyst_id is required")
        return self

    @property
    def entry_id(self) -> str:
        return self.analyst_id or self.scenario_id or ""


class ChatResponse(BaseModel):
    session_id: str | None = None
    answer: str
    answer_sections: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[str]
    citation_details: List[Dict[str, Any]] = Field(default_factory=list)
    modules_used: List[str]
    skills_used: List[str]
    confidence: float
    follow_up_questions: List[str]
    reflection: Dict[str, Any] = Field(default_factory=dict)
    task_plan: List[Dict[str, Any]] = Field(default_factory=list)
    execution_steps: List[Dict[str, Any]] = Field(default_factory=list)
