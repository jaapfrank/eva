#pragma once
#include "esphome/components/microphone/microphone_source.h"
#include <atomic>

namespace esphome::va_pipecat {
// Reuse ESPHome's configured channel/gain/bit-depth conversion. Capture fencing
// begins on the raw callback, before conversion, and is checked again afterward.
// Conversion storage belongs only to the microphone task; stop never frees it.
class EvaMicrophoneSource : public microphone::MicrophoneSource {
 public:
  explicit EvaMicrophoneSource(const microphone::MicrophoneSource &source)
    : microphone::MicrophoneSource(source) {}
  template<class Token,class Deliver> void subscribe(Token token,Deliver deliver) {
    this->mic_->add_data_callback([this,token,deliver](const std::vector<uint8_t> &data) {
      if (!this->active_.load(std::memory_order_acquire)) return;
      const uint64_t epoch=token();
      if (!epoch) return;
      this->process_audio_(data,this->converted_);
      deliver(epoch,this->converted_);
    });
  }
  void enable(bool enabled) {
    if (this->active_.exchange(enabled,std::memory_order_acq_rel)==enabled) return;
    if (enabled) this->mic_->start();
    else this->mic_->stop();
  }
  bool running() const { return this->active_.load(std::memory_order_acquire) && this->mic_->is_running(); }
 private:
  std::atomic<bool> active_{false};
  std::vector<uint8_t> converted_;
};
}  // namespace esphome::va_pipecat
