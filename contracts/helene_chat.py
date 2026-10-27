"""Investigation-scoped, bounded conversational retrieval; no client cost authority."""
from datetime import date
from typing import Literal
from pydantic import Field
from .models import Contract


class ChatScenario(Contract):
    hospital_id: Literal['sim-nc-central', 'sim-nc-north', 'sim-nc-south']
    additional_bags: int = Field(ge=1, le=10000, strict=True)
    arrival_on: date


class HeleneChatRequest(Contract):
    question: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    conversation_id: str | None = Field(default=None, pattern=r'^conversation-[a-f0-9]{32}$')
    mode: Literal['offline', 'live'] = 'offline'
    scenario: ChatScenario | None = None


class ChatCitation(Contract):
    reference: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    href: str = Field(pattern=r'^/api/v1/helene/cases/[^/]+/chat-sources/[^/]+$')


class ChatExecution(Contract):
    tool: str
    status: Literal['complete'] = 'complete'
    summary: str


class HeleneChatTurn(Contract):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=12000)
    citations: list[ChatCitation] = Field(max_length=16)
    executions: list[ChatExecution] = Field(max_length=8)
    limitations: list[str]


class HeleneChatResponse(Contract):
    schema_version: Literal['helene-chat-1'] = 'helene-chat-1'
    investigation_id: str
    conversation_id: str
    mode: Literal['offline-deterministic'] = 'offline-deterministic'
    model_executed: Literal[False] = False
    turns: list[HeleneChatTurn] = Field(min_length=1, max_length=20)
