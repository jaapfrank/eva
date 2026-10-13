from dataclasses import replace

import pytest

from eva.application.audio_playback import AudioPlaybackResult, PlaybackConflict


@pytest.mark.parametrize("change", [
    {"session_id": "0" * 16}, {"session_id": None}, {"generation": True},
    {"generation": 0}, {"sample_rate": True}, {"offered_frames": -1},
    {"offered_frames": 24000 * 180 + 1}, {"status": []},
    {"confirmed_frames": True}, {"confirmed_frames": 41},
    {"status": "completed", "confirmed_frames": 39},
    {"status": "unconfirmed", "confirmed_frames": 0},
])
def test_output_evidence_rejects_invented_confirmation_and_invalid_bounds(change):
    result = AudioPlaybackResult("a" * 16, 1, "interrupted", 40, 20)
    with pytest.raises(PlaybackConflict):
        replace(result, **change)


def test_unconfirmed_output_does_not_mean_zero_playback_or_human_hearing():
    result = AudioPlaybackResult("a" * 16, 1, "unconfirmed", 40, None)
    assert AudioPlaybackResult.from_wire(result.to_wire()) == result
    assert result.confirmed_frames is None
    with pytest.raises(PlaybackConflict):
        AudioPlaybackResult.from_wire({**result.to_wire(), "heard_text": "alles"})
