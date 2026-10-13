# The browser server and Chromium remain Microsoft Playwright MCP.  npm is a
# build-time package manager and is never called by Eva's MCP sidecar; removing
# it prevents an unused transitive package tree from becoming runtime surface.
FROM mcr.microsoft.com/playwright/mcp@sha256:add8756264bc95962597d2e5095b66317acb1d89a7c8b64f264e7d2dee140bc9

USER root
RUN /bin/rm -rf -- /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
    && test ! -e /usr/local/lib/node_modules/npm \
    && node --version

USER node
