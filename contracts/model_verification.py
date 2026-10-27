"""Actual native-run status, separate from immutable historical case payload."""

from typing import Literal
from pydantic import Field, model_validator
from .models import Contract


class ModelVerificationStatus(Contract):
    schema_version: Literal["model-verification-1"] = "model-verification-1"
    investigation_id: str
    available: bool
    mode: Literal["unavailable", "nightshift-live-model"]
    run_id: str | None = None
    status: Literal["complete", "partial"] | None = None
    roles_executed: int = Field(default=0, ge=0, le=12)
    findings_validated: int = Field(default=0, ge=0, le=12)
    model_requests: int = Field(default=0, ge=0, le=36)
    model: str | None = None
    roles: list[dict] = Field(default_factory=list, max_length=12)
    budget: dict = Field(default_factory=dict)
    data_layer: dict = Field(default_factory=dict)
    hmem: dict = Field(default_factory=dict)
    graph: list[dict] = Field(default_factory=list, max_length=100)
    trace: list[dict] = Field(default_factory=list, max_length=1000)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def truthful_counts(self):
        if self.findings_validated > self.roles_executed:
            raise ValueError("VALIDATED_CANNOT_EXCEED_EXECUTED")
        if not self.available and (
            self.mode != "unavailable"
            or self.run_id
            or self.roles
            or self.model_requests
            or self.roles_executed
            or self.findings_validated
        ):
            raise ValueError("UNAVAILABLE_CANNOT_CLAIM_EXECUTION")
        if self.available and (
            self.mode != "nightshift-live-model" or not self.run_id or len(self.roles) != 12
        ):
            raise ValueError("AVAILABLE_REQUIRES_ACTUAL_RUN")
        if self.available and (
            self.findings_validated != sum(r["status"] == "complete" for r in self.roles)
            or self.roles_executed != sum(r.get("continuation_occurred") is True for r in self.roles)
        ):
            raise ValueError("ROLE_COUNTS_MUST_MATCH_SAVED_RECORDS")
        return self
