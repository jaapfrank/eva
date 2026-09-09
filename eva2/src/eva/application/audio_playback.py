"""Output evidence, independent of a person, conversation engine or connector."""
from dataclasses import asdict, dataclass
import re
from typing import Literal


class PlaybackConflict(ValueError):
    """Playback evidence is unbound, conflicting or outside its reporting window."""


@dataclass(frozen=True, slots=True)
class AudioPlaybackResult:
    """Confirmed frames are a lower bound except for completed output.

    Output completion never proves human hearing. Interrupted frame counts
    cannot identify an exact spoken word prefix without alignment evidence.
    """
    session_id: str
    generation: int
    status: Literal["completed", "interrupted", "unconfirmed"]
    offered_frames: int
    confirmed_frames: int | None
    sample_rate: int = 24000

    def __post_init__(self):
        if (not isinstance(self.session_id, str) or not re.fullmatch(r"[0-9a-f]{16}", self.session_id)
                or self.session_id == "0" * 16
                or type(self.generation) is not int or not 1 <= self.generation <= 0xFFFFFFFF
                or type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 192000
                or type(self.offered_frames) is not int or not 0 <= self.offered_frames <= self.sample_rate * 180):
            raise PlaybackConflict("Invalid audio output binding or frame bounds")
        if not isinstance(self.status, str):
            raise PlaybackConflict("Invalid output result status")
        if self.status == "unconfirmed":
            if self.confirmed_frames is not None:
                raise PlaybackConflict("Unconfirmed output cannot claim played frames")
        elif self.status in {"completed", "interrupted"}:
            if (type(self.confirmed_frames) is not int
                    or not 0 <= self.confirmed_frames <= self.offered_frames):
                raise PlaybackConflict("Invalid confirmed output frame count")
            if self.status == "completed" and (not self.offered_frames or self.confirmed_frames != self.offered_frames):
                raise PlaybackConflict("Completed output requires all offered frames")
        else:
            raise PlaybackConflict("Invalid output result status")

    def to_wire(self) -> dict:
        return asdict(self)

    @classmethod
    def from_wire(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise PlaybackConflict("Invalid output result fields")
        return cls(**data)
