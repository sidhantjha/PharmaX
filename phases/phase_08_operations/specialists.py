"""Extend existing roles with bounded operational reads, never authorization tools."""
import json
from phases.phase_07_reconciliation.specialists import ReconciliationTools
from phases.phase_06_helene.specialists import remember
from phases.phase_04_agents.models import Finding
from phases.phase_01_foundation.sources import digest


class OperationalTools(ReconciliationTools):
    ALLOWED = {**ReconciliationTools.ALLOWED,
        'dependency': (*ReconciliationTools.ALLOWED['dependency'], 'identity.resolve'),
        'hospital': (*ReconciliationTools.ALLOWED['hospital'], 'readiness.inspect'),
        'mitigation': (*ReconciliationTools.ALLOWED['mitigation'], 'actions.inspect', 'receiving.inspect')}

    def __init__(self, case, workflow):
        super().__init__(case)
        self.workflow = workflow

    def call(self, role, tool):
        custom = {'identity.resolve', 'readiness.inspect', 'actions.inspect', 'receiving.inspect'}
        if tool not in custom:
            return super().call(role, tool)
        if self.calls >= 12 or tool not in self.ALLOWED.get(role, ()):
            raise ValueError('REPLAY_TOOL_BUDGET_OR_ROLE_DENIED')
        self.calls += 1
        view = self.workflow.view(self.case)
        if len(json.dumps(view).encode()) > 150000:
            raise ValueError('REPLAY_TOOL_RESPONSE_BUDGET')
        result = {'identity.resolve': view['readiness']['identities'], 'readiness.inspect': view['readiness'],
                  'actions.inspect': view['actions'], 'receiving.inspect': view['forecasts']}[tool]
        self.receipts.append(dict(agent_id=role, tool=tool, result=result,
            execution='local-deterministic', dataset_version=self.case.dataset_version))
        return result


def remember_operations(root, case, view, *, node=None):
    findings = [Finding(claim_key='operations-'+a['id'], value=json.dumps(dict(
        state=a['state'], receipts=[dict(receipt_id=r['receipt_id'], quantity=r['quantity'],
            quality=r['quality'], evidence_id=r['evidence']['evidence_id']) for r in a['receipts']],
        authorization=a['authorization'],
        forecasts=[dict(hospital_id=f['hospital_id'], total_unmet=f['total_unmet'], analysis_id=f['analysis_id'])
                   for f in view['forecasts']])), kind='derived', evidence_ids=a['evidence_ids'],
        source_ids=['synthetic-operational-ledger'], limitations=['Local simulation; no inference or physical action.'])
        for a in view['actions'][:12]]
    return remember(root, 'phase8', digest(view), case.checkpoint.cutoff, [], [], node=node,
        additional_findings=findings, query_text='operations pharmacy authorization partial receiving forecast')
