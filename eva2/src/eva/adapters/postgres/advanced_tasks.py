"""Durable admission of advanced task plans before any side effect."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    PostgresOperationalTimeline,
    TimelineScope,
)
from eva.application.advanced_tasks import (
    AdvancedTaskApproval,
    AdvancedTaskCanaryProof,
    AdvancedTaskDevicePreflight,
    AdvancedTaskError,
    AdvancedTaskPackageApproval,
    AdvancedTaskPlan,
    AdvancedTaskRepositoryApproval,
    HomeAssistantInventory,
    HomeAssistantRepository,
    approve_canary,
    approve_localtuya_package_install,
    approve_localtuya_repository_registration,
)


class PostgresAdvancedTaskStore:
    def __init__(
        self, engine: sa.Engine, *, tenant_id: str, installation_id: str
    ) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                uuid5(NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"),
            ),
        )

    def retain(self, plan: AdvancedTaskPlan) -> str:
        try:
            self._timeline.create_thread(
                thread_id=plan.plan_id,
                checkpoint_id=f"checkpoint:{plan.plan_id}:planned",
                occurred_at=plan.created_at,
                data=plan.to_payload(),
                event_type="ADVANCED_PLAN_CREATED",
                phase="advanced_planned",
                status="waiting_for_proof",
                operation_id=plan.operation_id,
            )
            return "planned"
        except CheckpointConflict:
            events = self._timeline.events(plan.plan_id)
            durable = events[0].payload.get("data") if events else None
            if durable != plan.to_payload():
                raise RuntimeError("advanced plan identity collision") from None
            return "already_planned"

    def load(self, plan_id: str) -> AdvancedTaskPlan:
        events = self._timeline.events(plan_id)
        if not events or events[0].event_type != "ADVANCED_PLAN_CREATED":
            raise AdvancedTaskError("advanced task plan does not exist")
        plan_events = [
            event
            for event in events
            if event.event_type in {"ADVANCED_PLAN_CREATED", "ADVANCED_PLAN_REFRESHED"}
        ]
        if not plan_events:
            raise AdvancedTaskError("advanced task plan has no durable version")
        raw = plan_events[-1].payload.get("data")
        return AdvancedTaskPlan.from_payload(raw)

    def find(self, plan_id: str) -> AdvancedTaskPlan | None:
        events = self._timeline.events(plan_id)
        if not events:
            return None
        if events[0].event_type != "ADVANCED_PLAN_CREATED":
            raise AdvancedTaskError("advanced task plan has wrong origin")
        return self.load(plan_id)

    def refresh(self, plan: AdvancedTaskPlan) -> str:
        """Append a new inventory-bound plan version without changing task identity."""

        current = self.load(plan.plan_id)
        if (
            current.operation_id != plan.operation_id
            or current.requested_by != plan.requested_by
            or current.task != plan.task
        ):
            raise AdvancedTaskError("advanced task refresh changed task identity")
        if (
            current.state == plan.state
            and current.blockers == plan.blockers
            and current.devices == plan.devices
            and current.steps == plan.steps
            and current.repositories == plan.repositories
        ):
            return "already_current"
        checkpoint = self._timeline.load(plan.plan_id)
        try:
            self._timeline.transition(
                thread_id=plan.plan_id,
                expected_revision=checkpoint.revision,
                checkpoint_id=(
                    f"checkpoint:{plan.plan_id}:plan-{plan.inventory_sha256[:16]}"
                ),
                phase="advanced_planned",
                status="waiting_for_proof",
                event_type="ADVANCED_PLAN_REFRESHED",
                occurred_at=plan.created_at,
                data=plan.to_payload(),
                operation_id=plan.operation_id,
            )
        except CheckpointConflict as exc:
            raise AdvancedTaskError(
                "advanced task refresh raced another update"
            ) from exc
        return "refreshed"

    def latest(self, *, requested_by: str) -> AdvancedTaskPlan | None:
        checkpoints = self._timeline.list_checkpoints(
            thread_prefix="advanced:",
            statuses=("active", "waiting_for_proof", "completed", "failed"),
            limit=32,
        )
        for checkpoint in checkpoints:
            # Scope before strict deserialization. An old or corrupt plan owned
            # by somebody else must never break this principal's ordinary chat
            # path or leak even its parse failure into that conversation.
            events = self._timeline.events(checkpoint.thread_id)
            plan_events = [
                event
                for event in events
                if event.event_type
                in {"ADVANCED_PLAN_CREATED", "ADVANCED_PLAN_REFRESHED"}
            ]
            raw = plan_events[-1].payload.get("data") if plan_events else None
            if not isinstance(raw, dict) or raw.get("requested_by") != requested_by:
                continue
            plan = self.find(checkpoint.thread_id)
            if plan is not None and plan.requested_by == requested_by:
                return plan
        return None

    def status(self, plan_id: str) -> str:
        return self._timeline.load(plan_id).status

    def package_install_started(self, plan_id: str) -> bool:
        return any(
            event.event_type
            in {"ADVANCED_PACKAGE_INSTALL_PREPARED", "ADVANCED_PACKAGE_INSTALLED"}
            for event in self._timeline.events(plan_id)
        )

    def repository_registration_started(self, plan_id: str) -> bool:
        return any(
            event.event_type
            in {
                "ADVANCED_REPOSITORY_REGISTRATION_PREPARED",
                "ADVANCED_REPOSITORY_REGISTERED",
            }
            for event in self._timeline.events(plan_id)
        )

    def canary_authority(
        self,
        *,
        plan_id: str,
        approval_sha256: str,
        preflight_sha256: str,
    ) -> tuple[AdvancedTaskApproval, AdvancedTaskDevicePreflight]:
        events = self._timeline.events(plan_id)
        approvals = [
            event for event in events if event.event_type == "ADVANCED_CANARY_APPROVED"
        ]
        preflights = [
            event
            for event in events
            if event.event_type == "ADVANCED_DEVICE_PREFLIGHT_VERIFIED"
        ]
        if len(approvals) != 1:
            raise AdvancedTaskError("advanced canary approval does not exist")
        approval = AdvancedTaskApproval.from_payload(approvals[0].payload.get("data"))
        matches = [
            AdvancedTaskDevicePreflight.from_payload(event.payload.get("data"))
            for event in preflights
            if isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("device_id") == approval.device_id
        ]
        if len(matches) != 1:
            raise AdvancedTaskError("advanced canary preflight does not exist")
        preflight = matches[0]
        if approval.sha256 != approval_sha256 or preflight.sha256 != preflight_sha256:
            raise AdvancedTaskError("advanced canary authority hash differs")
        return approval, preflight

    def prepare_canary(
        self,
        approval: AdvancedTaskApproval,
        preflight: AdvancedTaskDevicePreflight,
        *,
        configuration_sha256: str,
        before_inventory: HomeAssistantInventory,
        occurred_at: datetime,
    ) -> str:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("advanced canary preparation time must be timezone-aware")
        if occurred_at > approval.expires_at:
            raise AdvancedTaskError("advanced canary approval expired")
        if (
            approval.plan_id != preflight.plan_id
            or approval.plan_sha256 != preflight.plan_sha256
            or approval.inventory_sha256 != preflight.inventory_sha256
            or approval.device_id != preflight.device_id
        ):
            raise AdvancedTaskError("advanced canary preparation authority differs")
        if len(configuration_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in configuration_sha256
        ):
            raise ValueError("advanced canary configuration hash is invalid")
        data = {
            "approval_id": approval.approval_id,
            "approval_sha256": approval.sha256,
            "configuration_sha256": configuration_sha256,
            "device_id": approval.device_id,
            "preflight_sha256": preflight.sha256,
            "before_inventory": before_inventory.to_payload(),
            "before_inventory_sha256": before_inventory.sha256,
        }
        events = self._timeline.events(approval.plan_id)
        if any(event.event_type == "ADVANCED_CANARY_VERIFIED" for event in events):
            return "already_verified"
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_PREPARED"
            ),
            None,
        )
        if existing is not None:
            if existing.payload.get("data") != data:
                raise AdvancedTaskError("advanced canary preparation changed")
            return "already_prepared"
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.transition(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:canary-prepared",
            phase="canary_prepared",
            status="active",
            event_type="ADVANCED_CANARY_PREPARED",
            occurred_at=occurred_at,
            data=data,
            operation_id=self.load(approval.plan_id).operation_id,
            deadline=approval.expires_at,
        )
        return "prepared"

    def prepared_canary_context(
        self, *, plan_id: str
    ) -> tuple[
        AdvancedTaskApproval,
        AdvancedTaskDevicePreflight,
        str,
        HomeAssistantInventory,
    ]:
        events = self._timeline.events(plan_id)
        prepared = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_PREPARED"
            ),
            None,
        )
        if not isinstance(getattr(prepared, "payload", None), dict):
            raise AdvancedTaskError("advanced canary has no durable preparation")
        data = prepared.payload.get("data")
        if not isinstance(data, dict):
            raise AdvancedTaskError("advanced canary preparation is malformed")
        approval, preflight = self.canary_authority(
            plan_id=plan_id,
            approval_sha256=str(data.get("approval_sha256")),
            preflight_sha256=str(data.get("preflight_sha256")),
        )
        configuration_sha256 = data.get("configuration_sha256")
        inventory = HomeAssistantInventory.from_payload(data.get("before_inventory"))
        if (
            not isinstance(configuration_sha256, str)
            or data.get("before_inventory_sha256") != inventory.sha256
        ):
            raise AdvancedTaskError("advanced canary preparation hash differs")
        return approval, preflight, configuration_sha256, inventory

    def open_canary_call(
        self,
        *,
        plan_id: str,
        configuration_sha256: str,
        occurred_at: datetime,
    ) -> str:
        """Commit a one-use provider-call fence; an opened call is never replayed."""

        approval, preflight, prepared_sha256, _before = self.prepared_canary_context(
            plan_id=plan_id
        )
        if configuration_sha256 != prepared_sha256:
            raise AdvancedTaskError("advanced canary configuration hash differs")
        if occurred_at > approval.expires_at:
            raise AdvancedTaskError("advanced canary approval expired")
        events = self._timeline.events(plan_id)
        if any(event.event_type == "ADVANCED_CANARY_VERIFIED" for event in events):
            return "already_verified"
        if any(event.event_type == "ADVANCED_CANARY_CALL_OPENED" for event in events):
            return "already_opened"
        checkpoint = self._timeline.load(plan_id)
        self._timeline.transition(
            thread_id=plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{plan_id}:canary-call-opened",
            phase="canary_call_in_flight",
            status="waiting_for_proof",
            event_type="ADVANCED_CANARY_CALL_OPENED",
            occurred_at=occurred_at,
            data={
                "approval_sha256": approval.sha256,
                "configuration_sha256": configuration_sha256,
                "device_id": approval.device_id,
                "preflight_sha256": preflight.sha256,
            },
            operation_id=self.load(plan_id).operation_id,
            deadline=approval.expires_at,
        )
        return "opened"

    def record_canary_call_result(
        self,
        *,
        plan_id: str,
        configuration_sha256: str,
        receipt: dict[str, object],
        occurred_at: datetime,
    ) -> str:
        events = self._timeline.events(plan_id)
        if not any(
            event.event_type == "ADVANCED_CANARY_CALL_OPENED" for event in events
        ):
            raise AdvancedTaskError("advanced canary call was not durably opened")
        if (
            receipt.get("contract") != "EvaLocalTuyaCreationReceipt/v1"
            or receipt.get("configuration_sha256") != configuration_sha256
            or receipt.get("disposition") != "created"
        ):
            raise AdvancedTaskError("advanced canary provider receipt differs")
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_CALL_RETURNED"
            ),
            None,
        )
        if existing is not None:
            if existing.payload.get("data") != receipt:
                raise AdvancedTaskError("advanced canary provider receipt changed")
            return "already_recorded"
        checkpoint = self._timeline.load(plan_id)
        self._timeline.transition(
            thread_id=plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{plan_id}:canary-call-returned",
            phase="canary_call_returned",
            status="waiting_for_proof",
            event_type="ADVANCED_CANARY_CALL_RETURNED",
            occurred_at=occurred_at,
            data=receipt,
            operation_id=self.load(plan_id).operation_id,
        )
        return "recorded"

    def record_canary_call_failure(
        self, *, plan_id: str, error_text: str, occurred_at: datetime
    ) -> str:
        events = self._timeline.events(plan_id)
        if not any(
            event.event_type == "ADVANCED_CANARY_CALL_OPENED" for event in events
        ):
            raise AdvancedTaskError("advanced canary call was not durably opened")
        if any(event.event_type == "ADVANCED_CANARY_CALL_FAILED" for event in events):
            return "already_recorded"
        checkpoint = self._timeline.load(plan_id)
        self._timeline.transition(
            thread_id=plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{plan_id}:canary-call-failed",
            phase="canary_attention_required",
            status="failed",
            event_type="ADVANCED_CANARY_CALL_FAILED",
            occurred_at=occurred_at,
            data={"error": error_text[:1000]},
            operation_id=self.load(plan_id).operation_id,
        )
        return "recorded"

    def record_canary_verified(self, proof: AdvancedTaskCanaryProof) -> str:
        events = self._timeline.events(proof.plan_id)
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_VERIFIED"
            ),
            None,
        )
        if existing is not None:
            if existing.payload.get("data") != proof.to_payload():
                raise AdvancedTaskError("advanced canary readback changed")
            return "already_verified"
        prepared = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_PREPARED"
            ),
            None,
        )
        if not isinstance(getattr(prepared, "payload", None), dict):
            raise AdvancedTaskError("advanced canary has no durable preparation")
        prepared_data = prepared.payload.get("data")
        if not isinstance(prepared_data, dict) or (
            prepared_data.get("approval_sha256") != proof.approval_sha256
            or prepared_data.get("preflight_sha256") != proof.preflight_sha256
            or prepared_data.get("configuration_sha256") != proof.configuration_sha256
            or prepared_data.get("device_id") != proof.device_id
        ):
            raise AdvancedTaskError("advanced canary readback authority differs")
        checkpoint = self._timeline.load(proof.plan_id)
        self._timeline.transition(
            thread_id=proof.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{proof.plan_id}:canary-verified",
            phase="canary_verified",
            status="waiting_for_proof",
            event_type="ADVANCED_CANARY_VERIFIED",
            occurred_at=proof.observed_at,
            data=proof.to_payload(),
            operation_id=self.load(proof.plan_id).operation_id,
        )
        return "verified"

    def approve_canary(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        device_id: str,
        approved_by: str,
        approved_at: datetime,
    ) -> AdvancedTaskApproval:
        plan = self.load(plan_id)
        if plan.sha256 != plan_sha256 or plan.inventory_sha256 != inventory_sha256:
            raise AdvancedTaskError("advanced task approval has stale authority")
        events = self._timeline.events(plan_id)
        preflight_events = [
            event
            for event in events
            if event.event_type == "ADVANCED_DEVICE_PREFLIGHT_VERIFIED"
            and isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("device_id") == device_id
        ]
        preflight = (
            AdvancedTaskDevicePreflight.from_payload(
                preflight_events[-1].payload.get("data")
            )
            if preflight_events
            else None
        )
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_CANARY_APPROVED"
            ),
            None,
        )
        if existing is not None:
            durable = AdvancedTaskApproval.from_payload(existing.payload.get("data"))
            if (
                durable.device_id != device_id
                or durable.approved_by != approved_by.strip()
                or durable.plan_sha256 != plan_sha256
                or durable.inventory_sha256 != inventory_sha256
            ):
                raise AdvancedTaskError("advanced task already has another approval")
            return durable
        approval = approve_canary(
            plan,
            device_id=device_id,
            approved_by=approved_by,
            approved_at=approved_at,
            preflight=preflight,
        )
        checkpoint = self._timeline.load(plan_id)
        try:
            self._timeline.transition(
                thread_id=plan_id,
                expected_revision=checkpoint.revision,
                checkpoint_id=f"checkpoint:{plan_id}:canary-approved",
                phase="canary_approved",
                status="waiting_for_proof",
                event_type="ADVANCED_CANARY_APPROVED",
                occurred_at=approval.approved_at,
                data=approval.to_payload(),
                operation_id=plan.operation_id,
                deadline=approval.expires_at,
            )
        except CheckpointConflict as exc:
            raise AdvancedTaskError(
                "advanced task approval raced another update"
            ) from exc
        return approval

    def retain_device_preflight(self, preflight: AdvancedTaskDevicePreflight) -> str:
        plan = self.load(preflight.plan_id)
        if (
            preflight.plan_sha256 != plan.sha256
            or preflight.inventory_sha256 != plan.inventory_sha256
        ):
            raise AdvancedTaskError("advanced device preflight has stale authority")
        events = self._timeline.events(preflight.plan_id)
        existing = [
            event
            for event in events
            if event.event_type == "ADVANCED_DEVICE_PREFLIGHT_VERIFIED"
            and isinstance(event.payload.get("data"), dict)
            and event.payload["data"].get("device_id") == preflight.device_id
        ]
        if existing:
            durable = AdvancedTaskDevicePreflight.from_payload(
                existing[-1].payload.get("data")
            )
            if durable == preflight:
                return "already_verified"
            raise AdvancedTaskError("advanced device preflight changed")
        checkpoint = self._timeline.load(preflight.plan_id)
        try:
            self._timeline.transition(
                thread_id=preflight.plan_id,
                expected_revision=checkpoint.revision,
                checkpoint_id=(
                    f"checkpoint:{preflight.plan_id}:device-"
                    f"{preflight.device_id[:16]}-preflight"
                ),
                phase="device_preflight_verified",
                status="waiting_for_proof",
                event_type="ADVANCED_DEVICE_PREFLIGHT_VERIFIED",
                occurred_at=preflight.probed_at,
                data=preflight.to_payload(),
                operation_id=plan.operation_id,
            )
        except CheckpointConflict as exc:
            raise AdvancedTaskError(
                "advanced device preflight raced another update"
            ) from exc
        return "verified"

    def approve_package_install(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_id: str,
        version: str,
        approved_by: str,
        approved_at: datetime,
    ) -> AdvancedTaskPackageApproval:
        plan = self.load(plan_id)
        if plan.sha256 != plan_sha256 or plan.inventory_sha256 != inventory_sha256:
            raise AdvancedTaskError("advanced package approval has stale authority")
        events = self._timeline.events(plan_id)
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_PACKAGE_INSTALL_APPROVED"
            ),
            None,
        )
        if existing is not None:
            durable = AdvancedTaskPackageApproval.from_payload(
                existing.payload.get("data")
            )
            if (
                durable.repository_id != repository_id
                or durable.version != version
                or durable.approved_by != approved_by.strip()
                or durable.plan_sha256 != plan_sha256
            ):
                raise AdvancedTaskError(
                    "advanced task already has another package approval"
                )
            return durable
        approval = approve_localtuya_package_install(
            plan,
            repository_id=repository_id,
            version=version,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        checkpoint = self._timeline.load(plan_id)
        try:
            self._timeline.transition(
                thread_id=plan_id,
                expected_revision=checkpoint.revision,
                checkpoint_id=f"checkpoint:{plan_id}:package-approved",
                phase="package_install_approved",
                status="waiting_for_proof",
                event_type="ADVANCED_PACKAGE_INSTALL_APPROVED",
                occurred_at=approval.approved_at,
                data=approval.to_payload(),
                operation_id=plan.operation_id,
                deadline=approval.expires_at,
            )
        except CheckpointConflict as exc:
            raise AdvancedTaskError(
                "advanced package approval raced another update"
            ) from exc
        return approval

    def approve_repository_registration(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_full_name: str,
        expected_version: str,
        approved_by: str,
        approved_at: datetime,
    ) -> AdvancedTaskRepositoryApproval:
        plan = self.load(plan_id)
        if plan.sha256 != plan_sha256 or plan.inventory_sha256 != inventory_sha256:
            raise AdvancedTaskError("advanced repository approval has stale authority")
        events = self._timeline.events(plan_id)
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_REPOSITORY_REGISTRATION_APPROVED"
            ),
            None,
        )
        if existing is not None:
            durable = AdvancedTaskRepositoryApproval.from_payload(
                existing.payload.get("data")
            )
            if (
                durable.repository_full_name != repository_full_name
                or durable.expected_version != expected_version
                or durable.approved_by != approved_by.strip()
                or durable.plan_sha256 != plan_sha256
            ):
                raise AdvancedTaskError(
                    "advanced task already has another repository approval"
                )
            return durable
        approval = approve_localtuya_repository_registration(
            plan,
            repository_full_name=repository_full_name,
            expected_version=expected_version,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        checkpoint = self._timeline.load(plan_id)
        try:
            self._timeline.transition(
                thread_id=plan_id,
                expected_revision=checkpoint.revision,
                checkpoint_id=f"checkpoint:{plan_id}:repository-approved",
                phase="repository_registration_approved",
                status="waiting_for_proof",
                event_type="ADVANCED_REPOSITORY_REGISTRATION_APPROVED",
                occurred_at=approval.approved_at,
                data=approval.to_payload(),
                operation_id=plan.operation_id,
                deadline=approval.expires_at,
            )
        except CheckpointConflict as exc:
            raise AdvancedTaskError(
                "advanced repository approval raced another update"
            ) from exc
        return approval

    def repository_approval(
        self, *, plan_id: str, approval_sha256: str
    ) -> AdvancedTaskRepositoryApproval:
        events = self._timeline.events(plan_id)
        matches = [
            event
            for event in events
            if event.event_type == "ADVANCED_REPOSITORY_REGISTRATION_APPROVED"
        ]
        if len(matches) != 1:
            raise AdvancedTaskError("advanced repository approval does not exist")
        approval = AdvancedTaskRepositoryApproval.from_payload(
            matches[0].payload.get("data")
        )
        if approval.sha256 != approval_sha256:
            raise AdvancedTaskError("advanced repository approval hash differs")
        return approval

    def prepare_repository_registration(
        self, approval: AdvancedTaskRepositoryApproval, *, occurred_at: datetime
    ) -> str:
        events = self._timeline.events(approval.plan_id)
        if any(
            event.event_type == "ADVANCED_REPOSITORY_REGISTERED" for event in events
        ):
            return "already_verified"
        if any(
            event.event_type == "ADVANCED_REPOSITORY_REGISTRATION_PREPARED"
            for event in events
        ):
            return "already_prepared"
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.transition(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:repository-prepared",
            phase="repository_registration_prepared",
            status="active",
            event_type="ADVANCED_REPOSITORY_REGISTRATION_PREPARED",
            occurred_at=occurred_at,
            data={
                "approval_id": approval.approval_id,
                "approval_sha256": approval.sha256,
                "repository_full_name": approval.repository_full_name,
                "expected_version": approval.expected_version,
            },
            operation_id=self.load(approval.plan_id).operation_id,
            deadline=approval.expires_at,
        )
        return "prepared"

    def record_repository_registered(
        self,
        approval: AdvancedTaskRepositoryApproval,
        repository: HomeAssistantRepository,
        *,
        occurred_at: datetime,
    ) -> str:
        events = self._timeline.events(approval.plan_id)
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_REPOSITORY_REGISTERED"
            ),
            None,
        )
        data = {
            "approval_sha256": approval.sha256,
            "repository": asdict(repository),
            "repository_sha256": repository.sha256,
        }
        if existing is not None:
            if existing.payload.get("data") != data:
                raise AdvancedTaskError("advanced repository readback changed")
            return "already_verified"
        if (
            repository.full_name != approval.repository_full_name
            or repository.domain != "localtuya"
            or not repository.custom
            or not repository.can_download
            or repository.available_version is None
            or repository.available_version.removeprefix("v")
            != approval.expected_version.removeprefix("v")
        ):
            raise AdvancedTaskError("advanced repository registration proof differs")
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.transition(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:repository-registered",
            phase="repository_registered",
            status="waiting_for_proof",
            event_type="ADVANCED_REPOSITORY_REGISTERED",
            occurred_at=occurred_at,
            data=data,
            operation_id=self.load(approval.plan_id).operation_id,
        )
        return "verified"

    def record_repository_failure(
        self,
        approval: AdvancedTaskRepositoryApproval,
        *,
        error_text: str,
        occurred_at: datetime,
    ) -> None:
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.record_recovery_failure(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:repository-failed",
            operation_id=self.load(approval.plan_id).operation_id,
            error_text=error_text,
            occurred_at=occurred_at,
        )

    def package_approval(
        self, *, plan_id: str, approval_sha256: str
    ) -> AdvancedTaskPackageApproval:
        events = self._timeline.events(plan_id)
        matches = [
            event
            for event in events
            if event.event_type == "ADVANCED_PACKAGE_INSTALL_APPROVED"
        ]
        if len(matches) != 1:
            raise AdvancedTaskError("advanced package approval does not exist")
        approval = AdvancedTaskPackageApproval.from_payload(
            matches[0].payload.get("data")
        )
        if approval.sha256 != approval_sha256:
            raise AdvancedTaskError("advanced package approval hash differs")
        return approval

    def prepare_package_install(
        self, approval: AdvancedTaskPackageApproval, *, occurred_at: datetime
    ) -> str:
        events = self._timeline.events(approval.plan_id)
        if any(event.event_type == "ADVANCED_PACKAGE_INSTALLED" for event in events):
            return "already_verified"
        if any(
            event.event_type == "ADVANCED_PACKAGE_INSTALL_PREPARED" for event in events
        ):
            return "already_prepared"
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.transition(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:package-prepared",
            phase="package_install_prepared",
            status="active",
            event_type="ADVANCED_PACKAGE_INSTALL_PREPARED",
            occurred_at=occurred_at,
            data={
                "approval_id": approval.approval_id,
                "approval_sha256": approval.sha256,
                "repository_id": approval.repository_id,
                "version": approval.version,
            },
            operation_id=self.load(approval.plan_id).operation_id,
            deadline=approval.expires_at,
        )
        return "prepared"

    def record_package_installed(
        self,
        approval: AdvancedTaskPackageApproval,
        repository: HomeAssistantRepository,
        *,
        occurred_at: datetime,
    ) -> str:
        events = self._timeline.events(approval.plan_id)
        existing = next(
            (
                event
                for event in events
                if event.event_type == "ADVANCED_PACKAGE_INSTALLED"
            ),
            None,
        )
        data = {
            "approval_sha256": approval.sha256,
            "repository": asdict(repository),
            "repository_sha256": repository.sha256,
        }
        if existing is not None:
            if existing.payload.get("data") != data:
                raise AdvancedTaskError("advanced package readback changed")
            return "already_verified"
        if (
            repository.repository_id != approval.repository_id
            or not repository.installed
            or repository.installed_version != approval.version
        ):
            raise AdvancedTaskError("advanced package installation proof differs")
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.transition(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:package-installed",
            phase="package_installed",
            status="waiting_for_proof",
            event_type="ADVANCED_PACKAGE_INSTALLED",
            occurred_at=occurred_at,
            data=data,
            operation_id=self.load(approval.plan_id).operation_id,
        )
        return "verified"

    def record_package_failure(
        self,
        approval: AdvancedTaskPackageApproval,
        *,
        error_text: str,
        occurred_at: datetime,
    ) -> None:
        checkpoint = self._timeline.load(approval.plan_id)
        self._timeline.record_recovery_failure(
            thread_id=approval.plan_id,
            expected_revision=checkpoint.revision,
            checkpoint_id=f"checkpoint:{approval.plan_id}:package-failed",
            operation_id=self.load(approval.plan_id).operation_id,
            error_text=error_text,
            occurred_at=occurred_at,
        )


__all__ = ["PostgresAdvancedTaskStore"]
