# React-admin dashboard candidate

This is an isolated, read-only candidate for replacing Eva's hand-written
cockpit presentation. It does not run in the managed Eva route.

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
EVA_DASHBOARD_PLAYWRIGHT_PACKAGE=/absolute/path/to/playwright \
EVA_DASHBOARD_CHROMIUM=/absolute/path/to/chromium \
npm run verify:browser
```

The browser proof uses only a synthetic same-origin server. It requires no Eva
secret and performs no live mutation. Promotion requires a separate static
asset route, CSP tests, image build/scan, removal of the old embedded
presentation and the normal managed A/B cycle.
