"""Execute the firmware protocol's completion, cancellation and fragment boundaries."""

from pathlib import Path
import subprocess


EXPERIMENT = Path(__file__).resolve().parents[2] / "experiments/native-duplex-v1"


def test_cpp_output_protocol(tmp_path):
    source = tmp_path / "protocol.cpp"
    source.write_text(r'''
#include "eva_output_protocol.h"
#include <cassert>
#include <vector>
using namespace esphome::va_pipecat;
using P = EvaOutputProtocol;
std::vector<uint8_t> packet(uint8_t kind, uint32_t gen=0, uint32_t seq=0, size_t pcm=0) {
  std::vector<uint8_t> p(P::HEADER+pcm, 0);
  std::memcpy(p.data(), "EVA2", 4); p[4]=kind; p[8]=1;
  P::write32(p.data()+16, gen); P::write32(p.data()+20, seq);
  P::write32(p.data()+24, pcm);
  return p;
}
EvaOutputAction send(P &p, uint8_t kind, uint32_t gen=0, uint32_t seq=0,
                     size_t pcm=0, size_t free=960) {
  auto data=packet(kind,gen,seq,pcm);
  return p.receive(data.data(),data.size(),free);
}
void ready(P &p) {
  assert(send(p,P::OPEN).stop);
  assert(!p.poll(false,true,0,0,0).receipt.size);
  assert(p.poll(true,true,0,0,0).receipt.bytes[4]==P::OPENED);
  assert(send(p,P::BEGIN,1).reset_frames);
}
int main() {
  { // Ring empty / writes accepted alone cannot prove completed playback.
    P p; ready(p);
    assert(send(p,P::AUDIO,1,1,960).append_pcm);
    send(p,P::END,1,1);
    auto pad=p.poll(true,true,480,479,0,100);
    assert(pad.tail_frames==8 && !pad.finish && !pad.receipt.size);
    assert(!p.record_tail(3).failed);
    assert(p.poll(true,true,480,479,0,100).tail_frames==5);
    assert(!p.record_tail(5).failed);
    auto tail=p.poll(true,true,480,479,0,100);
    assert(tail.finish && tail.finish_diagnostic && !tail.stop && !tail.receipt.size);
    assert(!p.poll(true,true,480,479,0,101).finish);
    assert(!p.poll(true,true,479,480,0).stop);
    assert(!p.poll(true,false,480,480,0).stop);
    assert(!p.poll(true,true,480,500,40).stop); // priming silence is not answer audio
    assert(p.poll(false,true,480,520,40).stop);
    assert(!p.poll(false,true,480,520,40).receipt.size);
    auto done=p.poll(true,true,480,520,40).receipt;
    assert(done.bytes[4]==P::FINISHED && P::read32(done.bytes.data()+28)==480);
  }
  { // EOF is one-shot, diagnostic-only retries are bounded, and STOP wins.
    P p; ready(p); send(p,P::AUDIO,1,1,960); send(p,P::END,1,1);
    assert(!p.poll(false,false,480,479,0,50).finish);
    assert(!p.poll(false,true,479,479,0,99).finish);
    assert(p.poll(false,true,480,479,0,100).tail_frames==8);
    assert(!p.record_tail(8).failed);
    auto first=p.poll(false,true,480,479,0,100);
    assert(first.finish && first.finish_diagnostic && !first.receipt.size);
    assert(!p.poll(true,true,480,479,0,599).finish_diagnostic);
    for (uint32_t now=600; now<=3600; now+=500) {
      auto waiting=p.poll(true,true,480,479,0,now);
      assert(!waiting.finish && waiting.finish_diagnostic && !waiting.receipt.size);
      assert(p.state()==P::State::DRAINING); // even stopped cannot substitute for played
    }
    auto bounded=p.poll(true,true,480,479,0,4100);
    assert(!bounded.finish && !bounded.finish_diagnostic && !bounded.receipt.size);
    assert(send(p,P::STOP,1).stop);
    auto stopped=p.poll(true,true,480,479,0,4600);
    assert(!stopped.finish && !stopped.finish_diagnostic);
    assert(stopped.receipt.bytes[4]==P::STOPPED);
    assert(P::read32(stopped.receipt.bytes.data()+28)==479);
    assert(send(p,P::BEGIN,2).reset_frames);
    send(p,P::AUDIO,2,1,960); send(p,P::END,2,1);
    assert(p.poll(false,true,480,0,0,4601).tail_frames==8);
    assert(!p.record_tail(8).failed);
    auto next=p.poll(false,true,480,0,0,4601);
    assert(next.finish && next.finish_diagnostic && !next.receipt.size);
  }
  { // A malformed tail result fails; a cancelled tail cannot advance a new reply.
    P p; ready(p); send(p,P::AUDIO,1,1,960); send(p,P::END,1,1);
    assert(p.record_tail(9).failed);
    P q; ready(q); send(q,P::AUDIO,1,1,960); send(q,P::END,1,1);
    assert(!q.record_tail(3).failed); assert(send(q,P::STOP,1).stop);
    auto stopped=q.poll(true,true,480,470,0);
    assert(stopped.receipt.bytes[4]==P::STOPPED);
    assert(send(q,P::BEGIN,2).reset_frames);
    send(q,P::AUDIO,2,1,960); send(q,P::END,2,1);
    assert(q.poll(false,true,480,0,0).tail_frames==8);
  }
  { // A backpressured batch cannot block STOP or survive its tombstone.
    P p; ready(p);
    assert(send(p,P::AUDIO,1,1,960,0).hold_pcm);
    assert(send(p,P::STOP,1).stop);
    assert(!p.poll(false,true,0,0,0).receipt.size);
    assert(!send(p,P::AUDIO,1,1,960).append_pcm);
    auto stopped=p.poll(true,true,0,0,0).receipt;
    assert(stopped.bytes[4]==P::STOPPED && P::read32(stopped.bytes.data()+28)==0);
    assert(!send(p,P::BEGIN,1).reset_frames);
    assert(send(p,P::BEGIN,2).reset_frames);
    assert(!send(p,P::STOP,1).stop); // old STOP cannot silence new answer
    assert(!send(p,P::AUDIO,1,1,960).append_pcm);
    assert(send(p,P::AUDIO,2,1,960).append_pcm);
  }
  { // All future STOPs are acknowledged only after the source has stopped.
    P p; ready(p); send(p,P::AUDIO,1,1,960);
    assert(send(p,P::STOP,1).stop && send(p,P::STOP,2).stop);
    assert(!p.poll(false,true,480,90,40).receipt.size);
    auto first=p.poll(true,true,480,90,40).receipt;
    assert(P::read32(first.bytes.data()+16)==1 && P::read32(first.bytes.data()+28)==50);
    assert(p.state()==P::State::STOPPING);
    auto second=p.poll(true,true,480,90,40).receipt;
    assert(P::read32(second.bytes.data()+16)==2 && P::read32(second.bytes.data()+28)==0);
    assert(!send(p,P::BEGIN,2).reset_frames && send(p,P::BEGIN,3).reset_frames);
  }
  { P p; ready(p); send(p,P::STOP,1); assert(send(p,P::BEGIN,2).failed); }
  { P p; ready(p); auto foreign=packet(P::AUDIO,1,1,960); foreign[8]=2;
    assert(p.receive(foreign.data(),foreign.size(),960).failed);
    assert(!send(p,P::BEGIN,2).reset_frames); }
  { P p; ready(p); assert(send(p,P::AUDIO,1,2,960).failed); }
  { P p; ready(p); assert(send(p,P::AUDIO,1,1,959).failed); }
  { P p; ready(p); auto extra=packet(P::AUDIO,1,1,960); extra.push_back(0);
    assert(p.receive(extra.data(),extra.size(),1000).failed); }
  { // IDF fragments, followed by a real zero-length final continuation frame.
    EvaWireAssembler a; auto data=packet(P::AUDIO,1,1,960);
    assert(a.feed(2,false,0,data.size(),data.data(),17)==a.MORE);
    assert(a.feed(2,false,17,data.size(),data.data()+17,data.size()-17)==a.MORE);
    assert(a.feed(0,true,0,0,nullptr,0)==a.RECORD);
    assert(a.size()==data.size() && !std::memcmp(a.data(),data.data(),data.size()));
    a.reset(); assert(a.feed(0,true,0,1,data.data(),1)==a.INVALID);
    a.reset(); assert(a.feed(2,true,0,989,data.data(),1)==a.INVALID);
    a.reset(); assert(a.feed(2,true,0,28,data.data(),17)==a.MORE);
    assert(a.feed(2,true,18,28,data.data()+17,10)==a.INVALID);
    a.reset(); assert(a.feed(2,false,0,28,data.data(),28)==a.MORE);
    assert(a.feed(2,true,0,28,data.data(),28)==a.INVALID);
    a.reset(); assert(a.feed(2,true,0,28,nullptr,28)==a.INVALID);
  }
}
''')
    executable = tmp_path / "protocol"
    subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
         "-I", str(EXPERIMENT), str(source), "-o", str(executable)],
        check=True, capture_output=True, text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
