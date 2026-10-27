"""One run's programmatic logical-turn, possible-attempt and concurrency limits."""
from datetime import datetime, timezone
import json
import threading
from time import monotonic, sleep
from phases.phase_01_foundation.http import ServiceError


class VerificationBudget:
    def __init__(self, *, max_turns=25, max_attempts=75, token_cap=30000, seconds=240,
                 per_role=2, rate_limit=None):
        self.max_turns, self.max_attempts, self.token_cap = max_turns, max_attempts, token_cap
        self.per_role,self.rate_limit=per_role,rate_limit
        self.deadline = monotonic()+seconds
        self.started = monotonic()
        self.started_at=datetime.now(timezone.utc).isoformat()
        self.lock = threading.Lock()
        self.rate_gate = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.turns = self.attempts = self.reported_tokens = self.pending_tokens = 0
        self.roles = {}
        self.tickets = []
        self.stopped = False

    def reserve_attempts(self, count):
        with self.lock:
            if monotonic()>=self.deadline:
                raise ServiceError('VERIFICATION_TIME_BUDGET_EXHAUSTED')
            if count!=3 or self.attempts+count>self.max_attempts or self.stopped:
                raise ServiceError('UPSTREAM_ATTEMPT_BUDGET_EXHAUSTED')
            self.attempts += count

    def request_seconds(self):
        remaining=self.deadline-monotonic()
        if remaining<=0:
            raise ServiceError('VERIFICATION_TIME_BUDGET_EXHAUSTED')
        return min(30,remaining)

    def reserve(self, role, payload):
        if not self.slots.acquire(timeout=max(.001,self.deadline-monotonic())):
            raise ServiceError('VERIFICATION_TIME_BUDGET_EXHAUSTED')
        self.rate_gate.acquire()
        try:
            while self.rate_limit:
                with self.lock:
                    occupied=sum(t.get('rate_until',float('inf'))>monotonic() for t in self.tickets)
                    if self.stopped or monotonic()>=self.deadline:
                        raise ServiceError('VERIFICATION_TIME_BUDGET_EXHAUSTED')
                    if occupied<self.rate_limit:
                        break
                sleep(.1)
            with self.lock:
                cap = 1 if role=='smoke' else self.per_role
                # UTF-8 bytes + wire framing is a conservative guard for submitted
                # logical context, not a claim to measure invisible failed retries.
                input_bound = len(json.dumps(dict(messages=payload['messages'],tools=payload['tools']),
                                             ensure_ascii=False).encode())+128
                reserve = input_bound+payload['max_tokens']
                if (self.stopped or monotonic()>=self.deadline or self.turns>=self.max_turns
                    or self.roles.get(role,0)>=cap or not 1<=payload['max_tokens']<=1024
                    or self.reported_tokens+self.pending_tokens+reserve>self.token_cap):
                    raise ServiceError('VERIFICATION_BUDGET_EXHAUSTED')
                self.turns+=1
                self.roles[role]=self.roles.get(role,0)+1
                self.pending_tokens+=reserve
                t=dict(role=role,number=self.turns,started_at=datetime.now(timezone.utc).isoformat(),
                    possible_upstream_attempts=3,token_reservation=reserve,start=monotonic(),settled=False)
                self.tickets.append(t)
                return t
        except Exception:
            self.slots.release()
            raise
        finally:
            self.rate_gate.release()

    def observed(self,ticket,status,ids):
        ticket.update(http_status=status,provider_request_ids=ids)

    def settle(self,ticket,result):
        with self.lock:
            usage=result.get('usage',{})
            total=usage.get('total_tokens')
            measured=type(total) is int and total>=0
            consumed=total if measured else ticket['token_reservation']
            self.reported_tokens+=consumed
            self.pending_tokens-=ticket['token_reservation']
            ticket.update(settled=True,elapsed_seconds=round(monotonic()-ticket['start'],3),
                completed_at=datetime.now(timezone.utc).isoformat(),usage_reported=measured,
                rate_until=monotonic()+60)
            self.slots.release()
            if self.reported_tokens+self.pending_tokens>self.token_cap:
                self.stopped=True
                raise ServiceError('PROVIDER_USAGE_EXCEEDS_TOKEN_BUDGET')

    def fail(self,ticket):
        with self.lock:
            self.stopped=True
            if not ticket['settled']:
                # Unknown failed-request usage remains fully reserved.
                ticket.update(settled=True,elapsed_seconds=round(monotonic()-ticket['start'],3))
                self.slots.release()

    def summary(self):
        return dict(logical_turns_reserved=self.turns,possible_upstream_attempts_reserved=self.attempts,
            started_at_utc=self.started_at,
            actual_upstream_attempts=None,application_retries=0,reported_or_reserved_tokens=self.reported_tokens,
            unmeasured_failed_tokens_reserved=self.pending_tokens,elapsed_seconds=round(monotonic()-self.started,3),
            max_logical_turns=self.max_turns,max_possible_upstream_attempts=self.max_attempts,
            max_concurrent_logical_requests=2,max_output_tokens_per_turn=1024,
            max_reported_logical_tokens=self.token_cap,
            max_turns_per_role=self.per_role,rate_limit_envelopes_per_minute=self.rate_limit,
            limitation='Failed internal-retry token usage and exact attempts are not exposed by Nightshift; token guard covers logical submitted/reported usage, not unobservable retry tokens.')
