"""Execute the firmware's actual stop barrier with controlled speaker timing."""

from pathlib import Path
import subprocess


EXPERIMENT = Path(__file__).resolve().parents[3] / "experiments/native-duplex-v1"


def test_cpp_stop_barrier(tmp_path):
    source = tmp_path / "stop.cpp"
    source.write_text(r'''
#include "playback_stop_barrier.h"
#include <cassert>
#include <functional>
using esphome::va_pipecat::PlaybackStopBarrier;
struct Speaker {
  int stops = 0;
  bool stopped = false;
  std::function<void()> on_read;
  void stop() { ++stops; }
  bool is_stopped() { if (on_read) on_read(); return stopped; }
};
int main() {
  { // An asynchronous STOP command must be processed even if state starts STOPPED.
    PlaybackStopBarrier gate; Speaker sp; sp.stopped = true;
    assert(gate.poll(sp, 5, 10));
    gate.request();
    assert(!gate.poll(sp, 5, 11));
    assert(!gate.poll(sp, 5, 12));
    assert(!gate.poll(sp, 6, 13));
    assert(gate.poll(sp, 7, 14));
    assert(sp.stops == 1 && !gate.pending());
  }
  { // Local ring empty is not downstream stopped.
    PlaybackStopBarrier gate; Speaker sp;
    gate.request();
    assert(!gate.poll(sp, 1, 1));
    assert(!gate.poll(sp, 5, 50));
    sp.stopped = true;
    assert(gate.poll(sp, 6, 60));
  }
  { // Interrupt storms cannot postpone the timeout; failure cannot be reopened.
    PlaybackStopBarrier gate; Speaker sp;
    gate.request();
    assert(!gate.poll(sp, 1, 10));
    for (unsigned i = 1; i <= 30; ++i) {
      gate.request();
      assert(!gate.poll(sp, i+1, 10+i*100));
    }
    assert(gate.failed() && sp.stops == 1);
    sp.stopped = true;
    assert(!gate.poll(sp, 99, 3020));
    gate.request();
    assert(!gate.poll(sp, 100, 3030));
  }
  { // A new request arriving during completion is not acknowledged as the old one.
    PlaybackStopBarrier gate; Speaker sp;
    gate.request();
    assert(!gate.poll(sp, 1, 1));
    sp.stopped = true;
    sp.on_read = [&]() { gate.request(); };
    assert(!gate.poll(sp, 3, 5));
    sp.on_read = {};
    assert(gate.pending());
    assert(!gate.poll(sp, 4, 6));
    assert(!gate.poll(sp, 5, 7));
    assert(gate.poll(sp, 6, 8));
    assert(sp.stops == 2);
  }
  { // ESPHome millis()/loop counters can wrap.
    PlaybackStopBarrier gate; Speaker sp;
    gate.request();
    assert(!gate.poll(sp, UINT32_MAX, UINT32_MAX-5));
    sp.stopped = true;
    assert(!gate.poll(sp, 0, 0));
    assert(gate.poll(sp, 1, 1));
  }
  { // STOPPED first observed after the deadline is not timely confirmation.
    PlaybackStopBarrier gate; Speaker sp;
    gate.request();
    assert(!gate.poll(sp, 1, 10));
    sp.stopped = true;
    assert(!gate.poll(sp, 3, 3010));
    assert(gate.failed());
  }
}
''')
    executable = tmp_path / "stop"
    subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
         "-I", str(EXPERIMENT), str(source), "-o", str(executable)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
