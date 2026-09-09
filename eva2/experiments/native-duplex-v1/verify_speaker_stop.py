"""Execute actual patched drain/flush methods in deterministic host race probes.

ESPHome/FreeRTOS and the speaker are host stubs. This checks local interleavings,
not real task scheduling, downstream ESPHome commands or acoustic performance.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def method(source: str, signature: str) -> str:
    start = source.index(signature)
    # The pinned component uses an unindented closing brace for member methods.
    end = source.index("\n}\n", start) + 3
    return source[start:end]


HARNESS = r'''
#include "playback_stop_barrier.h"
#include <algorithm>
#include <cassert>
#include <cstring>
#include <functional>
#include <string>
#include <vector>
#define portENTER_CRITICAL(mux) ((void)0)
#define portEXIT_CRITICAL(mux) ((void)0)
#define ESP_LOGD(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
uint32_t clock_ms = 10;
uint32_t millis() { return clock_ms; }
using esphome::va_pipecat::PlaybackStopBarrier;
struct Speaker {
  bool stopped = false;
  int stops = 0, plays = 0;
  size_t accept_limit = 10000;
  std::function<void()> on_play, on_buffered;
  std::vector<uint8_t> heard;
  bool is_stopped() { return stopped; }
  bool has_buffered_data() { if (on_buffered) on_buffered(); return true; }
  void stop() { ++stops; }
  size_t play(const uint8_t *data, size_t size) {
    ++plays;
    if (on_play) on_play(); // Caller may cancel and overwrite ring during this call.
    size = std::min(size, accept_limit);
    heard.insert(heard.end(), data, data+size);
    return size;
  }
};
class VaPipecat {
 public:
  Speaker sp;
  Speaker *speaker_ = &sp;
  uint8_t storage[1920]{};
  uint8_t *audio_buf_ = storage;
  size_t audio_buf_bytes_ = sizeof(storage), audio_head_ = 0, audio_tail_ = 0, audio_fill_ = 0;
  uint32_t audio_generation_ = 0;
  int ring_mux_ = 0;
  PlaybackStopBarrier playback_stop_;
  std::atomic<uint32_t> playback_main_loop_{0};
  bool playback_failure_reported_ = false, underrun_logged_this_turn_ = false;
  std::atomic<bool> playback_priming_{false};
  std::atomic<uint32_t> playback_prebuffer_ms_{0}, prime_started_ms_{0};
  uint32_t last_fed_ms_ = 1;
  size_t chain_prime_remaining_ = 0;
  static constexpr uint32_t kChainColdMs = 600, kChainPrimeMs = 60, kPlaybackSampleRate = 24000;
  bool ensure_playback_speaker_ready_() { sp.stopped = false; return true; }
  void defer(std::function<void()> call) { call(); }
  void cancel_turn_(const char *) { flush_playback_queue_(); }
  void fire_error_(const std::string &, const std::string &) {}
  bool drain_audio_();
  size_t flush_playback_queue_();
  void queue(uint8_t value, size_t size=960) {
    std::memset(storage, value, sizeof(storage));
    audio_head_ = 0; audio_tail_ = size; audio_fill_ = size;
  }
};
__METHODS__
int main() {
  { // Real play() still reads its original bytes if ring resets during its call.
    VaPipecat v; v.queue(7);
    v.sp.on_play = [&]() { v.flush_playback_queue_(); v.queue(9); };
    assert(v.drain_audio_());
    assert(v.sp.heard == std::vector<uint8_t>(960,7));
    assert(v.audio_fill_ == 960); // old acceptance did not decrement new generation
    v.sp.on_play = {};
    assert(!v.drain_audio_());
    assert(v.sp.stops == 1 && v.sp.plays == 1);
    v.playback_main_loop_ = 1;
    assert(!v.drain_audio_());
    v.playback_main_loop_ = 2;
    assert(!v.drain_audio_()); // downstream still running
    v.sp.stopped = true;
    // Drain may prime silence after stop; the critical property is no play
    // before the barrier, and no generation-7 payload added after it.
    v.drain_audio_();
    assert(!v.playback_stop_.pending());
  }
  { // Interrupt between ring snapshot and the final copied PCM submission.
    VaPipecat v; v.queue(7);
    v.sp.on_buffered = [&]() { v.flush_playback_queue_(); };
    assert(!v.drain_audio_());
    assert(v.sp.plays == 0);
    v.sp.on_buffered = {};
    assert(!v.drain_audio_());
    assert(v.sp.stops == 1);
  }
  { // Stop also reaches the speaker when our local ring was already empty.
    VaPipecat v;
    v.flush_playback_queue_();
    assert(!v.drain_audio_());
    assert(v.sp.stops == 1);
    clock_ms += 3000;
    assert(!v.drain_audio_());
    assert(v.playback_failure_reported_);
    v.queue(8); v.sp.stopped = true; v.playback_main_loop_ = 3;
    assert(!v.drain_audio_());
    assert(v.sp.plays == 0);
    clock_ms = 10;
  }
  { // Partial downstream acceptance keeps the exact remainder queued.
    VaPipecat v; v.queue(6); v.sp.accept_limit = 100;
    assert(v.drain_audio_());
    assert(v.audio_fill_ == 860 && v.audio_head_ == 100);
    assert(v.sp.heard == std::vector<uint8_t>(100,6));
  }
}
'''


def verify(component: Path) -> dict:
    source = (component / "va_pipecat.cpp").read_text()
    selected = "\n".join(method(source, signature) for signature in [
        "bool VaPipecat::drain_audio_() {", "size_t VaPipecat::flush_playback_queue_() {",
    ])
    with tempfile.TemporaryDirectory(prefix="eva-speaker-race-") as directory:
        root = Path(directory)
        cpp, binary = root / "race.cpp", root / "race"
        cpp.write_text(HARNESS.replace("__METHODS__", selected))
        subprocess.run(["g++", "-std=c++17", "-fsanitize=undefined", "-I", str(component),
                        str(cpp), "-o", str(binary)], check=True, capture_output=True, text=True)
        subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    return {
        "status": "passed", "scenarios": 4,
        "componentCppSha256": hashlib.sha256((component / "va_pipecat.cpp").read_bytes()).hexdigest(),
        "stopBarrierSha256": hashlib.sha256((component / "playback_stop_barrier.h").read_bytes()).hexdigest(),
        "scope": "Actual drain/flush methods, deterministic host stubs, UBSan; not hardware timing.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, required=True)
    print(json.dumps(verify(parser.parse_args().component), indent=2))
