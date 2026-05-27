# 01 — New Mode: Particle Curtain via Depth

Status: SPEC / TODO. Not implemented.

## Concept

A new rendering mode for the particle subsystem where the particle cloud
behaves as a spatial **curtain** in the room. A performer walks toward
the camera through the curtain; particles always sit at a fixed z-plane,
and a depth-mask compositing rule decides whether the performer is
rendered **behind** the curtain (they are still approaching) or **in
front of** it (they have stepped through).

Visually: from the audience POV, the performer is gradually revealed
as they pass through a hanging sheet of particles. Behind the plane =
particles occlude them. In front of the plane = they occlude particles.

## Requires

A real depth sensor with metric depth output, not MediaPipe monocular
estimation. Confirmed candidates:

- Azure Kinect (Kinect DK) via the Kinect Azure TOP
- Intel RealSense (D435 / D455) via the RealSense TOP
- Apple Vision Pro depth (if exposed via the apple-vision plugin already
  in `Plugins/`)
- Orbbec Femto

Sensor produces a per-pixel z (metres) registered to the color image.
That is the input layer this mode consumes — it does NOT replace the
MediaPipe pose tracking (still used for the sensing/emission side).

## Geometry — two nested boxes in z

```
camera ──────────────────────── stage (depth)
         │   inner box  │
   ┌─────┼──────────────┼─────┐         ← outer box
   │     │              │     │
   │     │ ◄── z_center │     │
   │     │              │     │
   └─────┼──────────────┼─────┘
         │              │
         └──┬───────────┘
            │
        thickness_inner < thickness_outer
        center_inner.z == center_outer.z
```

- **Outer box** — full detection volume. People in this volume are
  recognised as "present" but may be in front or behind the curtain.
  Outside this box → depth pixel is ignored (no person silhouette).
- **Inner box** — same z-center as outer, narrower in depth (and may be
  narrower in x/y too). This is the "front of the curtain" zone — any
  depth pixel whose z is inside the inner box renders **in front** of
  the particles.
- **Curtain plane** — the z-position of the particle render. Particles
  always exist near `z_center` and stay anchored there regardless of
  performer position. Conceptually it's a thin slab the width of the
  inner box but more spread along x/y.

Practical compositing rule (per pixel):

| Pixel's depth z is in… | Render order |
| --- | --- |
| inner box (`abs(z - z_center) < t_inner/2`) | performer **in front** of particles |
| outer box only (`abs(z - z_center) < t_outer/2`) | performer **behind** particles |
| outside outer box | not a performer pixel (background) |

## Parameters (proposed)

Add a new "Curtain" page on `velocity_controller` (or a new sibling
COMP `depth_curtain` — see "Open questions"). All defaults are
guesses; tune on real hardware.

| Par | Default | Description |
| --- | --- | --- |
| `Curtainenable` | Off | Master toggle for the mode |
| `Depthop` | (op ref) | Kinect/RealSense TOP that provides metric depth |
| `Depthrangemin` | 0.5 m | Min depth considered (closer = clipped) |
| `Depthrangemax` | 5.0 m | Max depth considered (further = clipped) |
| `Outerz` | 2.0 m | Z-center of both boxes (sensor frame) |
| `Outerthickness` | 1.5 m | Outer box thickness along z |
| `Innerthickness` | 0.25 m | Inner box thickness along z (`< Outerthickness`) |
| `Outerxy` | 1.0 | Outer box xy extent as fraction of frame |
| `Innerxy` | 0.8 | Inner box xy extent as fraction of frame |
| `Curtainmask_feather` | 0.05 m | Soft edge on inner/outer transitions to avoid hard pop |
| `Curtaindebug` | Off | Render the two boxes as wireframe overlays |

## Implementation sketch

Three TD operators are added downstream of the existing particle render:

1. **`depth_mask_glsl` (GLSL TOP)** — reads the depth TOP, produces an
   R-channel mask:
   - 1.0 where the pixel is inside the inner box (foreground)
   - 0.0 where inside the outer box only (background relative to
     curtain)
   - alpha = 0 where outside outer box (not a person)
   - Optional soft feather over `Curtainmask_feather` so the matte
     doesn't pop discretely between zones.

2. **`particle_curtain_render` (TOP chain)** — the existing particle
   POP render output, no change. This is the "curtain layer".

3. **`composite_curtain` (Composite TOP, multi-input)** —
   ```
   background_color_in  →  ┐
   particle_curtain_render → ├ over (matte from depth_mask, foreground bit)
   foreground_person_in →   ┘
   ```
   Compositing order, top to bottom:
   - color camera feed (clipped to outer-box pixels only) as background
   - particles (always at curtain z) on top of background
   - color feed clipped to inner-box pixels on top of particles

The "color feed clipped to box X" step is a per-pixel mask + multiply
with the original color camera frame — no segmentation network needed,
it's purely depth-thresholded.

## Why two boxes (not one threshold)?

A single threshold creates a hard z=plane: walk through the plane and
suddenly the entire performer is reclassified front/back. The two-box
arrangement gives a transition zone (the gap between inner and outer):

- approach: performer enters outer box → silhouette appears behind
  particles (matte renders them with particles overlaid)
- continue forward: performer crosses into inner box → silhouette
  promotes to "in front" (matte renders them on top of particles)
- the gap between outer and inner is the "wading through" zone where
  parts of the performer may straddle the threshold (e.g. arm
  extended through the curtain) and individual pixels resolve their
  own front/back status correctly per-pixel

This matches the physical metaphor: the curtain has thickness, you
don't snap from "in" to "out" instantaneously.

## Integration with existing sensing

- MediaPipe pose tracking continues to drive the particle emission
  (limbs still emit, particles still respond). Curtain mode adds
  a compositing layer on top — it does NOT change how particles spawn
  or move.
- Particles should remain spatially anchored at the curtain plane.
  Recommend constraining `Boundsminz` / `Boundsmaxz` to a thin slab
  (`z_center ± Innerthickness/2`) when Curtain mode is enabled so
  particles don't drift forward/back and ruin the depth illusion.
- The bounds-reflect GLSL POP already supports per-axis bounds, so
  this is a parameter change at activation time, not a code change.

## Hardware-specific notes

- **Kinect Azure**: depth in millimetres, may need a scale step. The
  Kinect Azure TOP exposes a `pointcloud` mode that gives metric XYZ
  per pixel — easier than thresholding raw depth.
- **RealSense**: depth in mm too. Built-in registration to color is
  usually less accurate than Kinect Azure — be prepared to add a
  small offset par to align mask with color.
- **Apple Vision Pro**: depth output format depends on the plugin in
  `Plugins/`. Inspect first.

## Open questions

1. **New COMP vs. new page on `velocity_controller`?**
   The composite stage is fundamentally a rendering concern and adds
   non-trivial GPU cost. Probably cleaner as a sibling `depth_curtain`
   Base COMP that takes:
   - particle render TOP from `velocity_controller`
   - depth TOP from the sensor
   - color TOP from the sensor
   and emits a composited TOP. Keeps `velocity_controller` focused on
   sensing + particle generation.

2. **Should sensing also use depth?**
   MediaPipe gives `z` per landmark but it's monocular and noisy.
   With a real depth sensor we could re-project landmarks to metric
   z and replace MediaPipe's z entirely. Out of scope for this spec
   but worth noting — could become `02_metric_depth_for_sensing.md`.

3. **What happens when there's NO performer in the volume?**
   Particles still cook. Probably correct (they fade out via lifetime).
   Confirm with user that the empty-stage visual is acceptable.

4. **Two-person case**: rule is per-pixel so two performers at
   different depths get composited correctly automatically. No
   special case needed.

## Self-test plan (when implementing)

- Stand 3 m from sensor: silhouette behind particles.
- Walk to 1.5 m (within inner box): silhouette in front of particles.
- Extend an arm forward across the boundary: arm pixels in front,
  body pixels behind. Validates per-pixel resolution.
- Disable Curtainenable: behaviour reverts to existing particle render
  with no compositing.

## Out of scope

- Rim lighting / depth-based shading of the performer
- Background plate replacement (chroma key, etc.)
- Particle response to performer's metric position (would need to
  re-project depth into sensing space — separate spec)
