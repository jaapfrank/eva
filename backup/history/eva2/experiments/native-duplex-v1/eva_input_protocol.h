#pragma once
#include "eva_output_protocol.h"

namespace esphome::va_pipecat {

// Called under the component mutex. Only the network task commits writes.
class EvaInputProtocol {
 public:
  static constexpr uint8_t CAPTURE=6, CAPTURE_END=7, WAKE=144, PCM=145,
    LOCAL_STOP=146, MUTED=147, CAPTURED=148, CAPTURE_ENDED=149, END_SESSION=150;
  enum class State { IDLE, ARMED, STARTING, ACTIVE, INTERRUPTED, ENDING, FAILED };
  void reset() {
    const bool muted=muted_;
    *this=EvaInputProtocol();
    muted_=muted;
  }
  void opened(const EvaReceipt &receipt) {
    if (receipt.size==28 && receipt.bytes[4]==EvaOutputProtocol::OPENED) {
      std::memcpy(session_.data(),receipt.bytes.data()+8,8);
      ready_=true;
    }
  }
  void fail() { state_=State::FAILED; pending_=0; }
  bool failed() const { return state_==State::FAILED; }
  bool wants_microphone() const { return state_==State::ARMED || state_==State::STARTING || state_==State::ACTIVE; }
  uint32_t capture_token() const { return wants_microphone() && !muted_ ? (state_==State::ARMED ? epoch_+1 : epoch_) : 0; }
  bool accepts(uint32_t token) const { return token && token==capture_token(); }
  uint32_t epoch() const { return epoch_; }
  State state() const { return state_; }

  bool wake() {
    if (!ready_ || muted_ || failed()) return false;
    if (state_==State::STARTING || state_==State::ACTIVE) return stop(false, true);
    if (state_!=State::IDLE || woke_) return false;
    woke_=true;
    state_=State::ARMED;
    pending_=WAKE;
    return true;
  }
  bool stop(bool mute, bool resume=false) {
    if (mute) muted_=true;
    if (!ready_ || failed()) return false;
    // No capture exists yet to revoke on the wire. Invalidate the local grant
    // and reconnect rather than accepting a delayed CAPTURE after local stop.
    if (state_==State::ARMED) { fail(); return false; }
    if (!epoch_ || (state_!=State::ACTIVE && state_!=State::STARTING && state_!=State::INTERRUPTED && state_!=State::ENDING)) {
      if (mute) fail();  // Idle mute ends this connection; never mint a grant.
      return false;
    }
    // A terminal control can supersede an in-flight wakeword interruption.
    // Preserve ENDING so CAPTURE_ENDED can still confirm physical mic shutdown.
    if ((state_==State::INTERRUPTED || state_==State::ENDING) && !mute && (resume || !resume_)) return false;
    if (state_!=State::ENDING) state_=State::INTERRUPTED;
    pending_=muted_ ? MUTED : (resume ? LOCAL_STOP : END_SESSION);
    resume_=resume && !muted_;
    return true;
  }
  void unmute() { muted_=false; }  // A fresh wake is still required.

  bool receive(const uint8_t *p,size_t n) {
    if (failed()) return false;
    if (!ready_ || n!=28 || std::memcmp(p,"EVA2",4) || p[5] || p[6] || p[7] ||
        std::memcmp(p+8,session_.data(),8) || EvaOutputProtocol::read32(p+20) ||
        EvaOutputProtocol::read32(p+24)) { fail(); return false; }
    uint32_t epoch=EvaOutputProtocol::read32(p+16);
    if (p[4]==CAPTURE) {
      if (muted_ || state_!=State::ARMED || pending_ || epoch!=epoch_+1 || epoch>512) {
        fail(); return false;
      }
      epoch_=epoch;
      sequence_=0;
      state_=State::STARTING;
    } else if (p[4]==CAPTURE_END) {
      if (!epoch || epoch!=epoch_ || state_==State::IDLE || state_==State::ARMED) {
        fail(); return false;
      }
      state_=State::ENDING;
    } else { fail(); return false; }
    return true;
  }

  EvaReceipt control(bool mic_running) {
    if (failed() || !ready_) return {};
    if (pending_) return make(pending_, pending_==WAKE ? 0 : epoch_, pending_==WAKE ? 0 : sequence_);
    if (state_==State::STARTING && mic_running) return make(CAPTURED,epoch_,0);
    if (state_==State::ENDING && !mic_running) return make(CAPTURE_ENDED,epoch_,0);
    return {};
  }
  // Reserve the control transition before the serialized write: a peer reply
  // can arrive before send returns. The caller must fail closed on write failure.
  void committed(const EvaReceipt &record) {
    const uint8_t kind=record.bytes[4];
    if (kind==WAKE) { if (pending_==WAKE) pending_=0; }
    else if (kind==LOCAL_STOP || kind==MUTED || kind==END_SESSION) {
      if (pending_==kind) pending_=0;
    } else if (kind==CAPTURED) {
      if (state_==State::STARTING) state_=State::ACTIVE;
    } else if (kind==CAPTURE_ENDED) {
      if (state_==State::ENDING) {
        state_=resume_ && !muted_ ? State::ARMED : State::IDLE;
        resume_=false;
      }
    }
  }
  bool pcm_header(uint8_t *p,uint32_t token) const {
    if (state_!=State::ACTIVE || !accepts(token) || sequence_==0xFFFFFFFF) return false;
    auto header=make(PCM,epoch_,sequence_+1);
    std::memcpy(p,header.bytes.data(),28);
    EvaOutputProtocol::write32(p+24,640);
    return true;
  }
  bool pcm_committed(uint32_t epoch,uint32_t sequence) {
    if (epoch!=epoch_ || sequence!=sequence_+1) { fail(); return false; }
    sequence_=sequence;
    return true;
  }
 private:
  EvaReceipt make(uint8_t kind,uint32_t epoch,uint32_t sequence) const {
    EvaReceipt r;
    r.size=28;
    std::memcpy(r.bytes.data(),"EVA2",4);
    r.bytes[4]=kind;
    std::memcpy(r.bytes.data()+8,session_.data(),8);
    EvaOutputProtocol::write32(r.bytes.data()+16,epoch);
    EvaOutputProtocol::write32(r.bytes.data()+20,sequence);
    return r;
  }
  std::array<uint8_t,8> session_{};
  State state_{State::IDLE};
  uint32_t epoch_{0}, sequence_{0};
  uint8_t pending_{0};
  bool ready_{false}, muted_{true}, woke_{false}, resume_{false};
};
}  // namespace esphome::va_pipecat
