"""BugHunter Burp MCP: scoped Burp Suite control for any MCP client.

Launch:  python -m bughunter --burp-mcp --project-id <authorized website project>

Every tool acts for the project fixed at launch; a client cannot point Burp at
another project. Active steps (open in Repeater, start an audit) are authorized by
the operator's Autonomous-requests setting in the desktop app, never by the
client. Stopping an audit is always allowed. The desktop app must be running;
it owns the Burp bridge token and the emergency stop.
"""
from __future__ import annotations


def _spec(name, description, properties=None, required=()):
    return {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties or {}, 'required': list(required)}}


HISTORY_ID = {'history_id': {'type': 'integer', 'minimum': 1, 'description': 'Burp proxy history number (#N) from burp_history.'}}
SPECS = [
    _spec('burp_status', 'Burp bridge connection, active-audit state, and whether autonomous actions are allowed.'),
    _spec('burp_review', 'One-shot read-only review: scope-filtered proxy history, Scanner issues and passive leads for this project.'),
    _spec('burp_history', 'Scope-filtered Burp proxy history (method, URL, status, history id). Bodies, headers, cookies and query values are excluded.'),
    _spec('burp_issues', 'Burp Scanner issues inside this project scope. Scanner issues are unverified until reproduced.'),
    _spec('burp_open_in_repeater', 'Open an in-scope history item in a Burp Repeater tab for the user. Nothing is sent.', HISTORY_ID, ['history_id']),
    _spec('burp_start_audit', 'Start a Burp active audit seeded from an in-scope, query-free GET/HEAD history item. Sends many modified requests to the target.', HISTORY_ID, ['history_id']),
    _spec('burp_audit_status', 'Progress of the current Burp active audit (requests, errors, issues).'),
    _spec('burp_stop_audit', 'Stop the current Burp active audit. Always allowed.'),
]


class BurpCapability:
    def __init__(self, app, project_id: str):
        project = app.policy.project(project_id)
        if not project.get('authorization') or not project.get('scope'):
            raise SystemExit('The Burp MCP needs an authorized website project with a scope.')
        self.app, self.project_id = app, project['id']

    def _authorized(self) -> bool:
        from .autonomy import enabled
        try:
            return bool(enabled(self.app.store))
        except Exception:
            return False

    def call(self, name, arguments):
        burp = self.app.burp_history.dispatch
        scoped = {'project_id': self.project_id}
        history = {'history_id': arguments.get('history_id')}
        if name == 'burp_status':
            health = burp('burp.health', {})
            try:
                audit = burp('burp.audit.status', {})
            except Exception as error:
                audit = {'error': str(error)}
            return {'project_id': self.project_id, 'bridge': health, 'audit': audit,
                    'autonomous_actions_allowed': self._authorized()}
        if name == 'burp_review':
            return burp('burp.web.review', scoped)
        if name == 'burp_history':
            return burp('burp.history', scoped)
        if name == 'burp_issues':
            return burp('burp.issues', scoped)
        if name in {'burp_open_in_repeater', 'burp_start_audit'}:
            if not self._authorized():
                raise PermissionError('Autonomous requests are off in BugHunter Settings → General. Start this from the desktop app or turn them on.')
            method = 'burp.repeater.open' if name == 'burp_open_in_repeater' else 'burp.audit.start'
            return burp(method, {**scoped, **history, 'approved': True})
        if name == 'burp_audit_status':
            return burp('burp.audit.status', {})
        if name == 'burp_stop_audit':
            return burp('burp.audit.stop', {'approved': True})
        raise ValueError(f'Unknown tool: {name}')


async def serve(app, project_id: str):
    from .mcp_server import serve_capability
    await serve_capability(BurpCapability(app, project_id), SPECS, 'bughunter-burp')
