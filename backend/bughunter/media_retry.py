"""Persisted subscription cooldowns; never change provider or billing route."""
from __future__ import annotations

import math
import re
import time
import hashlib
import threading


def _account_fingerprint(limits):
    identity=limits.get('accountId') if isinstance(limits,dict) else None
    return hashlib.sha256(identity.encode()).hexdigest() if isinstance(identity,str) and identity else None


class _AccountProbe:
    """Share a short-lived metadata probe without blocking the source DB lock."""
    def __init__(self):
        self.lock=threading.Lock();self.running=False;self.last_started=-math.inf;self.snapshot=None

    def read(self, cancel_check=None):
        with self.lock:
            if not self.running and time.monotonic()-self.last_started>=30:
                self.running=True;self.last_started=time.monotonic()
                threading.Thread(target=self._refresh,args=(cancel_check,),daemon=True,name='media-account-retry').start()
            snapshot=self.snapshot
        return snapshot if snapshot and time.time()-snapshot['checked_at']<60 else None

    def _refresh(self,cancel_check):
        snapshot=None
        try:
            from .codex_subscription import CodexSession
            with CodexSession(timeout=10,cancel_check=cancel_check) as session:
                limits=session.call('account/rateLimits/read',{})
            snapshot={'fingerprint':_account_fingerprint(limits),'allowed':limits.get('ordinaryUsageAllowed') is True,'checked_at':time.time()}
        except Exception:
            pass
        finally:
            with self.lock:
                self.snapshot=snapshot;self.running=False


_account_probe=_AccountProbe()


def is_subscription_limit(error, config):
    if config.get('vision_provider') not in {'codex_subscription','claude_subscription'}:
        return False
    return bool(re.search(r"usage limit|rate[ -]?limit|quota.{0,30}(?:exceed|exhaust)|too many requests|hit your limit",
                          str(error), re.I))


def retry_plan(error, config, attempts=0, *, limits=None, current_time=None):
    if not is_subscription_limit(error, config):
        return None
    current_time=time.time() if current_time is None else current_time
    # A CLI clock string omits timezone/date. Use structured epoch metadata
    # only for exhausted windows; otherwise use a bounded polling backoff.
    snapshots=[]
    if isinstance(limits,dict):
        snapshots.append(limits.get('rateLimits'))
        extra=limits.get('rateLimitsByLimitId')
        if isinstance(extra,dict):
            snapshots.extend(extra.values())
    resets=[]
    for snapshot in snapshots:
        if not isinstance(snapshot,dict):
            continue
        for key in ('primary','secondary'):
            window=snapshot.get(key)
            if not isinstance(window,dict):
                continue
            used,reset=window.get('usedPercent'),window.get('resetsAt')
            if (isinstance(used,(int,float)) and not isinstance(used,bool) and used>=100
                    and isinstance(reset,(int,float)) and not isinstance(reset,bool)
                    and math.isfinite(reset) and current_time<reset<=current_time+31*86400):
                resets.append(reset)
    attempts=min(max(int(attempts),0),1000)+1
    delay=min(900*2**min(attempts-1,2),3600)
    provider=config['vision_provider']
    plan={'provider':provider,'model':config.get('claude_vision_model' if provider=='claude_subscription' else 'codex_vision_model'),
            'attempt':attempts,'retry_at':max(resets)+15 if resets else current_time+delay,
            'schedule_source':'provider_reset' if resets else 'backoff',
            'reason':('Claude' if provider=='claude_subscription' else 'ChatGPT')+' usage limit reached. Completed evidence is saved.'}
    fingerprint=_account_fingerprint(limits) if provider=='codex_subscription' else None
    if fingerprint:plan.update(account_fingerprint=fingerprint,checked_at=current_time)
    return plan


def subscription_retry(error, config, attempts=0, *, cancel_check=None):
    if not is_subscription_limit(error,config):
        return None
    if config['vision_provider']=='claude_subscription':
        return retry_plan(error,config,attempts)
    from .codex_subscription import CodexSession
    limits=None
    try:
        with CodexSession(timeout=20,cancel_check=cancel_check) as session:
            limits=session.call('account/rateLimits/read',{})
    except InterruptedError:
        raise
    except Exception:
        # Metadata lookup failure must preserve the completed extraction and
        # cannot trigger a provider/model change or an immediate request loop.
        pass
    return retry_plan(error,config,attempts,limits=limits)


def retry_due(source, current_time=None, *, cancel_check=None):
    wait=source.get('provider_wait')
    if not isinstance(wait,dict):
        return True
    deadline=wait.get('retry_at')
    if not (isinstance(deadline,(int,float)) and not isinstance(deadline,bool) and math.isfinite(deadline)):
        return False
    current_time=time.time() if current_time is None else current_time
    if deadline<=current_time:return True
    if wait.get('provider')=='codex_subscription' and wait.get('account_fingerprint'):
        snapshot=_account_probe.read(cancel_check)
        return bool(snapshot and snapshot.get('allowed') and snapshot.get('fingerprint')
                    and snapshot['fingerprint']!=wait['account_fingerprint']
                    and snapshot['checked_at']>=wait.get('checked_at',math.inf))
    return False
