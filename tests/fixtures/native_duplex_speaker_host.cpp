#include "va_pipecat.h"
#include "esp_timer.h"
uint32_t host_ms=10;
bool host_closed=false;
using namespace esphome::va_pipecat;

// Extracted verbatim from the prepared component at probe build time.
__DRAIN_AND_FLUSH__

void driver_boundaries() {
  auto send=[](VaPipecat &d, uint8_t kind, uint32_t gen, uint32_t seq=0, size_t pcm=0) {
    std::vector<uint8_t> p(EvaOutputProtocol::HEADER+pcm,0);
    std::memcpy(p.data(),"EVA2",4); p[4]=kind; p[8]=1;
    EvaOutputProtocol::write32(p.data()+16,gen);
    EvaOutputProtocol::write32(p.data()+20,seq);
    EvaOutputProtocol::write32(p.data()+24,pcm);
    d.eva_ws_data_(2,true,0,p.size(),p.data(),p.size());
  };
  auto tick=[](VaPipecat &d) {
    ++host_ms; ++d.playback_main_loop_; d.sp.commands(); d.drain_audio_();
  };
  auto ready=[&](VaPipecat &d) {
    d.eva_reset_connection_(); send(d,EvaOutputProtocol::OPEN,0);
    for (int i=0;i<4;++i) tick(d);
    assert(d.eva_output_.state()==EvaOutputProtocol::State::IDLE);
    d.eva_ack_count_=0;
    send(d,EvaOutputProtocol::BEGIN,1); d.eva_ack_count_=0;
  };
  { // Actual held buffer is discarded by STOP, before any ACCEPTED for it.
    VaPipecat d; ready(d);
    send(d,EvaOutputProtocol::AUDIO,1,1,960);
    send(d,EvaOutputProtocol::AUDIO,1,2,960);
    d.eva_ack_count_=0;
    send(d,EvaOutputProtocol::AUDIO,1,3,960);
    assert(d.eva_held_size_ && !d.eva_ack_count_);
    send(d,EvaOutputProtocol::STOP,1);
    assert(!d.eva_held_size_ && !d.audio_fill_ && !d.eva_ack_count_);
    for (int i=0;i<4;++i) tick(d);
    assert(d.eva_ack_count_==1);
    assert(d.eva_ack_queue_[d.eva_ack_head_].bytes[4]==EvaOutputProtocol::STOPPED);
  }
  { // STOP arrives inside an unfinished real drain/play call.
    VaPipecat d; ready(d); send(d,EvaOutputProtocol::AUDIO,1,1,960);
    d.eva_ack_count_=0;
    // Start with a warm downstream so the raced call contains answer PCM.
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    bool raced=false;
    d.sp.during_play=[&]() {
      raced=true; send(d,EvaOutputProtocol::STOP,1);
      d.eva_service_output_();
      assert(!d.eva_ack_count_); // no STOPPED while play can still add old PCM
    };
    d.drain_audio_(); d.sp.during_play={};
    assert(raced && d.sp.queued.size()==960 && !d.eva_ack_count_);
    tick(d); assert(!d.eva_ack_count_);
    tick(d); assert(!d.eva_ack_count_);
    tick(d); assert(d.eva_ack_count_==1 && d.sp.queued.empty());
    const auto old_epoch=d.eva_epoch_us_;
    d.eva_ack_count_=0; send(d,EvaOutputProtocol::BEGIN,2);
    d.eva_output_frames_(480,old_epoch);
    assert(!d.eva_played_frames_); // late callback from former lifetime
    d.eva_output_frames_(24,d.eva_epoch_us_);
    assert(d.eva_played_frames_==24);
  }
  { // Graceful EOF precedes the final callback; stopping alone proves no playback.
    VaPipecat d; ready(d); send(d,EvaOutputProtocol::AUDIO,1,1,960);
    d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_();
    assert(d.eva_written_frames_==480 && !d.audio_fill_ && d.sp.queued.size()==960);
    send(d,EvaOutputProtocol::END,1,1); tick(d); tick(d);
    assert(d.sp.queued.size()==976 && d.eva_written_frames_==480 && !d.eva_prime_frames_);
    assert(d.sp.finish_requests==1 && d.sp.finish_pending && !d.eva_ack_count_);
    tick(d); assert(d.sp.finish_requests==1 && !d.eva_ack_count_);
    d.sp.queued.clear(); // A stopped source without a callback cannot yield FINISHED.
    tick(d); assert(d.sp.stopped && !d.eva_ack_count_);
    assert(d.eva_output_.state()==EvaOutputProtocol::State::DRAINING);
    d.eva_output_frames_(480,d.eva_epoch_us_);
    for (int i=0;i<4;++i) tick(d);
    assert(d.sp.finish_requests==1 && d.eva_ack_count_==1);
    const auto receipt=d.eva_ack_queue_[d.eva_ack_head_];
    assert(receipt.bytes[4]==EvaOutputProtocol::FINISHED);
    assert(EvaOutputProtocol::read32(receipt.bytes.data()+28)==480);
  }
  { // STOP cancels a pending graceful EOF, without finishing the old generation.
    VaPipecat d; ready(d); send(d,EvaOutputProtocol::AUDIO,1,1,960);
    d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_(); send(d,EvaOutputProtocol::END,1,1); tick(d); tick(d);
    assert(d.sp.finish_requests==1 && d.sp.finish_pending);
    d.eva_output_frames_(120,d.eva_epoch_us_);
    send(d,EvaOutputProtocol::STOP,1);
    for (int i=0;i<4;++i) tick(d);
    assert(d.eva_ack_count_==1 && !d.sp.finish_pending && d.sp.queued.empty());
    const auto stopped=d.eva_ack_queue_[d.eva_ack_head_];
    assert(stopped.bytes[4]==EvaOutputProtocol::STOPPED);
    assert(EvaOutputProtocol::read32(stopped.bytes.data()+28)==120);
    d.eva_ack_count_=0; send(d,EvaOutputProtocol::BEGIN,2); d.eva_ack_count_=0;
    send(d,EvaOutputProtocol::AUDIO,2,1,960); d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_(); send(d,EvaOutputProtocol::END,2,1); tick(d); tick(d);
    assert(d.sp.finish_requests==2 && !d.eva_ack_count_);
  }
  { // Downstream backpressure retries only the remaining finite silent tail.
    VaPipecat d; ready(d); send(d,EvaOutputProtocol::AUDIO,1,1,960);
    d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_(); send(d,EvaOutputProtocol::END,1,1);
    d.sp.play_limit=0; tick(d);
    assert(d.sp.queued.size()==960 && !d.sp.finish_requests && !d.eva_ack_count_);
    d.sp.play_limit=6;
    tick(d); assert(d.sp.queued.size()==966 && !d.sp.finish_requests);
    tick(d); assert(d.sp.queued.size()==972 && !d.sp.finish_requests);
    tick(d); assert(d.sp.queued.size()==976 && !d.sp.finish_requests);
    tick(d); assert(d.sp.queued.size()==976 && d.sp.finish_requests==1);
    assert(d.eva_written_frames_==480 && !d.eva_prime_frames_ && !d.eva_ack_count_);
    d.eva_output_frames_(479,d.eva_epoch_us_);
    d.sp.queued.clear(); tick(d);
    assert(!d.eva_ack_count_ && d.eva_output_.state()==EvaOutputProtocol::State::DRAINING);
    d.eva_output_frames_(1,d.eva_epoch_us_);
    for (int i=0;i<4;++i) tick(d);
    assert(d.eva_ack_count_==1);
    const auto done=d.eva_ack_queue_[d.eva_ack_head_];
    assert(done.bytes[4]==EvaOutputProtocol::FINISHED && EvaOutputProtocol::read32(done.bytes.data()+28)==480);
  }
  { // A STOP racing the actual EOF play invalidates it before any finish call.
    VaPipecat d; ready(d); send(d,EvaOutputProtocol::AUDIO,1,1,960);
    d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_(); send(d,EvaOutputProtocol::END,1,1);
    bool raced=false;
    d.sp.during_play=[&]() {
      raced=true; send(d,EvaOutputProtocol::STOP,1); d.eva_service_output_();
      assert(!d.eva_ack_count_ && !d.sp.finish_requests);
    };
    tick(d); d.sp.during_play={};
    assert(raced && !d.sp.finish_requests);
    for (int i=0;i<4;++i) tick(d);
    assert(d.sp.queued.empty() && d.eva_ack_count_==1);
    assert(d.eva_ack_queue_[d.eva_ack_head_].bytes[4]==EvaOutputProtocol::STOPPED);
    d.eva_ack_count_=0; send(d,EvaOutputProtocol::BEGIN,2); d.eva_ack_count_=0;
    send(d,EvaOutputProtocol::AUDIO,2,1,960); d.eva_ack_count_=0;
    d.eva_reprime_=false; d.last_fed_ms_=host_ms; d.sp.stopped=false;
    d.drain_audio_(); send(d,EvaOutputProtocol::END,2,1); tick(d); tick(d);
    assert(d.sp.queued.size()==976 && d.sp.finish_requests==1 && !d.eva_ack_count_);
  }
}

int main(int argc, char **) {
  if (argc>1) { driver_boundaries(); return 0; }
  VaPipecat device;
  device.eva_reset_connection_();
  bool playback=false;
  auto step=[&]() {
    for (unsigned tick=0; tick<64; ++tick) {
      ++host_ms;
      ++device.playback_main_loop_;
      device.sp.commands();
      device.drain_audio_();
      if (playback && !device.sp.queued.empty()) {
        device.eva_output_frames_(device.sp.queued.size()/2, esp_timer_get_time());
        device.sp.queued.clear();
      }
      device.eva_pump_receipts_();
    }
  };
  while (!host_closed) {
    uint8_t prefix[4];
    if (!std::cin.read(reinterpret_cast<char *>(prefix),4)) break;
    const uint32_t size=EvaOutputProtocol::read32(prefix);
    if (!size) { playback=true; step(); continue; } // test-only physical callback release
    if (size>EvaOutputProtocol::MAX_RECORD) return 2;
    std::vector<uint8_t> record(size);
    if (!std::cin.read(reinterpret_cast<char *>(record.data()),size)) return 3;
    // Two WS frames, each split into ESP-IDF callback buffers of <=17 bytes.
    const size_t split=size/2;
    for (unsigned frame=0; frame<2; ++frame) {
      size_t base=frame ? split : 0, total=frame ? size-split : split;
      for (size_t offset=0; offset<total; offset+=17) {
        size_t chunk=std::min(size_t(17),total-offset);
        device.eva_ws_data_(frame ? 0 : 2, frame==1, offset,total,record.data()+base+offset,chunk);
      }
    }
    step();
  }
}
