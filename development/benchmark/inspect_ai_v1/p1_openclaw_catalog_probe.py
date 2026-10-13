"""Read managed native catalog metadata in a fresh, network-isolated profile.

Run under unshare --user --map-root-user --net. This never sends thread/turn,
login or inference RPCs. Catalog data is not model entitlement evidence.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys


async def main() -> None:
    node, package_root, profile = map(Path, sys.argv[1:])
    package = json.loads((package_root / 'package.json').read_text())
    if package['name'] != '@openai/codex' or package['version'] != '0.155.1':
        raise RuntimeError('Unexpected managed native package pin')
    profile.mkdir(mode=0o700)
    codex_home = profile / '.codex'
    codex_home.mkdir(mode=0o700)
    (profile / 'tmp').mkdir(mode=0o700)
    env = {'PATH': str(node.parent) + ':/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'HOME': str(profile), 'CODEX_HOME': str(codex_home),
           'TMPDIR': str(profile / 'tmp')}
    entry = package_root / 'bin/codex.js'
    version_process = await asyncio.create_subprocess_exec(
        str(node), str(entry), '--version', env=env, cwd=profile,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    version, version_error = await version_process.communicate()
    if version_process.returncode or version.strip() != b'codex-cli 0.155.1':
        raise RuntimeError('Managed binary version does not match its package')
    stderr_file = (profile / 'stderr.log').open('wb')
    proc = await asyncio.create_subprocess_exec(
        str(node), str(entry), 'app-server', '--listen', 'stdio://',
        env=env, cwd=profile, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=stderr_file)
    exchanges = []

    async def rpc(method, params, request_id=None):
        if method not in {'initialize', 'initialized', 'account/read', 'model/list'}:
            raise RuntimeError('Only metadata RPCs are permitted')
        request = {'method': method, 'params': params}
        if request_id is not None:
            request['id'] = request_id
        proc.stdin.write((json.dumps(request) + '\n').encode())
        await proc.stdin.drain()
        exchanges.append({'direction': 'request', 'packet': request})
        if request_id is None:
            return None
        while line := await proc.stdout.readline():
            packet = json.loads(line)
            exchanges.append({'direction': 'response', 'packet': packet})
            if packet.get('id') == request_id:
                return packet
        raise RuntimeError('Native metadata process closed before its response')

    try:
        initialize = await rpc('initialize', {'clientInfo': {
            'name': 'eva_native_catalog_probe', 'title': 'Eva native catalog probe',
            'version': '1'}, 'capabilities': {'experimentalApi': True}}, 1)
        await rpc('initialized', {})
        account = await rpc('account/read', {'refreshToken': False}, 2)
        if account.get('result', {}).get('account') is not None:
            raise RuntimeError('Fresh profile unexpectedly contains an account')
        models = await rpc('model/list', {'includeHidden': True, 'limit': 100}, 3)
        if (codex_home / 'auth.json').exists():
            raise RuntimeError('Metadata-only profile unexpectedly contains auth')
        report = {'contract': 'EvaManagedNativeCatalogReadback/v1',
                  'packageVersion': package['version'],
                  'packageJsonSha256': hashlib.sha256((package_root / 'package.json').read_bytes()).hexdigest(),
                  'entrySha256': hashlib.sha256(entry.read_bytes()).hexdigest(),
                  'binaryVersion': version.decode().strip(),
                  'profile': str(profile), 'initialize': initialize,
                  'account': account, 'models': models, 'exchanges': exchanges,
                  'inferenceRPCs': 0, 'authRPCs': 0,
                  'scope': 'Direct managed native metadata only; not OpenClaw host startup, authentication, model entitlement or full comparison admission.'}
        (profile / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'status': 'metadata_recorded', 'binaryVersion': report['binaryVersion'],
                          'accountPresent': False, 'modelError': models.get('error'),
                          'modelCount': len(models.get('result', {}).get('data', [])),
                          'artifact': str(profile / 'result.json'), 'inferenceRPCs': 0}), flush=True)
    finally:
        proc.stdin.close()
        proc.terminate()
        await proc.wait()
        stderr_file.close()


if __name__ == '__main__':
    asyncio.run(main())
