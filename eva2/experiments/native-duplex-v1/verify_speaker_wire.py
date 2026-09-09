"""Python output interoperates with actual prepared C++ driver/drain on host stubs."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from speaker_wire import FramedSpeakerOutput, Kind, Packet, PlaybackInterrupted
from verify_speaker_stop import method

HERE = Path(__file__).resolve().parent


def build_host(component: Path, target: Path) -> Path:
    fixture = HERE.parents[1] / "tests/fixtures"
    for name in ["eva_output_driver.cpp", "eva_output_protocol.h", "playback_stop_barrier.h", "eva_input_protocol.h"]:
        shutil.copyfile(component / name, target / name)
    shutil.copyfile(fixture / "native_duplex_speaker_host.h", target / "va_pipecat.h")
    (target / "esp_timer.h").write_text('#pragma once\n#include <cstdint>\nextern uint32_t host_ms;\ninline int64_t esp_timer_get_time() { return int64_t(host_ms)*1000; }\n')
    (target / "esp_websocket_client.h").write_text('#pragma once\nusing esp_websocket_client_handle_t=void *;\nextern bool host_closed;\ninline int esp_websocket_client_close(void *,int) { host_closed=true; return 0; }\n')
    source = (component / "va_pipecat.cpp").read_text()
    functions = "\n".join(method(source, signature) for signature in [
        "bool VaPipecat::drain_audio_() {", "size_t VaPipecat::flush_playback_queue_() {",
    ])
    (target / "main.cpp").write_text((fixture / "native_duplex_speaker_host.cpp").read_text().replace("__DRAIN_AND_FLUSH__", functions))
    binary = target / "speaker"
    subprocess.run(["g++", "-std=c++17", "-fsanitize=undefined", "-I", str(target),
                    str(target / "main.cpp"), str(target / "eva_output_driver.cpp"), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


class ProcessSocket:
    def __init__(self, process):
        self.process = process
        self.receipts = []

    async def send(self, data):
        self.process.stdin.write(struct.pack("!I", len(data)) + data)
        await self.process.stdin.drain()

    async def recv(self):
        size = struct.unpack("!I", await self.process.stdout.readexactly(4))[0]
        assert 28 <= size <= 32
        raw = await self.process.stdout.readexactly(size)
        self.receipts.append(Packet.decode(raw))
        return raw

    async def close(self):
        if self.process.returncode is None:
            self.process.stdin.close()
            await asyncio.wait_for(self.process.wait(), 2)


async def run_case(binary, *, interrupt):
    process = await asyncio.create_subprocess_exec(str(binary), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    socket = ProcessSocket(process)
    supplied = asyncio.Event()

    async def pcm(text):
        yield bytes([7])*960
        yield bytes([8])*200
        supplied.set()

    output = FramedSpeakerOutput(socket=socket, pcm=pcm, receipt_timeout=2)
    reply = None
    try:
        await output.start()
        reply = asyncio.create_task(output.speak(generation=1, text="hostproef"))
        await asyncio.wait_for(supplied.wait(), 2)
        # The sender hasn't received FINISHED: the host's physical callback is disabled.
        await asyncio.sleep(0)
        assert not reply.done()
        if interrupt:
            await output.interrupt(generation=1)
            try:
                await reply
            except PlaybackInterrupted:
                pass
            else:
                raise AssertionError("interrupted reply reported success")
            # Old validly-shaped PCM and BEGIN must never reopen that generation.
            await socket.send(Packet(Kind.AUDIO, output.session, 1, 3, bytes([9])*960).encode())
            await socket.send(Packet(Kind.BEGIN, output.session, 1).encode())
            supplied.clear()
            reply = asyncio.create_task(output.speak(generation=2, text="volgende beurt"))
            await asyncio.wait_for(supplied.wait(), 2)
        await socket.send(b"")  # test-only release of the emulated DAC callbacks
        await reply
        await output.close()
        assert process.returncode == 0
        counts = [(p.kind.name, p.generation, struct.unpack("!I", p.payload)[0])
                  for p in socket.receipts if p.kind in {Kind.FINISHED, Kind.STOPPED}]
        assert ("FINISHED", 2 if interrupt else 1, 580) in counts
        if interrupt:
            assert ("STOPPED", 1, 0) in counts
            assert not any(p.kind == Kind.ACCEPTED and p.generation == 1 and p.sequence == 3 for p in socket.receipts)
        return counts
    finally:
        if reply is not None and not reply.done():
            reply.cancel()
        try:
            await output.close()
        except Exception:
            pass
        if reply is not None:
            await asyncio.gather(reply, return_exceptions=True)
        if process.returncode is None:
            process.kill()
            await process.wait()
        error = await process.stderr.read()
        if error:
            raise AssertionError(error.decode(errors="replace"))


async def verify(component):
    with tempfile.TemporaryDirectory(prefix="eva-cpp-wire-") as directory:
        binary = build_host(component, Path(directory))
        subprocess.run([str(binary), "--boundaries"], check=True, capture_output=True, text=True)
        normal = await run_case(binary, interrupt=False)
        interrupted = await run_case(binary, interrupt=True)
    return {"status": "passed", "normalReceipts": normal, "interruptedReceipts": interrupted,
            "driverBoundaries": ["held PCM discarded before STOPPED", "STOP during unfinished play waits for barrier", "previous-lifetime callback excluded"],
            "sourceHashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in component.iterdir() if p.suffix in {".h", ".cpp"}},
            "scope": "Actual prepared C++ receiver, driver, drain and stop barrier; host speaker/clock/locks; UBSan. Not hardware."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True, type=Path)
    print(json.dumps(asyncio.run(verify(parser.parse_args().component)), indent=2))
