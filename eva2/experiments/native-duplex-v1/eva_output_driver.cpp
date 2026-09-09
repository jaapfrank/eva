#include "va_pipecat.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"

namespace esphome::va_pipecat {

// All *_locked_ methods require ring_mux_. They only update bounded buffers
// and state. The existing audio task alone operates the owned speaker.
void VaPipecat::eva_flush_locked_() {
  this->playback_stop_.request();
  this->audio_head_=this->audio_tail_=this->audio_fill_=0;
  ++this->audio_generation_;
  this->eva_held_size_=0;
}

void VaPipecat::eva_apply_locked_(const EvaOutputAction &action, const uint8_t *record, size_t size) {
  if (action.stop) this->eva_flush_locked_();
  if (action.failed) {
    this->eva_input_.fail();
    this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
    this->eva_close_requested_.store(true, std::memory_order_release);
  }
  if (action.reset_frames) {
    this->eva_written_frames_=this->eva_played_frames_=this->eva_prime_frames_=0;
    // DMA timestamps identify callbacks for the previous source lifetime even
    // if callback dispatch is delayed until after a new BEGIN resets counters.
    this->eva_epoch_us_=esp_timer_get_time();
    this->eva_reprime_.store(true, std::memory_order_release);
  }
  if (action.hold_pcm) {
    if (record != this->eva_held_record_.data()) std::memcpy(this->eva_held_record_.data(), record, size);
    this->eva_held_size_=size;
  }
  if (action.append_pcm) {
    const auto *pcm=record+EvaOutputProtocol::HEADER;
    const size_t bytes=size-EvaOutputProtocol::HEADER;
    float volume=this->volume_.load(std::memory_order_relaxed);
    if (!(volume>=0.0f && volume<=1.0f)) volume=0.0f;
    const int32_t scale=static_cast<int32_t>(volume*32768.0f);
    for (size_t i=0; i<bytes; i+=2) {
      int16_t value;
      std::memcpy(&value, pcm+i, sizeof(value));
      value=static_cast<int16_t>((static_cast<int32_t>(value)*scale)>>15);
      this->audio_buf_[this->audio_tail_]=static_cast<uint16_t>(value)&0xff;
      this->audio_buf_[(this->audio_tail_+1)%this->audio_buf_bytes_]=static_cast<uint16_t>(value)>>8;
      this->audio_tail_=(this->audio_tail_+2)%this->audio_buf_bytes_;
    }
    this->audio_fill_+=bytes;
  }
  if (action.receipt.size) {
    this->eva_input_.opened(action.receipt);
    if (this->eva_ack_count_ == this->eva_ack_queue_.size()) {
      // A stalled receiver cannot create an unbounded defer/task queue.
      this->eva_output_.fail();
      this->eva_flush_locked_();
      this->eva_ack_count_=0;
      this->eva_close_requested_.store(true, std::memory_order_release);
      return;
    }
    const size_t tail=(this->eva_ack_head_+this->eva_ack_count_)%this->eva_ack_queue_.size();
    this->eva_ack_queue_[tail]=action.receipt;
    ++this->eva_ack_count_;
  }
}

void VaPipecat::eva_reset_connection_() {
  this->eva_assembler_.reset();  // WS task owns message assembly
  portENTER_CRITICAL(&this->ring_mux_);
  this->eva_output_.reset_connection();
  this->eva_input_.reset();
  ++this->eva_connection_serial_;
  this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
  this->eva_ack_head_=this->eva_ack_count_=0;
  this->eva_epoch_us_=0;
  this->eva_flush_locked_();
  this->eva_close_requested_.store(false, std::memory_order_release);
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_ws_data_(uint8_t opcode, bool fin, int offset, int total,
                            const uint8_t *data, int size) {
  if (opcode==9 || opcode==10 || opcode==8) return;  // WS client handles control frames
  EvaWireAssembler::Result result=EvaWireAssembler::INVALID;
  if (offset>=0 && total>=0 && size>=0)
    result=this->eva_assembler_.feed(opcode, fin, offset, total, data, size);
  if (result==EvaWireAssembler::MORE) return;
  portENTER_CRITICAL(&this->ring_mux_);
  if (result==EvaWireAssembler::INVALID) {
    this->eva_apply_locked_(this->eva_output_.fail(), nullptr, 0);
  } else {
    const auto *record=this->eva_assembler_.data();
    const size_t length=this->eva_assembler_.size();
    // Sender may have only one AUDIO awaiting ACCEPTED. A held batch must not
    // be overwritten, while STOP/control still need prompt access to the parser.
    if (this->eva_held_size_ && length>=EvaOutputProtocol::HEADER && record[4]==EvaOutputProtocol::AUDIO) {
      this->eva_apply_locked_(this->eva_output_.fail(), nullptr, 0);
    } else if (length>=EvaOutputProtocol::HEADER && (record[4]==EvaInputProtocol::CAPTURE || record[4]==EvaInputProtocol::CAPTURE_END)) {
      if (!this->eva_input_.receive(record,length)) this->eva_apply_locked_(this->eva_output_.fail(),nullptr,0);
      if (record[4]==EvaInputProtocol::CAPTURE_END)
        this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
    } else {
      auto action=this->eva_output_.receive(record, length, this->audio_buf_bytes_-this->audio_fill_);
      this->eva_apply_locked_(action, record, length);
    }
  }
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_service_output_() {
  // Called by audio_task_ after polling PlaybackStopBarrier. Checking pending
  // again under the same mutex as command admission closes the receipt race.
  portENTER_CRITICAL(&this->ring_mux_);
  if (this->playback_stop_.failed()) {
    this->eva_apply_locked_(this->eva_output_.fail(), nullptr, 0);
  } else {
    if (this->eva_held_size_) {
      const size_t size=this->eva_held_size_;
      auto action=this->eva_output_.receive(this->eva_held_record_.data(), size,
                                           this->audio_buf_bytes_-this->audio_fill_);
      if (!action.hold_pcm) this->eva_held_size_=0;
      this->eva_apply_locked_(action, this->eva_held_record_.data(), size);
    }
    const bool quiet=!this->playback_stop_.pending() && this->speaker_->is_stopped();
    auto action=this->eva_output_.poll(quiet, this->audio_fill_==0 && this->eva_held_size_==0,
      this->eva_written_frames_, this->eva_played_frames_, this->eva_prime_frames_);
    this->eva_apply_locked_(action, nullptr, 0);
  }
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_output_frames_(uint32_t frames, int64_t timestamp) {
  portENTER_CRITICAL(&this->ring_mux_);
  if (this->eva_epoch_us_ && timestamp>=this->eva_epoch_us_) {
    // Bound a faulty downstream counter; it must not wrap and validate a later reply.
    const uint32_t limit=EvaOutputProtocol::MAX_FRAMES*2;
    if (frames>limit || this->eva_played_frames_>limit-frames)
      this->eva_apply_locked_(this->eva_output_.fail(), nullptr, 0);
    else
      this->eva_played_frames_+=frames;
  }
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_pump_receipts_() {
  // Main loop owns network receipt writes. Never block the audio task on I/O.
  for (unsigned i=0; i<4; ++i) {
    EvaReceipt receipt;
    portENTER_CRITICAL(&this->ring_mux_);
    if (this->eva_ack_count_) {
      receipt=this->eva_ack_queue_[this->eva_ack_head_];
      this->eva_ack_head_=(this->eva_ack_head_+1)%this->eva_ack_queue_.size();
      --this->eva_ack_count_;
    }
    portEXIT_CRITICAL(&this->ring_mux_);
    if (!receipt.size) break;
    if (!this->ws_connected_ || this->ws_send_bin_(reinterpret_cast<const char *>(receipt.bytes.data()),
        receipt.size, pdMS_TO_TICKS(10)) != static_cast<int>(receipt.size)) {
      portENTER_CRITICAL(&this->ring_mux_);
      this->eva_apply_locked_(this->eva_output_.fail(), nullptr, 0);
      portEXIT_CRITICAL(&this->ring_mux_);
      break;
    }
  }
  if (this->eva_close_requested_.exchange(false, std::memory_order_acq_rel) && this->ws_connected_ && this->ws_handle_)
    esp_websocket_client_close(static_cast<esp_websocket_client_handle_t>(this->ws_handle_), pdMS_TO_TICKS(100));
}

}  // namespace esphome::va_pipecat
