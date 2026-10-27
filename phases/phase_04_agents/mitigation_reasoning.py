"""Adaptive choices over returned alternatives and actual H-MEM rejection context."""
import json
from typing import Literal
from contracts.data import Identifier
from contracts.models import Contract
from .reasoning import NemotronReasoner


class CandidateChoice(Contract):
    candidate_id: Identifier | None
    reason: Literal['investigate_supply', 'revise_after_rejection', 'compare_alternatives', 'stop']


def recalled_rejections(memory):
    rejected = set()
    for retrieved in memory['result']['memories']:
        fact = json.loads(retrieved['memory']['content'])
        if fact['claim_key'].startswith('mitigation.'):
            value = json.loads(fact['value'])
            if value.get('feasible') is False:
                rejected.add(value['candidate_id'])
    return rejected


class FixtureMitigationReasoner:
    """Model fixture: optimistic quantity-first search, revised by actual failures/memory."""
    live = False

    def choose(self, candidates, evaluations, memory, trace):
        recalled = recalled_rejections(memory)
        tried = {e.candidate.candidate_id for e in evaluations}
        remaining = [c for c in candidates if c.candidate_id not in tried]
        # Inputs are proposed quantities, never LLM-supplied forecast results.
        chosen = min(remaining, key=lambda c: (c.candidate_id in recalled, -c.quantity, c.candidate_id)) if remaining else None
        choice = CandidateChoice(candidate_id=chosen.candidate_id if chosen else None,
            reason='revise_after_rejection' if recalled else 'compare_alternatives' if evaluations else 'investigate_supply')
        trace.event('coordinator.mitigation_choice', policy='deterministic-model-fixture',
            **choice.model_dump(), recalled_rejections=sorted(recalled))
        return choice


class NemotronMitigationReasoner(NemotronReasoner):
    def __init__(self, client, *, allow_paid=False, authorization=None):
        super().__init__(client, allow_paid=allow_paid, max_decisions=4, authorization=authorization)

    def choose(self, candidates, evaluations, memory, trace):
        tried = {e.candidate.candidate_id for e in evaluations}
        available = [c for c in candidates if c.candidate_id not in tried]
        state = [{'candidate_id': e.candidate.candidate_id, 'feasible': e.feasible,
                  'reasons': [f.code for f in e.failures], 'remaining_unmet': e.remaining_unmet}
                 for e in evaluations]
        choice = self.decide(CandidateChoice,
            'Choose one untried candidate ID from the returned simulated supply alternatives, or null to stop. '
            'Use rejection memory to investigate another source/quantity when an option fails. '
            'A matching product, safe donor and timely authorized route matter. Never invent inventory, '
            'candidate IDs, product equivalence or numerical outcomes. Compare feasible alternatives within budget.',
            {'available': [c.model_dump(mode='json') for c in available],
             'evaluations': state, 'shared_memory': memory['result']['context']}, trace)
        if choice.candidate_id is not None and choice.candidate_id not in {c.candidate_id for c in available}:
            from phases.phase_01_foundation.http import ServiceError
            raise ServiceError('MODEL_TOOL_SCOPE_DENIED')
        return choice
