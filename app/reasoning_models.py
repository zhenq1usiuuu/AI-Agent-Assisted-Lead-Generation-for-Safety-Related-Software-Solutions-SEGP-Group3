"""Strict output contract for the Module 3 LLM reasoning stage."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReasonedAccount(StrictModel):
    account_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    recommendation: Literal["shortlist", "research", "review", "exclude"]
    reason: str = Field(min_length=10)
    risks: list[str]
    supporting_signal_ids: list[str]
    next_step_hypothesis: str = Field(min_length=5)


class ReasoningOutput(StrictModel):
    summary: str = Field(min_length=10)
    accounts: list[ReasonedAccount]
    open_questions: list[str]
