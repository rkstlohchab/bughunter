"""Recipe resolution and feature ticks owned by the private helper's serialized lane."""
from __future__ import annotations
from contextlib import nullcontext
from ..game_runtime.contracts import MappingRecipe
from ..game_runtime.mappings import Recipe
from ..game_runtime.features import FeatureContext, FeatureRun
from ..game_runtime.pointers import resolve_recipe
from ..game_runtime.live_target import module_lookup


def recipe_from_wire(value):
    model = MappingRecipe.model_validate(value)
    return Recipe(**model.model_dump())


class RuntimeOperations:
    def __init__(self, server):
        self.server = server
        self.features = {}

    def _context(self, params, recipe):
        from .server import _identity_from
        session = self.server._session(params['session_id'], _identity_from(params))
        adapter = session.adapter
        if recipe.target_fingerprint != adapter.identity.target_fingerprint:
            raise ValueError('recipe fingerprint differs from attached target')
        bases = params.get('fixture_module_bases')
        lookup = (lambda name: int(bases[name], 16)) if bases else module_lookup(adapter)
        identity = adapter.identity
        return session, FeatureContext(adapter, lookup, recipe.target_fingerprint,
                                        f'{identity.pid}:{identity.start_time_ns}')

    def dispatch(self, params):
        from .server import _identity_from
        session=self.server._session(params['session_id'],_identity_from(params))
        transaction=getattr(session.adapter,'transaction',nullcontext)
        with transaction():
            return self._dispatch(params)

    def _dispatch(self, params):
        action = params['runtime_action']
        task = params.get('task_session_id')
        if not isinstance(task, str) or not task:
            raise ValueError('task_session_id is required')
        recipe = recipe_from_wire(params['recipe'])
        session, ctx = self._context(params, recipe)
        key = (task, params.get('feature_id', ''))
        if action == 'resolve':
            result = resolve_recipe(recipe, adapter=ctx.adapter, module_base_lookup=ctx.module_base_lookup,
                                    target_fingerprint=ctx.target_fingerprint, process_instance_id=ctx.process_instance_id)
            value = result.value.hex() if isinstance(result.value, bytes) else result.value
            return {'recipe_id':recipe.id, 'recipe_revision':recipe.revision, 'address':hex(result.final_address),
                    'value':value, 'value_type':recipe.value_type,
                    'intermediate_addresses':[hex(a) for a in result.intermediate_addresses],
                    'process_instance_id':ctx.process_instance_id, 'target_fingerprint':ctx.target_fingerprint}
        if action in ('feature_start', 'feature_tick', 'feature_stop') and not session.mutating:
            raise ValueError('read-only session cannot change features')
        if action == 'feature_start':
            if key in self.features:
                prior,_=self.features[key]
                if (prior.state!='stopped' or not params.get('lifecycle_id') or
                    prior.configuration.get('lifecycle_id')==params['lifecycle_id']):
                    raise ValueError('feature already started; replayed or uncertain starts are refused')
            guards = params.get('guards', [])
            paths = {}
            def validate():
                for guard in guards:
                    saved = recipe_from_wire(guard['recipe'])
                    resolved = resolve_recipe(saved, adapter=ctx.adapter, module_base_lookup=ctx.module_base_lookup,
                                              target_fingerprint=ctx.target_fingerprint, process_instance_id=ctx.process_instance_id)
                    if resolved.value != guard['expected']:
                        raise RuntimeError('runtime guard changed: '+guard['name'])
                    path = tuple(resolved.intermediate_addresses)
                    if guard.get('observed_path') and tuple(int(a,16) for a in guard['observed_path'])!=path:
                        raise RuntimeError('runtime guard differs from observed object: '+guard['name'])
                    if saved.id in paths and paths[saved.id] != path:
                        raise RuntimeError('runtime guard object changed: '+guard['name'])
                    paths[saved.id] = path
            ctx.validate_runtime = validate
            run = FeatureRun(recipe, params['feature_id'], {'lifecycle_id':params.get('lifecycle_id')})
            self.features[key] = (run, ctx)
            run.start(ctx)
            if params.get('observed_path') and tuple(int(a,16) for a in params['observed_path'])!=run._object_path:
                run.stop(ctx,write_cleanup=False)
                raise RuntimeError('resolved object differs from native observation; no write attempted')
            value = run.apply(ctx, params['value'], disable_value=params.get('disable_value'))
            return self._receipt(run, value)
        if key not in self.features:
            if action == 'feature_stop':
                return {'state':'stopped','verified':True,'cleanup_status':'not_started'}
            raise ValueError('feature is unavailable after helper restart')
        run, original_ctx = self.features[key]
        if params.get('lifecycle_id')!=run.configuration.get('lifecycle_id'):
            raise ValueError('obsolete feature lifecycle')
        if recipe.id != run.recipe.id or recipe.revision != run.recipe.revision:
            raise ValueError('feature recipe revision changed')
        if ctx.process_instance_id != original_ctx.process_instance_id:
            raise ValueError('feature process identity changed')
        if action == 'feature_tick':
            value = run.maintain(original_ctx)
        elif action == 'feature_monitor':
            value = run.monitor(original_ctx)
        elif action == 'feature_stop':
            value = run.stop(original_ctx, write_cleanup=True)
        else:
            raise ValueError('unknown runtime action')
        return self._receipt(run, value)

    @staticmethod
    def _receipt(run, value):
        return {'state':run.state,'verified':True,'value':value,'recipe_id':run.recipe.id,
                'recipe_revision':run.recipe.revision,'cleanup_status':run.cleanup_status,
                'events':[event.__dict__ for event in run.events[-8:]], 'gameplay_verified':False}

    def release_task(self, task):
        for key in [key for key in self.features if key[0]==task]:
            run,ctx=self.features.pop(key)
            if not run._stop_requested:run.stop(ctx,write_cleanup=False)

    def close(self):
        # Shutdown prevents further scheduling. Never invent a late cleanup write.
        for run, ctx in self.features.values():
            if not run._stop_requested:
                run.stop(ctx, write_cleanup=False)
        self.features.clear()
