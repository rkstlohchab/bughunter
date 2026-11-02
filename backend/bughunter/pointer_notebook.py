"""Operator-managed local process pointer candidates, not asserted stable offsets.

The application stores an explicit operator-supplied recipe and read-only tool
observations. A repeat observation is never elevated to a permanent guarantee.
"""
from __future__ import annotations

import re
from .store import now

TYPES = {'i8', 'i16', 'i32', 'i64', 'u8', 'u16', 'u32', 'u64', 'f32', 'f64'}


def address(value):
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ValueError('Expected a hexadecimal or decimal address.')
    try:
        result = int(value, 0) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError('Invalid address or offset.') from exc
    if not 0 <= result <= 0xffffffffffffffff:
        raise ValueError('Address or offset outside 64-bit range.')
    return result


class PointerNotebook:
    def __init__(self, store):
        self.store = store

    def save(self, process_label, label, base, offsets, value_type, expected_value, project_id=None):
        if project_id and not self.store.get('project', project_id):
            raise ValueError('Choose an existing project.')
        if not isinstance(process_label, str) or not 2 <= len(process_label.strip()) <= 100:
            raise ValueError('Enter an offline test process label.')
        if not isinstance(label, str) or not 2 <= len(label.strip()) <= 100:
            raise ValueError('Give the pointer candidate a name.')
        if not isinstance(offsets, list) or len(offsets) > 12:
            raise ValueError('At most 12 pointer hops are supported.')
        if value_type not in TYPES:
            raise ValueError('Unsupported numeric type.')
        if not isinstance(expected_value, (int, float, str)) or len(str(expected_value)) > 60:
            raise ValueError('Invalid expected value.')
        from .store import new_id
        return self.store.put('pointer_candidate', {
            'project_id': project_id, 'process_label': process_label.strip(), 'label': label.strip(),
            'base': hex(address(base)), 'offsets': [str(address(v)) for v in offsets],
            'value_type': value_type, 'expected_value': str(expected_value),
            'status': 'unverified', 'observations': [],
            'note': 'Raw absolute pointers are not ASLR-stable; restart tests must be repeated against a trusted module-relative anchor.'
        }, id=new_id())

    def list(self, project_id=None):
        if project_id:
            return [r for r in self.store.list('pointer_candidate') if r.get('project_id') in (None, project_id)]
        return self.store.list('pointer_candidate')

    def observe(self, id, tool_result):
        """Store a result copied from the direct read-only memory bridge tool.

        This record is desktop-reported, not cryptographically attested. It does
        not imply the base is module-relative or prove the game version stable.
        """
        row = self.store.get('pointer_candidate', id)
        if not row: raise ValueError('Pointer candidate not found.')
        if not isinstance(tool_result, dict): raise ValueError('Expected structured verification result.')
        if tool_result.get('stable_across_restarts') != 'not_established':
            raise ValueError('Unknown verification format.')
        if type(tool_result.get('matched')) is not bool:
            raise ValueError('Verification must report a boolean match.')
        identity = str(tool_result.get('process_instance_id') or '')
        if len(identity) > 160 or (identity and not re.fullmatch(r'[a-zA-Z0-9_.:+ -]+', identity)):
            raise ValueError('Invalid process identity.')
        pid = tool_result.get('attached_pid')
        if type(pid) is not int or pid < 2: raise ValueError('Invalid attached process ID.')
        observation = {'observed_at': now(), 'matched': tool_result['matched'],
                       'pid': pid, 'process_instance_id': identity or None,
                       'resolved_address': str(tool_result.get('resolved_address') or '')[:20],
                       'source': 'desktop_reported_memory_tool'}
        observations = (row.get('observations', []) + [observation])[-30:]
        observed_instances = {o['process_instance_id'] for o in observations if o.get('matched') and o.get('process_instance_id')}
        repeated = len(observed_instances) >= 2
        # Never call it permanently stable: module rebasing and future updates
        # can invalidate any absolute address.
        status = 'retested_across_processes' if repeated else 'observed_once' if any(o['matched'] for o in observations) else 'unverified'
        return self.store.put('pointer_candidate', {**row, 'status': status, 'observations': observations, 'updated_at': now()}, id=id)

    def dispatch(self, method, params):
        if method == 'memory.pointers.save': return self.save(**params)
        if method == 'memory.pointers.list': return self.list(**params)
        if method == 'memory.pointers.observe': return self.observe(**params)
        if method == 'memory.pointers.get':
            row = self.store.get('pointer_candidate', params['id'])
            if not row: raise ValueError('Pointer candidate not found.')
            return row
        raise ValueError('Unknown pointer notebook operation.')
