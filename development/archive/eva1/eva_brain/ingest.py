# Eva's wereld als genormaliseerde frames — de eerste stap van een geworteld brein.
#
# Verzamelt foto's (incl. HEIC van iPhone) en video-frames uit het archief en maakt
# er gelijkvormige beeldtegels van (korte zijde -> center-crop). Dit is het ruwe
# materiaal waar ze haar EIGEN representatie op leert (JEPA), niet uit taal.
# Strikt lokaal; niets gaat naar buiten.
import os, sys, glob, subprocess, hashlib
from PIL import Image, ImageOps
try:
    import pillow_heif; pillow_heif.register_heif_opener()  # iPhone .heic ondersteunen
except Exception:
    pass
import imageio_ffmpeg

IMG_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.bmp', '.webp', '.tiff'}
VID_EXT = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.3gp'}
SIZE = 160  # uitvoer is SIZE x SIZE


def norm_image(im):
    im = ImageOps.exif_transpose(im).convert('RGB')  # respecteer rotatie-EXIF
    w, h = im.size
    s = SIZE / min(w, h)
    im = im.resize((max(SIZE, round(w * s)), max(SIZE, round(h * s))))
    w, h = im.size
    l, t = (w - SIZE) // 2, (h - SIZE) // 2
    return im.crop((l, t, l + SIZE, t + SIZE))


def save(im, outdir, key):
    p = os.path.join(outdir, key + '.jpg')
    im.save(p, 'JPEG', quality=90)
    return p


def video_frames(path, outdir, every=2.0, maxf=20):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = os.path.join(outdir, '_vtmp')
    os.makedirs(tmp, exist_ok=True)
    subprocess.run([ff, '-y', '-i', path, '-vf', f'fps=1/{every}', '-frames:v', str(maxf),
                    os.path.join(tmp, 'f%03d.jpg')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = []
    for f in sorted(glob.glob(os.path.join(tmp, '*.jpg'))):
        try:
            im = norm_image(Image.open(f))
            key = hashlib.md5((path + os.path.basename(f)).encode()).hexdigest()[:16]
            out.append(save(im, outdir, key))
        except Exception:
            pass
        try:
            os.remove(f)
        except Exception:
            pass
    return out


def main(src, out):
    os.makedirs(out, exist_ok=True)
    n_img = n_vid = n_frames = n_fail = 0
    for root, _, files in os.walk(src):
        if os.path.basename(root) == '_vtmp':
            continue
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            path = os.path.join(root, f)
            if ext in IMG_EXT:
                try:
                    im = norm_image(Image.open(path))
                    save(im, out, hashlib.md5(path.encode()).hexdigest()[:16])
                    n_img += 1
                    n_frames += 1
                except Exception:
                    n_fail += 1
            elif ext in VID_EXT:
                fr = video_frames(path, out)
                n_vid += 1
                n_frames += len(fr)
    tmp = os.path.join(out, '_vtmp')
    if os.path.isdir(tmp):
        try:
            os.rmdir(tmp)
        except Exception:
            pass
    print(f"foto's:{n_img}  video's:{n_vid}  mislukt:{n_fail}  ->  totaal frames:{n_frames}")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
