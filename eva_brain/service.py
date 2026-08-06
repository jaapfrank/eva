# Eva's lokale brein-service — háár gezichten (ArcFace) + háár eigen beeld-representatie.
#
# Draait lokaal (127.0.0.1), de Node-app praat hier tegenaan. Géén cloud. De
# threadlimieten moeten vóór torch/numpy/onnxruntime staan; torch zelf blijft vóór
# onnxruntime/ArcFace zodat de CUDA-libs in de juiste volgorde geladen worden.
import os

try:
    BRAIN_CPU_THREADS = int(os.environ.get('EVA_BRAIN_CPU_THREADS', '4'))
except ValueError:
    BRAIN_CPU_THREADS = 4
BRAIN_CPU_THREADS = max(1, min(8, BRAIN_CPU_THREADS))
for _thread_env in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS'):
    os.environ.setdefault(_thread_env, str(BRAIN_CPU_THREADS))
os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')

import torch  # noqa: E402  — moet vóór onnxruntime/insightface blijven
torch.set_num_threads(BRAIN_CPU_THREADS)
try:
    torch.set_num_interop_threads(1)
except RuntimeError:
    pass

import io, base64, threading
import numpy as np
from PIL import Image, ImageOps
import torch.nn as nn
import torchvision.transforms as T
import cv2
from fastapi import FastAPI
from pydantic import BaseModel
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass
from insightface.app import FaceAnalysis
from insightface.model_zoo import model_zoo
from insightface.utils import ensure_available

DEV = 'cuda' if torch.cuda.is_available() else 'cpu'
ENC_PATH = os.environ.get('EVA_ENCODER', '/home/administrator/eva-archief/encoder_pilot.pt')
cv2.setNumThreads(BRAIN_CPU_THREADS)
FACE_PROVIDERS = ['CUDAExecutionProvider', 'CPUExecutionProvider']
FACE_PROVIDER_OPTIONS = [
    {
        'cudnn_conv_algo_search': 'HEURISTIC',
        'cudnn_conv_use_max_workspace': '0',
    },
    {},
]


def _block(ci, co):
    return nn.Sequential(nn.Conv2d(ci, co, 3, 2, 1), nn.BatchNorm2d(co), nn.ReLU())


class Encoder(nn.Module):
    """Zelfde architectuur als eva_brain/train_encoder.py — háár eigen representatie."""
    def __init__(self, latent=128):
        super().__init__()
        self.net = nn.Sequential(_block(3, 32), _block(32, 64), _block(64, 128), _block(128, 256),
                                 nn.AdaptiveAvgPool2d(1), nn.Flatten())
        self.proj = nn.Sequential(nn.Linear(256, 256), nn.ReLU(), nn.Linear(256, latent))

    def forward(self, x):
        return self.proj(self.net(x))


_base = T.Compose([T.Resize(128), T.CenterCrop(128), T.ToTensor()])

app = FastAPI(title='Eva brein-service')
_state = {'faces': None, 'encoder': None, 'warm': False}
_model_lock = threading.Lock()


class FocusedFaceAnalysis(FaceAnalysis):
    """InsightFace zonder drie ongebruikte CUDA-sessies."""

    def __init__(self, name='buffalo_l', root='~/.insightface'):
        self.models = {}
        model_dir = ensure_available('models', name, root=root)
        for filename in ('det_10g.onnx', 'w600k_r50.onnx'):
            path = os.path.join(model_dir, filename)
            model = model_zoo.get_model(
                path,
                providers=FACE_PROVIDERS,
                provider_options=FACE_PROVIDER_OPTIONS,
            )
            self.models[model.taskname] = model
        self.det_model = self.models['detection']


def _warm_models(faces, encoder):
    """Betaal CUDA's eenmalige initialisatie vóór de service zich gereed meldt."""
    faces.det_model.forward(np.zeros((640, 640, 3), dtype=np.uint8), 1.1)
    faces.models['recognition'].get_feat(np.zeros((112, 112, 3), dtype=np.uint8))
    with torch.no_grad():
        encoder(torch.zeros((1, 3, 128, 128), device=DEV))
    if DEV == 'cuda':
        torch.cuda.synchronize()


@app.on_event('startup')
def _load():
    # Eva gebruikt alleen bounding boxes en ArcFace-embeddings. De landmark-,
    # leeftijd- en geslachtsmodellen kostten per camerabeeld wel rekentijd, maar hun
    # uitkomsten werden nergens gebruikt.
    fa = FocusedFaceAnalysis()
    fa.prepare(ctx_id=0 if DEV == 'cuda' else -1, det_size=(640, 640))
    _state['faces'] = fa
    enc = Encoder().to(DEV).eval()
    if os.path.exists(ENC_PATH):
        enc.load_state_dict(torch.load(ENC_PATH, map_location=DEV))
        _state['encoder'] = enc
    else:
        _state['encoder'] = enc  # ongetraind, maar bruikbaar zodra er een checkpoint is
    _warm_models(fa, enc)
    _state['warm'] = True


def _open(path):
    return ImageOps.exif_transpose(Image.open(path)).convert('RGB')


def _open_b64(b64):
    raw = base64.b64decode(b64)
    return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')


def _faces_from_img(im):
    arr = np.array(im)[:, :, ::-1]  # RGB -> BGR voor insightface
    out = []
    # Eén GPU, één modelinstantie. Camerabursts mogen niet vijf inferenties tegelijk
    # laten vechten om dezelfde CUDA-context.
    with _model_lock:
        faces = _state['faces'].get(arr)
    for f in faces:
        b = f.bbox.tolist()
        out.append({'bbox': [int(x) for x in b],
                    'oppervlak': int(max(0, b[2] - b[0]) * max(0, b[3] - b[1])),
                    'det_score': round(float(f.det_score), 3),
                    'embedding': [round(float(v), 5) for v in f.normed_embedding.tolist()]})
    return {'aantal': len(out), 'gezichten': out}


def _embed_img(im):
    with _model_lock, torch.no_grad():
        z = _state['encoder'](_base(im).unsqueeze(0).to(DEV))[0]
    return {'dim': int(z.shape[0]), 'vector': [round(float(v), 5) for v in z.cpu().tolist()]}


class PathReq(BaseModel):
    path: str


class BytesReq(BaseModel):
    image_b64: str


@app.get('/health')
async def health():
    # Dit antwoord raakt geen model en hoort nooit achter ArcFace/GPU-werk in de
    # sync-threadpool te wachten. Zo blijft liveness ook tijdens camerabursts echt.
    return {
        'ok': True,
        'device': DEV,
        'faces_klaar': _state['faces'] is not None,
        'encoder_getraind': os.path.exists(ENC_PATH),
        'warm': _state['warm'],
        'cpu_threads': BRAIN_CPU_THREADS,
        'face_modules': sorted((_state['faces'].models if _state['faces'] else {}).keys()),
    }


@app.post('/faces')
def faces(req: PathReq):
    if not os.path.exists(req.path):
        return {'fout': f'bestand niet gevonden: {req.path}'}
    return _faces_from_img(_open(req.path))


@app.post('/faces_b64')
def faces_b64(req: BytesReq):
    return _faces_from_img(_open_b64(req.image_b64))


@app.post('/embed')
def embed(req: PathReq):
    if not os.path.exists(req.path):
        return {'fout': f'bestand niet gevonden: {req.path}'}
    return _embed_img(_open(req.path))


@app.post('/embed_b64')
def embed_b64(req: BytesReq):
    return _embed_img(_open_b64(req.image_b64))
