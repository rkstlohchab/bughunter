# Run in a NEW CELL in the SAME Kaggle session after the previous launcher cell.
# Reuse the loaded model and verify an HTTPS ngrok link.

required_helpers = ('known_keys', 'existing_server', 'show_status', 'connect_tunnel', 'redact')
connect_helpers = {name: globals().get(name) for name in required_helpers}
if not all(callable(helper) for helper in connect_helpers.values()):
    raise RuntimeError('Run this cell in the same session after the launcher cell; its helpers are missing.')

from pyngrok import ngrok

original_save_token = ngrok.set_auth_token


def save_token_compat(token, *args, _save=original_save_token, **kwargs):
    try:
        return _save(token, *args, **kwargs)
    except Exception as error:
        if 'Authtoken already set in configuration file:' not in str(error):
            raise
        print('ngrok token is already configured; continuing.', flush=True)


try:
    report, selected_key, process = connect_helpers['existing_server'](connect_helpers['known_keys']())
    connect_helpers['show_status'](report)
    if not report.get('responding'):
        raise RuntimeError('The server did not pass inference: ' + report['status'])
    if not report.get('authenticated'):
        raise RuntimeError('Supply the running server\'s LLAMA_API_KEY before opening ngrok.')
    if 'bughunter-moe' not in report.get('models', []):
        raise RuntimeError('The running API does not expose model bughunter-moe.')

    # Compatibility for connect_tunnel from the earlier pasted launcher.
    ngrok.set_auth_token = save_token_compat
    public_api_url = connect_helpers['connect_tunnel'](selected_key)
    if not public_api_url:
        raise RuntimeError('Enable NGROK_AUTHTOKEN in Kaggle Secrets for this notebook, then rerun this cell.')

    api_key = selected_key
    launcher_result = dict(report, process=process, api_url=public_api_url, tunnel_verified=True)
    print('\nNGROK API URL:', public_api_url, flush=True)
    print('Model: bughunter-moe')
    print('API key: use your existing LLAMA_API_KEY, not the ngrok token.')
except Exception as error:
    raise RuntimeError(connect_helpers['redact'](error)) from None
finally:
    ngrok.set_auth_token = original_save_token
