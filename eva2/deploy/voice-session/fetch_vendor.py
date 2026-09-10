"""Fetch exact released media assets; reject any checksum mismatch."""
from pathlib import Path
import base64
import hashlib
import io
import tarfile
from urllib.request import urlopen

ASSETS = (
    ("https://github.com/livekit/livekit/releases/download/v1.13.6/livekit_1.13.6_linux_amd64.tar.gz",
     "sha256", "2b61abef2b9ba14b4b8ca38b37de9a37ffc682b9931d5fc03ceca2f0b77d3e33", "livekit-server", "livekit-server"),
    ("https://registry.npmjs.org/livekit-client/-/livekit-client-2.22.3.tgz",
     "sha512", base64.b64decode("jw9zBKXY5Gtr5MZ7vEON3QhMNccuDvYHck1PFSyG1aaateQPqgKZFBMgZkFZaXHIf9RV4MDW5xpTK2b/+qbwOg==").hex(),
     "package/dist/livekit-client.umd.js", "livekit-client.js"),
)

def main():
    output = Path(__file__).parent / "vendor"
    output.mkdir(exist_ok=True)
    for url, algorithm, expected, member, name in ASSETS:
        with urlopen(url, timeout=60) as response:
            blob = response.read(64 * 1024 * 1024 + 1)
        if len(blob) > 64 * 1024 * 1024 or hashlib.new(algorithm, blob).hexdigest() != expected:
            raise ValueError(f"asset checksum mismatch: {name}")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"asset missing: {name}")
            (output / name).write_bytes(source.read())
        (output / name).chmod(0o755 if name == "livekit-server" else 0o644)
        print(f"Verified {name}")

if __name__ == "__main__":
    main()
