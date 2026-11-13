"""Public Claude versions, supplementing the CLI's shorter recommendation list.

Verified 2026-11-13 against the model overview and Claude Code effort table:
https://platform.claude.com/docs/en/models/overview
https://code.claude.com/docs/en/model-config#adjust-effort-level
Catalog presence does not assert account access or a successful inference test.
"""
VERSIONS = (
    ('fable', '5-2'), ('fable', '5-1'), ('fable', '5'),
    ('opus', '5-5'), ('opus', '5'), ('opus', '4-8'), ('opus', '4-7'), ('opus', '4-6'), ('opus', '4-5'),
    ('sonnet', '5-5'), ('sonnet', '5'), ('sonnet', '4-6'), ('sonnet', '4-5'),
    ('haiku', '5-5'), ('haiku', '4-5'),
)
MODEL_IDS = {f'claude-{family}-{version}' for family, version in VERSIONS}


def label(model):
    parts = model.removeprefix('claude-').split('-')
    return 'Claude ' + parts[0].title() + ' ' + '.'.join(parts[1:])


def effort(model, provider='claude_code'):
    full = {'claude-fable-5-2', 'claude-fable-5-1', 'claude-fable-5',
            'claude-opus-5-5', 'claude-opus-5', 'claude-opus-4-8', 'claude-opus-4-7',
            'claude-sonnet-5-5', 'claude-sonnet-5', 'claude-haiku-5-5'}
    levels = ['low', 'medium', 'high', 'xhigh', 'max'] if model in full else (
        ['low', 'medium', 'high', 'max'] if model in {'claude-opus-4-6', 'claude-sonnet-4-6'} else [])
    default = None
    if levels and provider == 'claude_code':
        default = 'medium' if model in {'claude-fable-5-2', 'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5'} else 'xhigh' if model == 'claude-opus-4-7' else 'high'
    return {'levels': levels, 'default': default,
            'source': 'documented' if levels else 'unreported',
            'detail': 'Claude Code model-specific effort levels.' if levels else 'This model does not expose a selectable effort level through this connection.'}


def catalog():
    return [{'id': model, 'label': label(model),
             'detail': 'Version catalog · availability depends on your plan.',
             'effort': effort(model)} for family, version in VERSIONS
            for model in [f'claude-{family}-{version}']]
