from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    scenario_id: str = Field(..., examples=["market_trend_analysis"])
    query: str = Field(..., examples=["达仁堂最近5年市场上的新品表现如何？"])


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
