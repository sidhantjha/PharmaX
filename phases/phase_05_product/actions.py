"""Offline draft preparation and explicit review, with no external execution connector."""
from datetime import datetime, timezone
from contracts.operator import ActionPackage

KINDS = ('supplier_inquiry', 'purchase_order', 'stock_transfer', 'procurement_approval', 'clinical_review', 'follow_up')


def prepare(case_id, result, source_organizations=None):
    selected = next((e for e in result.evaluations if e.feasible and
                     e.candidate.candidate_id == result.recommendation_id), None)
    if selected is None:
        return []
    candidate = selected.candidate
    source = (source_organizations or {}).get(candidate.source_id)
    if source is None:
        raise ValueError('SOURCE_ORGANIZATION_UNRESOLVED')
    outstanding = ['Verify actual product, lot, expiry, unreserved availability and route timing.',
                   'Confirm donor forecast and reservations against current operational records.']
    authorizations = ['Receiving pharmacy review', 'Source organization stock/release authority', 'Procurement approval']
    actions = []
    for kind in KINDS:
        conditional = ('Contingency inquiry/draft only; do not duplicate the selected transfer with an extra purchase. '
                       if kind in ('supplier_inquiry', 'purchase_order') and candidate.kind == 'transfer' else '')
        clinical = ('Therapeutic substitution: REQUIRES_CLINICAL_REVIEW; no validated alternative or regimen change. '
                    if kind == 'clinical_review' else '')
        justification = f'Calculated {selected.original_unmet} → {selected.remaining_unmet} unmet vials with {candidate.candidate_id}.'
        actions.append(ActionPackage(action_id=f'{case_id}:{kind}', investigation_id=case_id,
            mitigation_id=candidate.candidate_id, kind=kind, product_id=candidate.product_id,
            quantity=candidate.quantity, requesting_organization=candidate.hospital_id,
            receiving_organization=candidate.hospital_id, source_organization=source, source_record_id=candidate.source_id,
            target_on=candidate.arrival_on, justification=justification,
            expected_benefit=f'{selected.original_unmet-selected.remaining_unmet} fewer modeled unmet vials; donor checks required.',
            evidence_ids=selected.evidence_ids, calculation_ids=selected.analysis_ids,
            evidence_url=f'/api/v1/investigations/{case_id}/evidence',
            calculation_url=f'/api/v1/investigations/{case_id}/forecast',
            required_authorizations=authorizations, outstanding_verification=outstanding,
            draft=f'DRAFT FOR HUMAN REVIEW — {kind}\n{conditional}{clinical}'
                  f'Request {candidate.quantity} unopened vials of {candidate.product_id} from '
                  f'{source} (source record {candidate.source_id}) for {candidate.hospital_id} by {candidate.arrival_on}. '
                  f'{justification} All organizations and availability are simulated. '
                  'Confirm product/presentation, permissions, transport, donor safety and receiving capacity. '
                  'No stock release, purchase, communication or clinical change has occurred.',
            updated_at=datetime.now(timezone.utc)))
    return actions


def review(action, request):
    if request.expected_revision != action.revision:
        raise ValueError('REVISION_CONFLICT')
    allowed = {'DRAFT':'READY_FOR_REVIEW', 'READY_FOR_REVIEW':'APPROVED'}
    if allowed.get(action.status) != request.status:
        raise ValueError('ACTION_TRANSITION_DENIED')
    required = (action.required_authorizations + action.outstanding_verification
                if request.status == 'APPROVED' else ['Draft reviewed for accuracy and simulation labels'])
    if not set(required) <= set(request.attestations):
        raise ValueError('REVIEW_ATTESTATIONS_REQUIRED')
    now = datetime.now(timezone.utc)
    record = {'operator':request.operator, 'note':request.note, 'attestations':request.attestations,
              'from':action.status, 'to':request.status, 'at':now.isoformat()}
    return action.model_copy(update={'status':request.status, 'revision':action.revision+1,
        'updated_at':now, 'review_history':[*action.review_history, record]})
