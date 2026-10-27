# PharmaX operator experience

The default application now connects the established globe and visual language to saved operator investigations. Attention, hospital inventory, shortage forecasts, mitigation candidates, revision history, evidence, execution records and current internal review drafts come through the typed `/api/v1` client. Hospital positions and dependency connections are explicitly illustrative; source evidence does not establish observed medicine shipment delays. The earlier design reference remains in `DesignReferenceApp` with its original regressions and never substitutes for an API failure. Shared schemas and backend producers are unchanged. See the [operator UI handoff](../../../docs/PHARMAX_OPERATOR_UI_HANDOFF.md) and [original full-stack handoff](../../../docs/PHARMAX_FULLSTACK_HANDOFF.md).

Desktop refinement is the current review target. Mobile received basic fit checks before Sidhant requested that further mobile layout work be deferred. Genuine live inference and external action dispatch remain unavailable.

## Run locally

```powershell
cd phases/phase_01_foundation/dashboard_design
npm ci
npm run dev
```

Open the local URL printed by Vite. Run `npm test` for geometry and UI interaction coverage or `npm run build` for TypeScript and production bundling. Run `node pack-preview.mjs` after building to regenerate the ignored, self-contained `preview.html`.

## Interface behavior

The globe fits its initial camera to the stored priority route and current viewport. Drag rotates; wheel and touch gestures zoom; the floating controls zoom and reset. A small 3D vessel moves slowly near the existing Red Sea segment. It is an illustration with no vessel telemetry. Activity points and pulsing rings render in globe coordinates so they rotate with the sphere. Country geometry uses the bundled Natural Earth 50m dataset for a quicker first map render; the globe component loads separately from the compact application shell.

Selecting an alert keeps the globe mounted and reorganizes the page into a risk view, with the active location highlighted on the small globe. The panel surfaces real public context links, clearly labeled mock forecasts, a detailed alternate-route review, and confirmation for local-only approval. The agent section shows eight compact, illustrative audit-summary steps across six named roles. It does not expose private chain-of-thought or imply a live run.

The light theme follows the supplied globe screenshot's pale background, turquoise geography, and white surfaces. Dark mode remains available. Theme choice persists and defaults to the system preference when no prior choice exists. Sidebar labels stay visible; the SX avatar, Help item, map Layers button, redundant map headings, demo/workspace header labels, and activity search field were removed. The medicine selector, alert filter, zoom/reset, and chat trigger remain.

## Evidence and data limits

The linked IMO and UNCTAD pages are regional and industry context only; neither confirms the demo alert or a shipment. Delay, likelihood, costs, shipment totals, and all agent statuses/outcomes in the risk review are mock values for interface review, not observed or model-generated results. Those sample screens do not retrieve sources or track shipments. The Investigations drawer separately displays actual saved operator cases, calculated forecasts and retained provenance; it does not convert these demo routes into observed shipments. Approval only changes local component state; it does not send a logistics action.

## Open-source reuse

- [Coss UI](https://github.com/cosscom/ui/tree/main/apps/ui): reviewed its Base UI sheet and semantic surface patterns for the October 26 UI refinements. No source or new dependency was imported. `src/review.css` aligns the light/dark layouts, styles portaled drawers with shared theme tokens, centers counters, and gives disruption details and specialist activity a responsive workspace. The shared `BrandMark` keeps the green PX mark consistent across navigation and assistant entry points.

- [useLayouts](https://github.com/iurvish/uselayouts): `src/primitives.tsx` adapts the Base UI sheet and dropdown patterns. MIT notice: `THIRD_PARTY_NOTICES.md`.
- [OpenUI](https://github.com/thesysdev/openui): `src/OpenUIInput.tsx` adapts its MIT input component without model/inference integrations.
- [ThreeUI Community](https://github.com/MengTo/threeui): reviewed; no source was copied.
- [react-globe.gl](https://github.com/vasturiano/react-globe.gl), Three.js, [TopoJSON](https://github.com/topojson/topojson-client), and [world-atlas](https://github.com/topojson/world-atlas): globe rendering, local 3D vessel, and bundled country geometry.

## Validation and limitations

The automated tests cover route fitting, path interpolation, globe persistence through risk selection, evidence links, local review/approval, default risk order, theme persistence, removed controls, and chat. Full-stack acceptance additionally exercises the actual WebGL globe and API drawer in a headless browser at desktop/mobile sizes, including light/dark themes, revision navigation and offline/error states. `npm run build` may report a warning for the separately loaded globe bundle. Only read-only saved-case integration is connected; deployment and publication are excluded.


## Read-only operator connection

Start the backend from the repository root using the documented offline replay and
`--serve` commands. In a second **WSL** terminal:

```sh
export PHARMAX_API_TARGET=http://127.0.0.1:8767  # use 8766 when that backend port is free
export PHARMAX_USE_POLLING=1  # Windows-mounted WSL file watching
npm ci
npm run dev -- --port 5174 --strictPort
```

Open `http://127.0.0.1:5174` and select **Investigations**. The browser sends relative
`/api/v1` requests; Vite proxies both development and local preview to
`PHARMAX_API_TARGET` (default `http://127.0.0.1:8766`). This server-only setting is
not embedded in production JavaScript. Production hosting must supply a same-origin
`/api/v1` reverse proxy; no deployment is performed here.

`npm run test:http -- http://127.0.0.1:5174 /absolute/path/to/result.json` exercises
the exact frontend client over HTTP against the original and revised seeded cases.
`tests/fixtures/operator-http.json` retains only UI-consumed fields from the actual
operator responses and identifies their real saved IDs/timestamps.

The optional `tests/browser-smoke.mjs` uses a separately installed Playwright module
through `PHARMAX_PLAYWRIGHT_MODULE`; it is QA tooling, not an application dependency.
Never put provider tokens or other credentials in frontend environment variables.
