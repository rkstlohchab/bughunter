"""Hermetic fault tests. No Kaggle account, CUDA, large downloads or ngrok required."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import runpy
import subprocess
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/kaggle_launcher.py'
SPEC = importlib.util.spec_from_file_location('kaggle_launcher', SOURCE)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
DATA = b'GGUF' + bytes(range(256)) * 80


@contextlib.contextmanager
def web_fixture(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class DownloadHandler(BaseHTTPRequestHandler):
    mode = 'normal'
    ranges = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.ranges.append(self.headers.get('Range'))
        offset = int(self.headers['Range'][6:-1]) if self.headers.get('Range') else 0
        if self.mode == '404':
            self.send_error(404)
            return
        if self.mode == 'transient' and len(self.ranges) == 1:
            self.send_error(503)
            return
        if self.mode == '416' and offset:
            self.send_error(416)
            return
        if self.mode == 'ignore_range':
            offset = 0
        data = DATA[offset:]
        if self.mode == 'corrupt':
            data = b'Z' * len(data)
        code = 206 if offset else 200
        self.send_response(code)
        if offset:
            start = offset + 1 if self.mode == 'bad_range' else offset
            self.send_header('Content-Range', f'bytes {start}-{len(DATA)-1}/{len(DATA)}')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.mode == 'short_once' and len(self.ranges) == 1:
            self.wfile.write(data[:len(data)//2])
            self.close_connection = True
        else:
            self.wfile.write(data)


class APIHandler(BaseHTTPRequestHandler):
    mode = 'ready'
    seen_auth = []

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        self.seen_auth.append(self.headers.get('Authorization'))
        if self.mode == 'redirect':
            self.send_response(302)
            self.send_header('Location', '/redirected')
            self.end_headers()
        elif self.path == '/health':
            self.reply(503 if self.mode == 'loading' else 200, {'status': 'ok'})
        elif self.headers.get('Authorization') != 'Bearer correct' and self.mode != 'unprotected':
            self.reply(401, {'error': 'invalid API key'})
        elif self.mode == 'loading':
            self.reply(503, {})
        elif self.mode == 'malformed':
            self.reply(200, [])
        elif self.mode == 'empty':
            self.reply(200, {'data': []})
        else:
            self.reply(200, {'data': [{'id': launcher.ALIAS}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.mode == 'inference_failed':
            self.reply(500, {'error': 'GPU unavailable'})
        elif self.mode == 'blank':
            self.reply(200, {'choices': [{'message': {'content': ''}}]})
        else:
            self.reply(200, {'choices': [{'message': {'content': 'READY'}}]})


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(launcher, 'say').start()
        patch.object(launcher, 'free_bytes', return_value=1024**4).start()
        patch.object(launcher, 'SENSITIVE', set()).start()

    def artifact(self, url='http://unused/model'):
        return {'name': 'test.gguf', 'size': len(DATA),
                'sha256': hashlib.sha256(DATA).hexdigest(), 'url': url}

    def handler(self, mode):
        return type('Handler', (DownloadHandler,), {'mode': mode, 'ranges': []})

    def test_size_and_hash_both_required(self):
        path = self.root / 'model'
        for data in (DATA[:-1], b'GGUF' + b'x' * (len(DATA) - 4)):
            path.write_bytes(data)
            self.assertFalse(launcher.valid_file(path, self.artifact()))
        path.write_bytes(DATA)
        self.assertTrue(launcher.valid_file(path, self.artifact()))

    def test_resume_existing_truncated_final_without_second_copy(self):
        target = self.root / 'test.gguf'
        target.write_bytes(DATA[:1234])
        handler = self.handler('normal')
        with web_fixture(handler) as url:
            launcher.download(self.artifact(url), target)
        self.assertEqual(handler.ranges, ['bytes=1234-'])
        self.assertEqual(target.read_bytes(), DATA)
        self.assertFalse(target.with_name(target.name + '.part').exists())

    def test_range_ignored_restarts_without_appending(self):
        target = self.root / 'test.gguf'
        target.write_bytes(DATA[:1234])
        with web_fixture(self.handler('ignore_range')) as url:
            launcher.download(self.artifact(url), target)
        self.assertEqual(target.read_bytes(), DATA)

    def test_bad_range_keeps_partial_unchanged(self):
        target = self.root / 'test.gguf'
        partial = target.with_name(target.name + '.part')
        partial.write_bytes(DATA[:1234])
        with web_fixture(self.handler('bad_range')) as url:
            with self.assertRaisesRegex(launcher.LauncherError, 'Content-Range'):
                launcher.download(self.artifact(url), target)
        self.assertEqual(partial.read_bytes(), DATA[:1234])
        self.assertFalse(target.exists())

    def test_interrupted_read_is_resumed(self):
        target = self.root / 'test.gguf'
        handler = self.handler('short_once')
        with patch.object(launcher.time, 'sleep'), web_fixture(handler) as url:
            launcher.download(self.artifact(url), target, attempts=3)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(len(handler.ranges), 2)
        self.assertTrue(handler.ranges[1].startswith('bytes='))

    def test_transient_error_retries(self):
        handler = self.handler('transient')
        target = self.root / 'test.gguf'
        with patch.object(launcher.time, 'sleep'), web_fixture(handler) as url:
            launcher.download(self.artifact(url), target, attempts=2)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(len(handler.ranges), 2)

    def test_416_restarts_once(self):
        target = self.root / 'test.gguf'
        target.write_bytes(DATA[:1234])
        handler = self.handler('416')
        with patch.object(launcher.time, 'sleep'), web_fixture(handler) as url:
            launcher.download(self.artifact(url), target, attempts=2)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(handler.ranges, ['bytes=1234-', None])

    def test_404_is_not_retried(self):
        handler = self.handler('404')
        with web_fixture(handler) as url:
            with self.assertRaisesRegex(launcher.LauncherError, 'HTTP 404'):
                launcher.download(self.artifact(url), self.root / 'test.gguf')
        self.assertEqual(len(handler.ranges), 1)

    def test_repeated_bad_hash_never_publishes_final(self):
        target = self.root / 'test.gguf'
        handler = self.handler('corrupt')
        with patch.object(launcher.time, 'sleep'), web_fixture(handler) as url:
            with self.assertRaisesRegex(launcher.LauncherError, 'SHA-256'):
                launcher.download(self.artifact(url), target, attempts=4)
        self.assertFalse(target.exists())
        self.assertEqual(len(handler.ranges), 2)

    def test_corrupt_prefix_recovers_with_fresh_download(self):
        target = self.root / 'test.gguf'
        target.write_bytes(b'bad-prefix')
        handler = self.handler('normal')
        with patch.object(launcher.time, 'sleep'), web_fixture(handler) as url:
            launcher.download(self.artifact(url), target, attempts=3)
        self.assertEqual(target.read_bytes(), DATA)
        self.assertEqual(handler.ranges, ['bytes=10-', None])

    def test_complete_partial_publishes_without_network(self):
        target = self.root / 'test.gguf'
        target.with_name(target.name + '.part').write_bytes(DATA)
        with patch.object(launcher.urllib.request.OpenerDirector, 'open') as request:
            launcher.download(self.artifact(), target)
        request.assert_not_called()
        self.assertEqual(target.read_bytes(), DATA)

    def test_disk_requirement_counts_only_remaining_bytes(self):
        with patch.object(launcher, 'free_bytes', return_value=1000):
            launcher.require_space(self.root, 700, reserve=200)
            with self.assertRaisesRegex(launcher.LauncherError, 'remaining bytes'):
                launcher.require_space(self.root, 900, reserve=200)

    def test_disk_failure_keeps_partial_and_never_publishes_final(self):
        target = self.root / 'test.gguf'
        with web_fixture(self.handler('normal')) as url, \
                patch.object(launcher.os, 'fsync', side_effect=OSError(28, 'disk full')):
            with self.assertRaisesRegex(launcher.LauncherError, 'Disk/quota exhausted'):
                launcher.download(self.artifact(url), target)
        self.assertFalse(target.exists())
        self.assertTrue(target.with_name(target.name + '.part').exists())

    def test_cache_reclamation_preserves_binaries_libraries_and_models(self):
        build = self.root / 'llama.cpp/build'
        (build / 'bin').mkdir(parents=True)
        (build / 'CMakeFiles').mkdir()
        files = [build / 'bin/llama-server', build / 'bin/libggml.so',
                 build / 'CMakeCache.txt', self.root / 'keep.gguf']
        for file in files:
            file.touch()
        obj = build / 'CMakeFiles/source.cpp.o'
        obj.write_bytes(b'object')
        with patch.object(launcher, 'ROOT', self.root), \
                patch.object(launcher, 'free_bytes', return_value=0), \
                patch.object(launcher, 'run_capture', return_value=SimpleNamespace(returncode=1)):
            launcher.reclaim_download_and_build_cache(self.root, 100)
        self.assertFalse(obj.exists())
        self.assertTrue(all(file.exists() for file in files))

    def test_matching_input_used_without_download_or_copy(self):
        folder = self.root / 'input'
        folder.mkdir()
        model = folder / launcher.MODEL_NAME
        model.write_bytes(DATA)
        with patch.object(launcher, 'INPUT', folder), patch.object(launcher, 'MODEL', self.artifact()), \
                patch.object(launcher, 'download') as download:
            self.assertEqual(launcher.ensure_model(), model)
        download.assert_not_called()

    def api_report(self, mode, key='correct'):
        handler = type('Handler', (APIHandler,), {'mode': mode, 'seen_auth': []})
        with web_fixture(handler) as url:
            return launcher.probe(url, key, infer=True), handler

    def test_listing_is_not_inference(self):
        report, _ = self.api_report('inference_failed')
        self.assertEqual(report['status'], 'inference_failed')
        self.assertFalse(report['responding'])

    def test_readable_generated_response_confirms_inference(self):
        report, _ = self.api_report('ready')
        self.assertTrue(report['responding'])
        self.assertTrue(report['authenticated'])
        self.assertEqual(report['reply'], 'READY')

    def test_blank_response_does_not_confirm_inference(self):
        report, _ = self.api_report('blank')
        self.assertFalse(report['responding'])

    def test_wrong_key_loading_and_malformed_responses(self):
        for mode, key, status in [('ready', 'wrong', 'key_missing_or_wrong'),
                                  ('loading', 'correct', 'loading'),
                                  ('malformed', 'correct', 'unexpected_api_response'),
                                  ('empty', 'correct', 'no_model_loaded')]:
            with self.subTest(mode=mode):
                report, _ = self.api_report(mode, key)
                self.assertEqual(report['status'], status)
                self.assertFalse(report['responding'])

    def test_unprotected_server_not_reported_authenticated(self):
        report, _ = self.api_report('unprotected')
        self.assertTrue(report['responding'])
        self.assertFalse(report['authenticated'])

    def test_api_does_not_follow_redirects_with_credentials(self):
        report, handler = self.api_report('redirect')
        self.assertEqual(report['status'], 'unexpected_api_response')
        self.assertEqual(len(handler.seen_auth), 3)

    def test_same_filesystem_is_reported_not_counted_twice(self):
        temp = self.root / 'temp'
        temp.mkdir()
        with patch.object(launcher, 'ROOT', self.root), patch.object(launcher, 'SCRATCH', temp):
            launcher.storage_report()
        self.assertTrue(any('same filesystem' in str(call) for call in launcher.say.call_args_list))

    def archive(self, name, link=None):
        archive = self.root / 'archive.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            member = tarfile.TarInfo(name)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = link
                bundle.addfile(member)
            else:
                member.size = len(DATA)
                bundle.addfile(member, io.BytesIO(DATA))
        return archive

    def test_archive_path_and_symlink_escape_rejected(self):
        for name, link in [('../outside', None), ('/tmp/outside', None),
                           ('lib/evil', '../../outside'), ('lib/evil', '/tmp/outside')]:
            with self.subTest(name=name, link=link):
                archive = self.archive(name, link)
                with self.assertRaisesRegex(launcher.LauncherError, 'Unsafe'):
                    launcher.safe_extract(archive, self.root / 'extract')

    def test_safe_archive_extracts(self):
        launcher.safe_extract(self.archive('bin/llama-server'), self.root / 'extract')
        self.assertEqual((self.root / 'extract/bin/llama-server').read_bytes(), DATA)

    def test_internal_archive_symlink_works(self):
        archive = self.root / 'archive.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            member = tarfile.TarInfo('lib/libggml.so.1')
            member.size = len(DATA)
            bundle.addfile(member, io.BytesIO(DATA))
            link = tarfile.TarInfo('lib/libggml.so')
            link.type = tarfile.SYMTYPE
            link.linkname = 'libggml.so.1'
            bundle.addfile(link)
        launcher.safe_extract(archive, self.root / 'extract')
        self.assertEqual((self.root / 'extract/lib/libggml.so').read_bytes(), DATA)

    def test_runtime_libraries_pair_only_matching_release(self):
        binary = self.root / 'llama-b12098'
        matching = self.root / 'cudart-llama-b12098-bin-ubuntu-cuda-12.8-x64'
        other = self.root / 'cudart-llama-b11982-bin-ubuntu-cuda-12.8-x64'
        for folder in (binary, matching, other):
            folder.mkdir()
            (folder / 'library.so').touch()
        with patch.dict(os.environ, {'LLAMA_ARG_CONTEXT': '99999', 'LLAMA_API_KEY': 'stale'}):
            env = launcher.library_env(binary / 'llama-server', binary)
        self.assertIn(str(matching), env['LD_LIBRARY_PATH'])
        self.assertNotIn(str(other), env['LD_LIBRARY_PATH'])
        self.assertNotIn('LLAMA_API_KEY', env)
        self.assertNotIn('LLAMA_ARG_CONTEXT', env)

    def test_interrupted_runtime_install_restarts_extraction_and_reuses_final(self):
        scratch = self.root / 'temp'
        stage = scratch / 'bughunter-runtime' / (launcher.RELEASE + '.installing')
        stage.mkdir(parents=True)
        (stage / '.launcher-owned').touch()
        (stage / 'old-partial-library').write_bytes(b'bad')
        def fetch(artifact, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            name = 'llama-b12098/llama-server' if artifact['name'].startswith('llama-') else 'cudart-b12098/libcudart.so'
            with tarfile.open(target, 'w:gz') as bundle:
                member = tarfile.TarInfo(name)
                member.size = 4
                bundle.addfile(member, io.BytesIO(b'ELF!'))
            return target
        def check(server, root):
            return {'server': server, 'root': root}
        with patch.object(launcher, 'ROOT', self.root), patch.object(launcher, 'SCRATCH', scratch), \
                patch.object(launcher, 'download', side_effect=fetch) as download, \
                patch.object(launcher, 'check_binary', side_effect=check):
            result = launcher.ensure_runtime()
            self.assertTrue(result['server'].is_file())
            self.assertFalse((result['root'] / 'old-partial-library').exists())
            again = launcher.ensure_runtime()
        self.assertEqual(result['server'], again['server'])
        self.assertEqual(download.call_count, 2)

    def test_bounded_log_tail_redacts_secrets(self):
        log = self.root / 'server.log'
        key = 'unique-secret-value'
        launcher.SENSITIVE.add(key)
        log.write_text('a' * 20_000 + '\nBearer ' + key)
        result = launcher.tail(log, 200)
        self.assertNotIn(key, result)
        self.assertIn('[redacted]', result)
        self.assertLess(len(result), 220)

    def test_gpu_usage_filters_by_pid(self):
        result = SimpleNamespace(returncode=0, stdout='999, GPU-a, 7500\n123, GPU-b, 9123\n123, GPU-c, 9010\n')
        with patch.object(launcher, 'run_capture', return_value=result):
            self.assertEqual(launcher.process_gpu_usage(123), {'GPU-b': 9123, 'GPU-c': 9010})

    def ready(self):
        return {'status': 'responding', 'responding': True, 'authenticated': True,
                'models': [launcher.ALIAS], 'reply': 'READY'}

    def info(self, model=None):
        return {'pid': 123, 'model': str(self.root / (model or launcher.MODEL_NAME))}

    def test_existing_responding_model_skips_install_and_download(self):
        with patch.object(launcher, 'existing_server', return_value=(self.ready(), 'key', self.info())), \
                patch.object(launcher, 'ensure_runtime') as runtime, \
                patch.object(launcher, 'ensure_model') as model, \
                patch.object(launcher, 'process_gpu_usage', return_value={'GPU-a': 9000, 'GPU-b': 9000}), \
                patch.object(launcher, 'save_json'), patch.object(launcher, 'OPEN_TUNNEL', False):
            report = launcher.launch_locked(['key'])
        runtime.assert_not_called()
        model.assert_not_called()
        self.assertTrue(report['responding'])
        self.assertTrue(report['both_gpus_confirmed'])

    def run_reconnect_cell(self, report, url='https://example.ngrok.app/v1',
                           legacy=False, save_error=None, ngrok=None):
        ngrok = ngrok or Mock()
        original = ngrok.set_auth_token
        original.side_effect = save_error
        helpers = {
            'known_keys': Mock(return_value=['private-key']),
            'existing_server': Mock(return_value=(report, 'private-key', self.info())),
            'show_status': Mock(),
            'connect_tunnel': Mock(return_value=url),
            'redact': launcher.redact,
        }
        if legacy:
            def legacy_connect(key):
                ngrok.set_auth_token('ngrok-private-token')
                return url
            helpers['connect_tunnel'].side_effect = legacy_connect
        output = io.StringIO()
        source = SOURCE.with_name('kaggle_connect_running.py')
        try:
            with contextlib.redirect_stdout(output), \
                    patch.dict(launcher.sys.modules, {'pyngrok': SimpleNamespace(ngrok=ngrok)}):
                result = runpy.run_path(str(source), init_globals=helpers)
        finally:
            self.assertIs(ngrok.set_auth_token, original)
        return result, helpers, output.getvalue()

    def test_reconnect_cell_accepts_loaded_q3_and_prints_verified_link(self):
        result, helpers, output = self.run_reconnect_cell(self.ready())
        helpers['connect_tunnel'].assert_called_once_with('private-key')
        self.assertTrue(result['launcher_result']['tunnel_verified'])
        self.assertIn('https://example.ngrok.app/v1', output)
        self.assertNotIn('private-key', output)

    def test_reconnect_cell_requires_authentication(self):
        report = dict(self.ready(), authenticated=False)
        with self.assertRaisesRegex(RuntimeError, 'LLAMA_API_KEY'):
            self.run_reconnect_cell(report)

    def test_reconnect_cell_does_not_claim_link_when_token_missing(self):
        with self.assertRaisesRegex(RuntimeError, 'NGROK_AUTHTOKEN'):
            self.run_reconnect_cell(self.ready(), url=None)

    def test_reconnect_cell_recovers_exact_already_saved_error_and_can_rerun(self):
        error = RuntimeError("An error occurred when saving the auth token: "
                             "b'Authtoken already set in configuration file: "
                             "/root/.config/ngrok/ngrok.yml\\n'")
        ngrok = Mock()
        for _ in range(2):
            result, _, output = self.run_reconnect_cell(
                self.ready(), legacy=True, save_error=error, ngrok=ngrok)
            self.assertTrue(result['launcher_result']['tunnel_verified'])
            self.assertIn('token is already configured', output)
        self.assertEqual(ngrok.set_auth_token.call_count, 2)

    def test_reconnect_cell_preserves_real_token_errors(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid token'):
            self.run_reconnect_cell(self.ready(), legacy=True,
                                    save_error=RuntimeError('invalid token'))

    def test_wrong_model_or_occupied_service_is_preserved(self):
        for report, info in [(self.ready(), self.info('Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated.i1-Q4_K_M.gguf')),
                              ({'status': 'key_missing_or_wrong', 'responding': False}, None)]:
            with patch.object(launcher, 'existing_server', return_value=(report, 'key', info)), \
                    patch.object(launcher, 'start_server') as start, \
                    patch.object(launcher, 'ensure_runtime') as runtime, \
                    patch.object(launcher, 'stop_started_process') as stop:
                with self.assertRaises(launcher.LauncherError):
                    launcher.launch_locked(['key'])
            start.assert_not_called()
            runtime.assert_not_called()
            stop.assert_not_called()

    def test_failed_tunnel_leaves_inference_ready(self):
        with patch.object(launcher, 'existing_server', return_value=(self.ready(), 'key', self.info())), \
                patch.object(launcher, 'process_gpu_usage', return_value={}), \
                patch.object(launcher, 'save_json'), patch.object(launcher, 'OPEN_TUNNEL', True), \
                patch.object(launcher, 'connect_tunnel', side_effect=RuntimeError('quota exceeded')), \
                patch.object(launcher, 'stop_started_process') as stop:
            report = launcher.launch_locked(['key'])
        stop.assert_not_called()
        self.assertTrue(report['responding'])
        self.assertFalse(report['tunnel_verified'])
        self.assertIn('quota exceeded', report['tunnel_error'])

    def test_new_server_failure_cleans_only_its_popen(self):
        process = Mock(pid=123)
        runtime = {'server': Path('/bin/llama-server'), 'env': {}, 'ui_flag': '--no-ui'}
        with patch.object(launcher.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(launcher, 'LOG', self.root / 'log'), \
                patch.object(launcher, 'process_info', return_value=self.info()), \
                patch.object(launcher, 'save_json'), \
                patch.object(launcher, 'wait_ready', side_effect=launcher.LauncherError('timeout')), \
                patch.object(launcher, 'stop_started_process') as stop:
            with self.assertRaisesRegex(launcher.LauncherError, 'timeout'):
                launcher.start_server(runtime, self.root / 'model', 'private-key')
        stop.assert_called_once_with(process)
        args, kwargs = popen.call_args
        self.assertNotIn('private-key', args[0])
        self.assertNotIn('--api-key', args[0])
        self.assertEqual(kwargs['env']['LLAMA_API_KEY'], 'private-key')

    def test_stop_escalates_only_when_own_process_times_out(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('server', 15), 0]
        launcher.stop_started_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()

    def test_private_generated_key_permissions_and_not_printed(self):
        path = self.root / 'private/key'
        with patch.object(launcher, 'KEY_FILE', path):
            key = launcher.new_key()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.read_text(), key)
        self.assertNotIn(key, str(launcher.say.call_args_list))

    def test_tunnel_reuse_requires_loopback_not_just_port_suffix(self):
        for addr, expected in [('http://127.0.0.1:8080', True), ('localhost:8080', True),
                               ('http://external.example:8080', False), ('localhost:18080', False)]:
            with self.subTest(addr=addr):
                self.assertEqual(launcher.matches_upstream(SimpleNamespace(config={'addr': addr})), expected)
        self.assertTrue(launcher.matches_upstream(SimpleNamespace(
            config={}, data={'upstream': {'url': 'http://127.0.0.1:8080'}})))

    def test_failed_public_check_disconnects_only_new_tunnel(self):
        tunnel = SimpleNamespace(public_url='https://test.ngrok.example',
                                 config={'addr': 'http://127.0.0.1:8080'})
        for reused in (False, True):
            with self.subTest(reused=reused):
                ngrok = Mock()
                ngrok.get_tunnels.return_value = [tunnel] if reused else []
                ngrok.connect.return_value = tunnel
                first = {'status': 'loaded', 'authenticated': True}
                second = {'status': 'inference_failed', 'responding': False}
                conf = SimpleNamespace(get_default=Mock(return_value=SimpleNamespace(auth_token=None)))
                with patch.dict(launcher.sys.modules, {'pyngrok': SimpleNamespace(ngrok=ngrok, conf=conf)}), \
                        patch.object(launcher, 'kaggle_secret', return_value='private-token'), \
                        patch.object(launcher, 'probe', side_effect=[first, second]):
                    with self.assertRaisesRegex(launcher.LauncherError, 'Public inference'):
                        launcher.connect_tunnel('key')
                if reused:
                    ngrok.disconnect.assert_not_called()
                else:
                    config = ngrok.get_tunnels.call_args.kwargs['pyngrok_config']
                    ngrok.disconnect.assert_called_once_with(tunnel.public_url, pyngrok_config=config)

    def test_tunnel_uses_runtime_or_saved_token_without_resaving_configuration(self):
        for token in ('secret-token', None):
            with self.subTest(token_present=bool(token)):
                ngrok = Mock()
                ngrok.set_auth_token.side_effect = RuntimeError('Authtoken already set in configuration file: config.yml')
                ngrok.get_tunnels.return_value = []
                ngrok.connect.return_value = SimpleNamespace(public_url='https://test.ngrok.example')
                default = SimpleNamespace(auth_token='saved-token', config_path='/saved/ngrok.yml')
                conf = SimpleNamespace(get_default=Mock(return_value=default))
                with patch.dict(launcher.sys.modules, {'pyngrok': SimpleNamespace(ngrok=ngrok, conf=conf)}), \
                        patch.dict(os.environ, {}, clear=True), \
                        patch.object(launcher, 'kaggle_secret', return_value=token), \
                        patch.object(launcher, 'probe', side_effect=[
                            {'status': 'loaded', 'authenticated': True}, self.ready()]):
                    url = launcher.connect_tunnel('key')
                self.assertEqual(url, 'https://test.ngrok.example/v1')
                ngrok.set_auth_token.assert_not_called()
                config = ngrok.get_tunnels.call_args.kwargs['pyngrok_config']
                self.assertIsNot(config, default)
                self.assertEqual(default.auth_token, 'saved-token')
                self.assertEqual(config.auth_token, token or 'saved-token')
                self.assertEqual(config.config_path, '/saved/ngrok.yml')
                self.assertIs(ngrok.connect.call_args.kwargs['pyngrok_config'], config)
                ngrok.disconnect.assert_not_called()

    def test_older_loading_server_allows_retrying_known_keys(self):
        with patch.object(launcher, 'probe', return_value={'status': 'key_missing_or_wrong'}):
            self.assertEqual(launcher.wait_ready('http://127.0.0.1:8080', 'stale')['status'],
                             'key_missing_or_wrong')

    def test_check_only_does_not_create_files_or_launch(self):
        with patch.object(launcher, 'ROOT', self.root), patch.object(launcher.sys, 'platform', 'linux'), \
                patch.object(launcher, 'CHECK_ONLY', True), patch.object(launcher, 'known_keys', return_value=['key']), \
                patch.object(launcher, 'existing_server', return_value=(self.ready(), 'key', self.info())), \
                patch.object(launcher, 'process_gpu_usage', return_value={}), \
                patch.object(launcher, 'launch_locked') as launch:
            launcher.main()
        launch.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
