# PortWatch interface

The owner or reviewer sits at a daylight desk with a small laptop, moving between the harbor
map and time controls to check what was observable at a particular moment. A light, restrained
interface keeps those labels readable next to a naturally dense map.

## Palette

Use OKLCH tokens. Pure white main surface, neutral layered toolbar, dark ink. Primary hue40
comes from the design skill's burnt-orange seed; deepen it for accessible selected/play states.
Teal vessel points distinguish observed positions; amber marks stale evidence. Semantic colors
always have accompanying text. The map has muted blue water and subdued zone boundaries.

## Typography

One system sans family. Fixed rem scale with tabular figures for clocks, counts and identifiers.
No display type or decorative uppercase section labels. Heading32px, panel heading18px,
body14–16px, secondary labels12–13px with adequate contrast.

## Layout

Single-page map workspace: compact identity/source header, historical/live controls, a large
map and vessel inspector, replay rail directly below, then observed-activity/context panels.
Metrics form one flat ruled strip. On narrow screens the inspector and context stack below
the map; controls wrap and retain touch targets. Health uses a compact expandable section.

## Interaction

One playback loop, cleaned up on pause/mode change/unmount. Slider changes cancel obsolete
requests; stale results cannot replace the current cutoff. Loading retains context with an
explicit updating state. Map and vessel list provide equivalent selection. Overlays and basemap
are real toggles. Local vector map fallback remains usable without tiles or WebGL.

## Motion

160ms state/hover transitions. No decorative page entrance. Reduce transitions for reduced
motion; playback starts only on an explicit user action.

## Live and pipeline extension

Preserve the established map workspace. Add port selection and reported vessel/voyage details;
live mode is the default. The pipeline route inherits these tokens in Read mode: sequential
source-to-map flow, flat comparison tables per port, actual scoped workflow history, clear
failure/retry states and persistent map navigation on mobile. No public mutation controls.
