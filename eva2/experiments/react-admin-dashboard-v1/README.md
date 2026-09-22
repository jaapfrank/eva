# React-admin dashboard candidate

This is the source and browser fixture for Eva's packaged, read-only
React-admin dashboard candidate. The hand-written cockpit presentation has
been removed from the source candidate, but this build is not yet live in the
managed Eva route.

The candidate uses React-admin's data and authentication providers above Eva's
existing owner-only API. Eva remains the authority for people, privacy,
permissions, work state, evidence and every mutation. The browser stores no
credential or UI state, and React-admin telemetry is disabled.

## Why this candidate won the bake-off

| Candidate | Exact line tested | Result |
| --- | --- | --- |
| React-admin | `react-admin 5.15.1`, React `19.2.8` | 200 locked packages, clean peer install, zero npm audit findings, 254.7 kB gzip JavaScript |
| Refine | core `5.0.12`, MUI `8.0.2`, React `18.3.1`, MUI `6.5.0` | 267 locked packages and zero audit findings, but a real MUI 5/6 peer conflict through `notistack 2.0.8`; telemetry also had to be disabled after the browser test observed an external call |
| Appsmith | current self-hosted platform | Rejected before a bundle trial: it adds a second application/runtime instead of replacing Eva's presentation layer with a library |

React-admin's JavaScript bundle is larger than the Refine trial. That cost is
recorded, not hidden. It wins because its current supported UI line installs
without peer overrides and already supplies the admin, data, authentication,
routing, forms and access-control building blocks required by later dashboard
slices.

## Reproduce

```sh
npm ci --ignore-scripts
npm audit --json
npm run build
cd ../..
python3 tools/build_dashboard_assets.py --materialize
python3 tools/build_dashboard_assets.py --check
EVA_DASHBOARD_PLAYWRIGHT_PACKAGE=/absolute/path/to/playwright \
EVA_DASHBOARD_CHROMIUM=/absolute/path/to/chromium \
  npm --prefix experiments/react-admin-dashboard-v1 run verify:browser
```

The browser proof uses only a synthetic same-origin server. It requires no Eva
secret and performs no live mutation. The packaged static route, CSP tests and
removal of the old presentation are now part of the source candidate.
Promotion still requires the normal immutable image build, scan and managed
A/B cycle.

## Personal Chrome entry

The Browser card opens the existing personal Chrome controls at
`http://127.0.0.1:44718` in a new tab. It offers that link only when the dashboard
hostname is `localhost`, `127.0.0.1` or `[::1]`, and explains that it must be used
on Eva's Chrome computer. Remote dashboard visitors receive local-computer
instructions instead of a loopback link to their own device.

The card does not fetch browser status or send control requests. Sharing a tab,
taking over and reconnecting use the existing bridge page and explicit extension
selection. The backend's legacy browser routes remain unchanged. To run only
the synthetic entry-point checks, set `EVA_DASHBOARD_BROWSER_ONLY=1` alongside
the two browser-test paths above; no live bridge or tab sharing is used.
