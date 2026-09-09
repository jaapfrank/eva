#pragma once

#include <atomic>
#include <cstdint>

namespace esphome::va_pipecat {

// Only the audio task calls poll() or operates the owned speaker. Other tasks
// request cancellation; they never race a speaker.stop() against speaker.play().
// This is a local barrier, NOT a session-bound network playback receipt.
class PlaybackStopBarrier {
 public:
  void request() { requested_.fetch_add(1, std::memory_order_release); }
  bool pending() const {
    return requested_.load(std::memory_order_acquire) != completed_.load(std::memory_order_acquire);
  }
  bool failed() const { return failed_.load(std::memory_order_acquire); }

  template<class Speaker> bool poll(Speaker &speaker, uint32_t main_loop, uint32_t now_ms) {
    if (failed()) return false;
    const auto requested = requested_.load(std::memory_order_acquire);
    if (!stopping_ && requested == completed_.load(std::memory_order_acquire)) return true;
    if (!stopping_) {
      speaker.stop();
      stopping_ = true;
      requested_at_loop_ = main_loop;
      requested_at_ms_ = now_ms;
      return false;
    }
    // A late observation cannot turn an expired deadline into success.
    if (uint32_t(now_ms - requested_at_ms_) >= 3000) {
      failed_.store(true, std::memory_order_release);
      return false;
    }
    // stop() merely queues an ESPHome command. A STOPPED state in the same
    // iteration might still have an unprocessed START. Observe two main-loop
    // boundaries so the owned resampler gets an opportunity to consume STOP.
    if (uint32_t(main_loop - requested_at_loop_) >= 2 && speaker.is_stopped()) {
      completed_.store(requested, std::memory_order_release);
      stopping_ = false;
      return !pending();
    }
    // Duplicate interrupts cannot keep extending the deadline. Failure stays
    // latched: a timeout is never a successful stop or permission to play.
    return false;
  }

 private:
  std::atomic<uint32_t> requested_{0};
  std::atomic<uint32_t> completed_{0};
  std::atomic<bool> failed_{false};
  bool stopping_{false};  // audio-task-owned below
  uint32_t requested_at_loop_{0};
  uint32_t requested_at_ms_{0};
};

}  // namespace esphome::va_pipecat
