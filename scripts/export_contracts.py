"""Export the future frontend response boundary; --check detects schema drift."""
from pathlib import Path
import argparse
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import (  # noqa: E402
    DiagnosticReport, SourceEvidence, TradeFlow, DrugShortage, ToolRequest, ToolResult,
    InvestigationEvent, DrugProduct, DisruptionEvent, Supplier, Hospital, InventoryRecord,
    PurchaseOrder, Shipment, DailyConsumption, SupplierApproval, FDARecall, DataBundle, DataQuery, DataPage,
    TradeExposure, ShipmentRisk, InventoryForecast, HospitalRiskAssessment, ScenarioComparison,
    ForecastRequest, ScenarioOverlay, RiskRating,
    MitigationRequest, MitigationCatalog, MitigationCandidate, CandidateEvaluation, MitigationResult,
    DisruptionSignal, SourceStatus, SupplyDependency, DependencyMatch, DelayRisk, IntelligenceRun,
)

from contracts.operator import (  # noqa: E402
    OperatorCase, CaseRequest, ActionPackage, ActionReview, ClinicalSession, ClinicalImpact,
    AgentDecision, VerificationCheck, ApiResponse, ApiError, ChatRequest, ChatResponse,
)

from contracts.reconciliation import (  # noqa: E402
    ArrivalClaim, ReconciliationRequest, EvidenceConflict, ConflictResolution, CaseRevision, ForecastDelta,
)
from contracts.replay import (  # noqa: E402
    SourceRevision, AllocationNotice, OperationalImportReceipt, ReplayCheckpoint,
    ScenarioReplayRequest, ScenarioOperations,
    HeleneCase, HeleneForecast,
)
from contracts.risk import Quantity  # noqa: E402
from contracts.operations import ProductIdentity, ActionProposal, ActionTransition  # noqa: E402
from contracts.model_verification import ModelVerificationStatus  # noqa: E402
from contracts.helene_chat import HeleneChatRequest, HeleneChatResponse  # noqa: E402
from contracts.operational_claims import (  # noqa: E402
    OperationalClaim, CorroborationFinding, EvidenceVerification,
)

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    models = {
        'helene-chat-request': HeleneChatRequest, 'helene-chat-response': HeleneChatResponse,
        'product-identity': ProductIdentity, 'operational-action-proposal': ActionProposal,
        'operational-action-transition': ActionTransition,
        'operational-claim': OperationalClaim, 'corroboration-finding': CorroborationFinding,
        'evidence-verification': EvidenceVerification,
        'helene-case': HeleneCase, 'helene-forecast': HeleneForecast,
        'source-revision': SourceRevision, 'allocation-notice': AllocationNotice,
        'operational-import-receipt': OperationalImportReceipt, 'replay-checkpoint': ReplayCheckpoint,
        'scenario-replay-request': ScenarioReplayRequest, 'scenario-operations': ScenarioOperations,
        'quantity': Quantity,
        'arrival-claim': ArrivalClaim, 'reconciliation-request': ReconciliationRequest,
        'evidence-conflict': EvidenceConflict, 'conflict-resolution': ConflictResolution,
        'case-revision': CaseRevision, 'forecast-delta': ForecastDelta,
        'operator-case': OperatorCase, 'case-request': CaseRequest,
        'model-verification-status':ModelVerificationStatus,
        'action-package': ActionPackage, 'action-review': ActionReview,
        'clinical-session': ClinicalSession, 'clinical-impact': ClinicalImpact,
        'agent-decision': AgentDecision, 'verification-check': VerificationCheck,
        'operator-api-error': ApiError, 'operator-api-response': ApiResponse, 'chat-request': ChatRequest, 'chat-response': ChatResponse,
        'disruption-signal': DisruptionSignal, 'source-status': SourceStatus,
        'supply-dependency': SupplyDependency, 'dependency-match': DependencyMatch,
        'delay-risk': DelayRisk, 'intelligence-run': IntelligenceRun,
        'mitigation-request': MitigationRequest, 'mitigation-catalog': MitigationCatalog,
        'mitigation-candidate': MitigationCandidate, 'candidate-evaluation': CandidateEvaluation,
        'mitigation-result': MitigationResult,
        'diagnostic': DiagnosticReport, 'source-evidence': SourceEvidence,
        'trade-flow': TradeFlow, 'drug-shortage': DrugShortage,
        'tool-request': ToolRequest, 'tool-result': ToolResult,
        'investigation-event': InvestigationEvent,
        'drug-product': DrugProduct, 'disruption-event': DisruptionEvent, 'supplier': Supplier,
        'hospital': Hospital, 'inventory-record': InventoryRecord, 'purchase-order': PurchaseOrder,
        'shipment': Shipment, 'daily-consumption': DailyConsumption, 'supplier-approval': SupplierApproval,
        'trade-exposure': TradeExposure, 'shipment-risk': ShipmentRisk, 'inventory-forecast': InventoryForecast,
        'hospital-risk-assessment': HospitalRiskAssessment, 'scenario-comparison': ScenarioComparison,
        'forecast-request': ForecastRequest, 'scenario-overlay': ScenarioOverlay, 'risk-rating': RiskRating,
        'fda-recall': FDARecall, 'data-bundle': DataBundle, 'data-query': DataQuery, 'data-page': DataPage,
    }
    for name, model in models.items():
        value = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + '\n'
        target = ROOT / 'contracts' / (name + '.schema.json')
        if args.check:
            if not target.is_file() or target.read_text() != value:
                raise SystemExit('Contract schema differs; run scripts/export_contracts.py')
        else:
            target.write_text(value)
    if args.check:
        print(f'PASS: {len(models)} exported schemas match typed models')



if __name__ == '__main__':
    main()
