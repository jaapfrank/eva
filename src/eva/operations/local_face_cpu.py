"""One-shot CPU extraction with existing pinned ArcFace assets; no downloads.

Run this file with the installed InsightFace Python, in isolated mode. One bounded
image arrives on stdin; the existing faces_b64 JSON shape leaves stdout. This is
not a service, identity matcher or profile store. It imports no Eva1 module.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys


MODEL_HASHES = {
    "det_10g.onnx": "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
    "w600k_r50.onnx": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
}


def pinned_models(root: str | Path) -> dict[str, Path]:
    directory = Path(root).resolve(strict=True) / "models" / "buffalo_l"
    result = {}
    for name, expected in MODEL_HASHES.items():
        path = directory / name
        if not path.is_file() or path.resolve().parent != directory.resolve():
            raise ValueError("pinned local face model file is missing")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1_048_576), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("local face model differs from the pinned existing model")
        result[name] = path
    return result


def extract(data: bytes, models: dict[str, Path]) -> dict:
    # No model-zoo downloader or FaceAnalysis.ensure_available call is involved.
    import cv2
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageOps
    from insightface.app.common import Face
    from insightface.model_zoo.arcface_onnx import ArcFaceONNX
    from insightface.model_zoo.scrfd import SCRFD

    cv2.setNumThreads(1)
    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("installed local face runtime lacks CPU execution")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    sessions = {name: ort.InferenceSession(str(path), sess_options=options,
                  providers=["CPUExecutionProvider"]) for name, path in models.items()}
    if any(session.get_providers() != ["CPUExecutionProvider"] for session in sessions.values()):
        raise RuntimeError("local face session did not select CPU-only execution")
    detector = SCRFD(model_file=str(models["det_10g.onnx"]), session=sessions["det_10g.onnx"])
    recognizer = ArcFaceONNX(model_file=str(models["w600k_r50.onnx"]), session=sessions["w600k_r50.onnx"])
    # ctx_id >= 0 avoids resetting already CPU-only sessions; no GPU is created.
    detector.prepare(0, input_size=(640, 640), det_thresh=0.5)
    with Image.open(io.BytesIO(data)) as opened:
        if opened.width * opened.height > 20_000_000:
            raise ValueError("face input exceeds the decoded pixel bound")
        rgb = ImageOps.exif_transpose(opened).convert("RGB")
        image = np.array(rgb)[:, :, ::-1].copy()
    boxes, keypoints = detector.detect(image, max_num=9, metric="default")
    if len(boxes) > 8:
        raise ValueError("face count exceeds the observation bound")
    faces = []
    for index, box in enumerate(boxes):
        if keypoints is None:
            raise RuntimeError("local detector supplied no alignment landmarks")
        face = Face(bbox=box[:4], kps=keypoints[index], det_score=box[4])
        recognizer.get(image, face)
        bbox = [max(0, min(image.shape[1], int(box[0]))),
                max(0, min(image.shape[0], int(box[1]))),
                max(0, min(image.shape[1], int(box[2]))),
                max(0, min(image.shape[0], int(box[3])))]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        faces.append({"bbox": bbox, "oppervlak": (bbox[2]-bbox[0])*(bbox[3]-bbox[1]),
                      "det_score": round(float(face.det_score), 3),
                      "embedding": [round(float(value), 5) for value in face.normed_embedding]})
    return {"aantal": len(faces), "gezichten": faces}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        models = pinned_models(args.model_root)
        if args.check:
            import onnxruntime as ort
            if "CPUExecutionProvider" not in ort.get_available_providers():
                raise RuntimeError("CPU execution provider unavailable")
            result = {"ok": True, "execution_provider": "CPUExecutionProvider",
                      "model_hashes": MODEL_HASHES}
        else:
            data = sys.stdin.buffer.read(2_097_153)
            if not 1 <= len(data) <= 2_097_152:
                raise ValueError("face input exceeds the transport bound")
            with redirect_stdout(sys.stderr):
                result = extract(data, models)
        sys.stdout.write(json.dumps(result, allow_nan=False, separators=(",", ":")))
    except Exception:
        # No image, embedding, local path or unbounded backend trace escapes.
        sys.stderr.write("local CPU face extraction unavailable\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
