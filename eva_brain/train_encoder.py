# Eva's eigen beeld-representatie — zelf-superviserend, GEEN taal in de pijplijn.
#
# Een kleine conv-encoder leert uit haar eigen frames een latente ruimte, met VICReg
# (invariantie tussen twee augmentaties + variantie-hinge + decorrelatie tegen
# instorten). Dit is de kern van het gewortelde brein: een voorstelling die uit háár
# beelden komt, niet uit MiniLM. Pilot-schaal — bewijst de keten; echte structuur
# komt met het volledige archief.
import os, glob, sys
import torch, torch.nn as nn, torch.nn.functional as F
import torchvision.transforms as T
from PIL import Image
try:
    import pillow_heif; pillow_heif.register_heif_opener()
except Exception:
    pass

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get(
    'EVA_BRAIN_DATA',
    os.path.join(PROJECT_ROOT, 'data', 'runtime', 'eva-brain-data'),
)
FRAMES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_ROOT, 'frames')
LATENT = 128
dev = 'cuda' if torch.cuda.is_available() else 'cpu'

paths = sorted(glob.glob(os.path.join(FRAMES, '*.jpg')))
imgs = [Image.open(p).convert('RGB') for p in paths]
print(f'frames: {len(imgs)} | device: {dev}')

aug = T.Compose([
    T.RandomResizedCrop(128, scale=(0.5, 1.0)),
    T.RandomHorizontalFlip(),
    T.ColorJitter(0.4, 0.4, 0.4, 0.1),
    T.ToTensor(),
])
base = T.Compose([T.Resize(128), T.CenterCrop(128), T.ToTensor()])


class DS(torch.utils.data.Dataset):
    def __init__(self, imgs): self.imgs = imgs
    def __len__(self): return len(self.imgs)
    def __getitem__(self, i): im = self.imgs[i]; return aug(im), aug(im)


def block(ci, co):
    return nn.Sequential(nn.Conv2d(ci, co, 3, 2, 1), nn.BatchNorm2d(co), nn.ReLU())


class Encoder(nn.Module):
    def __init__(self, latent=LATENT):
        super().__init__()
        self.net = nn.Sequential(block(3, 32), block(32, 64), block(64, 128), block(128, 256),
                                 nn.AdaptiveAvgPool2d(1), nn.Flatten())
        self.proj = nn.Sequential(nn.Linear(256, 256), nn.ReLU(), nn.Linear(256, latent))

    def forward(self, x): return self.proj(self.net(x))


def vicreg(z1, z2):
    inv = F.mse_loss(z1, z2)
    def varloss(z): return torch.mean(F.relu(1 - torch.sqrt(z.var(0) + 1e-4)))
    def covloss(z):
        z = z - z.mean(0)
        c = (z.T @ z) / (z.shape[0] - 1)
        return (c.fill_diagonal_(0) ** 2).sum() / z.shape[1]
    return 25 * inv + 25 * (varloss(z1) + varloss(z2)) + (covloss(z1) + covloss(z2))


enc = Encoder().to(dev)
opt = torch.optim.Adam(enc.parameters(), 1e-3)
bs = min(64, len(imgs))
dl = torch.utils.data.DataLoader(DS(imgs), batch_size=bs, shuffle=True, drop_last=True)

for ep in range(200):
    tot = 0.0
    for a, b in dl:
        a, b = a.to(dev), b.to(dev)
        z1, z2 = enc(a), enc(b)
        loss = vicreg(z1, z2)
        opt.zero_grad(); loss.backward(); opt.step()
        tot += loss.item()
    if ep % 40 == 0 or ep == 199:
        print(f'  ep{ep:3d}  loss {tot/max(1,len(dl)):.3f}')

# eerlijke maat: spreiding (anti-instorten) + herkent ze gelijkende beelden?
enc.eval()
with torch.no_grad():
    Z = torch.cat([enc(base(im).unsqueeze(0).to(dev)) for im in imgs])
spread = Z.std(0).mean().item()
Zn = F.normalize(Z, dim=1)
S = Zn @ Zn.T; S.fill_diagonal_(-1)
nn_sim = S.max(1).values.mean().item()
print(f'representatie-spreiding (std/dim, ~0=ingestort, gezond>0.3): {spread:.3f}')
print(f'gem. cosine naar dichtstbijzijnde beeld (structuur, >toeval~0): {nn_sim:.3f}')
os.makedirs(DATA_ROOT, exist_ok=True)
encoder_path = os.path.join(DATA_ROOT, 'encoder_pilot.pt')
torch.save(enc.state_dict(), encoder_path)
print(f'encoder bewaard: {encoder_path}')
