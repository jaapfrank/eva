"""Actual C++ device protocol/driver on host stubs, without hardware claims."""
from pathlib import Path
import shutil
import struct
import subprocess

from test_native_duplex_speaker_wire import wire

ROOT = Path(__file__).parents[2]
EXPERIMENT = ROOT / "experiments/native-duplex-v1"


def test_native_input_driver_orders_stop_and_capture_and_rejects_old_audio(tmp_path):
    for name in ["eva_output_driver.cpp", "eva_output_protocol.h", "eva_input_driver.cpp",
                 "eva_input_protocol.h", "playback_stop_barrier.h"]:
        shutil.copyfile(EXPERIMENT / name, tmp_path / name)
    shutil.copyfile(ROOT / "tests/fixtures/native_duplex_speaker_host.h", tmp_path / "va_pipecat.h")
    (tmp_path / "esp_timer.h").write_text('#pragma once\n#include <cstdint>\nextern uint32_t host_ms;\ninline int64_t esp_timer_get_time(){return int64_t(host_ms)*1000;}\n')
    (tmp_path / "esp_websocket_client.h").write_text('#pragma once\nusing esp_websocket_client_handle_t=void*;\nextern bool host_closed;\ninline int esp_websocket_client_close(void*,int){host_closed=true;return 0;}\n')
    (tmp_path / "main.cpp").write_text(r'''
#include "va_pipecat.h"
using namespace esphome::va_pipecat;
uint32_t host_ms=0;
bool host_closed=false;
void receive(VaPipecat &v,uint8_t kind,uint32_t epoch=0) {
  uint8_t p[28]{};
  std::memcpy(p,"EVA2",4); p[4]=kind;
  std::memcpy(p+8,"session!",8);
  EvaOutputProtocol::write32(p+16,epoch);
  v.eva_ws_data_(2,true,0,28,p,28);
}
void open(VaPipecat &v) {
  v.eva_reset_connection_();
  receive(v,1);
  auto a=v.eva_output_.poll(true,true,0,0,0);
  v.eva_apply_locked_(a,nullptr,0);
  v.eva_pump_receipts_();
}
void capture(VaPipecat &v,uint32_t epoch) {
  receive(v,6,epoch);
  v.eva_update_microphone_();
  v.eva_pump_input_();
  assert(v.eva_input_.state()==EvaInputProtocol::State::ACTIVE);
}
int main() {
  VaPipecat v;
  open(v);
  v.eva_local_wake(); v.eva_pump_input_();
  assert(v.eva_input_.state()==EvaInputProtocol::State::IDLE); // muted by default
  v.eva_set_muted(false); v.eva_local_wake(); v.eva_pump_input_();
  v.eva_update_microphone_();
  assert(v.mic.enabled);
  const uint64_t old=v.eva_capture_token_();
  v.eva_mic_data_(old,std::vector<uint8_t>(1280,3));
  v.eva_pump_input_(); // No PCM may leave before the server capture grant.
  assert(v.eva_mic_fill_==1280);
  capture(v,1);
  assert(v.eva_capture_token_()==old && v.eva_mic_fill_==1280);
  v.during_send=[&](){v.eva_local_stop();}; // Stop arrives during an actual PCM write.
  v.eva_pump_input_();
  v.eva_pump_input_();
  assert(v.eva_mic_fill_==0 && v.eva_capture_token_()==0);
  receive(v,7,1); v.eva_update_microphone_(); v.eva_pump_input_();
  capture(v,2); // no second wake
  v.eva_mic_data_(old,std::vector<uint8_t>(640,9));
  assert(v.eva_mic_fill_==0); // old conversion cannot become a new question
  v.eva_mic_data_(v.eva_capture_token_(),std::vector<uint8_t>(640,4));
  v.eva_pump_input_();
  v.eva_set_muted(true); v.eva_update_microphone_(); v.eva_pump_input_();
  assert(!v.mic.enabled && v.eva_capture_token_()==0);
  receive(v,7,2); v.eva_pump_input_();
  receive(v,6,3); // Server cannot undo hardware mute.
  assert(v.eva_input_.failed() && v.eva_output_.state()==EvaOutputProtocol::State::FAILED);

  // Connection fences also reject an old epoch1 callback after reconnect epoch1.
  v.eva_set_muted(false); open(v); v.eva_local_wake(); v.eva_pump_input_(); capture(v,1);
  v.eva_mic_data_(old,std::vector<uint8_t>(640,9));
  v.eva_mic_data_(old,std::vector<uint8_t>(1,9)); // Even malformed old callbacks are fenced.
  assert(v.eva_mic_fill_==0 && !v.eva_input_.failed());
  v.eva_mic_data_(v.eva_capture_token_(),std::vector<uint8_t>(32768,1));
  assert(v.eva_input_.failed() && v.eva_mic_fill_==0); // no silent overflow

  // Stop during pre-roll revokes the grant before a delayed CAPTURE can arrive.
  v.eva_set_muted(false); open(v); v.eva_local_wake(); v.eva_pump_input_();
  const auto pending=v.eva_capture_token_();
  v.eva_local_stop();
  assert(v.eva_input_.failed() && v.eva_capture_token_()==0);
  v.eva_mic_data_(pending,std::vector<uint8_t>(640,7));
  assert(v.eva_mic_fill_==0);
  receive(v,6,1);
  assert(v.eva_output_.state()==EvaOutputProtocol::State::FAILED);

  // Local stop must not fabricate a physical STOPPED before quiescence.
  EvaOutputProtocol out;
  uint8_t p[28]{}; std::memcpy(p,"EVA2",4); std::memcpy(p+8,"session!",8); p[4]=1;
  out.receive(p,28,1000); out.poll(true,true,0,0,0);
  p[4]=2; EvaOutputProtocol::write32(p+16,1); out.receive(p,28,1000);
  assert(out.local_stop().stop);
  assert(out.poll(false,true,0,0,0).receipt.size==0);
  p[4]=5; out.receive(p,28,1000);
  assert(out.poll(false,true,0,0,0).receipt.size==0);
  assert(out.poll(true,true,0,0,0).receipt.bytes[4]==133);
}
''')
    binary = tmp_path / "input-driver"
    subprocess.run(["g++", "-std=c++17", "-fsanitize=undefined", "-I", str(tmp_path),
                    str(tmp_path / "main.cpp"), str(tmp_path / "eva_output_driver.cpp"),
                    str(tmp_path / "eva_input_driver.cpp"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([str(binary)], check=True, capture_output=True)
    assert not result.stderr
    data, packets = result.stdout, []
    while data:
        size = struct.unpack("!I", data[:4])[0]
        packets.append(wire.Packet.decode(data[4:4+size]))
        data = data[4+size:]
    before_reconnect = packets[:next(i for i, p in enumerate(packets[1:], 1) if p.kind==wire.Kind.OPENED)]
    assert [(p.kind.name, p.generation, p.sequence) for p in before_reconnect] == [
        ("OPENED", 0, 0), ("WAKE", 0, 0), ("CAPTURED", 1, 0),
        ("MICROPHONE", 1, 1), ("LOCAL_STOP", 1, 1), ("CAPTURE_ENDED", 1, 0),
        ("CAPTURED", 2, 0), ("MICROPHONE", 2, 1), ("MUTED", 2, 1),
        ("CAPTURE_ENDED", 2, 0),
    ]
    assert [p.payload for p in before_reconnect if p.kind==wire.Kind.MICROPHONE] == [bytes([3])*640, bytes([4])*640]


def test_raw_microphone_callback_keeps_token_across_conversion_and_stop(tmp_path):
    """Exercise the real wrapper with a stop/restart inside conversion."""
    include = tmp_path / "esphome/components/microphone"
    include.mkdir(parents=True)
    (include / "microphone_source.h").write_text(r'''
#pragma once
#include <cstdint>
#include <functional>
#include <vector>
namespace esphome::microphone {
inline std::function<void()> during_conversion;
struct Microphone {
  std::function<void(const std::vector<uint8_t>&)> callback;
  bool active=false;
  void add_data_callback(decltype(callback) cb){callback=cb;}
  void start(){active=true;}
  void stop(){active=false;}
  bool is_running(){return active;}
};
class MicrophoneSource {
 public:
  explicit MicrophoneSource(Microphone *mic):mic_(mic){}
 protected:
  Microphone *mic_;
  void process_audio_(const std::vector<uint8_t>& data,std::vector<uint8_t>& out){
    out=data;
    if(during_conversion){auto cb=std::move(during_conversion);during_conversion={};cb();}
  }
};
}
''')
    shutil.copyfile(EXPERIMENT / "eva_microphone_source.h", tmp_path / "eva_microphone_source.h")
    (tmp_path / "main.cpp").write_text(r'''
#include "eva_microphone_source.h"
#include <cassert>
int main(){
  esphome::microphone::Microphone mic;
  esphome::microphone::MicrophoneSource base(&mic);
  esphome::va_pipecat::EvaMicrophoneSource source(base);
  uint64_t token=1; unsigned calls=0, accepted=0;
  source.subscribe([&](){return token;},[&](uint64_t captured,const auto& data){
    ++calls; if(captured==token){++accepted;assert(data==std::vector<uint8_t>({4,5}));}
  });
  source.enable(true);
  esphome::microphone::during_conversion=[&](){source.enable(false);token=2;source.enable(true);};
  mic.callback({1,2,3});
  assert(calls==1 && accepted==0); // Conversion retains epoch 1 across stop/restart.
  mic.callback({4,5});
  assert(calls==2 && accepted==1);
  source.enable(false); mic.callback({9,9});
  assert(calls==2 && !source.running());
}
''')
    binary = tmp_path / "microphone-wrapper"
    subprocess.run(["g++", "-std=c++17", "-fsanitize=undefined", "-I", str(tmp_path),
                    str(tmp_path / "main.cpp"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([str(binary)], check=True, capture_output=True)
    assert not result.stderr
