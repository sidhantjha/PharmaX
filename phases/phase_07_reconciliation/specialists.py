"""Existing specialist roles with real bounded tools and the existing H-MEM bridge."""
import json
from phases.phase_06_helene.specialists import ReplayTools, remember
from phases.phase_04_agents.models import Finding


class ReconciliationTools(ReplayTools):
    ALLOWED = {**ReplayTools.ALLOWED,
        'verification': (*ReplayTools.ALLOWED['verification'], 'evidence.records', 'evidence.findings'),
        'dependency': (*ReplayTools.ALLOWED['dependency'], 'orders.implicated'),
        'mitigation': (*ReplayTools.ALLOWED['mitigation'], 'supply.validated')}

    def call(self, role, tool):
        custom = {'evidence.records', 'evidence.findings', 'orders.implicated', 'supply.validated'}
        if tool not in custom:
            return super().call(role, tool)
        if self.calls >= 12 or tool not in self.ALLOWED.get(role, ()):
            raise ValueError('REPLAY_TOOL_BUDGET_OR_ROLE_DENIED')
        self.calls += 1  # reserve before dispatch
        verification = self.case.verification
        if verification is None:
            raise ValueError('RECONCILIATION_NOT_SAVED')
        # Bound worst-case serialized response before dispatching a selectable reader.
        if len(verification.model_dump_json().encode()) > 150000:
            raise ValueError('REPLAY_TOOL_RESPONSE_BUDGET')
        readers = {
            'evidence.records': lambda: [c.model_dump(mode='json') for c in verification.claims],
            'evidence.findings': lambda: [f.model_dump(mode='json') for f in verification.findings],
            'orders.implicated': lambda: sorted({c.order_id for c in verification.claims if c.order_id}),
            'supply.validated': lambda: dict(receipts=verification.physical_receipts,
                forecasts=[f.model_dump(mode='json') for f in self.case.forecasts],
                requires_human_review=verification.requires_human_review),
        }
        result = readers[tool]()
        self.receipts.append(dict(agent_id=role, tool=tool, execution='local-deterministic',
            classification='executed-operation', result=result, dataset_version=self.case.dataset_version))
        return result


def remember_verification(root, result, version, *, node=None, prior_scope=None):
    # Reuse the reference bridge, preserving actual verification semantics in each finding.
    records = [dict(candidate_id=f.finding_id,
        category=f.category, reason=f.reason, evidence_ids=f.evidence_ids, claim_ids=f.claim_ids,
        verification_steps=f.verification_steps, requires_human_review=f.requires_human_review)
        for f in result.findings if f.category in {'conflict', 'missing', 'superseded', 'consistent'}]
    records.sort(key=lambda r: ({'conflict': 0, 'superseded': 1, 'consistent': 2, 'missing': 3}[r['category']],
                                r['candidate_id']))
    findings = [Finding(claim_key='reconciliation-'+r['category']+'-'+r['candidate_id'],
        value=json.dumps(r), kind='derived', evidence_ids=r['evidence_ids'], source_ids=['phase7-reconciliation'],
        limitations=['Derived verification of synthetic operations, not observed hospital history.'])
        for r in records[:12]]
    return remember(root, 'phase7-'+result.scenario, version, result.cutoff, [], [],
                    node=node, prior_scope=prior_scope, additional_findings=findings,
                    query_text='reconciliation conflict missing receipt evidence verification superseded')
