"""Verify the exact speaker launcher under systemd, inside unshare --net.

Uses existing explicit HA/core/device test fixtures. TLS, FFmpeg, Silero and
the service run unchanged. Requires root and the repository test environment.
No household credentials, physical audio or external provider calls are used.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "src"),
                str(HERE.parents[1] / "tests/operations")]
from test_native_duplex_speaker_service import upstream, config, TOKEN
from test_native_duplex_speaker_input import NativePeer, Kind
from test_native_duplex_speaker_endpoint import client
from aiohttp import web, ClientSession
from service_bundle import verify, digest


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


async def systemctl(*args):
    process = await asyncio.create_subprocess_exec("systemctl", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 25)
    if process.returncode:
        raise RuntimeError(f"systemctl {args[0]} failed: {stderr.decode()[:300]}")
    return stdout.decode()


async def until(predicate, *, timeout=8):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.02)


async def run(bundle: Path, output: Path):
    if os.geteuid() != 0:
        raise ValueError("requires root for temporary systemd units")
    if os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
        raise ValueError("requires a separate network namespace")
    if {row["ifname"] for row in json.loads(command("ip", "-j", "link", "show"))} != {"lo"}:
        raise ValueError("test namespace must contain only loopback")
    if output.exists() or not output.is_absolute():
        raise ValueError("output must be a new absolute directory")
    manifest = verify(bundle)
    output.mkdir(mode=0o700)
    command("ip", "link", "set", "lo", "up")
    name = "eva2-speaker-lifecycle-" + uuid.uuid4().hex[:8]
    parent, child = name + ".service", name + "-speaker.service"
    units = [Path("/run/systemd/system") / unit for unit in (parent, child)]
    assert all(not p.exists() for p in units)
    log = output / "service.log"
    report = {"sourceCommit": manifest["sourceCommit"], "manifestSha256": digest(bundle / "bundle.json"),
        "bundle": str(bundle), "isolatedNetwork": True, "physicalSpeaker": False,
        "providerCalls": 0, "parentUnit": parent, "childUnit": child, "steps": []}
    command("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(output / "key.pem"), "-out", str(output / "cert.pem"),
        "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1")
    command("ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "flite=text=Please tell me a story about a friendly robot who lives in a house:voice=slt",
        "-ar", "16000", "-ac", "1", "-f", "s16le", str(output / "speech.pcm"))
    audio = (output / "speech.pcm").read_bytes()
    report["syntheticAudioSha256"] = hashlib.sha256(audio).hexdigest()
    ctx = ssl.create_default_context(cafile=str(output / "cert.pem"))
    route_reads, proxy_requests = [], []
    peer_tasks = set()
    routes = []
    unit_created = False
    try:
        async with upstream(stt_frames=len(audio)//640*2) as (origin, observed), ClientSession(trust_env=False) as http:
            async def proxy(request):
                port = request.transport.get_extra_info("sockname")[1]
                proxy_requests.append({"slot": port, "method": request.method, "path": request.path})
                if request.path.endswith("/capabilities"):
                    route_reads.append(port)
                async with http.request(request.method, origin + request.path_qs,
                    headers={"Authorization": request.headers.get("Authorization", "")},
                    data=await request.read(), allow_redirects=False) as response:
                    return web.Response(status=response.status, body=await response.read(),
                        content_type="application/json")
            for port in (44710, 44711):
                app = web.Application(); app.router.add_route("*", "/{path:.*}", proxy)
                runner = web.AppRunner(app); await runner.setup(); routes.append(runner)
                await web.TCPSite(runner, "127.0.0.1", port).start()
            cfg = asdict(config(output, origin))
            cfg.update(port=44715, eva_url="http://127.0.0.1:44710",
                certificate=str(output / "cert.pem"), private_key=str(output / "key.pem"),
                model=str(bundle / "silero_vad.onnx"))
            (output / "config.json").write_text(json.dumps(cfg)); (output / "config.json").chmod(0o600)
            (output / "key.pem").chmod(0o600)
            template = (bundle / "eva2/deploy/home-assistant/eva2-native-speaker.service").read_text()
            template = template.replace("eva.service", parent).replace("RuntimeDirectory=eva2-native-speaker", "RuntimeDirectory=" + name)
            template = template.replace("/etc/eva2/native-speaker/config.json", str(output / "config.json"))
            template = template.replace("/etc/eva2/native-speaker/tls.crt", str(output / "cert.pem"))
            template = template.replace("/etc/eva2/native-speaker/tls.key", str(output / "key.pem"))
            template = template.replace("/opt/eva2/native-speaker/current", str(bundle))
            template = template.replace("service_launcher.py\n", "service_launcher.py --source-service " + parent + "\n")
            template = template.replace("[Service]\n", "[Service]\nNetworkNamespacePath=/proc/" + str(os.getpid()) + "/ns/net\nStandardOutput=append:" + str(log) + "\nStandardError=append:" + str(log) + "\n")
            units[1].write_text(template)
            unit_created = True
            def select(port):
                text = "[Unit]\nDescription=Isolated Eva speaker lifecycle fixture\nWants=" + child + "\n[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\nEnvironment=EVA2_CANARY_URL=http://127.0.0.1:" + str(port) + "\n"
                units[0].write_text(text)
                (output / ("parent-" + str(port) + ".service")).write_text(text)
            (output / "speaker.service").write_text(template)
            async def listening(count, port):
                await until(lambda: log.exists() and log.read_text().count('"state": "listening"') >= count)
                assert route_reads[-1] == port
                state = await systemctl("show", child, "-p", "MainPID", "-p", "ActiveState",
                    "-p", "User", "-p", "DynamicUser", "-p", "ProtectSystem", "-p", "ProtectHome",
                    "-p", "NoNewPrivileges", "-p", "RuntimeDirectoryMode", "-p", "KillMode")
                result = dict(line.split("=", 1) for line in state.splitlines())
                assert result["ActiveState"] == "active" and int(result["MainPID"]) > 0
                return result
            async def playing():
                socket = await client("wss://127.0.0.1:44715/eva-speaker/v1", TOKEN, ctx)
                peer = NativePeer(); peer.stop_gate = asyncio.Event(); peer.capture_end_gate = asyncio.Event()
                task = asyncio.create_task(peer.handle(socket)); peer_tasks.add(task)
                await peer.wake(); await asyncio.wait_for(peer.captures.get(), 3)
                async def feed():
                    for start in range(0, len(audio) - 639, 640):
                        peer.sequence += 1
                        await peer.send(Kind.MICROPHONE, payload=audio[start:start + 640])
                        await asyncio.sleep(.02)
                feeder = asyncio.create_task(feed())
                try:
                    await asyncio.wait_for(peer.answers.get(), 8)
                except TimeoutError:
                    report["playbackWaitFailure"] = {
                        "upstreamCounts": {key: len(value) for key, value in observed.items()},
                        "packets": [p.kind.name for p in peer.packets][-20:],
                        "deviceDone": task.done()}
                    raise
                finally:
                    feeder.cancel(); await asyncio.gather(feeder, return_exceptions=True)
                return peer, task, socket
            async def transition(action, old, *, slot=None, expected_listeners=None):
                peer, peer_task, socket = await playing()
                before = time.monotonic()
                change = asyncio.create_task(systemctl(action, parent))
                await until(lambda: any(p.kind == Kind.CAPTURE_END for p in peer.packets)
                                      and any(p.kind == Kind.STOP for p in peer.packets), timeout=2)
                await asyncio.sleep(.05)
                assert not change.done() and not peer_task.done(), "must wait for owned stop receipts"
                peer.stop_gate.set(); peer.capture_end_gate.set()
                await change; await asyncio.wait_for(peer_task, 3); await socket.close()
                assert not Path("/proc", old["MainPID"]).exists(), "old process must be gone"
                row = {"action": action, "fromPid": int(old["MainPID"]),
                    "waitedForBothStopReceipts": True, "oldProcessGone": True,
                    "elapsedMs": round((time.monotonic()-before)*1000, 2)}
                if slot is not None:
                    new = await listening(expected_listeners, slot)
                    assert new["MainPID"] != old["MainPID"]
                    row.update(toSlot=slot, effectiveProperties=new)
                else:
                    assert not Path("/run", name).exists()
                    assert (await systemctl("show", child, "-p", "ActiveState", "--value")).strip() == "inactive"
                    row["runtimeRemoved"] = True
                    new = None
                report["steps"].append(row)
                return new
            select(44710); await systemctl("daemon-reload"); await systemctl("start", parent)
            current = await listening(1, 44710)
            report["steps"].append({"action": "initial-start", "slot": 44710, "effectiveProperties": current})
            await transition("stop", current)
            await systemctl("start", parent); current = await listening(2, 44710)
            current = await transition("restart", current, slot=44710, expected_listeners=3)
            select(44711); await systemctl("daemon-reload")
            current = await transition("restart", current, slot=44711, expected_listeners=4)
            select(44710); await systemctl("daemon-reload")
            current = await transition("restart", current, slot=44710, expected_listeners=5)
            await transition("stop", current)
            assert observed["core"] and observed["playback"]
            ids = {r["request_id"] for r in observed["core"]}
            assert ids == {rid for rid, _ in observed["playback"]}
            assert all(row["result"]["status"] == "interrupted" for _, row in observed["playback"])
            report.update(status="passed", capabilityRoutes=route_reads,
                coreTurns=len(observed["core"]), playbackReports=len(observed["playback"]),
                playbackStatuses=[row["result"]["status"] for _, row in observed["playback"]],
                proxyRequests=proxy_requests)
    except BaseException as error:
        report.update(status="failed", errorType=type(error).__name__, error=str(error))
        raise
    finally:
        if unit_created:
            await systemctl("stop", parent, child)
            for path in units:
                path.unlink(missing_ok=True)
            await systemctl("daemon-reload")
        for runner in routes:
            await runner.cleanup()
        for task in peer_tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*peer_tasks, return_exceptions=True)
        report["temporaryUnitsRemoved"] = all(not p.exists() for p in units)
        report["runtimeRemoved"] = not Path("/run", name).exists()
        report["harnessSha256"] = digest(Path(__file__))
        report["testSupportSha256"] = {p.name: digest(p) for p in (HERE.parents[1] / "tests/operations").glob("test_native_duplex_speaker_*.py")}
        (output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "output": str(output), "steps": len(report["steps"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.bundle.resolve(), args.output))
