# Coordinator map — known problems and next phase

**Date:** 2026-08-11
**Status:** backlog, deliberately not actioned
**Ships on:** `9ea83e8 feat(web): draw the coordinator, not the hardware`
**Design:** `2026-08-11-zolli-coordinator-map-hero-design.md`

The map shipped. These are the things known to be wrong or worth doing next,
recorded now so nobody has to rediscover them. Findings marked *measured* came
from a screenshot pass at 1440×900, 1024×768 and 390×844 across all four story
phases — they are not impressions.

Two defects found in that pass were fixed before the commit and are listed in
§5 so they are not re-reported as open.

---

## 1. The next phase, in priority order

### 1.1 Recognisable hardware — the biggest remaining gap

The four silhouettes are distinguishable from each other, which was the goal
for this pass. They are not yet *recognisable as objects*. "Everyday machines"
should read as a laptop — the wedge profile, the hinge, the screen at an angle
— next to a desktop tower and a small mini-PC. A viewer should not have to read
the label to know what they are looking at.

Same for the rest: the rack wants visible rails and unit gaps, the GPU pair
wants the unmistakable triple-fan front, and the cloud blocks want the density
of a real row of cabinets.

This is where the "anyone can start their own compute" argument actually gets
made. A generic block does not say *your laptop*.

Cost: geometry only. `MapNode.tsx` already draws each silhouette from
`spec.size` and the projection helpers; the work is authoring better shapes,
not rearchitecting. No new dependency, no raster.

### 1.2 Click a component to inspect it

**Asked: can a node open an explanation without leaving the SVG design
language? Yes.** Three approaches, with a recommendation.

**(a) Panel drawn inside the SVG.** Anchored beside the clicked node as
`rect` + `text`, or a `foreignObject`. Perfectly consistent, scales with the
viewBox, exports as one asset. But SVG text does not wrap — every line has to
be positioned by hand — and `foreignObject` behaves inconsistently enough that
it is not worth the risk on a hero.

**(b) HTML panel positioned from the SVG's own coordinates.** ← recommended.
The panel is real HTML, absolutely positioned over the figure using the node's
projected point. Styled from the same tokens and the same hairline-border,
monospace-label vocabulary, so it reads as part of the drawing. Text wraps
properly, is selectable and translatable, and screen readers get real content.

This is close to free: `nodeAnchor(spec, viewport)` already returns the screen
point, the `viewBox` is known, so SVG units → CSS pixels is one scale factor.

**(c) Focus mode.** Clicking dims the rest of the map, the composition
re-settles, and the detail occupies the space vacated. The most cinematic, the
most work, and it fights the scroll story for control of the same beat.

**Recommended shape:** extend the state that already exists. Hovering a source
already lifts it and dims the other three; a click *latches* that state and
opens a (b)-style panel. Escape and a click outside close it. Nodes are already
`role="button"` and focusable, so Enter and Space come almost free — they need
`aria-expanded`, and focus must move into the panel and return to the node on
close.

**The centre is the most valuable one to make clickable.** Clicking the Zolli
coordinator should explain the mechanism: it leases work to machines it does
not trust to survive, listens for heartbeats, holds the checkpoint so the
machine becomes disposable, and re-leases from that checkpoint when the
heartbeat stops. That copy already exists — it is the four explanation cells on
the prototype page at `apps/web/.artifacts/inspiration/coordinator.html`.

Worth saying plainly: this turns the hero into an *explorable diagram*. Baseten
and Modal both ship static ones. It would be the differentiated thing on the
page.

### 1.3 The scroll story only covers the hero

The Apple-style scroll was asked for across the whole page. Only the hero
participates today — the other nine sections are unchanged. The natural
extension is for the map to persist as a pinned element while sections scroll
past it, each section advancing a beat, rather than the story completing inside
the hero alone.

Decide this before 1.1 or 1.2: if the map becomes a page-length element, its
composition and the detail-panel placement both change.

## 2. Layout problems (measured)

| # | Where | Problem | Severity |
|---|---|---|---|
| 2.1 | 1024×768 | Map largely below the fold. The figure starts at y=468 in a 768px viewport, so only ~300px of a 533px figure is visible: RENTED GPU, the coordinator's caption and the whole readout strip are cut off. Needs a shorter composition at laptop heights, not a scaled one. | **major** |
| 2.2 | 390×844 | The "ZOLLI COORDINATOR" caption's right edge sits **1.5px** from the Google Cloud badge circle and 4.6px from the BURST chip. Not overlapping; too close to read as deliberate. | **major** |
| 2.3 | 390×844 | Orange lease tokens clip the coordinator label's bounding box in transit — max overlap 6.6×6.6px, in three of four beats. The route stroke itself never crosses a glyph. | minor |
| 2.4 | 1440×900 | The active everyday route passes 3.6px from the "EVERYDAY MACHINES" label box — the tightest clearance on the desktop map. Tokens graze the box by up to 0.7px. | minor |

## 3. Consistency problems (measured)

| # | Problem | Severity |
|---|---|---|
| 3.1 | Label/badge/chip stacking order differs per node. Owned stacks name → badges → chip → machine (all above); Rented stacks machine → badge → chip → name (all below). The eye has to relearn the pattern at each source. It was done to keep furniture off the coordinator, but the inconsistency is now the more visible cost. | minor |
| 3.2 | A dead node's label block stays at full brightness while its silhouette fades to near-invisible. Through `lost`, `resumed` and `accepted` the name, the "12 NODES" chip and all three badges keep burning. The whole node should dim together. | minor |
| 3.3 | The Linux mark is an outline while Apple and Windows are solid fills, so at 23px badge diameter it reads noticeably fainter than its neighbours. Ubuntu, Docker and NVIDIA are solid. | minor |
| 3.4 | The "AWS" type badge renders at 7px against 9.9px node names — legible at 3× DPI but at the floor, and its thin letterforms read lighter than the solid marks beside it. | minor |

## 4. Open judgment calls

**4.1 The STATE value is orange.** It is the only orange outside the map's job
path. Left as-is: it describes the job, so it is consistent with the rule
rather than a leak. Revisit if the readout ever grows.

**4.2 Windows and AWS have no icon.** Both were removed from `simple-icons` at
those companies' request. Windows currently ships as a four-pane glyph drawn
for this project and AWS as type, and the weight mismatch in §3.3–3.4 is the
visible cost. Three ways out: commission matched custom glyphs for the whole
row, drop marks entirely and label every platform as type (zero trademark
exposure, loses instant recognition), or keep the mix. Do not "fix" it by
importing the real marks from another icon set —
`lib/coordinator-map-icons.ts` carries a header saying so.

**4.3 The readout numbers are per-phase constants.** They come from
`readoutFor(phase)`, not from the API. That is right for a landing page, but
if it ever reads live fleet data the honesty bar changes — a number presented
as live must be live.

## 5. Fixed before the commit — do not re-report

- **The readout strip changed height mid-story.** Longer state values wrap
  where shorter ones do not, growing the strip 22px at the phase boundary and
  shoving the map up the page on every loop. Every cell now reserves two lines.
- **The hero visibly rewound on load.** The pre-hydration frame was `resumed`
  while the timer started at `running`, so the story ran backwards for a beat.
  The animated story now initialises where the story starts. Reduced motion
  still gets `resumed`, which is the frame with the whole claim in it.

## 6. Operational note

A `next dev` server left running across a large refactor can serve stale client
code with a dead HMR socket (`ERR_INVALID_HTTP_RESPONSE`), which presents as
the story being frozen on one phase and the motion provider appearing never to
mount. It cost real debugging time on 2026-08-11 and produced a wrong root
cause before the server was restarted. Restart the dev server after a refactor
of this size before diagnosing anything.
