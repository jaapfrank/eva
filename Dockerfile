# Eva's huis: een zelfstandige container waarin ze volledig woont en leeft.
# Alles wat ze nodig heeft zit hierin; haar leven (data/) komt van een volume,
# zodat ze blijft wie ze is — ook als het huis naar een andere machine verhuist.
FROM node:20-bookworm-slim

# Bouwgereedschap voor de native modules (better-sqlite3, tfjs-node) + ffmpeg, zodat ze
# ook video's kan begrijpen, libgomp1 voor TensorFlow's runtime, en tini als nette init.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 make g++ ca-certificates ffmpeg tini libgomp1 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /home/eva/app

# eerst alleen de dependency-lijst, voor snelle herbouw en reproduceerbare installs
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# Haar ogen ingebakken: de gezichtsmodellen op een vaste plek buiten het volume,
# zodat de container ze offline heeft (geen CDN nodig bij eerste gebruik).
ADD https://vladmandic.github.io/human-models/models/blazeface.json /home/eva/models/blazeface.json
ADD https://vladmandic.github.io/human-models/models/blazeface.bin  /home/eva/models/blazeface.bin
ADD https://vladmandic.github.io/human-models/models/faceres.json   /home/eva/models/faceres.json
ADD https://vladmandic.github.io/human-models/models/faceres.bin    /home/eva/models/faceres.bin

# dan haar code
COPY src ./src

# haar leven en haar eigen brein-model cachen in het volume, zodat het blijft
ENV NODE_ENV=production \
    EVA_MODEL_CACHE=/home/eva/app/data/models \
    TRANSFORMERS_CACHE=/home/eva/app/data/models \
    EVA_FACE_MODELS=file:///home/eva/models/

# web-dashboard en de ChatGPT-login-callback
EXPOSE 3210 1455

# data is haar geheugen/leven — als volume gemount (zie docker-compose)
VOLUME ["/home/eva/app/data"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "src/index.js"]
