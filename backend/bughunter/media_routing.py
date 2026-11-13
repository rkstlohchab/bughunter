"""Explicitly enabled subscription lanes with stable evidence provenance."""
import time
import math
from pathlib import Path

HOSTED={'codex_subscription','claude_subscription','gemini_subscription','openai','anthropic','gemini'}


def frame_config(config, lane=0, *, current_time=None):
    result=dict(config)
    if (config.get('vision_provider')=='codex_subscription'
            and config.get('claude_parallel_enabled') and lane==1
            and (time.time() if current_time is None else current_time)>=config.get('claude_parallel_after',0)):
        result['vision_provider']='claude_subscription'
    return result


def preserves_hosted_evidence(config, source):
    return bool(source.get('media_mixed_providers') or source.get('analysis_model') or (
        config.get('vision_provider') in HOSTED and config.get('claude_parallel_enabled')))


def cache_model(entry):
    return f"{entry.get('provider')}:{entry.get('model')}"+(
        ':low-memory-v1' if entry.get('vision_profile')=='low_memory' else '')+((':effort-'+(entry.get('effort') or 'provider')) if entry.get('effort','medium')!='medium' else '')


def cached_context_key(entry, source_hash, frame, narration, artifacts):
    """Validate the original image context rather than the current model's context policy."""
    from .media_context import context_key
    if entry.get('active') is False or entry.get('review_status') == 'rejected':
        return None
    previous = entry.get('previous_frames')
    if not isinstance(previous, list) or len(previous) > 2:
        return None
    root, frames = Path(artifacts).resolve(), []
    last_time = -1
    try:
        for item in previous:
            relative = Path(item['artifact_id'])
            stamp = item['time']
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or not isinstance(stamp, (int, float)) or isinstance(stamp, bool)
                    or not math.isfinite(stamp) or not last_time < stamp < frame['time']):
                return None
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
            frames.append({'path':str(path), 'time':stamp})
            last_time = stamp
        return context_key(source_hash, cache_model(entry), frame, frames, narration)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def detail_config(config):
    result=dict(config)
    if result.get('vision_provider')=='codex_subscription' and result.get('codex_detail_model'):
        result['codex_vision_model']=result['codex_detail_model']
    return result


class VisualProviderFailure(ValueError):
    def __init__(self,message,provider,model):
        super().__init__(message)
        self.provider=provider
        self.model=model
