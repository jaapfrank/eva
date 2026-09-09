#include "va_pipecat.h"
#include "esp_websocket_client.h"

namespace esphome::va_pipecat {

bool VaPipecat::eva_session_active() {
  portENTER_CRITICAL(&this->ring_mux_);
  auto state=this->eva_input_.state();
  bool active=state!=EvaInputProtocol::State::IDLE && state!=EvaInputProtocol::State::FAILED;
  portEXIT_CRITICAL(&this->ring_mux_);
  return active;
}

void VaPipecat::eva_local_wake() {
  portENTER_CRITICAL(&this->ring_mux_);
  const bool active=this->eva_input_.capture_token()!=0;
  if (this->eva_input_.wake() && active) {
    this->eva_apply_locked_(this->eva_output_.local_stop(),nullptr,0);
    this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
  }
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_local_stop() {
  portENTER_CRITICAL(&this->ring_mux_);
  this->eva_input_.stop(false);
  this->eva_apply_locked_(this->eva_output_.local_stop(),nullptr,0);
  this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
  portEXIT_CRITICAL(&this->ring_mux_);
}

void VaPipecat::eva_set_muted(bool muted) {
  portENTER_CRITICAL(&this->ring_mux_);
  if (muted) {
    this->eva_input_.stop(true);
    this->eva_apply_locked_(this->eva_output_.local_stop(),nullptr,0);
    this->eva_mic_head_=this->eva_mic_tail_=this->eva_mic_fill_=0;
  } else this->eva_input_.unmute();
  portEXIT_CRITICAL(&this->ring_mux_);
}

uint64_t VaPipecat::eva_capture_token_() {
  portENTER_CRITICAL(&this->ring_mux_);
  const uint64_t epoch=this->eva_input_.capture_token();
  const uint64_t token=epoch ? (uint64_t(this->eva_connection_serial_)<<32)|epoch : 0;
  portEXIT_CRITICAL(&this->ring_mux_);
  return token;
}

void VaPipecat::eva_mic_data_(uint64_t token,const std::vector<uint8_t> &samples) {
  portENTER_CRITICAL(&this->ring_mux_);
  const bool current=uint32_t(token>>32)==this->eva_connection_serial_ && this->eva_input_.accepts(uint32_t(token));
  portEXIT_CRITICAL(&this->ring_mux_);
  if (!current) return;
  if (samples.empty() || samples.size()%2 || samples.size()>65536) {
    portENTER_CRITICAL(&this->ring_mux_);
    this->eva_apply_locked_(this->eva_output_.fail(),nullptr,0);
    portEXIT_CRITICAL(&this->ring_mux_);
    return;
  }
  // Copy in bounded pieces; the raw-callback token survives conversion and
  // every piece is checked again against the current capture generation.
  for (size_t offset=0;offset<samples.size();offset+=640) {
    const size_t count=std::min(size_t(640),samples.size()-offset);
    portENTER_CRITICAL(&this->ring_mux_);
    if (uint32_t(token>>32)!=this->eva_connection_serial_ || !this->eva_input_.accepts(uint32_t(token))) {
      portEXIT_CRITICAL(&this->ring_mux_);
      return;
    }
    if (this->eva_mic_fill_+count>kMicTxMaxQueuedBytes) {
      this->eva_apply_locked_(this->eva_output_.fail(),nullptr,0);
      portEXIT_CRITICAL(&this->ring_mux_);
      return;
    }
    for (size_t i=0;i<count;++i) {
      this->mic_tx_buf_[this->eva_mic_tail_]=samples[offset+i];
      this->eva_mic_tail_=(this->eva_mic_tail_+1)%kMicTxMaxQueuedBytes;
    }
    this->eva_mic_fill_+=count;
    portEXIT_CRITICAL(&this->ring_mux_);
  }
}

void VaPipecat::eva_update_microphone_() {
  // ESPHome main loop owns start/stop; the microphone task only converts data.
  portENTER_CRITICAL(&this->ring_mux_);
  const bool wanted=this->eva_input_.wants_microphone();
  const auto input=this->eva_input_.state();
  const auto output=this->eva_output_.state();
  const bool replying=output==EvaOutputProtocol::State::ACTIVE || output==EvaOutputProtocol::State::DRAINING || output==EvaOutputProtocol::State::FINISHING;
  const char *phase=replying ? "speaking" : (input!=EvaInputProtocol::State::IDLE && input!=EvaInputProtocol::State::FAILED ? "listening" : "idle");
  portEXIT_CRITICAL(&this->ring_mux_);
  if (this->eva_mic_source_) {
    this->eva_mic_source_->enable(wanted);
    this->eva_mic_running_.store(this->eva_mic_source_->running(),std::memory_order_release);
  }
  if (this->eva_phase_!=phase) {
    this->eva_phase_=phase;
    this->fire_phase_led_(phase);
  }
}

void VaPipecat::eva_pump_input_() {
  // Same network task as output receipts: no two writers can reorder the last
  // PCM frame, LOCAL_STOP, CAPTURE_ENDED and the following capture's first PCM.
  EvaReceipt control;
  uint8_t record[28+640];
  size_t size=0;
  uint32_t serial=0,epoch=0,sequence=0;
  portENTER_CRITICAL(&this->ring_mux_);
  serial=this->eva_connection_serial_;
  if (this->eva_input_.failed()) {
    this->eva_apply_locked_(this->eva_output_.fail(),nullptr,0);
  } else {
    control=this->eva_input_.control(this->eva_mic_running_.load(std::memory_order_acquire));
    if (control.size) {
      std::memcpy(record,control.bytes.data(),control.size);
      size=control.size;
      // Admit a peer response that arrives before ws_send returns. Writes are
      // serialized; failure below still disables capture and the connection.
      this->eva_input_.committed(control);
    } else if (this->eva_mic_fill_>=640 && this->eva_input_.pcm_header(record,this->eva_input_.epoch())) {
      epoch=EvaOutputProtocol::read32(record+16);
      sequence=EvaOutputProtocol::read32(record+20);
      for (size_t i=0;i<640;++i) {
        record[28+i]=this->mic_tx_buf_[this->eva_mic_head_];
        this->eva_mic_head_=(this->eva_mic_head_+1)%kMicTxMaxQueuedBytes;
      }
      this->eva_mic_fill_-=640;
      size=sizeof(record);
    }
  }
  portEXIT_CRITICAL(&this->ring_mux_);
  if (!size) return;
  const int sent=this->ws_send_bin_(reinterpret_cast<const char *>(record),size,pdMS_TO_TICKS(10));
  portENTER_CRITICAL(&this->ring_mux_);
  if (serial==this->eva_connection_serial_) {
    if (sent!=static_cast<int>(size) || (epoch && !this->eva_input_.pcm_committed(epoch,sequence)))
      this->eva_apply_locked_(this->eva_output_.fail(),nullptr,0);
  }
  portEXIT_CRITICAL(&this->ring_mux_);
}
}  // namespace esphome::va_pipecat
