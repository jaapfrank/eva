"""Task-bound package review and publication on the existing evaluation host.

There is one immutable candidate per handoff. A restart never re-runs dispatched
tests or application. A distinct ordinary native task owns the quality review.
"""
from __future__ import annotations

import fcntl
import json
import os
import hmac
import secrets
from pathlib import Path
import threading
import tarfile
import time
from collections import OrderedDict

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.owned_package import (
    archive_files, json_object, key, read_workspace_file, sha, snapshot_candidate,
)
from eva.operations.owned_package_application import configuration_status, validate_policy, validate_review
from eva.operations.reviewed_component_application import _artifact, _path, _save


class OwnedPackageHandoff:
    def __init__(self, config, *, runtime, programming, applications, verify=None, start=True):
        if (not isinstance(config, dict) or not {"receipt_root", "runtime_policy"} <= set(config)
                or set(config) - {"receipt_root", "runtime_policy", "endpoint_base", "owner_scopes", "package_settings", "owner_base_url"}
                or not isinstance(config["runtime_policy"], dict)):
            raise ValueError("invalid host-owned package handoff configuration")
        if config['runtime_policy'].get('environment') or config['runtime_policy'].get('secret_files'):
            raise ValueError('package settings must be scoped to their package name')
        if not isinstance(config.get('package_settings', {}), dict):
            raise ValueError('host package settings differ')
        self.config, self.runtime, self.programming = config, runtime, programming
        self.effect_replies, self.effect_reply_bytes = OrderedDict(), 0
        self.applications, self.verify = applications, verify or self._verify
        self.root = _path(config["receipt_root"], exists=False)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_uid != os.geteuid() or self.root.stat().st_mode & 0o077:
            raise ValueError("private host-owned package receipt root required")
        self.lock = (self.root / ".dispatcher.lock").open("a+b")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.jobs = {}
        for path in self.root.glob("*.json"):
            job = json_object(path.read_bytes())
            if path.name != key(job["review_id"]) + ".json" or job.get("contract") != "EvaOwnedPackageHandoff/v1":
                raise ValueError("package handoff receipt differs")
            if job["state"] in {"verifying", "publishing"}:
                job.update(state="unconfirmed", reason="inspect_existing_package_evidence")
                _save(path, job)
            self.jobs[job["review_id"]] = job
        self.condition, self.closed = threading.Condition(), False
        self.thread = threading.Thread(target=self._run, name="owned-package-handoff", daemon=True)
        if start:
            self.thread.start()

    def _call(self, action, **arguments):
        return self.runtime.package_call(action, **arguments)

    def _save(self, job):
        _save(self.root / (job["review_id"] + ".json"), job)

    def _job(self, review_id):
        key(review_id)
        matches = [job for job in self.jobs.values()
                   if review_id in {job["review_id"], job["package_sha256"]}]
        if len(matches) != 1:
            raise ValueError("package handoff is unavailable")
        return matches[0]

    def contains(self, review_id):
        return review_id == '0' * 64 or any(review_id in {job["review_id"], job["package_sha256"]} for job in self.jobs.values())

    def _public(self, job):
        findings = {}
        review_file = self.root / job['review_id'] / 'review.json'
        if review_file.exists():
            review = json_object(read_workspace_file(self.root, job['review_id'], 'review.json', maximum=65536))
            # Reviewer text is evidence to inspect, never a new owner command.
            findings = {'independent_review': {'decision': review.get('decision'),
                'reason': review.get('reason'), 'checks': review.get('checks'),
                'classification': 'reviewer_findings_not_owner_instruction'}}
        return {**findings, "contract": "EvaOwnedPackageHandoff/v1", "review_id": job["review_id"],
                "package_sha256": job["package_sha256"], "task_id": job["task_id"],
                "state": job["state"], "installed": job["state"] == "completed",
                "semantic_success_verified": False,
                **({'owner_setup_url': self.setup_link(job)} if self.setup_link(job)
                   and job['state'] in {'waiting_for_configuration', 'application_requested', 'completed'} else {}),
                **({'mcp_endpoint': self.endpoint(job), 'connection_state': 'verify_through_existing_connector_registry',
                    'next_action': 'Connect this exact managed endpoint through the existing connect_service route, verify actual use and return to the original user.'}
                   if job['state'] == 'completed' and self.config.get('endpoint_base') else {}),
                **({"reason": job["reason"]} if "reason" in job else {}),
                **({"configuration": job["configuration"]} if "configuration" in job else {})}

    def _package(self, job):
        package = json_object((self.root / job['review_id'] / 'package.json').read_bytes())
        if (package.get('package_sha256') != job['package_sha256']
                or canonical_sha256({k: v for k, v in package.items() if k != 'package_sha256'}) != job['package_sha256']):
            raise ValueError('immutable package snapshot changed')
        return package

    def setup_link(self, job):
        from eva.adapters.connectors.mcp_registration import canonical_mcp_endpoint
        base = self.config.get('owner_base_url')
        if not isinstance(base, str) or canonical_mcp_endpoint(base).rstrip('/') != base:
            return None
        return base + '/internal/v1/dashboard/owned-packages/' + job['review_id']

    def _settings_path(self, package):
        identity = canonical_sha256({'owner_binding': package['origin']['owner_binding_sha256'],
                                     'name': package['descriptor']['name']})
        return self.root / ('settings-' + identity + '.private')

    def _settings(self, package):
        path = self._settings_path(package)
        if path.exists():
            return _artifact({'path': str(path), 'sha256': sha(path.read_bytes())})[1]
        return self.config.get('package_settings', {}).get(package['descriptor']['name'], {})

    def _settings_fingerprint(self, job):
        package = self._package(job)
        return canonical_sha256({'configuration': self.config, 'package_settings': self._settings(package)})

    def setup(self, action, review_id, scope_sha256, *, environment=None, secret_values=None, query=''):
        from eva.operations.owned_package_application import OwnedPackageRuntime
        if scope_sha256 not in self.config.get('owner_scopes', ()):
            raise ValueError('current package owner scope required')
        job = self._job(review_id)
        owner = self._call('owner_current', scope_sha256=scope_sha256)
        if owner.get('current') is not True or owner.get('owner_binding_sha256') != job['origin']['owner_binding_sha256']:
            raise ValueError('package setup current owner binding differs')
        if job['state'] not in {'waiting_for_configuration', 'application_requested', 'completed'}:
            raise ValueError('package has no independent acceptance for owner setup')
        directory = self.root / job['review_id']
        package = self._package(job)
        files = archive_files((directory / 'archive.tar.gz').read_bytes())
        schema = json_object(files[package['descriptor']['config_schema']])
        settings = self._settings(package)
        bearer = package['descriptor']['transport']['bearer_env']
        names = set(package['descriptor']['permissions']['secret_names']) - {bearer}
        if action == 'describe':
            defaults = {k: v['default'] for k, v in schema.get('properties', {}).items()
                        if isinstance(v.get('default'), str)}
            return {'name': package['descriptor']['name'], 'state': job['state'],
                'schema': schema, 'environment': {**defaults, **settings.get('environment', {})},
                'secret_names': sorted(names), 'configured_secrets': sorted(settings.get('secret_files', {})),
                'activation_available': bool(package['descriptor'].get('activation')),
                'redirect_env': package['descriptor'].get('activation', {}).get('redirect_env'),
                'installed': job['state'] == 'completed', 'actual_service_use_verified': False}
        if action == 'save':
            if job['state'] != 'waiting_for_configuration' or not isinstance(environment, dict) or not isinstance(secret_values, dict):
                raise ValueError('only a waiting package can receive initial owner configuration')
            if (not set(environment) <= set(schema.get('properties', {})) or not set(secret_values) <= names
                    or any(not isinstance(value, str) or len(value.encode()) > 8192 or any(c in value for c in '\x00\r\n')
                           for value in [*environment.values(), *secret_values.values()])):
                raise ValueError('package configuration fields differ')
            selected = dict(settings.get('secret_files', {}))
            for name, value in secret_values.items():
                if not value:
                    continue  # A blank form never erases an existing credential.
                destination = self.root / ('credential-' + secrets.token_hex(24) + '.private')
                self._put_raw(destination, value.encode())
                selected[name] = str(destination)
            settings = {'environment': environment, 'secret_files': selected}
            policy = {**self.config['runtime_policy'], **settings,
                      'network_hosts': package['descriptor']['permissions']['network_hosts']}
            # Validate before selecting any new settings. No credentials are
            # returned to the browser, model, query string or application receipt.
            status = configuration_status(policy, package, files)
            validate_policy(policy, package)
            _save(self._settings_path(package), settings)
            with self.condition:
                self.condition.notify_all()
            return {'saved': True, **status, 'installed': False}
        if job['state'] != 'completed' or action not in {'start', 'callback'}:
            raise ValueError('package activation is not available')
        receipt = json_object((directory / 'application.private.json').read_bytes())
        callback = None
        if action == 'callback':
            from urllib.parse import parse_qs
            parameters = parse_qs(query, strict_parsing=True, max_num_fields=16)
            states = parameters.get('state', [])
            if len(states) != 1 or not 32 <= len(states[0]) <= 512:
                raise ValueError('package callback has no issued state')
            state_key = sha(states[0].encode())
            issued_path = directory / ('authorization-state-' + state_key + '.private')
            if not issued_path.exists():
                raise ValueError('package callback state was not issued by this activation')
            issued = json_object(issued_path.read_bytes())
            if issued.get('package_sha256') != package['package_sha256'] or time.time() - issued.get('created_at', 0) > 600:
                raise ValueError('package authorization state expired or changed')
            callback = directory / ('callback-' + state_key + '.private')
        if callback is not None:
            if callback.exists():
                previous = json_object(callback.read_bytes())
                if previous.get('input_sha256') != sha(query.encode()):
                    raise ValueError('one-time package callback input changed')
                if previous.get('effect_status') == 'confirmed':
                    return {'authorization_callback_processed': True, 'actual_service_use_verified': False}
                raise ValueError('previous authorization callback outcome is unconfirmed')
            _save(callback, {'effect_key': state_key, 'input_sha256': sha(query.encode()), 'effect_status': 'unknown',
                             'verification_ref': None}, initial=True)
        result = OwnedPackageRuntime().activation(receipt, package, action=action, query=query)
        if action == 'start':
            from urllib.parse import urlsplit, parse_qs
            url = result.get('authorization_url')
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (parsed is None or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.hostname not in package['descriptor']['permissions']['network_hosts']):
                raise ValueError('package returned no approved HTTPS authorization URL')
            states = parse_qs(parsed.query, strict_parsing=True, max_num_fields=30).get('state', [])
            if len(states) != 1 or not 32 <= len(states[0]) <= 512:
                raise ValueError('package authorization URL lacks one bounded state')
            _save(directory / ('authorization-state-' + sha(states[0].encode()) + '.private'),
                {'package_sha256': package['package_sha256'], 'created_at': time.time()}, initial=True)
            return {'authorization_url': url}
        if result.get('authorized') is not True:
            raise ValueError('package did not confirm its authorization callback')
        _save(callback, {'effect_key': state_key, 'input_sha256': sha(query.encode()), 'effect_status': 'confirmed',
                         'verification_ref': canonical_sha256(result)})
        self._notify(job, 'authorization_completed')
        return {'authorization_callback_processed': True, 'actual_service_use_verified': False}

    def endpoint(self, job):
        from eva.adapters.connectors.mcp_registration import canonical_mcp_endpoint
        base = self.config.get('endpoint_base')
        if not isinstance(base, str) or canonical_mcp_endpoint(base).rstrip('/') != base:
            raise ValueError('exact managed host endpoint is not configured')
        return base + '/owned-package/' + job['review_id'] + '/mcp'

    def transport_auth(self, endpoint, scope_sha256):
        if scope_sha256 not in self.config.get('owner_scopes', ()):
            raise ValueError('package registration owner scope differs')
        matches = [job for job in self.jobs.values() if job['state'] == 'completed' and self.endpoint(job) == endpoint]
        if len(matches) != 1:
            raise ValueError('managed package endpoint is not active')
        owner = self._call('owner_current', scope_sha256=scope_sha256)
        if owner.get('current') is not True or owner.get('owner_binding_sha256') != matches[0]['origin']['owner_binding_sha256']:
            raise ValueError('managed package current owner binding differs')
        token = (self.root / matches[0]['review_id'] / 'proxy-token.private').read_text()
        return {'auth_type': 'bearer', 'auth_token': token}

    def proxy(self, review_id, authorization, message):
        from eva.operations.owned_package_application import OwnedPackageRuntime, receipt_confirms
        job = self._job(review_id)
        if job['state'] != 'completed' or not isinstance(authorization, str):
            raise ValueError('package is not active')
        directory = self.root / job['review_id']
        expected = 'Bearer ' + (directory / 'proxy-token.private').read_text()
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise PermissionError('package transport authentication required')
        package = self._package(job)
        receipt = json_object((directory / 'application.private.json').read_bytes())
        if not receipt_confirms(receipt, package, job['task_id']):
            raise ValueError('package activation receipt differs')
        method = message.get('method')
        if method == 'notifications/initialized' and 'id' not in message:
            return None
        if message.get('jsonrpc') != '2.0' or method not in {'initialize', 'ping', 'tools/list', 'tools/call'}:
            raise ValueError('unsupported stateless package request')
        if method == 'tools/call' and message.get('params', {}).get('name') not in package['descriptor']['tool_names']:
            raise ValueError('package tool is outside the reviewed catalog')
        if method != 'tools/call':
            return OwnedPackageRuntime().rpc(receipt, package, message)
        params = message.get('params', {})
        effect = key(params.get('_meta', {}).get('eva_effect_key'))
        inputs = {k: v for k, v in params.items() if k != '_meta'}
        input_sha256 = canonical_sha256({'package': package['package_sha256'], 'params': inputs})
        effects = directory / 'effects'; effects.mkdir(mode=0o700, exist_ok=True)
        effect_file = effects / (effect + '.json')
        with self.condition:
            if effect_file.exists():
                previous = json_object(effect_file.read_bytes())
                if previous.get('input_sha256') != input_sha256:
                    raise ValueError('package effect identity was reused for changed arguments')
                cached = self.effect_replies.get((job['review_id'], effect))
                if previous.get('effect_status') == 'confirmed' and cached is not None:
                    return {**json.loads(cached), 'id': message.get('id')}
                return {'jsonrpc': '2.0', 'id': message.get('id'), 'error': {'code': -32011,
                    'message': 'Existing package dispatch is not repeated. Inspect its retained receipt.',
                    'data': {**previous, 'semantic_success_verified': False}}}
            _save(effect_file, {'effect_key': effect, 'input_sha256': input_sha256,
                               'effect_status': 'unknown', 'verification_ref': None}, initial=True)
        # The durable reservation exists before the one possible service call.
        # No database/global queue lock is held over this external operation.
        result = OwnedPackageRuntime().rpc(receipt, package, {**message, 'params': inputs})
        if result.get('jsonrpc') != '2.0' or result.get('id') != message.get('id') or ('result' in result) == ('error' in result):
            raise ValueError('package returned an unbound response')
        raw = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
        with self.condition:
            _save(effect_file, {'effect_key': effect, 'input_sha256': input_sha256,
                'effect_status': 'confirmed', 'verification_ref': sha(raw), 'semantic_success_verified': False})
            # Response bytes are an ephemeral transport cache, never another
            # permanent mailbox/conversation archive. Restart retains only proof.
            while self.effect_replies and self.effect_reply_bytes + len(raw) > 4 * 1024 * 1024:
                _, removed = self.effect_replies.popitem(last=False)
                self.effect_reply_bytes -= len(removed)
            if len(raw) <= 4 * 1024 * 1024:
                self.effect_replies[(job['review_id'], effect)] = raw
                self.effect_reply_bytes += len(raw)
        return result

    def _linked_candidate(self, metadata):
        origin = metadata.get("linked_package_origin")
        if not origin or metadata.get("can_publish_package") is not True or metadata.get("review_origin"):
            return None
        root = self.programming.config.workspace_root
        try:
            candidate = json_object(read_workspace_file(
                root, origin["workspace_key"], "package/candidate.json", maximum=65536))
            package, raw, _ = snapshot_candidate(root, origin, candidate["archive_sha256"])
        except (FileNotFoundError, ValueError, tarfile.TarError):
            return None
        self._require_no_handoff(package)
        return package, raw

    def request(self, review_id, request_id, task_id, *, scope_sha256=None):
        key(review_id); key(request_id)
        metadata = self._call("metadata", task_id=task_id, **({'scope_sha256': scope_sha256} if scope_sha256 else {}))
        if scope_sha256 is not None and metadata['scope_sha256'] != scope_sha256:
            raise ValueError('package caller source scope differs')
        if metadata.get("review_origin") is not None:
            raise ValueError("an independent reviewer cannot publish a package")
        if metadata.get('can_publish_package') is not True:
            raise ValueError('original package application capability is required')
        with self.condition:
            if review_id in self.jobs:
                job = self.jobs[review_id]
                if any(metadata[name] != job['origin'][name] for name in
                       ('execution_id', 'owner_binding_sha256', 'source_ingress_sha256', 'workspace_key', 'scope_sha256')):
                    raise ValueError("package handoff is already owned by another execution")
                # A legitimate resumed generation observes the immutable original
                # request. It cannot retarget or dispatch the application again.
                return self._public(job)
            if metadata["state"] != "call_started":
                raise ValueError("package submission requires its active originating task")
            if self.programming.processes_idle(metadata["session_key"], workspace_key=metadata["workspace_key"]).get("idle") is not True:
                raise ValueError("package builder still has running processes")
            source_package = None
            try:
                try:
                    package, raw, _ = snapshot_candidate(self.programming.config.workspace_root, metadata, review_id)
                except FileNotFoundError:
                    linked = self._linked_candidate(metadata)
                    if linked is None or linked[0]["archive_sha256"] != review_id:
                        raise
                    source_package, raw = linked
                    package = {**source_package, "origin": {
                        name: metadata[name] for name in source_package["origin"]}}
                    del package["package_sha256"]
                    package["package_sha256"] = canonical_sha256(package)
            except (ValueError, FileNotFoundError, tarfile.TarError):
                # Validation precedes every durable handoff and every external
                # effect. Give the builder correctable input feedback, not an
                # ambiguous transport failure. Authority errors above stay fatal.
                return {'contract':'EvaOwnedPackageHandoff/v1','review_id':review_id,
                    'task_id':task_id,'state':'candidate_invalid','installed':False,
                    'reason':'candidate_files_do_not_match_the_host_contract',
                    'application_requested':False,'semantic_success_verified':False}
            current = self._call("metadata", task_id=task_id, scope_sha256=metadata['scope_sha256'])
            if current != metadata:
                raise ValueError("package task changed during snapshot")
            directory = self.root / review_id
            directory.mkdir(mode=0o700)
            self._put_raw(directory / "archive.tar.gz", raw)
            _save(directory / "package.json", package, initial=True)
            task_input = {"task": metadata["task_text"], "origin": package["origin"]}
            if source_package is not None:
                task_input["candidate_source"] = {
                    "origin": source_package["origin"], "package_sha256": source_package["package_sha256"]}
            _save(directory / "task.json", task_input, initial=True)
            job = {"contract": "EvaOwnedPackageHandoff/v1", "review_id": review_id,
                "package_sha256": package["package_sha256"], "request_id": request_id,
                "task_id": task_id, "origin": package["origin"], "state": "waiting_for_builder"}
            self._save(job)
            self.jobs[review_id] = job
            self.condition.notify_all()
            return self._public(job)

    @staticmethod
    def _put_raw(path, raw):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def no_dispatch_proof(self, *, task_id, scope_sha256):
        """Host-only observation; no review, installation or application call."""
        metadata = self._call('metadata', task_id=task_id, scope_sha256=scope_sha256)
        if (metadata.get('scope_sha256') != scope_sha256 or metadata.get('review_origin') is not None
                or metadata.get('can_publish_package') is not True or metadata.get('active_lease') is not False
                or metadata.get('state') != 'ambiguous' or metadata.get('latest_work_id') != task_id):
            raise ValueError('closed original package builder required')
        candidate = json_object(read_workspace_file(self.programming.config.workspace_root,
            metadata['workspace_key'], 'package/candidate.json', maximum=65536))
        package, _, _ = snapshot_candidate(self.programming.config.workspace_root, metadata, candidate['archive_sha256'])
        with self.condition:
            self._require_no_handoff(package)
            return {'contract': 'EvaOwnedPackageNoDispatch/v1', 'origin': package['origin'],
                'archive_sha256': package['archive_sha256'], 'package_sha256': package['package_sha256'],
                'handoff_exists': False, 'application_requested': False, 'installed': False}

    def _require_no_handoff(self, package):
        archive_id = package['archive_sha256']
        if (self.contains(archive_id) or self.contains(package['package_sha256'])
                or archive_id in self.applications.releases):
            raise ValueError('an existing application handoff cannot be pardoned')
        # A partial admission can exist on disk before the job is registered.
        for path in (self.root / archive_id, self.root / (archive_id + '.json')):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError('an existing application handoff cannot be pardoned')

    def status(self, review_id, *, task_id, scope_sha256=None):
        metadata = self._call("metadata", task_id=task_id, **({'scope_sha256': scope_sha256} if scope_sha256 else {}))
        if scope_sha256 is not None and metadata['scope_sha256'] != scope_sha256:
            raise ValueError('package status source scope differs')
        if review_id == '0' * 64:
            with self.condition:
                linked = self._linked_candidate(metadata)
            linked_details = {}
            if linked is not None:
                package, _ = linked
                linked_details = {"linked_candidate": {
                    "name": package["descriptor"]["name"], "version": package["descriptor"]["version"],
                    "archive_sha256": package["archive_sha256"], "work_id": package["origin"]["task_id"]}}
            return {**linked_details, "state": "package_contract", "installed": False,
                "contract": "EvaOwnedPackageCandidate/v1", "submission_file": "/workspace/package/candidate.json",
                "fields": {"contract": "EvaOwnedPackageCandidate/v1", "archive": "relative path of your gzip tar archive in /workspace",
                    "archive_sha256": "exact SHA-256 of that archive", "descriptor": {
                        "contract": "EvaOwnedPackage/v1", "name": "lowercase package identifier", "version": "semantic x.y.z",
                        "files": "exact relative-path -> SHA-256 map of ALL archive files",
                        "entrypoint": "relative Python HTTP entrypoint", "tests": "list of relative test files",
                        "config_schema": "relative closed JSON Schema file, no remote references",
                        "permissions": {"network_hosts": "exact required DNS hosts", "secret_names": "required secret environment names",
                                        "persistent_data": "boolean; only /data persists"},
                        "tool_names": "exact MCP catalog names",
                        "transport": {"kind": "http-json-rpc", "port": "integer 1024..65535", "path": "local MCP path",
                                      "bearer_env": "host-generated secret name also declared in permissions.secret_names"},
                            "activation": "OPTIONAL object: start_path (POST {} -> authorization_url), callback_path (GET -> authorized boolean), redirect_env (declared configuration name). Accept the exact host-provided redirect URL, including its path. OAuth state must be single-use and at least 32 characters."}},
                "limits": {"compressed_bytes": 8388608, "expanded_bytes": 33554432, "files": 256},
                "handoff": "For a linked_candidate, use its exact archive_sha256 without copying files. Otherwise prepare the local candidate. Call apply_reviewed_component_improvement(review_id=archive_sha256), then finish this programming stage. The host independently reviews and applies; no self-approved success.",
                "runtime": "Host-pinned Python image; no package Dockerfile or pip install. Read-only /package, optional private /data. Exact host configuration and required independent rights review precede network or secret access.",
                "configuration": "Configuration schema describes only non-secret string environment values. Actual secrets come from separate private host bindings. Missing access remains an explicit prerequisite, never a fabricated link or successful connection."}
        if not self.contains(review_id):
            key(review_id)
            return {'contract': 'EvaOwnedPackageHandoff/v1', 'review_id': review_id,
                'task_id': task_id, 'state': 'not_submitted', 'installed': False,
                'semantic_success_verified': False,
                'next_action': 'No package handoff exists for this identifier. If your candidate is ready, submit its exact archive_sha256 with apply_reviewed_component_improvement.'}
        job = self._job(review_id)
        if metadata.get("review_package_sha256") == job["package_sha256"]:
            if metadata.get("review_origin") != job["origin"]:
                raise ValueError("package review origin differs")
            self._seed_review(job, metadata)
            return {**self._public(job), "review_input": "/workspace/package-review",
                    "review_output": "/workspace/package/review.json",
                    "verification_output": "/workspace/package/verify.py"}
        if metadata["owner_binding_sha256"] != job["origin"]["owner_binding_sha256"]:
            raise ValueError("package status is outside the original owner scope")
        result = self._public(job)
        if job['state'] == 'completed':
            from eva.operations.owned_package_application import OwnedPackageRuntime, receipt_confirms
            try:
                package = self._package(job)
                receipt = json_object((self.root / job['review_id'] / 'application.private.json').read_bytes())
                if not receipt_confirms(receipt, package, job['task_id']):
                    raise ValueError('package application receipt differs')
                OwnedPackageRuntime().inspect(receipt, package)
                result['runtime_available'] = True
            except (OSError, ValueError, RuntimeError):
                result.update(runtime_available=False, installed=False, reason='managed_package_not_currently_available')
        return result


    def _seed_review(self, job, metadata):
        from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
        directory = self.root / job["review_id"]
        package = self._package(job)
        inputs = {"package-review/task.json": (directory / "task.json").read_bytes(),
                  "package-review/descriptor.json": json.dumps(package["descriptor"]).encode()}
        for name, content in archive_files((directory / "archive.tar.gz").read_bytes()).items():
            inputs["package-review/source/" + name] = content
        RuntimeRepairArtifacts(self.programming)._seed(metadata, inputs)

    def _independent_check(self, job, metadata):
        directory = self.root / job["review_id"]
        review = json_object(read_workspace_file(self.programming.config.workspace_root,
            metadata["workspace_key"], "package/review.json", maximum=65536))
        if review.get("decision") == "reject":
            _save(directory / "review.json", review, initial=True)
            _save(directory / "review-work.json", metadata, initial=True)
            return False
        test = read_workspace_file(self.programming.config.workspace_root,
            metadata["workspace_key"], "package/verify.py", maximum=65536)
        if sha(test) in json_object((directory / "package.json").read_bytes())["descriptor"]["files"].values():
            raise ValueError("builder test cannot substitute for separate reviewer verification")
        self._put_raw(directory / "verify.py", test)
        for filename, content in {
                'review-descriptor.input.json': json.dumps(self._package(job)['descriptor']).encode(),
                'review-task.input.json': (directory / 'task.json').read_bytes()}.items():
            self._put_raw(directory / filename, content)
            (directory / filename).chmod(0o444)
        source = directory / "source"
        source.mkdir(mode=0o755)
        files = archive_files((directory / "archive.tar.gz").read_bytes())
        for name, content in files.items():
            path = source / name; path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            path.write_bytes(content); path.chmod(0o444)
        (directory / "verify.py").chmod(0o444)
        # Private parent directories are bind sources, not mounted into the sandbox.
        image = self.config["runtime_policy"]["image"]
        result = self.verify(image, source, directory / "verify.py")
        stdout, stderr = result["stdout"].encode(), result["stderr"].encode()
        self._put_raw(directory / "verification.stdout.private", stdout)
        self._put_raw(directory / "verification.stderr.private", stderr)
        verification = {"contract": "EvaOwnedPackageIndependentVerification/v1",
            "package_sha256": job["package_sha256"], "review_spec_sha256": metadata["spec_sha256"],
            "image": image, "test_sha256": sha(test), "exit_code": result["exit_code"],
            "timed_out": result["timed_out"], "truncated": result["stdout_truncated"] or result["stderr_truncated"],
            "stdout_sha256": sha(stdout), "stderr_sha256": sha(stderr)}
        _save(directory / "review.json", review, initial=True)
        _save(directory / "review-work.json", metadata, initial=True)
        _save(directory / "verification.json", verification, initial=True)
        package = self._package(job)
        if result['exit_code'] != 0 or result['timed_out'] or result['stdout_truncated'] or result['stderr_truncated']:
            job['reason'] = 'independent_package_checks_did_not_pass'
            return False
        validate_review(package, review, metadata, verification)
        return True

    @staticmethod
    def _verify(image, source, test):
        from eva.operations.component_evaluation import ComponentEvaluator, EvaluationConfig
        command = ("/usr/local/bin/python", '-c',
            "import os,runpy,sys;os.chdir('/package');sys.path.insert(0,'/package');runpy.run_path('/review/verify.py',run_name='__main__')")
        evaluator = ComponentEvaluator(EvaluationConfig(image=image, command=command,
            deadline_seconds=90, output_bytes=65536, memory_mb=512, cpus=1, pids=64))
        mounts = [(source, '/package'), (test, '/review/verify.py')]
        for filename, target in [('review-descriptor.input.json', '/review/descriptor.json'),
                                 ('review-task.input.json', '/review/task.json')]:
            if (test.parent / filename).exists():
                mounts.append((test.parent / filename, target))
        return evaluator._container(image, command, extra_mounts=tuple(mounts))

    def ready_to_apply(self, task_id, release):
        origin = release["package"]["origin"]
        metadata = self._call("metadata", task_id=task_id, scope_sha256=origin['scope_sha256'])
        if {name: metadata[name] for name in origin} != origin:
            return "cancelled"
        if metadata["active_lease"] or metadata["state"] in {"admitted", "call_started"}:
            return "waiting"
        self._call('authorize_network', origin=origin,
            hosts=release['package']['descriptor']['permissions']['network_hosts'], scope_sha256=origin['scope_sha256'])
        current, _, _ = snapshot_candidate(self.programming.config.workspace_root, metadata,
            release['package']['archive_sha256'])
        if current['package_sha256'] != release['package']['package_sha256']:
            return 'cancelled'
        files = archive_files((self.root / release["package"]["archive_sha256"] / "archive.tar.gz").read_bytes())
        return "ready" if configuration_status(release["policy"], release["package"], files)["ready"] else "waiting"

    def run_once(self):
        with self.condition:
            jobs = [job for job in self.jobs.values() if job["state"] not in {
                "completed", "rejected", "cancelled", "unconfirmed", "blocked"}]
        for job in jobs:
            if job['state'] == 'waiting_for_configuration' and job.get('settings_sha256') == self._settings_fingerprint(job):
                continue  # No unchanged source/configuration probe or model loop.
            try:
                self._advance(job)
            except Exception:
                job.update(state="unconfirmed", reason="inspect_existing_package_evidence")
            with self.condition:
                self._save(job)
        self._notify_changed_facts()

    def _notify_changed_facts(self):
        for job in tuple(self.jobs.values()):
            reason = ({'completed': 'application_ready', 'rejected': 'review_rejected',
                       'blocked': 'host_handoff_unconfirmed', 'unconfirmed': 'host_handoff_unconfirmed'}
                      .get(job['state']))
            if job['state'] == 'waiting_for_configuration' and job.get('configuration', {}).get('ready') is False:
                reason = 'owner_access_required'
            if reason is not None:
                self._notify(job, reason)
            if job['state'] == 'completed' and any(
                    json_object(path.read_bytes()).get('effect_status') == 'confirmed'
                    for path in (self.root / job['review_id']).glob('callback-*.private')):
                self._notify(job, 'authorization_completed')

    def _notify(self, job, reason):
        notifications = job.setdefault('notifications', {})
        if notifications.get(reason, {}).get('state') in {
                'scheduled', 'original_task_not_resumable', 'native_continuation_requires_current_native_admission'}:
            return
        notifications[reason] = {'state': 'admission_pending'}
        self._save(job)
        try:
            result = self._call('continue', origin=job['origin'], scope_sha256=job['origin']['scope_sha256'],
                review_id=job['review_id'], package_sha256=job['package_sha256'], reason=reason)
            notifications[reason] = result
        except Exception:
            # The exact idempotent admission can be observed on the next poll;
            # no new notification key, task, source or effect is created.
            notifications[reason] = {'state': 'admission_unconfirmed'}
        self._save(job)

    def _advance(self, job):
        metadata = self._call("metadata", task_id=job["task_id"], scope_sha256=job['origin']['scope_sha256'])
        directory = self.root / job["review_id"]
        package = self._package(job)
        if metadata["active_lease"] or metadata["state"] in {"admitted", "call_started"}:
            return
        if job["state"] == "waiting_for_builder":
            # Durable native identity makes a lost admission reply inspectable.
            admission = self._call("admit_review", origin=job["origin"], package_sha256=job["package_sha256"], scope_sha256=job['origin']['scope_sha256'])
            if admission.get('status') != 'adopted':
                job['reason'] = 'waiting_for_existing_initiative_capacity'
                return
            _save(directory / "review-admission.json", admission, initial=True)
            job["state"] = "awaiting_independent_review"
        elif job["state"] == "awaiting_independent_review":
            review_work = self._call("review_metadata", origin=job["origin"], package_sha256=job["package_sha256"], scope_sha256=job['origin']['scope_sha256'])
            if review_work["state"] in {"not_admitted", "admitted", "call_started"} or review_work.get("active_lease"):
                return
            if review_work["state"] not in {"succeeded", "verified"}:
                job.update(state="blocked", reason="independent_reviewer_not_completed")
                return
            job["state"] = "verifying"; self._save(job)
            job["state"] = "waiting_for_configuration" if self._independent_check(job, review_work) else "rejected"
        elif job["state"] == "waiting_for_configuration":
            policy = dict(self.config["runtime_policy"])
            settings = self._settings(package)
            if not isinstance(settings, dict) or set(settings) - {'environment', 'secret_files'}:
                raise ValueError('host package settings differ')
            policy.update(settings)
            hosts = package['descriptor']['permissions']['network_hosts']
            network = self._call('authorize_network', origin=job['origin'], hosts=hosts,
                scope_sha256=job['origin']['scope_sha256'])
            policy['network_hosts'] = hosts
            if not hosts:
                policy['network'] = 'none'
            files = archive_files((directory / 'archive.tar.gz').read_bytes())
            schema = json_object(files[package['descriptor']['config_schema']])
            defaults = {k: v['default'] for k, v in schema.get('properties', {}).items()
                        if isinstance(v.get('default'), str)}
            policy['environment'] = {**defaults, **policy['environment']}
            validate_policy(policy, package)
            job["configuration"] = configuration_status(policy, package, files)
            if not job["configuration"]["ready"]:
                job['settings_sha256'] = self._settings_fingerprint(job)
                return
            job["state"] = "publishing"; self._save(job)
            _save(directory / "policy.private.json", policy, initial=True)
            _save(directory / 'network-authority.json', network, initial=True)
            self._put_raw(directory / 'proxy-token.private', secrets.token_urlsafe(48).encode())
            bindings = {name: {"path": str(directory / filename), "sha256": sha((directory / filename).read_bytes())}
                for name, filename in {"package": "package.json", "archive": "archive.tar.gz", "review": "review.json",
                    "review_work": "review-work.json", "verification": "verification.json", "policy": "policy.private.json",
                    'network_authority': 'network-authority.json'}.items()}
            release = {"kind": "owned_package", **bindings,
                       "update_receipt": str(directory / "application.private.json")}
            self.applications.register(job["review_id"], release)
            self.applications.request(job["review_id"], job["request_id"], job["task_id"])
            job["state"] = "application_requested"
        elif job["state"] == "application_requested":
            status = self.applications.status(job["request_id"], task_id=job["task_id"])
            if status["state"] in {"completed", "rejected", "cancelled", "unconfirmed"}:
                job.update(state=status["state"], reason=status.get("reason", "package_application_recorded"))

    def _run(self):
        while not self.closed:
            self.run_once()
            with self.condition:
                if not self.closed:
                    self.condition.wait(timeout=5)

    def close(self):
        with self.condition:
            self.closed = True; self.condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)
        if not self.thread.is_alive():
            self.lock.close()
