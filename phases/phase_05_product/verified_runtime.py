"""Explicitly reviewed NVIDIA Build route; source/config guards before each turn."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit
from phases.phase_01_foundation.http import ServiceError
from phases.phase_01_foundation.inference_policy import InferenceAuthorization

MODEL='nvidia/nemotron-3.5-lightning-30b-a3b'
NVIDIA_ORIGIN='https://integrate.api.nvidia.com/v1'
SOURCE_HASHES={
    'js/engine/orchestrator.js':'e466fcfd68cd8ba3277ee14926c4f4c7535b438e39519d9e29e2d5871ae60acf',
    'js/engine/crosscheck.js':'34983a542780bb67de7a6afaba93ccbe85afa9ac68e58cbcdb40f1678e96e0b9',
    'js/engine/mcp.js':'48bf76f203f61a73b777ba0f79720c75d38a653bb6251077d080336cb31d8834',
    'openai.mjs':'38aa97cd51183bdd9b1ff6eac631045edb59f7632b9a937dc5e5fb8935db71ed',
    'js/engine/providers.js':'9ba942efe541ac62ca3c67ec61f1f56bd903df21f91cc03ab7fd59bac3005d2e',
    'js/engine/proxy.js':'caec97f6cfaf45a29033c9fcf47fd530a0a68af98b260d15bf7fadfb04af4da5',
}


def reviewed_authorization(endpoint,engine_root,state_root,budget):
    route=urlsplit(endpoint)
    if (route.scheme!='http' or route.hostname not in {'127.0.0.1','localhost','10.192.232.47'}
            or route.port!=8088 or route.path!='/v1' or route.query or route.fragment or route.username):
        raise ServiceError('REVIEWED_NIGHTSHIFT_ENDPOINT_REQUIRED')
    engine_root,state_root=Path(engine_root),Path(state_root)
    def guard():
        for name,expected in SOURCE_HASHES.items():
            if sha256((engine_root/name).read_bytes()).hexdigest()!=expected:
                raise ServiceError('RUNTIME_REVIEWED_SOURCE_CHANGED')
        providers=(engine_root/'js/engine/providers.js').read_text()
        if "const NVIDIA_BASE_URL = 'https://integrate.api.nvidia.com/v1'" not in providers:
            raise ServiceError('NVIDIA_BUILD_ORIGIN_UNVERIFIED')
        tools=json.loads((state_root/'tools.json').read_text())
        if tools.get('servers')!=[]:
            raise ServiceError('RUNTIME_EXTERNAL_TOOLS_ENABLED')
        apps=json.loads((state_root/'state/app-policy.json').read_text())
        if apps.get('pharmax',{}).get('mode','auto') not in {'auto','fast','cheap','deep'}:
            raise ServiceError('RUNTIME_APP_MODE_NOT_REVIEWED')
    guard()
    return InferenceAuthorization(endpoint=endpoint,model=MODEL,provider='nvidia',
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=20),
        evidence_refs=('https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317',
            'https://forums.developer.nvidia.com/t/request-more-4-000-credits-option-on-build-nvidia-com/344567/2',
            'User 2026-10-27: active PharmaX-Test free-tier account attestation and free Build-only authorization',
            'Installed reviewed fixed-origin/pinned-route/zero-price-no-verifier source; empty external-server registry'),
        no_charge_confirmed=True,runtime_single_attempt=False,runtime_external_tools_disabled=True,
        max_upstream_attempts_per_request=3,runtime_guard=guard,reserve_attempts=budget.reserve_attempts)
