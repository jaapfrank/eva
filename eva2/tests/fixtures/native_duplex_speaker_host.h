#pragma once
// Host substitutes for ESPHome/FreeRTOS. Used only with actual prepared driver
// and drain/flush methods by verify_speaker_wire.py. No physical timing claim.
#include "playback_stop_barrier.h"
#include "eva_output_protocol.h"
#include "eva_input_protocol.h"
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstring>
#include <functional>
#include <iostream>
#include <vector>
#define portENTER_CRITICAL(mux) ((void)0)
#define portEXIT_CRITICAL(mux) ((void)0)
#define ESP_LOGD(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define pdMS_TO_TICKS(ms) (ms)
extern uint32_t host_ms;
extern bool host_closed;
inline uint32_t millis() { return host_ms; }
inline std::function<void(uint32_t)> host_task_delay=[](uint32_t) {};
inline void vTaskDelay(uint32_t ticks) { host_task_delay(ticks); }

namespace esphome::va_pipecat {
struct HostSpeaker {
  bool stopped=true, start_pending=false, stop_pending=false;
  std::vector<uint8_t> queued;
  std::function<void()> during_play;
  bool is_stopped() const { return stopped; }
  bool has_buffered_data() const { return !queued.empty(); }
  void start() { start_pending=true; }
  void stop() { stop_pending=true; }
  void commands() {
    if (stop_pending) { stopped=true; queued.clear(); start_pending=false; stop_pending=false; }
    else if (start_pending) { stopped=false; start_pending=false; }
  }
  size_t play(const uint8_t *data, size_t size) {
    if (stopped) return 0;
    if (during_play) during_play();
    size=std::min(size, size_t(4800)-queued.size());
    queued.insert(queued.end(), data, data+size);
    return size;
  }
};

struct EvaMicrophoneSource {
  bool enabled=false;
  void enable(bool value) { enabled=value; }
  bool running() const { return enabled; }
};

class VaPipecat {
 public:
  HostSpeaker sp;
  HostSpeaker *speaker_=&sp;
  std::array<uint8_t,1920> storage{};
  uint8_t *audio_buf_=storage.data();
  size_t audio_buf_bytes_=storage.size(), audio_head_=0, audio_tail_=0, audio_fill_=0;
  uint32_t audio_generation_=0;
  int ring_mux_=0;
  bool eva_pcm_protocol_=true;
  PlaybackStopBarrier playback_stop_;
  std::atomic<uint32_t> playback_main_loop_{0};
  bool playback_failure_reported_=false, underrun_logged_this_turn_=false;
  std::atomic<bool> playback_priming_{false};
  std::atomic<uint32_t> playback_prebuffer_ms_{0}, prime_started_ms_{0};
  uint32_t last_fed_ms_=0;
  size_t chain_prime_remaining_=0;
  static constexpr uint32_t kChainColdMs=600, kChainPrimeMs=60, kPlaybackSampleRate=24000;
  bool ensure_playback_speaker_ready_() { if (sp.stopped) sp.start(); return true; }
  template<class F> void defer(F f) { f(); }
  void cancel_turn_(const char *) { flush_playback_queue_(); }
  void fire_error_(const char *, const char *) {}
  bool ws_connected_=true;
  void *ws_handle_=reinterpret_cast<void *>(1);
  std::atomic<float> volume_{1.0f};
  std::string eva_phase_;
  void fire_phase_led_(const std::string &) {}
  bool eva_session_active();
  EvaInputProtocol eva_input_;
  EvaMicrophoneSource mic;
  EvaMicrophoneSource *eva_mic_source_=&mic;
  std::atomic<bool> eva_mic_running_{false};
  uint32_t eva_connection_serial_{0};
  size_t eva_mic_head_=0,eva_mic_tail_=0,eva_mic_fill_=0;
  static constexpr size_t kMicTxMaxQueuedBytes=16384;
  std::array<uint8_t,kMicTxMaxQueuedBytes> mic_storage{};
  uint8_t *mic_tx_buf_=mic_storage.data();
  void eva_local_wake();
  void eva_local_stop();
  void eva_set_muted(bool);
  void eva_update_microphone_();
  void eva_pump_input_();
  void eva_input_task_();
  uint64_t eva_capture_token_();
  void eva_mic_data_(uint64_t,const std::vector<uint8_t> &);
  EvaOutputProtocol eva_output_;
  EvaWireAssembler eva_assembler_;
  std::array<uint8_t,EvaOutputProtocol::MAX_RECORD> eva_held_record_{};
  size_t eva_held_size_=0;
  std::array<EvaReceipt,8> eva_ack_queue_{};
  size_t eva_ack_head_=0, eva_ack_count_=0;
  uint32_t eva_written_frames_=0, eva_played_frames_=0, eva_prime_frames_=0;
  int64_t eva_epoch_us_=0;
  std::atomic<bool> eva_reprime_{false}, eva_close_requested_{false};
  bool drain_audio_();
  size_t flush_playback_queue_();
  void eva_flush_locked_();
  void eva_apply_locked_(const EvaOutputAction &, const uint8_t *, size_t);
  void eva_reset_connection_();
  void eva_ws_data_(uint8_t,bool,int,int,const uint8_t *,int);
  void eva_service_output_();
  void eva_output_frames_(uint32_t,int64_t);
  void eva_pump_receipts_();
  std::function<void()> during_send;
  int ws_send_bin_(const char *data, int size, int) {
    if (during_send) { auto hook=during_send; during_send=nullptr; hook(); }
    uint8_t prefix[4]; EvaOutputProtocol::write32(prefix,size);
    std::cout.write(reinterpret_cast<const char *>(prefix),4);
    std::cout.write(data,size); std::cout.flush(); return size;
  }
};
}  // namespace esphome::va_pipecat
