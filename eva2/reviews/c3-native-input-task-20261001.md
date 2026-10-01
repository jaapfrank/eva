# C3 native input task — independent bounded review

**GO for the source/build bundle and one targeted Woonkamer OTA. Physical recovery
and complete conversation behavior remain unproven.**

Reviewer: `/root/open_questions_triage`, 1 October 2026. No tests, firmware builds,
uploads, provider requests or source changes were performed by this reviewer.

## Reviewed change

The microphone task selects its entry function before task creation. Native PCM
uses `eva_input_task_()` directly; the legacy path keeps `mic_tx_task_()`. The
native receipt-then-PCM loop is moved unchanged out of the legacy function, which
contains a stack-resident 1,280-byte buffer unused by native PCM.

Task name, 4,096-byte allocation, priority, handle, core selection and failure
cleanup remain unchanged. No second WebSocket writer is introduced. Mute, stop,
capture tokens, epochs, sequence ordering and the earlier ARMED pre-roll behavior
are preserved. The updated existing host harness checks online/offline/online
pumping and exact chronological PCM output.

The author reports exact reconstruction against pinned component revision
`a538d1d1bacc8609061719f0b05c1622147ccb4b`, followed by applying the existing
stop/wire/auth patches and the revised input patch. The reviewer read the retained
reconstructed candidate and independently matched its bytes to the actual
generated build sources:

| Source | SHA256 |
| --- | --- |
| `va_pipecat.cpp` | `7b8298aad38639efbd6f52ab9bf689978d3cac4b52d39cbf4d8cf23ceb8ef530` |
| `va_pipecat.h` | `d0ed8217203cb00fbe94b016828aa13bdd977daaaf94b42c8f5b707d04ebc070` |
| `eva_input_driver.cpp` | `e997399743da2b68d002be9a9703388afa4d3dbcd3e6f7afb9321b13c314beec` |

## Build evidence

Reviewed the retained `input-task-build.json` and the actual
`native-input-task.ota.bin`. The coordinator reports successful compilation with
the pinned ESPHome setup and **2 host cases passing in 3.46 seconds**. No checks
were rerun for this review.

The recorded new ELF entry instructions show:

| Function | Statically allocated entry frame |
| --- | ---: |
| Native `eva_input_task_()` | 32 bytes |
| Legacy `mic_tx_task_()` | 1,312 bytes (`0x520`) |
| `eva_pump_input_()` | 800 bytes (`0x320`), unchanged |

The earlier native entry used the legacy function's 1,312-byte frame. The direct
native entry therefore removes **1,280 bytes of unnecessary entry-frame overhead**
while retaining the same task allocation. This is an ELF frame comparison, not a
measurement of total stack high-water usage or proof of physical recovery.

| Build artifact | SHA256 |
| --- | --- |
| Recorded ELF | `d55410a2c750e6e0f7b50b8af36fb3ef4cb65df191aa99129e197876e4b3f612` |
| Actual OTA binary, 2,936,064 bytes | `91b723d3e4c3469b013aadf3daba842105dd67ba4520704172861b54274130ac` |
| Unchanged firmware wrapper | `070f725dd32c916adfcf19f806a0f8288b03747e98c40ceca7ce4bfbf3eab75b` |

The reviewer independently calculated the OTA and wrapper hashes. The generated
generic source hashes above match the reviewed candidate. The coordinator reports
that the actual OTA retains the provisioned URL and CA; the intended encrypted
Woonkamer identity and preserved configuration remain the existing target binding.

## Remaining outcome

Fresh crash metadata identified the stack-overflow hook, and this bundle removes
the measured unnecessary native frame. It does not yet establish that all crash
causes or conversation gaps are solved. After the targeted upload, physical
stability, direct speech while thinking, interruption while speaking and the
original research return remain to be observed. No full-duplex acceptance is
inferred from compilation, the host harness or transport connectivity.

The existing pre-admission queue retains only the latest approximately 0.512
seconds of PCM. That limitation remains. Development credit cost is unknown.
