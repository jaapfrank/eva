#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace esphome::va_pipecat {

// Pure protocol logic; the component serializes calls with its ring mutex.
// No network calls, speaker calls, dynamic allocations or blocking here.
struct EvaReceipt {
  std::array<uint8_t, 32> bytes{};
  size_t size{0};
};

struct EvaOutputAction {
  bool stop{false};
  bool reset_frames{false};
  bool append_pcm{false};
  bool hold_pcm{false};
  bool failed{false};
  EvaReceipt receipt{};
};

class EvaOutputProtocol {
 public:
  static constexpr size_t HEADER = 28, MAX_PCM = 960, MAX_RECORD = HEADER + MAX_PCM;
  static constexpr uint32_t MAX_FRAMES = 24000 * 180;
  enum Kind : uint8_t { OPEN=1, BEGIN=2, AUDIO=3, END=4, STOP=5,
    OPENED=129, BEGUN=130, ACCEPTED=131, FINISHED=132, STOPPED=133, ERROR=134 };
  enum class State { FRESH, OPENING, IDLE, ACTIVE, DRAINING, FINISHING, STOPPING, LOCAL_STOPPING, FAILED };

  static uint32_t read32(const uint8_t *p) {
    return (uint32_t(p[0])<<24) | (uint32_t(p[1])<<16) | (uint32_t(p[2])<<8) | p[3];
  }
  static void write32(uint8_t *p, uint32_t value) {
    p[0]=value>>24; p[1]=value>>16; p[2]=value>>8; p[3]=value;
  }

  State state() const { return state_; }
  void reset_connection() { *this = EvaOutputProtocol(); }

  EvaOutputAction fail() {
    EvaOutputAction action;
    if (state_ == State::FAILED) return action;
    state_ = State::FAILED;
    action.stop = action.failed = true;
    if (session_set_) action.receipt = receipt_(ERROR, current_, 0);
    return action;
  }

  EvaOutputAction local_stop() {
    EvaOutputAction action;
    if (state_==State::FAILED || state_==State::FRESH || state_==State::OPENING) return action;
    action.stop=true;
    if (state_!=State::STOPPING) state_=State::LOCAL_STOPPING;
    return action;
  }

  EvaOutputAction receive(const uint8_t *record, size_t size, size_t free_bytes) {
    EvaOutputAction action;
    if (state_ == State::FAILED) return action;
    if (size < HEADER || size > MAX_RECORD || std::memcmp(record, "EVA2", 4) != 0 ||
        record[5] || record[6] || record[7] || read32(record+24) != size-HEADER) return fail();
    const auto kind = record[4];
    const uint32_t generation=read32(record+16), sequence=read32(record+20);
    const size_t payload=size-HEADER;
    bool nonzero=false;
    for (size_t i=8; i<16; ++i) nonzero |= record[i] != 0;
    if (!nonzero) return fail();
    if (kind == OPEN) {
      if (state_ != State::FRESH || generation || sequence || payload) return fail();
      std::memcpy(session_.data(), record+8, 8);
      session_set_=true;
      state_=State::OPENING;
      action.stop=true;
      return action;
    }
    if (!session_set_ || std::memcmp(session_.data(), record+8, 8) != 0 || generation==0 ||
        state_ == State::OPENING || state_ == State::FRESH) return fail();
    if (kind != AUDIO && payload) return fail();
    if (kind == STOP) {
      if (sequence) return fail();
      if (generation < current_ || (state_ == State::IDLE && generation <= maximum_)) {
        action.receipt=receipt_(STOPPED, generation, 0, remembered_(generation));
        return action;  // never stop a newer generation for a stale STOP
      }
      for (size_t i=0; i<stop_count_; ++i) if (stops_[i]==generation) return action;
      if (stop_count_ == stops_.size()) return fail();
      stops_[stop_count_++]=generation;
      maximum_=std::max(maximum_, generation);
      state_=State::STOPPING;
      action.stop=true;
      return action;
    }
    if (kind == BEGIN) {
      if (sequence) return fail();
      if (generation <= maximum_) return action;  // permanent invalidation, no timed guard
      if (state_ != State::IDLE) return fail();
      maximum_=current_=generation;
      frames_=sequence_=0;
      state_=State::ACTIVE;
      action.reset_frames=true;
      action.receipt=receipt_(BEGUN, generation, 0);
      return action;
    }
    if (kind != AUDIO && kind != END) return fail();
    if (generation < current_ || (generation <= maximum_ &&
        (state_ == State::STOPPING || state_ == State::LOCAL_STOPPING || state_ == State::IDLE))) return action;
    if (generation != current_ || state_ != State::ACTIVE) return fail();
    if (kind == AUDIO) {
      if (!payload || payload%2 || sequence != sequence_+1 || frames_+payload/2 > MAX_FRAMES) return fail();
      if (payload > free_bytes) { action.hold_pcm=true; return action; }
      frames_+=payload/2;
      sequence_=sequence;
      action.append_pcm=true;
      action.receipt=receipt_(ACCEPTED, generation, sequence, frames_);
    } else {
      if (!frames_ || sequence != sequence_) return fail();
      state_=State::DRAINING;
    }
    return action;
  }

  EvaOutputAction poll(bool stopped_confirmed, bool ring_empty, uint32_t written_frames,
                       uint32_t played_frames, uint32_t prime_frames) {
    EvaOutputAction action;
    if (state_ == State::OPENING && stopped_confirmed) {
      state_=State::IDLE;
      action.receipt=receipt_(OPENED, 0, 0);
    } else if (state_ == State::DRAINING && ring_empty && written_frames == frames_ &&
               played_frames >= prime_frames+frames_) {
      // All answer frames have a downstream callback. Quiesce the owned source
      // as well before acknowledging completion or resetting its counters.
      state_=State::FINISHING;
      action.stop=true;
    } else if (state_ == State::FINISHING && stopped_confirmed) {
      state_=State::IDLE;
      remember_(current_, frames_);
      action.receipt=receipt_(FINISHED, current_, sequence_, frames_);
    } else if (state_ == State::LOCAL_STOPPING && stopped_confirmed) {
      const auto heard=std::min(frames_, played_frames>prime_frames ? played_frames-prime_frames : 0);
      if (current_) remember_(current_,heard);
      state_=State::IDLE;
    } else if (state_ == State::STOPPING && stopped_confirmed) {
      const auto heard=std::min(frames_, played_frames>prime_frames ? played_frames-prime_frames : 0);
      if (current_) remember_(current_, heard);
      const uint32_t stopped=stops_[0];
      for (size_t i=1; i<stop_count_; ++i) stops_[i-1]=stops_[i];
      --stop_count_;
      action.receipt=receipt_(STOPPED, stopped, 0, stopped==current_ ? heard : remembered_(stopped));
      if (!stop_count_) state_=State::IDLE;
    }
    return action;
  }

 private:
  EvaReceipt receipt_(uint8_t kind, uint32_t generation, uint32_t sequence, uint32_t frames=0) const {
    EvaReceipt receipt;
    auto *p=receipt.bytes.data();
    std::memcpy(p, "EVA2", 4); p[4]=kind;
    std::memcpy(p+8, session_.data(), 8);
    write32(p+16, generation); write32(p+20, sequence);
    const bool count=kind==ACCEPTED || kind==FINISHED || kind==STOPPED;
    if (count) { write32(p+24, 4); write32(p+28, frames); }
    receipt.size=HEADER+(count ? 4 : 0);
    return receipt;
  }
  void remember_(uint32_t generation, uint32_t frames) {
    for (auto &item: history_) if (item[0]==generation) { item[1]=frames; return; }
    history_[history_index_]={generation, frames};
    history_index_=(history_index_+1)%history_.size();
  }
  uint32_t remembered_(uint32_t generation) const {
    for (const auto &item: history_) if (item[0]==generation) return item[1];
    return 0;  // conservative confirmed lower bound if an old count was evicted
  }
  State state_{State::FRESH};
  std::array<uint8_t,8> session_{};
  bool session_set_{false};
  uint32_t maximum_{0}, current_{0}, frames_{0}, sequence_{0};
  std::array<uint32_t,8> stops_{};
  size_t stop_count_{0};
  std::array<std::array<uint32_t,2>,8> history_{};
  size_t history_index_{0};
};

// Assemble one binary WS message from both ESP-IDF buffer fragments and actual
// WebSocket continuation frames. No trailing second record or unbounded buffer.
class EvaWireAssembler {
 public:
  enum Result { MORE, RECORD, INVALID };
  Result feed(uint8_t opcode, bool fin, size_t offset, size_t total, const uint8_t *data, size_t size) {
    if (size && data==nullptr) return INVALID;
    if (offset==0) {
      if (frame_offset_ != frame_total_) return INVALID;
      if (opcode==2) {
        if (open_) return INVALID;
        used_=0; open_=true;
      } else if (opcode!=0 || !open_) return INVALID;
      frame_offset_=0; frame_total_=total; frame_opcode_=opcode; frame_fin_=fin;
      if (total > buffer_.size()-used_) return INVALID;
    }
    if (!open_ || offset!=frame_offset_ || total!=frame_total_ || opcode!=frame_opcode_ ||
        fin!=frame_fin_ || size>frame_total_-frame_offset_ || size>buffer_.size()-used_) return INVALID;
    if (size) std::memcpy(buffer_.data()+used_, data, size);
    used_+=size; frame_offset_+=size;
    if (frame_offset_==frame_total_ && fin) { open_=false; return RECORD; }
    return MORE;
  }
  void reset() { *this=EvaWireAssembler(); }
  const uint8_t *data() const { return buffer_.data(); }
  size_t size() const { return used_; }
 private:
  std::array<uint8_t,EvaOutputProtocol::MAX_RECORD> buffer_{};
  size_t used_{0}, frame_offset_{0}, frame_total_{0};
  uint8_t frame_opcode_{0};
  bool frame_fin_{false}, open_{false};
};

}  // namespace esphome::va_pipecat
