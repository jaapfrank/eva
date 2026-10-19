"""Optional camera discovery and bounded enrichment of the existing HA stream.

Registry device identities bind sources; names never bind cameras or people.
No frame archive, permanent polling loop, enrollment or greeting policy lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import OrderedDict
from datetime import UTC, datetime
import logging
import queue
import threading
import time

from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
from eva.application.sensory import SensoryEvent
from eva.contracts.canonical_json import canonical_sha256

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraDevice:
    device_id: str
    camera_entity_id: str
    trigger_entity_ids: tuple[str, ...]
    alternate_camera_count: int
    integration: str = ''
    area_id: str = ''


@dataclass(frozen=True)
class CameraInventory:
    devices: tuple[CameraDevice, ...]
    discovered_camera_count: int
    unsupported_camera_count: int
    revision: str
    discovered_at: str


def discover_cameras(registry: list[dict], states: list[dict]) -> CameraInventory:
    """Use exact device joins and source semantic types, never room/entity names.

    Multiple channels of a physical device are sampled once, smallest advertised
    image first. Alternate views and cameras without typed triggers stay explicit.
    A 'camera' exposing a map with no camera-device triggers is not monitored.
    """
    live = {row['entity_id']: row for row in states if isinstance(row, dict)
            and isinstance(row.get('entity_id'), str)}
    by_device: dict[str, list[dict]] = {}
    cameras = []
    for row in registry:
        entity = row.get('entity_id', '')
        if row.get('disabled_by') is not None or entity not in live:
            continue
        if entity.startswith('camera.'):
            cameras.append(row)
        device = row.get('device_id')
        if isinstance(device, str) and device:
            by_device.setdefault(device, []).append(row)
    devices = []
    covered = 0
    for device, rows in sorted(by_device.items()):
        views = [row for row in rows if row['entity_id'].startswith('camera.')
                 and live[row['entity_id']].get('state') not in {'unknown', 'unavailable'}]
        triggers = []
        for row in rows:
            entity = row['entity_id']
            attrs = live[entity].get('attributes') or {}
            semantic = row.get('translation_key')
            device_class = attrs.get('device_class') or row.get('device_class') or row.get('original_device_class')
            if entity.startswith('binary_sensor.') and (device_class in {'motion', 'occupancy', 'presence'}
                    or semantic in {'person_detected', 'motion', 'occupancy', 'doorbell'}):
                triggers.append(entity)
        if not views or not triggers:
            continue
        def resolution(row):
            attrs = live[row['entity_id']].get('attributes') or {}
            width, height = attrs.get('width'), attrs.get('height')
            pixels = width * height if (type(width) is int and type(height) is int
                                       and width > 0 and height > 0) else float('inf')
            return pixels, row['entity_id']
        views.sort(key=resolution)
        covered += len(views)
        devices.append(CameraDevice(device, views[0]['entity_id'], tuple(sorted(triggers)), len(views)-1,
            views[0].get('platform') or '', views[0].get('area_id') or ''))
    stable = [{'device': d.device_id, 'camera': d.camera_entity_id,
               'triggers': list(d.trigger_entity_ids), 'alternates': d.alternate_camera_count,
               'integration': d.integration, 'area_id': d.area_id} for d in devices]
    return CameraInventory(tuple(devices), len(cameras), len(cameras)-covered,
                           canonical_sha256(stable), datetime.now(UTC).isoformat())


class HomeAssistantCameraDiscovery:
    def __init__(self, *, base_url: str, token: str):
        self._reader = HomeAssistantAdminAdapter(base_url=base_url, token=token)

    def discover(self) -> CameraInventory:
        # Reuse the authenticated registry transport, including its frame bounds.
        with self._reader._session() as socket:
            registry = self._reader._command(socket, 1, 'config/entity_registry/list')
            states = self._reader._command(socket, 2, 'get_states')
        if not isinstance(registry, list) or not isinstance(states, list):
            raise RuntimeError('camera discovery returned an invalid inventory')
        return discover_cameras(registry, states)


class CameraObservationSource:
    """Wrap one existing sensory source; unrelated HA events never await vision.

    One queue, one analysis worker. Bursts on a camera coalesce for a configurable
    interval. No autonomous model call is made here; the existing household
    decision receives one enriched immutable source event, or the original signal
    with explicit unavailable/busy metadata. Raw images/embeddings are not retained.
    """
    def __init__(self, source, *, discovery, snapshots, vision=None, faces=None,
                 refresh_seconds=300.0, minimum_interval_seconds=30.0, max_event_age_seconds=60.0):
        if refresh_seconds <= 0 or minimum_interval_seconds < 0 or max_event_age_seconds <= 0:
            raise ValueError('camera observer timing is invalid')
        self._source, self._discovery, self._snapshots = source, discovery, snapshots
        self._vision, self._faces = vision, faces
        self._refresh, self._interval, self._max_age = refresh_seconds, minimum_interval_seconds, max_event_age_seconds
        self._inventory = None
        self._next_refresh = 0.0
        self._last: dict[str, float] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()

    def revalidate(self, event: SensoryEvent, *, now: datetime) -> SensoryEvent:
        # A current binary state does not refresh a historical image or identity.
        return self._source.revalidate(event, now=now)

    def _refresh_inventory(self):
        if time.monotonic() < self._next_refresh:
            return
        self._next_refresh = time.monotonic() + self._refresh
        try:
            self._inventory = self._discovery.discover()
            _LOG.info('camera_inventory cameras=%d devices=%d unsupported=%d revision=%s',
                      self._inventory.discovered_camera_count, len(self._inventory.devices),
                      self._inventory.unsupported_camera_count, self._inventory.revision)
            devices = {d.device_id for d in self._inventory.devices}
            self._last = {key: value for key, value in self._last.items() if key in devices}
        except Exception as error:
            # Do not continue observing removed/disabled cameras from a stale map.
            self._inventory = None
            _LOG.warning('camera_inventory_unavailable error=%s', type(error).__name__)

    def enrich(self, event: SensoryEvent, device: CameraDevice, *, stop: threading.Event,
               discovery_revision: str | None = None) -> SensoryEvent:
        metadata = {'contract': 'EvaCameraObservation/v1', 'status': 'unavailable',
                    'camera_entity_id': device.camera_entity_id, 'device_id': device.device_id,
                    'discovery_revision': discovery_revision or self._inventory.revision, 'alternate_camera_count': device.alternate_camera_count,
                    'captured_at': None, 'interpretation_not_verified_fact': True,
                    'identity_is_not_authentication': True}
        if device.integration:
            metadata['integration'] = device.integration
        if device.area_id:
            metadata['area_id'] = device.area_id
        if stop.is_set():
            return replace(event, payload={**event.payload, 'camera_observation': metadata})
        age = (datetime.now(UTC)-event.occurred_at).total_seconds()
        if age < -5 or age > self._max_age:
            metadata['status'] = 'stale_trigger'
        else:
            try:
                snapshot = self._snapshots.snapshot(device.camera_entity_id)
                metadata.update(image_sha256=snapshot.sha256, retrieved_at=snapshot.retrieved_at.isoformat())
                if self._faces is not None and not stop.is_set():
                    try:
                        metadata['recognition'] = self._faces.analyze(snapshot)
                    except Exception as error:
                        metadata['recognition_error'] = type(error).__name__
                if self._vision is not None and not stop.is_set():
                    try:
                        result = self._vision.analyze(snapshot).payload
                        metadata['scene'] = {key: result[key] for key in (
                            'description', 'model', 'model_revision', 'analyzed_at')}
                    except Exception as error:
                        metadata['vision_error'] = type(error).__name__
                metadata['status'] = 'observed' if ('recognition' in metadata or 'scene' in metadata) else 'analysis_unavailable'
            except Exception as error:
                metadata['snapshot_error'] = type(error).__name__
        from eva.contracts.camera_observation import normalize_camera_observation
        metadata = normalize_camera_observation(metadata)
        return replace(event, payload={**event.payload, 'camera_observation': metadata})

    def listen(self, stop: threading.Event, handler):
        pending = queue.Queue(maxsize=4)
        self._refresh_inventory()
        def receive(event):
            if stop.is_set():
                return
            if event.source_event_id in self._seen:
                return
            self._seen[event.source_event_id] = None
            if len(self._seen) > 2048:
                self._seen.popitem(last=False)
            self._refresh_inventory()
            device = next((d for d in self._inventory.devices
                           if event.payload.get('entity_id') in d.trigger_entity_ids), None) if self._inventory else None
            if device is None or event.payload.get('new_state') != 'on':
                handler(event)
                return
            now = time.monotonic()
            if now - self._last.get(device.device_id, -float('inf')) < self._interval:
                # The existing stream still retains source telemetry without a
                # second expensive image interpretation of the same burst.
                handler(event)
                return
            try:
                pending.put_nowait((event, device, self._inventory.revision))
                self._last[device.device_id] = now
            except queue.Full:
                handler(event)
        def analyze():
            while not stop.is_set():
                try:
                    event, device, revision = pending.get(timeout=0.25)
                except queue.Empty:
                    continue
                try:
                    # Do not use a camera removed/disabled by a refreshed registry.
                    if self._inventory is None or device not in self._inventory.devices:
                        if not stop.is_set():
                            handler(event)
                        continue
                    enriched = self.enrich(event, device, stop=stop, discovery_revision=revision)
                    if not stop.is_set():
                        handler(enriched)
                        _LOG.info('camera_observation_retained source=%s status=%s', event.source_event_id,
                                  enriched.payload['camera_observation']['status'])
                except Exception as error:
                    _LOG.warning('camera_observation_failed source=%s error=%s', event.source_event_id, type(error).__name__)
                    if not stop.is_set():
                        handler(event)
                finally:
                    pending.task_done()
        worker = threading.Thread(target=analyze, name='eva-camera-analysis', daemon=True)
        worker.start()
        try:
            self._source.listen(stop, receive)
        finally:
            stop.set()
            worker.join(timeout=5)


__all__ = ['CameraDevice', 'CameraInventory', 'CameraObservationSource', 'HomeAssistantCameraDiscovery', 'discover_cameras']
