# Candidate only: current official MCP release plus current Debian security fixes.
# The parent remains exact.  The resulting local image is scanned and tested
# before it can be considered for any Eva provider change.
FROM mcr.microsoft.com/playwright/mcp@sha256:18c0a9c934004fe9580cc79f1e8e6e6cde7c667348b215335e8a23fd3e509804

USER root

RUN apt-get update \
 && apt-get upgrade --yes --no-install-recommends \
 && apt-get clean \
 && /bin/rm -rf -- /var/lib/apt/lists/* \
 && /bin/rm -rf -- /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx \
 && test ! -e /usr/local/lib/node_modules/npm \
 && node --version

USER node
