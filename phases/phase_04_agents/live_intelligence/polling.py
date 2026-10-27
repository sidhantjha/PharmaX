"""Persistent due checks/backoff. Polling never runs a model or an investigation."""
from datetime import timedelta
from contracts import SourceStatus
from phases.phase_01_foundation.http import ServiceError
from .matching import match_dependency
from .registry import investigation_key

CADENCE={'gdelt':1800,'marad':21600}


def refresh(registry,fetcher,now,*,sources=('marad','gdelt'),force=False,cadences=None,dependency=None,delays=(0,3,7)):
    cadences=cadences or CADENCE
    states={s.source:s for s in registry.statuses()}
    output=[]
    for source in sources:
        cadence=cadences[source]
        if not 300<=cadence<=86400:
            raise ValueError('Cadence must be 5 minutes..24 hours')
        old=states.get(source,SourceStatus(source=source,target_cadence_seconds=cadence))
        if old.last_attempt_at and (now-old.last_attempt_at).total_seconds()<5:
            continue  # Even manual refresh cannot hammer a provider.
        if old.next_check_at and old.next_check_at>now and (not force or old.last_error):
            continue  # Honor retry backoff even when manually refreshed.
        state=old.model_copy(update={'target_cadence_seconds':cadence,'last_attempt_at':now,'new_revisions':0})
        before=len(fetcher.receipts)
        try:
            signals=fetcher.fetch(source)
            prior=registry.latest(max([now,*[s.retrieved_at for s in signals]]))
            previous={s.event_id:{e.content_sha256 for e in s.evidence} for s in prior}
            raw_changed=any(s.event_id in previous and previous[s.event_id]!={e.content_sha256 for e in s.evidence} for s in signals)
            new=registry.ingest(signals,receipts=fetcher.receipts[before:],source=source,checked=now)
            state.last_success_at=max([now,*[s.retrieved_at for s in signals]])
            state.raw_content_changed=raw_changed
            state.content_needs_review=(source=='marad' and raw_changed and not new)
            state.latest_receipt_ids=fetcher.receipts[before:]
            state.last_error=None
            state.consecutive_failures=0
            state.new_revisions=len(new)
            state.next_check_at=state.last_success_at+timedelta(seconds=cadence)
            available=registry.latest(state.last_success_at)
            relevant=[s.revision_id for s in new if dependency and
                match_dependency(s,dependency,state.last_success_at,available_signals=available).status=='MATCHED']
            for signal in new:
                if signal.revision_id in relevant:
                    registry.queue(investigation_key(signal,dependency,delays))
            output.append({'source':source,'new_revisions':[s.revision_id for s in new],
                'relevant_revisions':relevant,'retrieved_count':len(signals),'status':'success',
                'receipts':fetcher.receipts[before:]})
        except (ServiceError,ValueError) as exc:
            state.consecutive_failures+=1
            state.last_error=exc.code if isinstance(exc,ServiceError) else 'NORMALIZATION_FAILED'
            retry=getattr(exc,'diagnostics',{}).get('retry_after_seconds',0)
            seconds=max(retry,min(cadence,60*2**min(state.consecutive_failures-1,6)))
            state.next_check_at=now+timedelta(seconds=seconds)
            output.append({'source':source,'status':'failed','error_code':state.last_error,
                'receipts':fetcher.receipts[before:],'next_check_at':state.next_check_at.isoformat()})
        registry.set_status(state)
    return output
