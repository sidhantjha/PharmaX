"""Backend-owned authorization; environment flags are never cost evidence.

No production approval is shipped. A policy reviewer must independently verify the
route, account terms and runtime controls before supplying an authorization. This
object is deliberately not accepted from HTTP requests or environment JSON.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from .http import ServiceError


@dataclass(frozen=True)
class InferenceAuthorization:
    endpoint: str
    model: str
    provider: str
    expires_at: datetime
    evidence_refs: tuple[str, ...]
    no_charge_confirmed: bool
    runtime_single_attempt: bool
    runtime_external_tools_disabled: bool
    max_upstream_attempts_per_request: int = 1
    runtime_guard: Callable[[], None] | None = None
    reserve_attempts: Callable[[int], None] | None = None

    def require(self, endpoint, model):
        if (self.endpoint != endpoint or self.model != model or self.provider != 'nvidia'
                or self.expires_at.tzinfo is None or self.expires_at <= datetime.now(timezone.utc)
                or not self.evidence_refs or not all(isinstance(r, str) and r for r in self.evidence_refs)
                or self.no_charge_confirmed is not True
                or (self.runtime_single_attempt is not True and not (
                    self.max_upstream_attempts_per_request == 3 and self.runtime_guard is not None
                    and self.reserve_attempts is not None))
                or self.runtime_external_tools_disabled is not True):
            raise ServiceError('NO_COST_ROUTE_UNCONFIRMED', blocked=True)
        if self.runtime_guard is not None:
            self.runtime_guard()

    def reserve(self):
        if self.reserve_attempts is not None:
            self.reserve_attempts(self.max_upstream_attempts_per_request)
