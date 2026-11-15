from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .sources import SeedId

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Plan(StrictModel):
    status: Literal['ready', 'needs_clarification']
    workflow: str
    country: Literal['Malaysia']
    sectors: list[str]
    keywords: list[str]
    seed_ids: list[SeedId]
    questions: list[str]
    assumptions: list[str]

class Signal(StrictModel):
    document_id: str
    organisation: str | None
    organisation_specific: bool
    sector: str | None
    safety_topic: str
    trigger_type: Literal['guidance','initiative','tender','project','hiring','operational_change','enforcement','certification','esg_index','incident','association_activity','other']
    workflow: str
    urgency: str | None
    excerpt: str = Field(min_length=15, max_length=800)
    need_hypothesis: str
    buyer_role_hypothesis: str | None
    confidence: float = Field(ge=0, le=1)
    ambiguity_flags: list[str]
    recommended_next_step: Literal['research_more','human_review','no_action']

class Extraction(StrictModel):
    signals: list[Signal]
    abstention_reason: str | None

class ChunkExtraction(Extraction):
    coverage_complete: bool
    coverage_note: str | None

class ReviewItem(StrictModel):
    signal_id: str
    verdict: Literal['supported', 'uncertain', 'unsupported']
    issues: list[str]

class SemanticReview(StrictModel):
    reviews: list[ReviewItem]

class RunRequest(StrictModel):
    prompt: str = Field(min_length=5, max_length=6000)
    mode: Literal['live','demo'] = 'live'
    additional_urls: list[str] = Field(default_factory=list, max_length=4)
    additional_source_class: Literal['company_owned','industry_procurement'] = 'company_owned'
    source_ids: list[SeedId] = Field(default_factory=list, max_length=len(SeedId))
    permissions_confirmed: bool = False
    max_pages: int = Field(default=20, ge=1, le=32)
    max_depth: int = Field(default=3, ge=0, le=3)
    links_per_page: int = Field(default=3, ge=1, le=5)
    max_chunks: int = Field(default=24, ge=1, le=100)
    budget_usd: float = Field(default=1.0, gt=0, le=10, allow_inf_nan=False)
    semantic_review: Literal['off', 'risk'] = 'risk'
    ocr: bool = False
    source_query: str | None = Field(default=None, max_length=200)
    resume_run_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
