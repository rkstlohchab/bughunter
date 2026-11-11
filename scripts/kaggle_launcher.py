# BugHunterClaudex Kaggle one-cell launcher. Paste this entire file into one cell.
# Notebook settings: Internet ON, accelerator GPU T4 x2.
# Kaggle Secrets: LLAMA_API_KEY (recommended), NGROK_AUTHTOKEN (for remote access).
# CHECK_ONLY tests an existing server without downloads, restarts or file changes.
# Q3_K_M matches the model already confirmed responding on the user's T4 x2.
CHECK_ONLY = False
OPEN_TUNNEL = True
PORT = 8080
CONTEXT = 4096
BATCH = 256
MICRO_BATCH = 128
LOAD_TIMEOUT = 900
INFERENCE_TIMEOUT = 120
DOWNLOAD_ATTEMPTS = 5
DISK_RESERVE = 512 * 1024**2

import contextlib
import copy
import csv
import errno
import hashlib
import http.client
import importlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path('/kaggle/working')
SCRATCH = Path('/kaggle/temp')
INPUT = Path('/kaggle/input')
LOG = ROOT / 'llama-server.log'
STATE = ROOT / 'bughunter-launcher-state.json'
KEY_FILE = SCRATCH / 'bughunter-private' / 'llama-api-key'
ALIAS = 'bughunter-moe'
MODEL_QUANT = 'Q3_K_M'
MODEL_REPO = 'mradermacher/Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated-i1-GGUF'
MODEL_REVISION = '78b94f476da55d960f88d1830d0993f72f0ee1aa'
MODEL_NAME = f'Huihui-Qwen3-Coder-30B-A3B-Instruct-abliterated.i1-{MODEL_QUANT}.gguf'
MODEL = {
    'name': MODEL_NAME,
    'size': 14_711_850_496,
    'sha256': '00e4bd529eb749e8eb3397fb9b20d3fedcc63ac82fbed238c7c7ef22c23facd7',
    'url': f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{MODEL_NAME}',
}
# A matched, published CUDA 12.8 pair; no dependency on GitHub's latest-release API.
RELEASE = 'b12098'
RUNTIME_ASSETS = [
    {'name': f'llama-{RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz',
     'size': 187_311_965,
     'sha256': '00e1d8078f6184802ac03cae399fa66b5b07e263b3cdf176d1e55a217f1eab89'},
    {'name': f'cudart-llama-{RELEASE}-bin-ubuntu-cuda-12.8-x64.tar.gz',
     'size': 594_371_566,
     'sha256': '2e8dc09d01b4a0d84f5ac2b1df46d035a626a43ee49ad3819a2785dc30400223'},
]
SENSITIVE = set()


class LauncherError(RuntimeError):
    pass


def redact(value):
    value = str(value)
    for secret in sorted(SENSITIVE, key=len, reverse=True):
        if secret:
            value = value.replace(secret, '[redacted]')
    value = re.sub(r'(?i)(Bearer\s+|--api-key[ =]+|LLAMA_API_KEY=)\S+',
                   r'\1[redacted]', value)
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[redacted]', value)


def say(message):
    print(redact(message), flush=True)


def tail(path, limit=5000):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - limit))
            return redact(stream.read(limit).decode('utf-8', errors='replace'))
    except OSError:
        return '(log unavailable)'


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextlib.contextmanager
def launch_lock():
    import fcntl
    with (ROOT / '.bughunter-launcher.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LauncherError('Another launcher cell is running. Wait for that cell to finish.') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_request(base, route, key=None, payload=None, timeout=8):
    # Credentials never follow redirects and localhost never traverses HTTP_PROXY.
    headers = {'Accept': 'application/json', 'ngrok-skip-browser-warning': '1'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base.rstrip('/') + route, data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise LauncherError('API response exceeded the diagnostic size limit.')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = None
        return response.code, data


def port_open(port=PORT):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            return True
    except OSError:
        return False


def probe(base, key, infer=False, timeout=8):
    result = {'status': 'unreachable', 'responding': False, 'authenticated': False}
    try:
        health, _ = api_request(base, '/health', timeout=timeout)
        denied, _ = api_request(base, '/v1/models', timeout=timeout)
        accepted, data = api_request(base, '/v1/models', key, timeout=timeout)
        result.update(health_http=health, unauth_http=denied, auth_http=accepted)
        if accepted == 401:
            result['status'] = 'key_missing_or_wrong'
            return result
        if accepted == 503 or health == 503:
            result['status'] = 'loading'
            return result
        if accepted != 200 or not isinstance(data, dict):
            result['status'] = 'unexpected_api_response'
            return result
        models = data.get('data')
        if not isinstance(models, list):
            result['status'] = 'invalid_model_listing'
            return result
        ids = [item.get('id') for item in models if isinstance(item, dict)
               and isinstance(item.get('id'), str)]
        result.update(models=ids, authenticated=bool(key) and denied == 401)
        if not ids:
            result['status'] = 'no_model_loaded'
            return result
        result['status'] = 'loaded'
        if infer:
            code, completion = api_request(base, '/v1/chat/completions', key, {
                'model': ALIAS if ALIAS in ids else ids[0],
                'messages': [{'role': 'user', 'content': 'Reply with the single word READY.'}],
                'max_tokens': 32, 'temperature': 0, 'stream': False,
            }, timeout=INFERENCE_TIMEOUT)
            result['inference_http'] = code
            choices = completion.get('choices') if isinstance(completion, dict) else None
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get('message') if isinstance(first, dict) else None
            reply = message.get('content') if isinstance(message, dict) else None
            if code == 200 and isinstance(reply, str) and reply.strip():
                result.update(status='responding', responding=True, reply=redact(reply[:200]))
            else:
                result['status'] = 'inference_failed'
    except (OSError, urllib.error.URLError, http.client.HTTPException, LauncherError) as exc:
        result['status'] = 'inference_unconfirmed' if result.get('models') else 'unreachable'
        result['detail'] = redact(type(exc).__name__ + ': ' + str(exc))[:300]
    return result


def kaggle_secret(name):
    try:
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret(name)
    except Exception:
        return None


def known_keys():
    try:
        saved = KEY_FILE.read_text().strip()
    except OSError:
        saved = None
    values = [kaggle_secret('LLAMA_API_KEY'), os.environ.get('LLAMA_API_KEY'),
              globals().get('api_key'), saved]
    keys = list(dict.fromkeys(value.strip() for value in values
                             if isinstance(value, str) and value.strip()))
    SENSITIVE.update(keys)
    if any(',' in key or any(ord(c) < 32 or ord(c) == 127 for c in key) for key in keys):
        raise LauncherError('LLAMA_API_KEY must be one key without commas or control characters.')
    return keys


def new_key():
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    KEY_FILE.parent.chmod(0o700)
    key = secrets.token_urlsafe(32)
    try:
        fd = os.open(KEY_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise LauncherError(f'The saved API key at {KEY_FILE} is empty or unreadable. '
                            'Supply LLAMA_API_KEY in Kaggle Secrets.') from None
    with os.fdopen(fd, 'w') as stream:
        stream.write(key)
    SENSITIVE.add(key)
    say(f'Generated an API key in {KEY_FILE} (private; outside notebook outputs).')
    return key


def process_info(pid):
    try:
        folder = Path('/proc') / str(pid)
        status = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
        if status[0] == 'Z':
            return None
        argv = (folder / 'cmdline').read_bytes().decode(errors='replace').strip('\0').split('\0')
        if not argv or Path(argv[0]).name != 'llama-server':
            return None
        def argument(names, default=None):
            for index, arg in enumerate(argv):
                if arg in names and index + 1 < len(argv):
                    return argv[index + 1]
                for name in names:
                    if arg.startswith(name + '='):
                        return arg.split('=', 1)[1]
            return default
        return {'pid': int(pid), 'start_ticks': status[19], 'executable': argv[0],
                'model': argument(('-m', '--model')), 'port': argument(('--port',), '8080')}
    except (OSError, IndexError, ValueError):
        return None


def llama_processes():
    return [info for path in Path('/proc').glob('[0-9]*')
            if (info := process_info(path.name))]


def listener_pid(port):
    # Match the listening socket, not just an argv --port value or a stale saved PID.
    inodes = set()
    for table in ('tcp', 'tcp6'):
        try:
            for row in Path('/proc/net', table).read_text().splitlines()[1:]:
                fields = row.split()
                if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == port:
                    inodes.add('socket:[' + fields[9] + ']')
        except (OSError, ValueError, IndexError):
            continue
    for process in llama_processes():
        for fd in Path('/proc', str(process['pid']), 'fd').glob('*'):
            try:
                if os.readlink(fd) in inodes:
                    return process['pid']
            except OSError:
                continue
    return None


def existing_server(keys):
    if not port_open(PORT):
        return {'status': 'not_running', 'responding': False}, None, None
    base = f'http://127.0.0.1:{PORT}'
    selected = keys[0] if keys else None
    for key in keys or [None]:
        report = probe(base, key)
        selected = key
        if report['status'] != 'key_missing_or_wrong':
            break
    if report['status'] == 'loaded':
        report = probe(base, selected, infer=True)
    pid = listener_pid(PORT)
    info = process_info(pid) if pid is not None else None
    if info:
        report['process'] = info
    return report, selected, info


def show_status(report):
    say('Server status: ' + report['status'])
    if report.get('process'):
        say(f"Server PID: {report['process']['pid']}; loaded path: {report['process']['model']}")
    if report.get('models'):
        say('API model IDs: ' + ', '.join(report['models']))
    if report.get('responding'):
        say('Inference response: ' + report['reply'])
    if report.get('detail'):
        say(report['detail'])
    if report['status'] == 'key_missing_or_wrong':
        say('The port is active. Supply its existing LLAMA_API_KEY to test inference.')


def run_capture(command, timeout=30, env=None):
    try:
        return subprocess.run(command, env=env, text=True, errors='replace',
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LauncherError(f'{Path(command[0]).name}: {type(exc).__name__}: {exc}') from None


def gpu_inventory():
    result = run_capture(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.free',
                          '--format=csv,noheader,nounits'])
    if result.returncode:
        raise LauncherError('NVIDIA GPUs unavailable. Enable GPU T4 x2. ' + result.stderr[-300:])
    rows = list(csv.reader(result.stdout.splitlines(), skipinitialspace=True))
    if len(rows) < 2:
        raise LauncherError('This launcher needs two GPUs. Select GPU T4 x2 in Kaggle settings.')
    for row in rows:
        say(f'GPU {row[0]}: {row[2]}, {row[4]} MiB free / {row[3]} MiB total')
    return rows


def process_gpu_usage(pid):
    if pid is None:
        return {}
    try:
        result = run_capture(['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_gpu_memory',
                              '--format=csv,noheader,nounits'])
        if result.returncode:
            return {}
        usage = {}
        for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True):
            if len(row) >= 3 and row[0] == str(pid) and row[2].isdigit():
                usage[row[1]] = int(row[2])
        return usage
    except LauncherError:
        return {}


def sha256(path):
    digest = hashlib.sha256()
    progress = time.monotonic()
    count = 0
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(chunk)
            count += len(chunk)
            if time.monotonic() - progress >= 20:
                say(f'Verifying {Path(path).name}: {count / 1024**3:.2f} GiB hashed')
                progress = time.monotonic()
    return digest.hexdigest()


def valid_file(path, artifact):
    path = Path(path)
    return (path.is_file() and path.stat().st_size == artifact['size']
            and sha256(path) == artifact['sha256'])


def free_bytes(path):
    return shutil.disk_usage(path).free


def require_space(folder, additional, reserve=DISK_RESERVE):
    free = free_bytes(folder)
    need = max(0, additional) + reserve
    if free < need:
        raise LauncherError(
            f'Insufficient disk at {folder}: {free / 1024**3:.2f} GiB free; '
            f'{need / 1024**3:.2f} GiB needed for remaining bytes and reserve. '
            'Partial downloads are retained. Free space on this filesystem or attach the exact '
            f'{MODEL_QUANT} GGUF as a Kaggle input. The selected model will not be changed.')


def storage_report():
    seen = {}
    for folder in (ROOT, SCRATCH):
        device = folder.stat().st_dev
        same = f'; same filesystem as {seen[device]}' if device in seen else ''
        say(f'{folder}: {free_bytes(folder) / 1024**3:.2f} GiB free{same}')
        seen.setdefault(device, folder)
    say('Kaggle output quota and filesystem free space are separate limits; temp files are ephemeral.')


def reclaim_download_and_build_cache(folder, needed):
    if free_bytes(folder) >= needed + DISK_RESERVE:
        return
    # Pip caches are reproducible downloads; never remove other models or datasets.
    result = run_capture([sys.executable, '-m', 'pip', 'cache', 'dir'])
    if result.returncode == 0:
        cache = Path(result.stdout.strip())
        if cache.is_dir() and cache.stat().st_dev == folder.stat().st_dev:
            say('Reclaiming pip download cache on the constrained filesystem.')
            run_capture([sys.executable, '-m', 'pip', 'cache', 'purge'], timeout=120)
    # Old launcher archives and finished build objects are reproducible, not weights.
    caches = [ROOT / 'llama-prebuilt/downloads', SCRATCH / 'llama-prebuilt/downloads']
    candidates = []
    for cache in caches:
        if cache.is_dir() and cache.stat().st_dev == folder.stat().st_dev:
            candidates.extend(p for p in cache.glob('*.tar.gz')
                              if re.fullmatch(r'(?:cudart-)?llama-b\d+-bin-ubuntu-cuda-[\w.-]+\.tar\.gz', p.name))
    build = ROOT / 'llama.cpp/build'
    build_commands = {'cmake', 'make', 'gmake', 'ninja', 'nvcc', 'cc1plus', 'cicc', 'cudafe++'}
    building = False
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            building |= comm.read_text().strip() in build_commands
        except OSError:
            continue
    if (not building and (build / 'CMakeCache.txt').is_file()
            and (build / 'bin/llama-server').is_file()
            and build.resolve().is_relative_to(ROOT.resolve())
            and build.stat().st_dev == folder.stat().st_dev):
        candidates.extend((build / 'CMakeFiles').rglob('*.o'))
    for path in candidates:
        if free_bytes(folder) >= needed + DISK_RESERVE:
            break
        if path.is_file():
            say('Reclaiming reproducible cache file: ' + str(path))
            path.unlink()


def recover_partial(target, artifact):
    partial = target.with_name(target.name + '.part')
    for path in (target, target.with_suffix('.repair.part'),
                 target.with_name(target.name + '.repair.part')):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if 0 < size < artifact['size'] and (not partial.exists() or size > partial.stat().st_size):
            say(f'Resuming incomplete {path.name} ({size:,} bytes).')
            path.replace(partial)
        else:
            say('Removing invalid or duplicate incomplete file: ' + str(path))
            path.unlink()
    if partial.exists() and partial.stat().st_size > artifact['size']:
        say('Discarding oversized partial: ' + partial.name)
        partial.unlink()
    return partial


def download(artifact, target, attempts=DOWNLOAD_ATTEMPTS):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if valid_file(target, artifact):
        return target
    partial = recover_partial(target, artifact)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_error = 'download incomplete'
    checksum_failures = 0
    range_reset = False
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == artifact['size']:
            if valid_file(partial, artifact):
                partial.replace(target)
                say('Verified size and SHA-256: ' + target.name)
                return target
            checksum_failures += 1
            partial.unlink()
            if checksum_failures > 1:
                raise LauncherError('Repeated SHA-256 mismatch for ' + target.name)
            say('Checksum mismatch. Retrying this exact file from byte zero.')
            offset = 0
        remaining = artifact['size'] - offset
        reclaim_download_and_build_cache(target.parent, remaining)
        require_space(target.parent, remaining)
        headers = {'Accept-Encoding': 'identity', 'User-Agent': 'BugHunterClaudex-Kaggle/2'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        request = urllib.request.Request(artifact['url'], headers=headers)
        try:
            say(f'Downloading {target.name}: {offset:,}/{artifact["size"]:,} bytes; attempt {attempt + 1}')
            with opener.open(request, timeout=60) as response:
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise LauncherError('Unexpected download encoding; refusing to append transformed data.')
                if response.code == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',
                                         response.headers.get('Content-Range', ''))
                    if not match or tuple(map(int, match.groups())) != (
                            offset, artifact['size'] - 1, artifact['size']):
                        raise LauncherError('Invalid Content-Range; partial file was not appended.')
                elif response.code == 200:
                    if offset:
                        say('Server ignored Range. Restarting in the same partial file.')
                        partial.unlink()
                        offset = 0
                        require_space(target.parent, artifact['size'])
                else:
                    raise LauncherError(f'Unexpected download HTTP {response.code}')
                length = response.headers.get('Content-Length')
                if length is not None and int(length) != artifact['size'] - offset:
                    raise LauncherError('Download Content-Length does not match the pinned artifact size.')
                written = offset
                progress = time.monotonic()
                with partial.open('ab' if offset else 'wb') as output:
                    while True:
                        chunk = response.read(4 * 1024**2)
                        if not chunk:
                            break
                        if written + len(chunk) > artifact['size']:
                            raise LauncherError('Download exceeded the pinned size; refusing extra bytes.')
                        output.write(chunk)
                        written += len(chunk)
                        if time.monotonic() - progress >= 20:
                            say(f'{target.name}: {100 * written / artifact["size"]:.1f}%')
                            progress = time.monotonic()
                    output.flush()
                    os.fsync(output.fileno())
            if valid_file(partial, artifact):
                partial.replace(target)
                say('Verified size and SHA-256: ' + target.name)
                return target
            last_error = 'short download or checksum mismatch'
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code == 416 and offset and not range_reset:
                partial.unlink(missing_ok=True)
                range_reset = True
                say('Upstream rejected the saved range. Retrying the same file from byte zero.')
            elif code not in (408, 429, 500, 502, 503, 504):
                raise LauncherError(f'Download HTTP {code} for {target.name}. '
                                    'Check Kaggle Internet access and upstream availability.') from None
            last_error = f'HTTP {code}'
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            if getattr(exc, 'errno', None) in (errno.ENOSPC, errno.EDQUOT):
                raise LauncherError('Disk/quota exhausted while downloading. The partial file is retained. '
                                    'Free space or attach the exact GGUF as input, then rerun.') from None
            last_error = redact(type(exc).__name__ + ': ' + str(exc))
        if attempt + 1 < attempts:
            say('Download interrupted: ' + last_error + '; retrying with resume.')
            time.sleep(min(2 ** attempt, 10))
    # A final complete partial can arrive on the last allowed attempt.
    if valid_file(partial, artifact):
        partial.replace(target)
        return target
    raise LauncherError(f'Download failed after {attempts} attempts: {last_error}. '
                        f'Rerun to resume {partial}.')


def safe_extract(archive, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) > 100_000:
            raise LauncherError('Runtime archive has too many entries.')
        require_space(destination, sum(m.size for m in members if m.isfile()))
        for member in members:
            path = destination / member.name
            if Path(member.name).is_absolute() or not path.resolve().is_relative_to(destination):
                raise LauncherError('Unsafe runtime archive path: ' + member.name)
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise LauncherError('Unsupported runtime archive entry: ' + member.name)
            if member.issym() or member.islnk():
                link = (path.parent if member.issym() else destination) / member.linkname
                if Path(member.linkname).is_absolute() or not link.resolve().is_relative_to(destination):
                    raise LauncherError('Unsafe runtime archive link: ' + member.name)
        # Links are created last so archive content cannot redirect subsequent writes.
        for member in sorted(members, key=lambda m: bool(m.issym() or m.islnk())):
            path = destination / member.name
            if not path.resolve().is_relative_to(destination) or path.is_symlink():
                raise LauncherError('Unsafe extraction destination: ' + member.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                path.mkdir(exist_ok=True)
            elif member.isfile():
                with bundle.extractfile(member) as source, path.open('wb') as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)
            elif member.issym():
                path.symlink_to(member.linkname)
            else:
                link = (destination / member.linkname).resolve()
                if not link.is_relative_to(destination) or not link.is_file():
                    raise LauncherError('Invalid runtime hard link: ' + member.name)
                os.link(link, path)


def library_env(server, runtime_root):
    env = os.environ.copy()
    paths = [str(server.parent)]
    library_roots = [runtime_root]
    if re.fullmatch(r'llama-b\d+', runtime_root.name):
        # Official CUDA archives unpack as siblings; pair only the same release.
        sibling = runtime_root.parent / f'cudart-{runtime_root.name}-bin-ubuntu-cuda-12.8-x64'
        if sibling.is_dir():
            library_roots.append(sibling)
    paths += sorted({str(p.parent) for folder in library_roots
                     for p in folder.rglob('*.so*') if p.is_file()})
    paths += ['/usr/local/cuda/lib64', '/usr/local/cuda/targets/x86_64-linux/lib']
    prior = [p for p in env.get('LD_LIBRARY_PATH', '').split(':') if p
             and 'llama-prebuilt' not in p and 'bughunter-runtime' not in p]
    env['LD_LIBRARY_PATH'] = ':'.join(dict.fromkeys(paths + prior))
    # Notebook state must not silently override this launcher's explicit configuration.
    for name in list(env):
        if name.startswith('LLAMA_ARG_') or name == 'LLAMA_API_KEY':
            env.pop(name)
    return env


def check_binary(server, runtime_root):
    env = library_env(server, runtime_root)
    try:
        server.chmod(server.stat().st_mode | stat.S_IXUSR)
        version = run_capture([str(server), '--version'], env=env)
        help_result = run_capture([str(server), '--help'], env=env)
        devices = run_capture([str(server), '--list-devices'], env=env)
        help_text = help_result.stdout + help_result.stderr
        devices_text = devices.stdout + devices.stderr
        if version.returncode or help_result.returncode or devices.returncode:
            say('Runtime check failed: ' + (version.stderr + help_result.stderr + devices.stderr)[-700:])
            return None
        required = ('--split-mode', '--tensor-split', '--api-key', '--jinja')
        cuda_devices = set(re.findall(r'CUDA(\d+)\s*:', devices_text))
        if len(cuda_devices) < 2 or any(flag not in help_text for flag in required):
            say('Runtime does not expose two CUDA devices and the required server options.')
            return None
        ui_flag = '--no-ui' if '--no-ui' in help_text else '--no-webui'
        say('CUDA llama-server verified: ' + str(server))
        return {'server': server, 'root': runtime_root, 'env': env, 'ui_flag': ui_flag}
    except LauncherError as exc:
        say(exc)
        return None


def ensure_runtime():
    roots = [SCRATCH / 'bughunter-runtime' / RELEASE, ROOT / 'bughunter-runtime' / RELEASE,
             ROOT / 'llama-prebuilt', SCRATCH / 'llama-prebuilt', ROOT / 'llama.cpp/build/bin']
    for folder in roots:
        for server in folder.rglob('llama-server') if folder.is_dir() else []:
            # Legacy prebuilt roots may contain several releases; isolate their library trees.
            runtime_root = folder
            if folder.name == 'llama-prebuilt' and server.parent != folder:
                runtime_root = folder / server.relative_to(folder).parts[0]
            found = check_binary(server, runtime_root)
            if found:
                return found
    parent = SCRATCH / 'bughunter-runtime'
    parent.mkdir(parents=True, exist_ok=True)
    # Reuse only this launcher's incomplete staging directory on subsequent runs.
    stage = parent / (RELEASE + '.installing')
    if stage.exists():
        if not (stage / '.launcher-owned').is_file():
            raise LauncherError('Unrecognized installation staging directory: ' + str(stage))
        shutil.rmtree(stage)
    stage.mkdir(exist_ok=True)
    (stage / '.launcher-owned').touch()
    cache = parent / 'downloads'
    for specification in RUNTIME_ASSETS:
        artifact = dict(specification,
                        url=f'https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/{specification["name"]}')
        archive = download(artifact, cache / artifact['name'])
        say('Extracting ' + artifact['name'])
        safe_extract(archive, stage)
        archive.unlink()
    server = next(stage.rglob('llama-server'), None)
    if server is None or not check_binary(server, stage):
        raise LauncherError('The pinned CUDA runtime is incompatible with this session. Check the '
                            'CUDA/GLIBC diagnostics above, GPU T4 x2, and CUDA_VISIBLE_DEVICES. '
                            'Existing model files were preserved.')
    relative = server.relative_to(stage)
    final = parent / RELEASE
    if final.exists():
        if not (final / '.launcher-owned').is_file():
            raise LauncherError('Refusing to replace an unrecognized runtime directory: ' + str(final))
        shutil.rmtree(final)
    stage.replace(final)
    return check_binary(final / relative, final)


def model_candidates():
    candidates = []
    if INPUT.is_dir():
        candidates.extend(INPUT.rglob(MODEL_NAME))
    candidates.extend([ROOT / 'models' / MODEL_NAME, SCRATCH / 'models' / MODEL_NAME])
    return list(dict.fromkeys(candidates))


def ensure_model():
    for path in model_candidates():
        if path.is_file():
            say(f'Checking existing model: {path} ({path.stat().st_size:,} bytes)')
            if valid_file(path, MODEL):
                return path
            say('Invalid size or SHA-256; this file will not be loaded.')
    choices = []
    # Scratch avoids the notebook output quota, but may share filesystem capacity.
    for folder in (SCRATCH / 'models', ROOT / 'models'):
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / MODEL_NAME
        partial = recover_partial(target, MODEL)
        saved = partial.stat().st_size if partial.exists() else 0
        if saved == MODEL['size'] and valid_file(partial, MODEL):
            partial.replace(target)
            return target
        choices.append((target, saved))
    # Prefer a resumable file over a new copy when its filesystem has room.
    choices.sort(key=lambda item: item[1], reverse=True)
    for target, saved in choices:
        needed = MODEL['size'] - saved
        reclaim_download_and_build_cache(target.parent, needed)
        if free_bytes(target.parent) >= needed + DISK_RESERVE:
            return download(MODEL, target)
    target, saved = choices[0]
    require_space(target.parent, MODEL['size'] - saved)
    raise LauncherError('No writable location for the exact model.')


def wait_ready(base, key, process=None):
    deadline = time.monotonic() + LOAD_TIMEOUT
    progress = 0
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise LauncherError(f'llama-server exited with code {process.returncode}.\n' + tail(LOG))
        report = probe(base, key, timeout=min(5, max(0.1, deadline - time.monotonic())))
        if report['status'] == 'loaded':
            return report
        if report['status'] == 'key_missing_or_wrong':
            if process is None:
                return report  # Re-probe all known keys once an older server finishes loading.
            raise LauncherError('The active server rejected LLAMA_API_KEY. Its process was preserved.')
        if time.monotonic() >= progress:
            say('Waiting for model: ' + report['status'])
            progress = time.monotonic() + 20
        time.sleep(2)
    raise LauncherError(f'Model startup timed out after {LOAD_TIMEOUT}s.\n' + tail(LOG))


def stop_started_process(process):
    # Only a Popen object created by this invocation is ever passed here.
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def start_server(runtime, model, key):
    command = [str(runtime['server']), '-m', str(model), '--alias', ALIAS,
               '--host', '127.0.0.1', '--port', str(PORT), '--split-mode', 'layer',
               '--tensor-split', '1,1', '-ngl', '99', '-c', str(CONTEXT), '-np', '1',
               '-b', str(BATCH), '-ub', str(MICRO_BATCH), '--jinja', runtime['ui_flag']]
    env = dict(runtime['env'], LLAMA_API_KEY=key)
    with LOG.open('w') as output:
        process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    globals()['server_proc'] = process
    try:
        save_json(STATE, {'stage': 'loading', 'process': process_info(process.pid), 'model': str(model)})
        say('Started llama-server PID ' + str(process.pid))
        wait_ready(f'http://127.0.0.1:{PORT}', key, process)
        report = probe(f'http://127.0.0.1:{PORT}', key, infer=True)
        if not report.get('responding') or not report.get('authenticated'):
            raise LauncherError('The new server did not pass authenticated inference: '
                                + json.dumps(report) + '\n' + tail(LOG))
        return report, process_info(process.pid)
    except BaseException:
        stop_started_process(process)
        raise


def matches_upstream(tunnel):
    config = getattr(tunnel, 'config', {}) or {}
    data = getattr(tunnel, 'data', {}) or {}
    addr = config.get('addr')
    if not addr:
        upstream = data.get('upstream', {})
        addr = upstream.get('url') if isinstance(upstream, dict) else None
    if not addr:
        return False
    parsed = urlsplit(str(addr) if '://' in str(addr) else 'http://' + str(addr))
    try:
        return parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port == PORT
    except ValueError:
        return False


def connect_tunnel(key):
    token = kaggle_secret('NGROK_AUTHTOKEN') or os.environ.get('NGROK_AUTHTOKEN')
    if token:
        SENSITIVE.add(token)
    try:
        from pyngrok import conf, ngrok
    except ImportError:
        result = run_capture([sys.executable, '-m', 'pip', 'install', '--no-cache-dir',
                              '--disable-pip-version-check', 'pyngrok>=7.2,<10'], timeout=180)
        if result.returncode:
            raise LauncherError('Cannot install pyngrok: ' + result.stderr[-500:])
        importlib.invalidate_caches()
        from pyngrok import conf, ngrok
    # Pass credentials at startup. Re-saving the same token can make older
    # pyngrok versions reject ngrok's successful "Authtoken already set" reply.
    config = copy.copy(conf.get_default())
    if token:
        config.auth_token = token
    else:
        say('Using ngrok credentials already saved in its configuration, if available.')
    tunnels = ngrok.get_tunnels(pyngrok_config=config)
    tunnel = next((t for t in tunnels if t.public_url.startswith('https://') and matches_upstream(t)), None)
    created = tunnel is None
    if created:
        tunnel = ngrok.connect(addr=f'http://127.0.0.1:{PORT}', proto='http', bind_tls=True,
                               pyngrok_config=config)
    try:
        if not tunnel.public_url.startswith('https://'):
            raise LauncherError('ngrok did not provide an HTTPS endpoint.')
        for attempt in range(6):
            report = probe(tunnel.public_url, key, infer=False, timeout=15)
            if report.get('authenticated') and report['status'] == 'loaded':
                break
            if report.get('unauth_http') == 200:
                raise LauncherError('Public endpoint accepted an unauthenticated models request.')
            time.sleep(min(attempt + 1, 5))
        else:
            raise LauncherError('Public endpoint did not pass authentication: ' + json.dumps(report))
        report = probe(tunnel.public_url, key, infer=True, timeout=15)
        if not report.get('responding') or not report.get('authenticated') or ALIAS not in report.get('models', []):
            raise LauncherError('Public inference was not verified: ' + json.dumps(report))
        return tunnel.public_url.rstrip('/') + '/v1'
    except BaseException:
        if created:
            try:
                ngrok.disconnect(tunnel.public_url, pyngrok_config=config)
            except Exception:
                say('Could not disconnect the tunnel created by this run; inspect the ngrok dashboard.')
        raise


def launch_locked(keys):
    report, key, info = existing_server(keys)
    show_status(report)
    if report['status'] == 'loading':
        say('An earlier server is loading. Waiting without restarting it.')
        wait_ready(f'http://127.0.0.1:{PORT}', key)
        report, key, info = existing_server(keys)
        show_status(report)
    if report['status'] != 'not_running':
        if not report.get('responding'):
            raise LauncherError('An existing service occupies the port; it was left running. '
                                'Resolve the reported key/API/loading issue before rerunning.')
        if not report.get('authenticated'):
            raise LauncherError('The existing server responds but is not protected by LLAMA_API_KEY. '
                                'It was preserved; no public tunnel was opened.')
        if not info or Path(info.get('model') or '').name != MODEL_NAME or ALIAS not in report.get('models', []):
            raise LauncherError('The existing model responds, but its process/model does not match the '
                                f'requested {MODEL_QUANT} and alias. It was preserved. Use CHECK_ONLY to inspect it; '
                                'stop it yourself if you want to replace it with the selected model.')
        say(f'Reusing the responding {MODEL_QUANT} process. No model download or restart needed.')
    else:
        active = llama_processes()
        if active:
            raise LauncherError('Other llama-server processes still hold resources: '
                                + ', '.join(str(p['pid']) for p in active)
                                + '. Inspect those PIDs before starting another large model.')
        gpu_inventory()
        storage_report()
        if os.environ.get('BUG_HUNTER_QUANT', MODEL_QUANT) != MODEL_QUANT:
            say(f'Ignoring stale BUG_HUNTER_QUANT: this launcher uses the selected {MODEL_QUANT} model.')
        runtime = ensure_runtime()
        model = ensure_model()
        key = keys[0] if keys else new_key()
        report, info = start_server(runtime, model, key)
        report.update(model_file_verified=True, model_sha256=MODEL['sha256'])
    globals()['api_key'] = key
    usage = process_gpu_usage(info['pid'] if info else None)
    report.update(process=info, gpu_mib=usage, both_gpus_confirmed=sum(v > 500 for v in usage.values()) >= 2)
    say('Local inference verified. Reply: ' + report['reply'])
    say('Server-specific GPU usage (MiB): ' + json.dumps(usage))
    if not report['both_gpus_confirmed']:
        say('Both-GPU placement is unconfirmed; API inference passed. Check nvidia-smi and the server log.')
    report['api_url'] = f'http://127.0.0.1:{PORT}/v1'
    report['tunnel_verified'] = False
    save_json(STATE, report)
    if OPEN_TUNNEL:
        try:
            public_url = connect_tunnel(key)
            if public_url:
                report.update(api_url=public_url, tunnel_verified=True)
        except Exception as exc:
            report['tunnel_error'] = redact(str(exc))[:1000]
            say('Local model remains ready; tunnel setup failed: ' + report['tunnel_error'])
    save_json(STATE, report)
    say('API URL: ' + report['api_url'])
    say('Model: ' + ALIAS)
    say('API key: your LLAMA_API_KEY secret, or the private generated key file. Never use NGROK_AUTHTOKEN here.')
    say('Readiness report: ' + str(STATE))
    return report


def main():
    if not sys.platform.startswith('linux') or not ROOT.is_dir():
        raise LauncherError('Run this cell inside Kaggle with Internet ON and GPU T4 x2.')
    keys = known_keys()
    if CHECK_ONLY:
        report, _, info = existing_server(keys)
        report['gpu_mib'] = process_gpu_usage(info['pid'] if info else None)
        show_status(report)
        say('Server-specific GPU usage (MiB): ' + json.dumps(report['gpu_mib']))
        return report
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with launch_lock():
        return launch_locked(keys)


if __name__ == '__main__':
    try:
        launcher_result = main()
    except KeyboardInterrupt:
        say('Interrupted. Partial downloads are retained; rerun this cell to resume.')
        raise
    except Exception as error:
        raise RuntimeError(redact(error)) from None
