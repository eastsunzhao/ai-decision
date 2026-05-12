from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RetrievalDocument(BaseModel):
    id: str
    score: float = 0.0
    source: str = "elasticsearch"
    text: str
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResearchState(BaseModel):
    scenario_id: str
    query: str
    task_plan: List[Dict[str, Any]] = Field(default_factory=list)
    execution_steps: List[Dict[str, Any]] = Field(default_factory=list)
    scenario_config: Optional[Dict[str, Any]] = None
    route: Optional[str] = None
    rewritten_query: Optional[str] = None
    retrieval_results: List[RetrievalDocument] = Field(default_factory=list)
    related_entities: List[str] = Field(default_factory=list)
    reflection_iterations: int = 0
    reflection_trace: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_coverage: Dict[str, bool] = Field(default_factory=dict)
    chosen_instruction_modules: List[str] = Field(default_factory=list)
    chosen_skill: Optional[str] = None
    skill_output: Optional[Dict[str, Any]] = None
    answer: Optional[str] = None
    answer_sections: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    citation_details: List[Dict[str, Any]] = Field(default_factory=list)
    modules_used: List[str] = Field(default_factory=list)
    skills_used: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    follow_up_questions: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
