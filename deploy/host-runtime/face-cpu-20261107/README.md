# Existing face CPU runtime library maintenance

This closure prepares the existing single-shot CPU extractor with InsightFace2.1,
ONNX Runtime CPU1.31.1, NumPy2.5.4, OpenCV5.0.0.93 and Pillow13.0.0.
The complete lock contains23 runtime distributions plus pip26.3. Ubuntu's maintained
CPython3.12.3 interpreter is retained. No GUI extras, GPU runtime, model download,
enrollment, new service or authentication role is enabled.

Set `ORT_DISABLE_TELEMETRY=1` in the existing bridge service **before Python starts**.
ONNX Runtime1.31 enables native telemetry by default; its documented process-wide
opt-out prevents uploader/events/device identifiers at initialization. The existing
extractor imports ORT before InsightFace, so InsightFace's own later environment
setting is insufficient for this call order. The prepared environment file is part
of this closure; installation must verify the actual parent environment and its
inherited one-shot child environment. See the [upstream privacy contract](https://github.com/microsoft/onnxruntime/blob/v1.31.1/docs/Privacy.md).

The two existing ONNX files keep their exact hashes. Existing profiles stay
read-only and separate from this context. Code licensing and model licensing
remain separate: see the [upstream code documentation](https://github.com/deepinsight/insightface/blob/master/python-package/README.md).
This recipe includes no model weights or household data.

Run `python3 prepare_context.py --wheelhouse /absolute/wheels --source-script
/absolute/Eva2/src/eva/operations/local_face_cpu.py --output /new/offline-context`.
It verifies all24 official artifact hashes and the unchanged extractor before
copying the bounded context. Install the lock in a separate CPython3.12 venv with
`pip install --no-index --find-links /new/offline-context --require-hashes
-r /new/offline-context/requirements.lock`, then run `pip check`.

The deployment must select this interpreter in the existing bridge and bind a
new truthful `:cpu` backend revision from the manifest in both bridge and caller. That requires a
separate guarded current-generation rollout; preparing the context does not
install or accept the function. Existing runtime and model/profile storage remain
retained for a source-bound rollback.

Candidate verification uses one public scikit-image fixture and a synthetic blank
image, network isolation and UID1000. The existing matcher and actual HTTP bridge
were exercised with a synthetic gallery: no household images/profiles were used.
One public-fixture embedding comparison is a technical observation, not a new
family recognition-accuracy result or performance benchmark.
