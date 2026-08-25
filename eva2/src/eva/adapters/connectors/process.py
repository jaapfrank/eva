"""Short-lived connector worker process exchange for the sealed 4D protocol."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, TypeAlias

from connector_sdk.fixture_runner import (
    FixtureCase,
    FixtureRunnerError,
    load_fixture_case,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.connector import ConnectorManifest
from eva.contracts.connector_rpc import (
    MAX_CONNECTOR_EXCHANGE_BYTES,
    ConnectorErrorResponse,
    ConnectorInitializeRequest,
    ConnectorInitializeSuccess,
    ConnectorInvokeRequest,
    ConnectorInvokeSuccess,
    ConnectorProtocolFailure,
    ConnectorProtocolPhase,
    ConnectorProtocolViolation,
    classify_connector_protocol_failure,
    decode_connector_rpc_frame_prefix,
    encode_connector_rpc_frame,
)


InvokeFailurePhase: TypeAlias = Literal[
    "invoke_before_external_io", "invoke_external_io_possible"
]
ConnectorProcessResult: TypeAlias = (
    ConnectorInvokeSuccess | ConnectorErrorResponse | ConnectorProtocolFailure
)
_MAX_PINNED_COMMAND_TARGET_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ConnectorFixtureSelection:
    """Trusted harness binding to one sealed case and current provider state."""

    artifact_path: Path
    artifact_sha256: str
    worker_key: str
    case_id: str
    current_state_sha256: str


@dataclass(frozen=True, slots=True)
class ConnectorCommandTargetPin:
    """One absolute command argument bound to its current regular-file bytes."""

    argument_index: int
    target_path: Path
    target_sha256: str

    def __post_init__(self) -> None:
        if self.argument_index < 0:
            raise ValueError("connector command target index must be nonnegative")
        if not self.target_path.is_absolute():
            raise ValueError("connector command target path must be absolute")
        if len(self.target_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.target_sha256
        ):
            raise ValueError("connector command target hash must be lowercase sha256")


@dataclass(frozen=True, slots=True)
class ConnectorProgramBinding:
    """Closed launcher mode that consumes the sealed worker program bytes."""

    kind: Literal["direct_target", "python_inline_code"]
    argument_index: int

    def __post_init__(self) -> None:
        if self.kind not in {"direct_target", "python_inline_code"}:
            raise ValueError("connector program binding kind is invalid")
        if self.argument_index < 0:
            raise ValueError("connector program binding index must be nonnegative")


@dataclass(frozen=True, slots=True)
class _OpenedConnectorCommand:
    command: tuple[str, ...]
    pass_fds: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ConnectorProcessSpec:
    """Executable command plus the exact content-addressed worker program bytes."""

    command: tuple[str, ...]
    program_bytes: bytes
    invoke_failure_phase: InvokeFailurePhase = "invoke_external_io_possible"
    fixture_selection: ConnectorFixtureSelection | None = None
    command_target_pins: tuple[ConnectorCommandTargetPin, ...] = ()
    program_binding: ConnectorProgramBinding | None = None

    def __post_init__(self) -> None:
        if not self.command or any(not item for item in self.command):
            raise ValueError("connector worker command must be nonempty")
        if not isinstance(self.program_bytes, bytes):
            raise TypeError("connector worker program must be bytes")
        if self.invoke_failure_phase not in {
            "invoke_before_external_io",
            "invoke_external_io_possible",
        }:
            raise ValueError("connector invoke failure phase is invalid")
        indexes: set[int] = set()
        for pin in self.command_target_pins:
            if pin.argument_index in indexes:
                raise ValueError("connector command target index must be unique")
            if pin.argument_index >= len(self.command):
                raise ValueError("connector command target index is out of range")
            if self.command[pin.argument_index] != str(pin.target_path):
                raise ValueError("connector command target differs from command")
            indexes.add(pin.argument_index)
        binding = self.program_binding
        if binding is None:
            return
        if binding.argument_index >= len(self.command):
            raise ValueError("connector program binding index is out of range")
        matching_pin = next(
            (
                pin
                for pin in self.command_target_pins
                if pin.argument_index == binding.argument_index
            ),
            None,
        )
        if binding.kind == "direct_target":
            if (
                binding.argument_index != 0
                or matching_pin is None
                or matching_pin.target_sha256
                != hashlib.sha256(self.program_bytes).hexdigest()
            ):
                raise ValueError("connector direct program target differs from program bytes")
            return
        if binding.argument_index < 2 or self.command[binding.argument_index - 1] != "-c":
            raise ValueError("connector Python program must immediately follow -c")
        if self.command.count("-c") != 1 or "-m" in self.command[1:binding.argument_index]:
            raise ValueError("connector Python launcher mode is ambiguous")
        allowed_flags = {"-B", "-E", "-I", "-O", "-OO", "-S", "-s", "-u"}
        if any(
            item not in allowed_flags
            for item in self.command[1 : binding.argument_index - 1]
        ):
            raise ValueError("connector Python launcher flag is not allowed")
        if self.command[binding.argument_index].encode("utf-8") != self.program_bytes:
            raise ValueError("connector Python inline code differs from program bytes")

    def verify_command_targets(self) -> None:
        """Verify every pin through the same immutable snapshot used at execution."""

        with self.open_pinned_command():
            pass

    @contextmanager
    def open_pinned_command(self) -> Iterator[_OpenedConnectorCommand]:
        """Snapshot pinned files into sealed memfds and yield only those exec paths."""

        command = list(self.command)
        snapshot_fds: list[int] = []
        try:
            for pin in self.command_target_pins:
                snapshot_fd = _snapshot_pinned_target(pin)
                snapshot_fds.append(snapshot_fd)
                command[pin.argument_index] = f"/proc/self/fd/{snapshot_fd}"
            yield _OpenedConnectorCommand(tuple(command), tuple(snapshot_fds))
        finally:
            for descriptor in snapshot_fds:
                os.close(descriptor)


def _snapshot_pinned_target(pin: ConnectorCommandTargetPin) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(pin.target_path, flags)
    except OSError as error:
        raise ValueError("connector command target is unavailable") from error
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError("connector command target is not a regular file")
        if source_stat.st_size > _MAX_PINNED_COMMAND_TARGET_BYTES:
            raise ValueError("connector command target exceeds snapshot limit")
        target_bytes = bytearray()
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            target_bytes.extend(chunk)
            if len(target_bytes) > _MAX_PINNED_COMMAND_TARGET_BYTES:
                raise ValueError("connector command target exceeds snapshot limit")
    finally:
        os.close(source_fd)
    if hashlib.sha256(target_bytes).hexdigest() != pin.target_sha256:
        raise ValueError("connector command target bytes differ from pin")
    try:
        snapshot_fd = os.memfd_create(
            "eva-connector-command",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        view = memoryview(target_bytes)
        while view:
            written = os.write(snapshot_fd, view)
            if written <= 0:
                raise OSError("short write to connector command snapshot")
            view = view[written:]
        os.fchmod(snapshot_fd, 0o500)
        fcntl.fcntl(
            snapshot_fd,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_SEAL,
        )
        os.lseek(snapshot_fd, 0, os.SEEK_SET)
        return snapshot_fd
    except (AttributeError, OSError) as error:
        if "snapshot_fd" in locals():
            os.close(snapshot_fd)
        raise ValueError("connector command snapshot is unavailable") from error


def _failure_evidence(
    *, stdout: bytes, stderr: bytes, returncode: int | None, detail: str
) -> str:
    payload = {
        "contract": "EvaConnectorProcessObservation/v1",
        "detail": detail,
        "returncode": returncode,
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _protocol_failure(
    *,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    phase: ConnectorProtocolPhase,
    violation: ConnectorProtocolViolation,
    observed_at: str,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int | None = None,
    detail: str,
) -> ConnectorProtocolFailure:
    request = initialize if phase == "initialize" else invoke
    return classify_connector_protocol_failure(
        request_id=request.id,
        request_sha256=canonical_sha256(request),
        phase=phase,
        violation=violation,
        evidence_sha256=_failure_evidence(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            detail=detail,
        ),
        observed_at=observed_at,
    )


def _decode_violation(exc: ValueError, phase: ConnectorProtocolPhase) -> str:
    detail = str(exc)
    if "exceeds" in detail or "too large" in detail:
        return "frame_too_large"
    if "incomplete" in detail:
        return "framing_error" if phase == "initialize" else "unexpected_eof"
    if "JSON" in detail or "UTF-8" in detail or "canonical" in detail:
        return "invalid_json"
    if "Content-Length" in detail or "header" in detail or "framing" in detail:
        return "framing_error"
    return "invalid_message"


def _response_failure(
    *,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    spec: ConnectorProcessSpec,
    observed_at: str,
    stdout: bytes,
    stderr: bytes,
    returncode: int,
    phase: ConnectorProtocolPhase,
    violation: ConnectorProtocolViolation,
    detail: str,
) -> ConnectorProtocolFailure:
    return _protocol_failure(
        initialize=initialize,
        invoke=invoke,
        phase=phase,
        violation=violation,
        observed_at=observed_at,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        detail=detail,
    )


def run_connector_process(
    *,
    spec: ConnectorProcessSpec,
    manifest: ConnectorManifest,
    initialize: ConnectorInitializeRequest,
    invoke: ConnectorInvokeRequest,
    observed_at: str,
    timeout_seconds: float,
) -> ConnectorProcessResult:
    """Run and fully bind one initialize/invoke connector child exchange."""

    if timeout_seconds <= 0:
        raise ValueError("connector timeout must be positive")
    if hashlib.sha256(spec.program_bytes).hexdigest() != manifest.executable_sha256:
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase="initialize",
            violation="executable_mismatch",
            observed_at=observed_at,
            detail="program bytes differ from ConnectorManifest.executable_sha256",
        )
    fixture_case: FixtureCase | None = None
    if spec.fixture_selection is not None:
        fixture = spec.fixture_selection
        try:
            fixture_case = load_fixture_case(
                fixture.artifact_path,
                fixture.artifact_sha256,
                fixture.worker_key,
                fixture.case_id,
            )
        except (OSError, FixtureRunnerError) as exc:
            return _protocol_failure(
                initialize=initialize,
                invoke=invoke,
                phase="initialize",
                violation="manifest_mismatch",
                observed_at=observed_at,
                detail=f"sealed fixture selection is invalid: {exc}",
            )
        if fixture_case.program_bytes != spec.program_bytes:
            return _protocol_failure(
                initialize=initialize,
                invoke=invoke,
                phase="initialize",
                violation="executable_mismatch",
                observed_at=observed_at,
                detail="sealed fixture program bytes differ from process spec",
            )
        if fixture_case.manifest_sha256 != canonical_sha256(manifest):
            return _protocol_failure(
                initialize=initialize,
                invoke=invoke,
                phase="initialize",
                violation="manifest_mismatch",
                observed_at=observed_at,
                detail="sealed fixture manifest differs from selected manifest",
            )
        if fixture.current_state_sha256 != fixture_case.provider_state_before_sha256:
            return _protocol_failure(
                initialize=initialize,
                invoke=invoke,
                phase="initialize",
                violation="manifest_mismatch",
                observed_at=observed_at,
                detail="sealed fixture current provider state differs",
            )
    try:
        initialize.validate_against_manifest(manifest)
        invoke.validate_against_initialize(initialize, manifest)
    except ValueError as exc:
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase="initialize",
            violation="manifest_mismatch",
            observed_at=observed_at,
            detail=str(exc),
        )

    stdin = encode_connector_rpc_frame(initialize) + encode_connector_rpc_frame(invoke)
    if fixture_case is not None and stdin != fixture_case.input_exchange:
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase="initialize",
            violation="manifest_mismatch",
            observed_at=observed_at,
            detail="host request exchange differs from sealed fixture case",
        )
    try:
        with spec.open_pinned_command() as opened:
            completed = subprocess.run(
                opened.command,
                input=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
                pass_fds=opened.pass_fds,
            )
    except ValueError as error:
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase="initialize",
            violation="executable_mismatch",
            observed_at=observed_at,
            detail=str(error),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase=spec.invoke_failure_phase,
            violation="timeout",
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            detail="connector worker exceeded its host timeout",
        )
    except OSError as exc:
        return _protocol_failure(
            initialize=initialize,
            invoke=invoke,
            phase="initialize",
            violation="unexpected_eof",
            observed_at=observed_at,
            detail=f"connector worker could not start: {exc}",
        )

    stdout = completed.stdout
    stderr = completed.stderr
    if fixture_case is not None and stdout != fixture_case.success_exchange:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="invalid_message",
            detail="worker stdout differs from the sealed fixture result exchange",
        )
    if len(stdout) > min(manifest.maximum_stdout_bytes, MAX_CONNECTOR_EXCHANGE_BYTES):
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="frame_too_large",
            detail="connector stdout exceeded the sealed exchange budget",
        )
    if len(stderr) > manifest.maximum_stderr_bytes:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="invalid_message",
            detail="connector stderr exceeded the sealed byte budget",
        )
    if completed.returncode != 0:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="unexpected_eof" if not stdout else "invalid_message",
            detail="connector worker exited nonzero",
        )

    try:
        first, remainder = decode_connector_rpc_frame_prefix(stdout)
    except ValueError as exc:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase="initialize",
            violation=_decode_violation(exc, "initialize"),
            detail=str(exc),
        )

    if isinstance(first, ConnectorErrorResponse):
        if remainder:
            return _response_failure(
                initialize=initialize,
                invoke=invoke,
                spec=spec,
                observed_at=observed_at,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
                phase="initialize",
                violation="extra_frame",
                detail="initialize error was followed by another stdout frame",
            )
        if first.id != initialize.id:
            return _response_failure(
                initialize=initialize,
                invoke=invoke,
                spec=spec,
                observed_at=observed_at,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
                phase="initialize",
                violation="response_id_mismatch",
                detail="initialize error response ID differs",
            )
        try:
            first.validate_against_request(
                initialize, external_io_may_have_started=False
            )
        except ValueError as exc:
            return _response_failure(
                initialize=initialize,
                invoke=invoke,
                spec=spec,
                observed_at=observed_at,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
                phase="initialize",
                violation="invalid_message",
                detail=str(exc),
            )
        return first
    if not isinstance(first, ConnectorInitializeSuccess):
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase="initialize",
            violation="invalid_message",
            detail="first stdout frame did not close initialize",
        )
    if first.id != initialize.id:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase="initialize",
            violation="response_id_mismatch",
            detail="initialize success response ID differs",
        )
    try:
        first.validate_against_request(initialize)
    except ValueError as exc:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase="initialize",
            violation="invalid_message",
            detail=str(exc),
        )
    if not remainder:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="unexpected_eof",
            detail="initialize success was not followed by an invoke response",
        )

    try:
        second, trailing = decode_connector_rpc_frame_prefix(remainder)
    except ValueError as exc:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation=_decode_violation(exc, spec.invoke_failure_phase),
            detail=str(exc),
        )
    if trailing:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="extra_frame",
            detail="invoke response was followed by a third stdout frame",
        )
    if not isinstance(second, (ConnectorInvokeSuccess, ConnectorErrorResponse)):
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="invalid_message",
            detail="second stdout frame did not close invoke",
        )
    if second.id != invoke.id:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="response_id_mismatch",
            detail="invoke response ID differs",
        )
    try:
        if isinstance(second, ConnectorErrorResponse):
            second.validate_against_request(
                invoke,
                external_io_may_have_started=(
                    spec.invoke_failure_phase == "invoke_external_io_possible"
                ),
            )
        else:
            second.validate_against_request(invoke, manifest)
    except ValueError as exc:
        return _response_failure(
            initialize=initialize,
            invoke=invoke,
            spec=spec,
            observed_at=observed_at,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            phase=spec.invoke_failure_phase,
            violation="invalid_message",
            detail=str(exc),
        )
    return second


__all__ = [
    "ConnectorCommandTargetPin",
    "ConnectorFixtureSelection",
    "ConnectorProgramBinding",
    "ConnectorProcessResult",
    "ConnectorProcessSpec",
    "run_connector_process",
]
