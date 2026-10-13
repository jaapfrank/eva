# Disposable evaluation image only.  It is not a production or managed-B image.
# Pin the complete Node base so a later tag move cannot silently change the
# browser, OS packages, or native agent-browser binary being evaluated.
FROM node@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df

ARG AGENT_BROWSER_VERSION=0.35.1
ARG CHROME_FOR_TESTING_VERSION=152.0.7977.64

# The upstream `--with-deps` helper assumes `sudo`, which a deliberately
# minimal root build image does not carry.  Install its documented Debian
# runtime set directly, then let the upstream CLI fetch its pinned
# Chrome-for-Testing binary.  The build is deliberately the only networked
# phase; the later runtime probe uses a gatewayless Docker network.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      libxcb-shm0 libx11-xcb1 libx11-6 libxcb1 libxext6 libxrandr2 \
      libxcomposite1 libxcursor1 libxdamage1 libxfixes3 libxi6 libgtk-3-0 \
      libpangocairo-1.0-0 libpango-1.0-0 libatk1.0-0 libcairo-gobject2 \
      libcairo2 libgdk-pixbuf-2.0-0 libxrender1 libasound2 libfreetype6 \
      libfontconfig1 libdbus-1-3 libnss3 libnss3-tools libnspr4 \
      libatk-bridge2.0-0 libdrm2 libxkbcommon0 libatspi2.0-0 libcups2 \
      libxshmfence1 libgbm1 fonts-noto-color-emoji fonts-noto-cjk \
      fonts-freefont-ttf \
 && rm -rf /var/lib/apt/lists/* \
 && npm install --global --allow-scripts=agent-browser "agent-browser@${AGENT_BROWSER_VERSION}" \
 && agent-browser install \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin eva-browser \
 && mkdir --parents /opt/agent-browser \
 && mv /root/.agent-browser/browsers /opt/agent-browser/browsers \
 && chown --recursive root:root /opt/agent-browser \
 && npm cache clean --force

USER 10001:10001
ENV HOME=/home/eva-browser
ENV AGENT_BROWSER_SESSION=eva-isolated
# Runtime session, profile, socket, cache and crash reports are mounted as
# disposable tmpfs paths.  The downloaded browser itself is immutable here,
# outside the writable user home.
ENV AGENT_BROWSER_EXECUTABLE_PATH=/opt/agent-browser/browsers/chrome-${CHROME_FOR_TESTING_VERSION}/chrome

ENTRYPOINT ["agent-browser"]
