# velocity_controller — setup guide

Companion to `painting_controller`, same conventions (parent-pars-only, pure-Python
logic module, Lag CHOP does the smoothing, Select CHOP chooses landmarks upstream).
**Everything lives inside a single `velocity_controller` Base COMP** — sensing
chain and rendering chain are siblings inside the same COMP so every GLSL uniform,
emitter spawn rates, Feedback TOP fade, etc. can read their parameters locally as
`parent().par.*` with no custom COMP pointers. Targets TD **2025.30960+**.

## TL;DR of what this ships

A single `velocity_controller` Base COMP with two sub-chains:

- **Sensing** — reads 5 MediaPipe landmarks, emits per-limb
  `x/y/vx/vy/speed/accel/emit/burst/visible` plus `total_motion/total_burst/frame_dt`
  on `out1` (for any external consumer) AND feeds the renderer directly via the
  internal `lag1` CHOP.
- **Rendering** — reads `lag1` by channel name through two small Script ops
  (Script TOP + Script CHOP) that fan it out into a texture and a reshaped
  CHOP. The texture feeds a GLSL TOP velocity field; the CHOP feeds a
  stock CHOP-to-POP converter that seeds the POP spawn+advect chain. Final
  output is a TOP.

## Input contract (into the `velocity_controller` Base COMP)

One CHOP input carrying normalized MediaPipe pose channels. After the upstream
Select CHOP narrows to this experiment's landmarks, you should have, for each of
the five default landmarks, at minimum:

```
left_wrist:x   left_wrist:y   [left_wrist:z]    [left_wrist:visible]
right_wrist:x  right_wrist:y  [right_wrist:z]   [right_wrist:visible]
left_ankle:x   left_ankle:y   [left_ankle:z]    [left_ankle:visible]
right_ankle:x  right_ankle:y  [right_ankle:z]   [right_ankle:visible]
nose:x         nose:y         [nose:z]          [nose:visible]
```

`:z` is MediaPipe's depth estimate — same rough unit scale as x, hip-center
at 0, negative = toward camera, positive = away. Optional (missing → 0,
pipeline falls back to 2D behavior). Less reliable than x/y since it's
monocular depth, but usable for forward/back motion detection.

`:visible` is MediaPipe's 0..1 confidence score; anything below
`Visibilitythreshold` is treated as off-frame. If the channel isn't
present the landmark is assumed fully visible.

The exact landmark set is configurable via the `Landmarks` parent par (space or
comma separated); the Script CHOP rebuilds its state dict on change.

## Output contract (from the `velocity_controller` Base COMP)

Pre-Lag channels from the Script CHOP, in emission order:

Per landmark `<L>`:
- `<L>:x`, `<L>:y`, `<L>:z` — pass-through position (3D; z in MediaPipe depth units)
- `<L>:vx`, `<L>:vy`, `<L>:vz` — smoothed velocity (1/s in MediaPipe-space)
- `<L>:speed` — 3D magnitude sqrt(vx²+vy²+vz²)
- `<L>:accel` — smoothed |a| (3D)
- `<L>:emit` — 0..1 emission rate (`speed / Speedscale`, clamped)
- `<L>:burst` — 0..1 burst envelope (`|a|` spike above threshold, decays)
- `<L>:visible` — 0 or 1

Globals:
- `total_motion` — sum of per-limb speed
- `total_burst` — sum of per-limb burst
- `frame_dt` — observed seconds between cooks (diagnostic; don't drive visuals with it)

Post-Lag (the Base COMP's actual output CHOP), these are all smoothed by a single
Lag CHOP whose `Lag 1` and `Lag 2` both reference `parent().par.Blendtime`. Keep
Blendtime short (0.05–0.15s) — we already smooth upstream, this is just to remove
frame-to-frame jitter for the renderer.

**Position-hold on dropout (hysteresis).** Confidence from MediaPipe
typically degrades *gradually* as a limb leaves the frame — position
becomes garbage several frames before confidence drops below any single
threshold. To handle that cleanly, the sensing side uses **two
thresholds**:

- `Visibilitythreshold` (default 0.5) — the *output gate*. Below this,
  `<L>:visible` emits 0 and emit/burst envelopes fade out.
- `Trustthreshold` (default 0.75) — the *commit threshold*. Only frames
  at or above this confidence update the cached "last good" position and
  run the velocity math.

That gives three behavioral zones on `:visible`:

| MediaPipe confidence | Zone | Output position | Output `visible` |
| --- | --- | --- | --- |
| ≥ Trustthreshold | Trusted | raw `x, y` | 1 |
| Visibilitythreshold..Trustthreshold | Marginal | last-good (frozen) | 1 |
| < Visibilitythreshold | Invisible | last-good (held) | 0 |

The key win is the marginal zone: the emitter stays on for spawning but
is pinned to the last genuinely-trusted position, so it doesn't slide
toward garbage during the confidence ramp-down. By the time `:visible`
goes to 0, position is already at the correct last-good — lag1 sees no
change in position, and the blob fades in place instead of sliding.

`Maxjump` is a secondary safeguard: within a *continuous* trusted stream,
any single-frame position jump larger than `Maxjump` UV units demotes the
frame to the marginal zone (output last-good, don't commit). The check
runs against the previous *frame's* position, not the cached last-good,
so after any dropout / marginal period it's naturally skipped —
re-acquisition always accepts the new position, even if the joint
reappears on the opposite side of the frame. (Without that, a joint that
leaves on the right and returns on the left would get stuck at the old
right-side cached position forever, because every re-acquisition frame
exceeds `Maxjump`.) Tune `Maxjump` against your expected fastest
legitimate motion: at 60 fps a very fast whip is ~0.05 UV/frame, so
0.2–0.3 is a safe ceiling. Set to 0 to disable.

`Settleframes` (default 5) is a third safeguard layered on top of
`Maxjump`. For the first N trusted frames after any dropout, the
`Maxjump` check is suspended. MediaPipe's first trusted frame on
re-acquisition often lands near the re-entry edge before locking onto the
real joint position a frame or two later — without the grace, that second
frame gets rejected as a teleport (it's > `Maxjump` from the edge `prev_x`)
and the blob would be stuck at the re-entry edge for a cook. During the
grace window we simply accept whatever MediaPipe sends; normal teleport
protection resumes once the tracker has had `Settleframes` cooks to lock
on. If you still see your blob briefly snap from the edge inward after
reappearance, raise `Trustthreshold` toward 0.85–0.9 — that's MediaPipe's
own edge-lock noise, which only a higher confidence threshold can filter
out at the source.

**3D / z-axis behaviour.** The pipeline tracks MediaPipe's z alongside x/y
end-to-end. `<L>:z` and `<L>:vz` appear in the output CHOP; 3D speed
(`sqrt(vx²+vy²+vz²)`) drives `emit` so forward/back motions contribute to
particle emission the same as side-to-side; 3D acceleration magnitude drives
`burst` so a sudden forward thrust triggers a puff. On the renderer side:

- `emitters_chop` emits `P[2]=z` and `v[2]=vz`, so particles get launched
  with 3D initial velocity and the POP Advance integrates motion on all
  three axes — particles really do get flung forward or back.
- `emitters_tex` packs z into row 0 and vz into row 1.
- The velocity-field shader uses the per-limb z to scale each emitter's
  splat size (closer to camera = bigger splat; `uZGain` controls
  strength), and outputs RGB = full 3D velocity so the Force POP pushes
  particles on all three axes.
- The shader also elongates the gaussian kernel along the limb's velocity
  direction (`uVelStretch`). A limb moving fast throws a longer "cone" of
  force ahead of itself, so particles in the direction of motion get
  shoved further than those to the side. That's what gives the "flung"
  feel beyond what round kernels alone would produce.

If your input pose CHOP doesn't carry `:z` channels (some wrappers strip
it), the pipeline falls back to z=0 everywhere — you get the same 2D
behavior as before, no visual change. You can mix: some landmarks with z,
some without.

**Tuning depth sensitivity.** By default, z-axis motion contributes less
to emit rate and burst detection than x/y motion does — controlled by
the `Zspeedweight` parameter (Sensing page, default `0.35`). Rationale:
MediaPipe's z is noisier than x/y, and leaning forward shouldn't produce
the same emission spike as a full arm whip. The weight multiplies `vz`
before it enters the speed/accel magnitude calculations, so at 0.35 a
pure-depth motion produces ≈35% the emit/burst response of the same raw
motion in-plane. `vz` itself is still emitted as an output channel at
full fidelity for the renderer to use — the weight only tames *sensing*
sensitivity, not *output* accuracy.

If depth motion still feels over-reactive (very close performer, noisy
tracker, etc.), drop `Zspeedweight` toward 0.1–0.2. Set it to 0 to make
depth motion completely inert for emit/burst while still letting vz push
particles in the z direction via the velocity field. Crank it up to 1.0
if you specifically want "lean-in = explosive burst" behaviour.

**Two separate z-axis tamers — know which one to reach for:**

| Par | Layer | What it controls | Lower if you see… |
| --- | --- | --- | --- |
| `Zspeedweight` | Sensing | How much `vz` contributes to `speed` & `accel` magnitudes → emit rate & burst triggering | Too many particles spawn when you lean in or out |
| `Zforceweight` | Renderer | Scales `vz` on both the flowfield (force on live particles) AND `StartPartvel.z` (launch velocity of newborns) | Particles drift forward/back during pure horizontal motion |

MediaPipe's monocular depth estimate is noisy even during pure xy
motion — hand pose changes cause spurious vz readings of several UV/s
as the learned depth model wobbles. `Zforceweight = 0.05` knocks that
down to ~5% on both render paths, which makes z-motion essentially
disappear from the particle visual unless the performer deliberately
leans in or out at significant speed. Set to `0` if you want the
pipeline to behave as purely 2D on the render side regardless of what
MediaPipe reports for z.

On the renderer side, the splat-size-from-z formula is also tightened
against close-up blowup: `size_mult = clamp(1.0 - z * Zgain, 0.25, 1.8)`.
Very-close limbs (large negative z) get capped at 1.8× the base radius
rather than the 3× they could hit previously. `Zgain` default lowered to
0.35 so size variation from depth is noticeable but subtle. `Fieldradius`
default also lowered to 0.09 so the base blob is tighter — both together
should keep close-up limbs from dominating the frame.

**NaN/Inf resilience.** MediaPipe occasionally emits non-finite position
or confidence values (first cook of an invisible landmark, tracker
restart, certain tox builds mid-dropout). The Script CHOP scrubs all
input channels with `math.isfinite` before the logic sees them, the logic
scrubs stored state on every cook, and `_emit` guards every outbound
channel. End result: NaN can never reach the Lag CHOP, and any
corruption that somehow does land in state heals on the next cook. If
you previously had to manually reset the Lag CHOP to clear stuck
accel/burst values, that should no longer happen.

**Tuning hierarchy if you still see a teleport:** raise `Trustthreshold`
first (0.8–0.9 is common for jittery MediaPipe output); then tighten
`Maxjump` toward 0.15. Don't raise `Visibilitythreshold` unless the joint
lingers as a fading blob for too long after it actually leaves frame —
that's what it's for.

## Inside the `velocity_controller` Base COMP

One COMP, three sub-chains: a sensing CHOP chain, a POP particle sim, and a
screen-space feedback loop on top. The diagrams below split them apart for
legibility; in the COMP itself they're all peers.

### Sensing chain + fan-out

```mermaid
flowchart LR
    in1([in1<br/>pose CHOP input])
    select1[select1<br/>Select CHOP<br/>limb channels]
    script1[script1<br/>Script CHOP<br/>velocity_logic]
    lag1[lag1<br/>Lag CHOP<br/>Blendtime smooth]
    out1([out1<br/>external consumers])
    emitters_tex[[emitters_tex<br/>Script TOP<br/>N×2 RGBA32F<br/>→ velocity_field shader]]
    emitters_chop[[emitters_chop<br/>Script CHOP<br/>N samples]]
    emitters_pop[emitters_pop<br/>CHOP to POP<br/>N points P/v/w/id<br/>→ Particle POP input]

    in1 --> select1 --> script1 --> lag1 --> out1
    lag1 -. reads by name .-> emitters_tex
    lag1 -. reads by name .-> emitters_chop
    emitters_chop --> emitters_pop
```

Both Script ops pull from `op('lag1')` by channel name — no Select/Shuffle/Rename
between them and the Lag CHOP.

Text DATs (all peers of the ops they drive):
- `velocity_logic` — paste `velocity_logic.py`
- `velocity_script_chop` — paste `velocity_script_chop.py`, referenced by `script1`
- `install_velocity_params` — paste `install_velocity_params.py`, right-click ▸
  Run Script once. You can delete this DAT afterward.
- `emitters_tex_script` — paste `emitters_tex_script.py`, referenced by `emitters_tex`
- `emitters_chop_script` — paste `emitters_chop_script.py`, referenced by `emitters_chop`

Sensing chain wiring:
- `select1` pattern: `left_wrist:* right_wrist:* left_ankle:* right_ankle:* nose:*`
- `script1` Callbacks DAT: `velocity_script_chop`
- `lag1` Lag 1 / Lag 2: both expression `parent().par.Blendtime`
- `out1` just dangles off `lag1` for external consumers (the renderer reads
  `lag1` directly by name, so `out1` is optional).

Parent pars installed onto two pages:
- **Sensing**: `Landmarks`, `Visibilitythreshold`, `Trustthreshold`, `Velocitysmooth`,
  `Accelsmooth`, `Speedscale`, `Accelthreshold`, `Accelscale`, `Burstdecay`,
  `Maxjump`, `Settleframes`, `Zspeedweight`, `Blendtime`.
- **Renderer**: `Spawnrate`, `Burstgain`, `Spawncount`, `Spawnspread`,
  `Spawnspreadref`, `Spawnspreadmin`, `Spawnperpratio`, `Spawnvelscale`,
  `Spawnvelfan`, `Fieldradius`, `Fieldforce`, `Fielddecay`, `Zgain`,
  `Zforceweight`, `Velstretch`, `Stretchspeedref`, `Curlgain`,
  `Curlscale`, `Lifemin`, `Lifemax`, `Boundsminx`, `Boundsminy`,
  `Boundsminz`, `Boundsmaxx`, `Boundsmaxy`, `Boundsmaxz`,
  `Boundsbounce`, `Boundsmargin`, `Feedbackenable`, `Feedbackfade`,
  `Feedbackzoom`.

The page split is purely organisational — both pages live on the same COMP, and
every renderer op reads its pars via `parent().par.*` because `parent()` inside
any op is `velocity_controller`. Sensing tuning doesn't disturb rendering and
vice versa, even though they share a COMP.

## Renderer sub-chain (inside `velocity_controller`)

The render side reads from the sensing-side `lag1` CHOP via two small Python
operators. No Shuffle/Rename/Select fan-out — both scripts look up channels by
name (`left_wrist:x`, etc.) so they don't care about channel order.

### 1. `emitters_tex` — Script TOP

Feeds the velocity-field shader. Produces an **`N × 2` RGBA32F** texture:

- **Width `N`**: the landmark count — derived dynamically from
  `parent().par.Landmarks` inside the Script TOP's callback (same source
  of truth as every other op in the pipeline). If you change the
  `Landmarks` par, the texture resizes on the next cook automatically
  (`copyNumpyArray` sets the TOP's shape from the numpy buffer). You don't
  need to hardcode N anywhere — the Output Resolution field on the Script
  TOP is just an initial hint to avoid a one-frame black flash at startup;
  the runtime size tracks the landmark count. Set it to `5 × 2` for the
  default landmark set, or just leave it at defaults — the callback will
  resize on first cook.
- **Height `2`**: because we pack **8 floats per landmark** into an RGBA
  texture (4 floats per texel), so 8 / 4 = 2 texels per column. The
  layout:
    - Row 0: `(x, y, z, visible)` — 3D position + visibility gate
    - Row 1: `(vx, vy, vz, force_gain)` — 3D velocity + pre-combined
      `(emit + Burstgain * burst) * visible` weight

  Why 8 floats? We need `x, y, z` (3), `vx, vy, vz` (3), `visible` (1) and
  `force_gain` (1) in the shader to do everything it does. Any fewer and
  we lose a capability (drop z → no depth scaling; drop visible → no
  dropout gating; drop force_gain → back to separate emit/burst uniforms).
  8 floats is the minimum for the current feature set, hence 2 rows.

  If you want to extend the shader with more per-landmark data later
  (per-limb color hint, per-limb custom scale, etc.), you'd bump this to
  3 rows = 12 floats per landmark and teach the shader to sample the
  extra row.

Setup:

1. Inside `velocity_controller`, create a **Text DAT** named
   `emitters_tex_script`, paste `emitters_tex_script.py`.
2. Create a **Script TOP** named `emitters_tex`. No inputs — it reads
   `op('lag1')` by name from inside its callback.
3. Set its Callbacks DAT to `emitters_tex_script`.
4. Set Output Resolution to Custom, e.g. `5 × 2` (matches default landmark
   count). The callback also calls `copyNumpyArray` with the correct shape,
   so TD resizes automatically on cook — but setting it explicitly avoids a
   one-frame black flash on startup.

### 2. `velocity_field` — GLSL TOP (+ external persistence chain)

Samples `emitters_tex`, splats gaussians, outputs the **instantaneous**
advection field. Persistence (force trails lingering in the air) lives
outside the shader so it compiles with a single input and is tuneable
without recompile.

**GLSL TOP itself:**

- **Pixel Shader**: `velocity_field.frag` (load via the GLSL TOP's `Pixel
  Shader` par pointing at the file on disk, or paste into a Text DAT and
  reference that).
- **Resolution**: `256 × 256`, Format `RGBA 16-bit float`.
- **Input 0**: `emitters_tex`. **No other inputs** — the shader declares
  `sTD2DInputs[0]` only; wiring an input 1 is neither needed nor valid.
- **Vectors 1 uniforms** (all expressions, reading `parent().par.*`):

| Uniform | Expression |
| --- | --- |
| `uNumEmitters` | `len(parent().par.Landmarks.eval().replace(',', ' ').split())` |
| `uRadius` | `parent().par.Fieldradius` |
| `uForceGain` | `parent().par.Fieldforce` |
| `uZGain` | `parent().par.Zgain` |
| `uVelStretch` | `parent().par.Velstretch` |
| `uStretchSpeedRef` | `parent().par.Stretchspeedref` |

The shader's `force_gain` input already bakes in `Burstgain` on the Python
side (`emitters_tex_script.py` computes `(emit + Burstgain*burst) * visible`
into the texture's row-1 alpha channel), so the shader no longer needs a
separate `uBurstGain` uniform.

**External persistence chain** (follows the GLSL TOP, output of the chain is
what the Force POP samples):

```mermaid
flowchart LR
    velocity_field[velocity_field<br/>GLSL TOP<br/>instantaneous]
    field_mix[field_mix<br/>Composite TOP<br/>Add]
    field_out([field_out<br/>Null TOP<br/>= what Force POP reads])
    field_fb[field_fb<br/>Feedback TOP<br/>target = field_out]
    field_decay[field_decay<br/>Level TOP<br/>multiplier = Fielddecay]

    velocity_field --> field_mix --> field_out
    field_out -. 1-frame delay .-> field_fb
    field_fb --> field_decay --> field_mix
```

`field_decay`'s RGB Multiplier = `parent().par.Fielddecay`. Same knob, same
semantics as before — at 0 the field snaps every frame, at 0.9 it trails for
about a second. The Force POP points at `field_out` (not `velocity_field`)
so it reads the persistent field, not the instantaneous one.

### 3. `emitters_chop` (Script CHOP) → `emitters_pop` (CHOP to POP)

Two-op chain. TD has no Script POP, so we stage the work in CHOP-land (where
Script CHOP has always been reliable) and hand off to a native CHOP-to-POP
converter for the final conversion. Script CHOP reshapes `lag1`'s
1-sample-many-channels output into an N-sample-few-channels shape with
attribute-style channel names; CHOP-to-POP then reads those channels into
the vec3 / scalar point attributes the downstream emission POP needs as
its emitter input.

**`emitters_chop` — Script CHOP:**

- Text DAT `emitters_chop_script`, paste `emitters_chop_script.py`.
- Create a **Script CHOP** named `emitters_chop`, Callbacks DAT =
  `emitters_chop_script`. No inputs — it reads `op('lag1')` by name from
  inside the callback.

Output CHOP has N samples and these channels (per landmark, one sample
each). **Note:** TD doesn't allow `[` or `]` in channel names (it silently
replaces them with `_`), so we use bare numbered suffixes and wire up
vec3 grouping explicitly on the CHOP-to-POP.

| Channel | Meaning |
| --- | --- |
| `P0`, `P1`, `P2` | Point position (x, y, z) |
| `v0`, `v1`, `v2` | Initial velocity handed to new particles (3D) |
| `w` | Spawn weight = `(emit + Burstgain * burst) * visible` |
| `id` | Landmark index, for per-limb color |

Drop a Trail CHOP on `emitters_chop` while debugging — you should see 5
samples, each tracking the matching landmark's live position/velocity.

**`emitters_pop` — CHOP to POP:**

- Create a **CHOP to POP** op named `emitters_pop`, plug `emitters_chop`
  into its CHOP input.
- Configure the parameters page (this is the part that needs to be
  explicit — CHOP-to-POP doesn't auto-group our channels without help):

    - **Connectivity**: `Points`. (Default is "Line Strip" which draws
      lines between consecutive samples — that's where the lines you see
      running through the emitter points are coming from. We want
      isolated points.)
    - **Specify Position**: `Off`. (Leaving it on generates extra points
      along a line; we want points directly from the CHOP samples.)
    - **Channels Selection**: `Specify Channels` (or whatever mode lets
      you define attribute rows).
    - **Channel Scope**: `*` (consider all channels from the CHOP).

- Add **four attribute rows** under the "New Attribute" section (use
  the `+` button to add rows):

    | Row | Attribute Name | Type / Size | Channel Scope | Default Value |
    | --- | --- | --- | --- | --- |
    | 0 | `P` | float, size 3 (vec3) | `P0 P1 P2` | `0 0 0` |
    | 1 | `v` | float, size 3 (vec3) | `v0 v1 v2` | `0 0 0` |
    | 2 | `w` | float, size 1 | `w` | `0` |
    | 3 | `id` | int, size 1 | `id` | `0` |

  Row 0's attribute name `P` is special — TD recognises it as the
  built-in point position, so the POP viewport will place points at the
  landmark coordinates. Rows 1–3 become per-point custom attributes.

  The **Default Value** is only used if the Channel Scope fails to match
  any channel. With our config it never falls back, but TD requires the
  field to be set. All zeros here is a safe failure mode — if something
  ever misfires, the worst you get is a dead emitter at origin, not a
  runaway spawn at a weird position. (Ignore the `0.5 0.5 0.5 1` or
  `v[0]` placeholders TD pre-fills when you first add a row — those are
  suggestions, type the real values over them.)

- Verify via right-click ▸ Info on `emitters_pop`: you should see one
  point per landmark with `P` (vec3), `v` (vec3), `w` (float), `id`
  (int) attributes. No warnings.

That's it. `emitters_pop` is now a 5-point POP with `P`, `v`, `w`, `id`
attributes — a stable, well-formed emitter feed for whatever spawn/respawn
POP op you hook up next. The downstream sim reads `P` as spawn position,
`v` as initial velocity, and `w` to choose which emitter fires on each
respawn event.

### 4. POP spawn + advect chain

All POPs, all inside `velocity_controller`. The real TD 2025 Particle POP
architecture is hub-based: **Particle POP itself handles spawn, lifetime,
and integration** — no separate source/advance/feedback ops needed. Forces
live in a feedback chain that adds to the `PartForce` attribute, which
Particle POP's Time Integration converts to `PartVel → P` internally each
frame.

```mermaid
flowchart LR
    emitters_tex[[emitters_tex<br/>Script TOP]]
    velocity_field[velocity_field<br/>GLSL TOP<br/>instantaneous]
    field_mix[field_mix<br/>Composite Add]
    field_out([field_out<br/>Null TOP])
    field_fb[field_fb<br/>Feedback TOP]
    field_decay[field_decay<br/>Level × Fielddecay]

    emitters_chop[[emitters_chop<br/>Script CHOP<br/>N samples]]
    emitters_pop[emitters_pop<br/>CHOP to POP<br/>N points]

    particle_pop[particle_pop<br/>Particle POP<br/>input: emitters_pop<br/>Target Update POP: force_null]
    lookup_pop[field_sample<br/>Lookup Texture POP<br/>writes fieldforce]
    noise_pop[curl_noise<br/>Noise POP<br/>writes curlforce]
    math_mix[add_to_force<br/>Math/Mix POP<br/>Partforce = fieldforce + curlforce]
    bounds_field[bounds_field<br/>Field POP<br/>Weight = 1 inside box]
    force_null[force_null<br/>Null POP<br/>= feedback target]

    render_null[render_null<br/>Null POP<br/>side-tee for rendering]
    particle_geo["particle_geo<br/>Geometry COMP<br/>instanced from render_null<br/>Translate ← P, Color ← id"]
    render_top([render_top<br/>Render TOP<br/>→ raster particle visual])

    emitters_tex --> velocity_field --> field_mix --> field_out
    field_out -. 1-frame delay .-> field_fb --> field_decay --> field_mix
    field_out -. TOP param .-> lookup_pop

    bounds_reflect[bounds_reflect<br/>GLSL POP<br/>clamp P to box,<br/>flip Partvel on wall hits]

    emitters_chop --> emitters_pop --> particle_pop
    particle_pop --> lookup_pop --> noise_pop --> math_mix --> bounds_reflect --> force_null
    force_null -. Target Particles Update POP ref .-> particle_pop
    particle_pop --> render_null --> particle_geo --> render_top
```

Two feeds into the sim: the **emitter point stream**
(`emitters_pop` → Particle POP's input) provides birth positions and the
`w` birth-rate attribute; the **force field** (`emitters_tex` →
`velocity_field` → sampled by Lookup Texture POP's TOP parameter) gets
baked into `Partforce` via the force chain that Particle POP reads back
through its `Target Particles Update POP` reference.

**Crucial wiring point:** every op in the force chain (Lookup Texture POP,
Math/Mix POP, Noise POP, …) takes the *particle stream* as its POP input.
Lookup Texture POP needs both a POP input (the particles, providing `P`
for sampling) AND the TOP reference (the field to sample) — assigning only
the TOP throws "not enough sources". Wire the previous op's output into
POP input 0 on every force-chain node.

**The Null POP at the end closes the loop.** The force chain doesn't
terminate at Render POP — it terminates at a Null POP that's referenced
in Particle POP's `Target Particles Update POP` parameter. That's how
per-cook `Partforce` accumulations actually get consumed by the next
integration. Skip this (leave `Target Particles Update POP` empty) and
particles emit but never react to any force in the chain. Render POP is
a side branch off Particle POP's direct output.

### Node-by-node setup

- **`particle_pop`** — [Particle POP](https://derivative.ca/UserGuide/Particle_POP)
    - **Input (emitters):** `emitters_pop`. (Particle POP has a single
      POP input for the birth source; the force feedback comes back in
      via the `Target Particles Update POP` parameter below, not a
      second cable.)
    - **Target Particles Update POP** *(on the Particles page)*: set to
      the Null POP at the end of the force chain (`force_null` in the
      diagram). This is how the feedback loop closes — that Null POP's
      output is what Particle POP reads back as "the current particle
      state with all `Partforce` contributions summed" on the next cook.
      **If this is empty, particles emit but don't react to any force
      chain** — they just fall through Time Integration with no forces
      applied beyond your Initial Velocity.
    - **Emission from**: `Birth Attribute`. **Input Birth Attribute**:
      `w`. Each input point then emits `int(w)` particles per frame, so
      a silent frame with all `w=0` spawns nothing; a whip with `w≈6`
      on one limb spawns ~6 particles from that limb per frame.
    - **Randomize Input Points**: `On`. Without this, successive births
      cycle through the input points deterministically, which reads as
      mechanical.
    - **Attributes** page: transfer `v` → **`StartPartvel`** so newborn
      particles inherit the spawning limb's current velocity (fast-moving
      limbs throw particles with initial momentum, not from rest).
      `P` is transferred automatically (it's the built-in position).

      *Gotcha:* don't rename it to `PartVel` — that's a reserved
      attribute name Particle POP uses for the current-velocity state
      it updates every cook. TD will warn and auto-rename to
      `StartPartvel` anyway. The `Start*` prefix is the convention for
      "seed value at birth"; use it directly to avoid the warning.
      See *"Reserved attribute names"* below.

    - **Initial Velocity** *(on the Particles page)*: `0 0 0`. This is
      the fallback when a particle is born without a `StartPartvel`
      attribute — since we always provide `StartPartvel` via the
      Attributes transfer, the fallback never fires. A nonzero value
      here would add to every particle's starting velocity uniformly,
      which isn't what you want.
    - **Life Expect**: `parent().par.Lifemax`.
      **Life Variance (Fraction)**: `1 - parent().par.Lifemin / parent().par.Lifemax`.
      With those two, effective life range ≈ `[Lifemin, Lifemax]`.
    - **Maximum Particles**: `10000` default is fine (headroom for
      ~5 emitters × ~6 peak `w` × 60 fps × 3 s life ≈ 5400 alive).
    - **Play**: `On`. This is what drives the per-cook integration
      (`Partforce → Partvel → P`). There's no separate "Time
      Integration" toggle in the UI — Play On is the equivalent. Use
      Play Off to pause the sim.

#### Reserved attribute names on Particle POP

Particle POP owns these names internally — they're the per-cook sim
state and get overwritten every frame by Time Integration. Don't write
directly to them; use the `Start*` prefix for seed values at birth:

| Reserved (internal state) | Seed equivalent (user-provided at birth) |
| --- | --- |
| `Partvel` — current velocity | `StartPartvel` — initial velocity |
| `Partmass` — current mass | `StartPartmass` — initial mass |
| `Partage` — current age | (not seeded; always starts at 0) |
| `Partforce` — per-cook force accumulator | (not seeded; resets each cook) |
| `Partdeath` — death flag | (not seeded; usually set via kill ops) |

If you accidentally transfer an input attribute to a reserved name, TD
prepends `Start` automatically and emits a warning. The renamed
attribute works correctly for seeding, but the cleaner move is to set
the target name explicitly. Custom attributes with no reserved collision
(`w`, `id`, your own `fieldforce`, etc.) pass through untouched.

- **`field_sample`** — [Lookup Texture POP](https://docs.derivative.ca/Lookup_Texture_POP)
    - **Attribute Class**: `Point`
    - **TOP**: `field_out` (the Null TOP at the end of the persistence
      chain — *not* the raw `velocity_field`).
    - **Lookup Index Attribute U / V**: `P(0)` / `P(1)`. W empty.
    - **Lookup Index Units**: `Normalized` (our P is in 0..1, matches).
    - **Input Extend Mode**: `Zero` on all axes (particles outside the
      field get no force rather than wrapping).
    - **Interpolate**: `On` (bilinear).
    - **Channel Mask**: R, G, B on; A off (we want RGB → vec3, alpha is
      debug-only from the shader).
    - **Output Attribute Scope**: `fieldforce`, size 3, float.
    - The "Attribute already exists and will be overwritten" warning is
      expected — feedback loop means `fieldforce` persists from last
      cook. Harmless.

- **`curl_noise`** — [Noise POP](https://derivative.ca/UserGuide/Experimental:Noise_POP), curl output
    - **Noise page:**
        - **Noise Lookup Attribute**: `P` (each particle samples noise
          at its own position, so the drift has spatial coherence)
        - **Type**: `Simplex 4D (GPU)`
        - **Noise Size**: `3` (vec3 field, needed for 3D curl)
        - **Period**: `parent().par.Curlscale`
        - **Amplitude**: `parent().par.Curlgain`
        - **Harmonics / Spread / Gain**: `2 / 2 / 0.7` defaults are
          fine; bump Harmonics to 4 for more chaotic turbulence
        - **Positive Only**: `Off` (curl needs both directions)
        - **Attribute Class**: `Point`
    - **Output page:**
        - Enable **Curl** (or "Curl 3D" depending on label)
        - Name the output attribute **`curlforce`** (not the default
          `NoiseCurl` — keeps the downstream Math/Mix expression cleaner)

- **`add_to_force`** — Math/Mix POP (or Math POP)
    - Sums `fieldforce + curlforce → Partforce`. That's where both
      contributions finally land on the reserved `Partforce` attribute
      that Particle POP's integration consumes.
    - Operation: `Add`. Inputs: `fieldforce`, `curlforce`. Output: `Partforce`.
    - Put this AFTER both the Lookup Texture POP and the Noise POP
      (chain order: `particle_pop → field_sample → curl_noise → add_to_force → bounds_field (opt) → force_null`).

- **`bounds_field`** — [Field POP](https://derivative.ca/UserGuide/Field_POP) for bounding-box death
    - **Shape**: `Box`
    - **Translate**: `0.5 0.5 0.0` (center of the MediaPipe (0..1, 0..1)
      plane, z=0 for the hip-center reference)
    - **Scale**: `1.0 1.0 1.0` (1×1×1 box — x/y cover the full 0..1
      range, z covers −0.5 to +0.5 which is the typical MediaPipe z
      extent; tighten or loosen depending on how much depth you want
      to allow particles to drift into/out of)
    - **Invert**: `Off` — Weight=1 inside the box, 0 outside, which is
      what we want for the "inside = alive" semantic.
    - The op outputs a `Weight` attribute per particle.
    - **Kill-outside pattern:** follow `bounds_field` with a small
      Math/Mix POP (or Attribute POP) that sets
      `Partdeath = max(Partdeath, 1 - Weight)`. Particles outside the
      box get `Weight=0` → `1 - Weight = 1` → marked dying, Particle POP
      removes them on the next integration cook.
    - **Soft-mask pattern (alternative):** if you don't want to kill
      out-of-bounds particles but just let them drift freely without
      force, multiply `Partforce` by `Weight` instead. Particles outside
      the box get zero force and coast with residual velocity, but
      aren't removed.
    - Skip this node entirely if your `Lifemax` already short enough
      that off-screen particles age out before becoming visible noise.

- **Optional: per-emitter `Force Radial POP`** — if you want each limb
  to *also* push particles radially away from it (in addition to the
  velocity-field advection), chain in a
  [Force Radial POP](https://derivative.ca/UserGuide/Force_Radial_POP).
  Axial mode along the limb's velocity vector gives a directional push
  on top of the field sampling. Not needed for the basic effect — the
  Lookup Texture POP path already captures all directional motion via
  the shader's kernel — but it adds a stronger "shove" feel near each
  limb if you want more impact.

- **`force_null`** — Null POP, end of force chain
    - Nothing to configure — it's just a passthrough. Its job is to be
      the op `particle_pop`'s `Target Particles Update POP` points at.

### Rendering — Geometry COMP instancing

There's no Render POP. TD renders POPs by using a [Geometry COMP with
instancing](https://docs.derivative.ca/Geometry_COMP) — one instance of
a small piece of geometry per particle, position/scale/color driven by
POP attributes. A Render TOP then rasters the instanced scene.

**Setup:**

1. **`particle_geo`** — Geometry COMP inside `velocity_controller`.
2. Inside `particle_geo`, drop a minimal per-instance shape:
    - `Rectangle SOP` for flat sprite quads (cheapest, textures well), or
    - `Circle SOP` for round billboards, or
    - `Sphere SOP` (low resolution) for volumetric dots.

   Wire it to an `Out SOP` so the COMP has renderable content.
3. On `particle_geo`'s **Instance page**:
    - **Instancing**: `On`
    - **Instance OP**: point at your final particle POP — usually a
      `render_null` teed off `particle_pop`'s direct output (*not* the
      force-chain `force_null`, which is for feedback only).
    - **Translate X**: `P` attribute, component 0
    - **Translate Y**: `P` attribute, component 1
    - **Translate Z**: `P` attribute, component 2
    - **Scale X / Y / Z**: either a small constant (e.g. `0.01`) for
      uniform particle size, or bind to a per-particle `Partage` or
      custom `size` attribute for age-driven shrink.
    - **Color R / G / B**: bind to `id` via a Lookup Texture TOP for
      per-limb palette, or to `Partage` for an age gradient.
4. **`particle_cam`** — Camera COMP with orthographic projection so
   MediaPipe's normalized (0..1) UV space maps straight to screen.
   - **Projection**: `Orthographic`
   - **Orthographic Width**: `1.0`, centered so the view covers (0,0)
     to (1,1). (Or place camera at z = some offset looking at z=0.)
5. **`render_top`** — Render TOP.
   - **Geometry**: `particle_geo`
   - **Camera**: `particle_cam`
   - **Resolution**: target display size, e.g. `1920×1080`.

`render_top`'s output is then what feeds into the screen-space smear
chain (Composite + Feedback TOP) to produce `out_render`.

**Per-instance attribute mapping cheat-sheet:**

| Instance slot | POP attribute | Purpose |
| --- | --- | --- |
| Translate X / Y / Z | `P(0)` / `P(1)` / `P(2)` | 3D particle position |
| Scale X / Y / Z | `Partage` or custom `size` | Age-driven shrink = life tail |
| Rotate | derive from `Partvel` if you want motion-aligned sprites | optional |
| Color | `id` via lookup TOP, or `Partage` gradient | per-limb palette |

**Quick sanity check before full instancing:** you can also wire any
POP directly into a Render TOP's `POPs` list — that renders each point
as a single pixel (no instanced geometry). Fast "are particles alive
and moving?" verification before setting up the instancing plumbing.

> **Why the Lookup Texture POP *and* the Noise POP?** The Lookup Texture POP
> applies directed motion from the limb velocity field (particles near a
> moving limb inherit direction from that limb). The Noise POP in curl mode
> gives particles somewhere to drift when the performer is still — otherwise
> the visual freezes on every pause. Default `Curlgain` is low (0.15) so
> limbs dominate when someone's actually moving.

### 5. Screen-space feedback smear

On top of `render_pop`'s TOP output:

```mermaid
flowchart LR
    render_pop[render_pop<br/>raster TOP]
    composite_add[composite_add<br/>Composite TOP Add]
    out_render([out_render<br/>Out TOP])
    feedback_top[feedback_top<br/>Feedback TOP]
    feedback_xform[feedback_xform<br/>Transform zoom<br/>= Feedbackzoom]
    feedback_level[feedback_level<br/>Level multiply<br/>= Feedbackfade]
    switch_enable[switch_enable<br/>Switch TOP<br/>= Feedbackenable]

    render_pop --> composite_add --> out_render
    out_render -. 1-frame delay .-> feedback_top
    feedback_top --> feedback_xform --> feedback_level --> switch_enable --> composite_add
```

The Switch TOP lets you kill the whole feedback branch with a single toggle
(`parent().par.Feedbackenable`) without detaching cables.

Keep `Feedbackfade` around 0.9–0.95 and `Feedbackzoom` barely above 1.0
(1.002–1.01). That's the optical-flow smear look: recent particle positions
persist and slowly dim/drift, which reads as velocity trails *behind* the
particles on top of the directed motion they already have.

## Resolution & aspect

Three resolutions in the pipeline, each serving a different role — they do
NOT all need to match each other.

| Op | Resolution | Role | Aspect considerations |
| --- | --- | --- | --- |
| `emitters_tex` | `N × 2` (e.g. `5 × 2`) | Lookup table sampled by the shader. Not displayed. | None — aspect is meaningless for a texture you index by explicit UV. |
| `velocity_field` + persistence chain | `256 × 256` default | Sampling fidelity of the 2D force field. Both emitters and particles live in 0..1 UV, so this is about how finely gaussians splat, not about matching a viewport. | Aspect doesn't matter. Drop to `128 × 128` if GPU-bound; go to `512 × 512` for finer splats from tight kernels. Above that is wasted — a sigma-0.12 gaussian doesn't carry information past ~512. |
| `render_pop` output → `out_render` | Match your display target (e.g. `1920 × 1080`) | What actually hits the projector / downstream stack. | Match your **display** aspect. Use an orthographic camera on the Render POP with its view box covering `(0..1, 0..1)` so particle `P.xy` lands correctly at all viewport aspects. |

**Common pitfall — source ≠ display aspect.** MediaPipe emits landmarks in its
**source-image** 0..1 space. If your camera is 16:9 but your projection is
4:3 (or vice versa), particle positions will stretch visibly. `painting_controller`
solves this with `Sourceaspect` / `Viewaspect` pars plus letterbox logic inside
`painting_logic.wrists_in_bounds`. This controller ships without that, because
for free-floating particles the stretch is usually unnoticeable. If your
installation needs it, either:

- Add a `Math CHOP` / `Stretch CHOP` upstream of `in1` that remaps landmark
  `x, y` from source aspect into viewport aspect, or
- Port the `_remap_for_aspect()` helper from `painting_logic.py` into
  `velocity_logic.py` and apply it in `update_landmark()`.

**Subtle shader aspect detail.** Inside `velocity_field.frag` the gaussian is
`exp(-|d|² / 2r²)` where `d` is in raw UV. That's round in UV space, which
means slightly elliptical on a non-square render. Rarely visible at the
default `Fieldradius`; only worth aspect-correcting if you see it as a flaw.

## Emission shape — 2D velocity-aligned scatter

By default `emitters_chop` doesn't just output one point per landmark.
It outputs `Spawncount` (default 12) sub-emitter points per landmark,
scattered pseudo-randomly within a **2D region aligned with the limb's
xy velocity direction**. The region has two independent extents:

- **Along-velocity (length)**: scales from `Spawnspreadmin` (default
  `0.02` UV) at rest up to `Spawnspread` (default `0.08` UV) when the
  limb reaches `Spawnspreadref` (default `0.8` UV/s). This is what
  stretches the emission source into a "streak" when limbs move fast.
- **Perpendicular (width)**: scales similarly but multiplied by
  `Spawnperpratio` (default `0.3`). Smaller than along-axis so the
  region elongates into an ellipse/rectangle rather than staying square.

Shape behaviour:

- **At rest** (speed = 0): both extents collapse to `Spawnspreadmin`,
  producing a small square-ish "lump" of sub-emitters around the limb.
  Matches the flow-field shader's gaussian-at-rest kernel.
- **At full speed**: along extent = `Spawnspread`, perp extent =
  `Spawnspread × Spawnperpratio`. An elongated ellipse/rectangle
  aligned with motion direction. Matches the shader's
  velocity-stretched kernel.
- **In between**: linear ramp on speed, so gentle motion gives a
  gently-elongated lump, fast motion gives a pronounced streak.

Sub-emitter positions within the region are pseudo-random with a **fixed
seed**, so sub-emitter `k` always lands at the same relative position
within the region — no per-cook jitter, just a stable scatter that
rotates and stretches with the limb direction. That keeps the visual
coherent instead of noisy.

Edge sub-emitters (large perpendicular offset) also get a fan kick on
their initial velocity, so the emission region doesn't just *shape*
the spawn pattern — it also *aims* particles outward at the edges,
giving the wavefront a cone-like expansion as it travels.

| Par | Default | Effect |
| --- | --- | --- |
| `Spawncount` | 12 | Sub-emitters per limb inside the emission region. 1 = single point. Higher = denser fill of the region. |
| `Spawnspread` | 0.08 | Maximum **along-velocity** extent of the emission region at full speed (streak length). |
| `Spawnspreadref` | 0.8 | Speed (UV/s) at which `Spawnspread` is fully engaged. Below, size scales linearly. 0.8 engages full size at gentle hand-waving; raise to 2–3 for "only whips open the region"; lower to 0.3 for "any motion = full size". |
| `Spawnspreadmin` | 0.02 | Minimum extent at rest (lump size). Gives emission a small 2D shape even when the limb is stationary. 0 = collapse to point at rest. |
| `Spawnperpratio` | 0.3 | Ratio of perpendicular to along-velocity extent at speed. 0 = pure streak along motion direction, 1 = square region, 0.3 = clearly elongated streak with some width. Lower for sleeker streaks, higher for rounder clouds. |
| `Spawnvelscale` | 0.3 | Multiplier on limb velocity written to `StartPartvel`. 1.0 = particles fly off-screen fast on whips; 0.3 = gentle launch, velocity field continues to push over time. |
| `Spawnvelfan` | 0.5 | Angular fan on `StartPartvel` — edge sub-emitters get a perpendicular kick scaled by their position along the spread line times limb speed. Center particle stays parallel to motion; edges tilt outward. 0 = parallel wavefront (all particles fly exactly the same direction), 0.25 = subtle, 0.5 = ~27° edge tilt (visible cone), 1.0 = ~45° (strong fan). Combined with curl noise, this gives organic-looking wavefront curvature instead of a straight line. |

Total emission rate **scales with `Spawncount`** — each sub-emitter
independently emits `int(w)` particles per frame (Particle POP's
integer-truncation birth rule means we can't divide `w` across
sub-emitters without losing everything to rounding). So doubling
`Spawncount` doubles total particles/sec. Budget accordingly — raise
Particle POP's **Maximum Particles** ceiling if you crank `Spawncount`
past ~15 at peak `w`. Rough formula:

```
peak_alive ≈ n_landmarks × Spawncount × peak_w × fps × Lifemax
```

With defaults (5 × 12 × 5 × 60 × 3) that's 54000 at max whip across all
limbs — well over the 10000 Max Particles default. Either:
- Raise Max Particles to `~100000`
- Reduce `Spawncount` to `6`
- Reduce `Burstgain` to `3` (caps peak `w` lower)
- Shorten `Lifemax` to `1.5s`

**Tuning recipes:**

- **Want a single tight stream per limb (old behaviour):** `Spawncount = 1`.
- **Want particles to linger near the limb instead of flying off:** drop
  `Spawnvelscale` toward `0.1`.
- **Want violent whips that genuinely throw particles far:** raise
  `Spawnvelscale` to `0.7–1.0`, and raise `Spawnspread` to `0.12` so
  the wavefront is wider.
- **Wavefronts too wide / particles spawning off the limb:** drop
  `Spawnspread` to `0.04` or raise `Spawnspreadref` to `3–4` so full
  width only engages on extreme motion.

## "Water" vs "vacuum" feel

> **Critical diagnostic first:** if particles are flying fast and
> scattering wildly, you almost certainly don't have **Velocity Damping**
> set on your Particle POP. The flowfield applies a constant force; with
> no damping, that force accumulates into unbounded velocity over
> particle lifetime. The terminal velocity of a particle is approximately
> `Fieldforce / VelocityDamping`. With `VelocityDamping = 0` (Particle
> POP's default), there is no terminal — particles keep accelerating
> until they die. Check it in the Textport:
>
> ```python
> pp = op('/project1/velocity_controller/particle1')  # your Particle POP
> print("Velocity Damping:", pp.par.velocitydamping.eval())
> print("Initial Drag:    ", pp.par.initialdrag.eval())
> ```
>
> If Velocity Damping is `0`, **nothing on the `velocity_controller` COMP
> can save you.** Set both on Particle POP:
>
> ```python
> pp.par.velocitydamping = 3.0    # strong water-like drag
> pp.par.initialdrag    = 0.5
> ```
>
> These live on Particle POP itself, not on velocity_controller, which is
> why `install_velocity_params` can't set them.

By default, particles live in a near-vacuum: the flow field pushes them
with no friction, so they keep flying until their life runs out. That
feels great for explosive/energetic effects but wrong for anything
meant to read as "swimming through a medium" or "paint dispersing in
water".

Three knobs convert vacuum → water:

1. **Particle POP → Velocity Damping** (on the Particle POP itself, NOT
   on velocity_controller). `1.5` is a good starting point: it multi­
   plicatively reduces `Partvel` each frame, so a particle at 1 UV/s
   slows to ~0.22 UV/s in one second and settles within 2–3 seconds.
   At `0` particles never lose momentum; at `3+` they feel like they're
   moving through molasses. This is *the* biggest dial for the overall
   feel.
2. **Particle POP → Initial Drag** to `0.2`. Gives every newly-spawned
   particle a baseline friction attribute so it doesn't start life in
   zero-g while the sim is trying to damp it.
3. **`Fieldforce` on velocity_controller → drop to `0.4`** (default I
   just lowered). With damping cranked, you want less aggressive
   acceleration from the flowfield, or particles still get flung away
   before damping can catch them.

Recipe summary:

| Feel | Velocity Damping | Initial Drag | Fieldforce | Spawnvelscale |
| --- | --- | --- | --- | --- |
| Vacuum (old default) | 0 | 0 | 1.5 | 0.3 |
| Light breeze | 0.5 | 0.1 | 0.7 | 0.2 |
| **Water (new default)** | **1.5** | **0.2** | **0.4** | **0.15** |
| Molasses | 3.0 | 0.5 | 0.2 | 0.1 |

Swap rows to taste. `Velocity Damping` and `Initial Drag` are both on
the Particle POP's Particles page; the rest live on the COMP pars.

## Bounding-box containment (reflection)

Out of the box, particles that escape the visible area just keep flying
until their life runs out — they disappear off-screen. The `bounds_field`
Field POP + Partdeath pattern described earlier *kills* them when they
exit, but the user experience is a thinning cloud of particles as they
leave the frame. Most installations want **reflection** instead:
particles bounce off invisible walls and stay contained.

### Setup — `bounds_reflect` GLSL POP

Add as the **last op** in the force chain, immediately before the
`force_null` that Particle POP points at via `Target Particles Update POP`.

1. Create a **GLSL POP** named `bounds_reflect` inside
   `velocity_controller`.
2. Point its Program parameter at `shaders/bounds_reflect.glsl` (or
   paste the code into a Text DAT and reference it). The shader
   clamps each particle's `P` inside a 3D axis-aligned box and flips
   `Partvel` when the particle hits a wall.
3. Declare uniforms on the GLSL POP's Vectors 1 / Scalars page, binding
   each to the corresponding parent par:

    | Uniform | Binding | Meaning |
    | --- | --- | --- |
    | `uBoxMin` | `(parent().par.Boundsminx, parent().par.Boundsminy, parent().par.Boundsminz)` | Min corner of the box in particle space |
    | `uBoxMax` | `(parent().par.Boundsmaxx, parent().par.Boundsmaxy, parent().par.Boundsmaxz)` | Max corner |
    | `uBounce` | `parent().par.Boundsbounce` | 0 = stick, 1 = elastic, 0.4 = water-like |
    | `uMargin` | `parent().par.Boundsmargin` | Small inset from walls |

4. Wire `bounds_reflect` into the force chain as the last stage before
   `force_null`:

    ```
    Particle POP → Lookup Texture POP → Noise POP → Math/Mix POP
                 → bounds_reflect GLSL POP → force_null
    ```

5. **Verify**: the GLSL POP's output POP should show particles clamped
   to `(0..1, 0..1, -0.5..+0.5)` in particle-space. Drop a Null POP
   after it, right-click ▸ Info — the `P` attribute's min/max across
   all particles should match your bounds values. Move a limb
   aggressively — particles hitting walls should visibly reverse
   direction rather than escaping.

> **Syntax caveat**: the shader file has placeholder comments like
> `/* READ P */` and `/* WRITE P */` on the attribute-access lines. The
> per-point read/write API of TD's GLSL POP varies across builds (some
> use `inPointAttribs.P` members, some helper functions, some require
> declaring the attribute layout on the op's pages). The shader's
> reflection math is standard GLSL and doesn't change — only those
> four attribute-access lines need adapting. Check
> docs.derivative.ca/GLSL_POP for your build's exact syntax.

### Simplest containment — kill-outside via Field POP + Math POP

No GLSL and no force ops needed. Uses the existing `bounds_field` Field
POP you may already have in the chain:

1. **Field POP** (`bounds_field`): shape Box, Translate `(0.5, 0.5, 0.0)`,
   Scale `(1.0, 1.0, 1.0)`, Invert Off. Outputs a `Weight` attribute = 1
   inside the box, 0 outside.
2. **Math POP** (or Attribute POP) after it: set `Partdeath = 1 - Weight`.
   Particles outside the box get `Partdeath = 1` → Particle POP kills
   them on the next integration.

This doesn't *reflect* — particles just die and disappear when they
leave the box. But it **contains** the visual, which is usually enough:
you get a steady stream of fresh particles spawning at limbs and aging
out cleanly, with nothing escaping off-screen. Combined with a short
`Lifemax` and the tighter velocity defaults, the scene stays bounded
without any shader gymnastics.

Use this as the baseline; upgrade to `bounds_reflect` GLSL POP later if
you specifically want bounce behaviour.

### Force-based soft containment (6 Force Radial POPs)

If adapting the shader is fiddly but you want reflection-like behaviour
without kills, place **six Force Radial POPs in Planar mode** around
the box, each pushing inward:

| Wall | Translate | Direction | Radius (rolloff) | Strength |
| --- | --- | --- | --- | --- |
| Left  | `(0.0, 0.5, 0.0)` | `(+1, 0, 0)` | `0.1` | `8` |
| Right | `(1.0, 0.5, 0.0)` | `(-1, 0, 0)` | `0.1` | `8` |
| Bottom | `(0.5, 0.0, 0.0)` | `(0, +1, 0)` | `0.1` | `8` |
| Top | `(0.5, 1.0, 0.0)` | `(0, -1, 0)` | `0.1` | `8` |
| Back | `(0.5, 0.5, -0.5)` | `(0, 0, +1)` | `0.1` | `8` |
| Front | `(0.5, 0.5, +0.5)` | `(0, 0, -1)` | `0.1` | `8` |

Chain all six into the force chain between your existing Math/Mix POP
and `force_null`. Combined with strong `Velocity Damping`, this makes
particles slow dramatically as they approach walls, reversing direction
gradually rather than bouncing instantaneously. Uses only native ops
but gets you 6 nodes instead of 1.

## Velocity-field resolution (if the field looks chunky)

The `velocity_field` GLSL TOP resolution controls how finely the
gaussian splats get resolved. Default `256 × 256` looks tessellated when
the splat radius is tight (after the `Fieldradius` default dropped to
0.09 and close-up limbs shrink past `0.07`, the gaussian's 3-sigma
spread covers only ~25 pixels at 256, which can read as blocky).

- **Bump to `512 × 512`** on both `velocity_field` (the GLSL TOP) AND
  the persistence chain that follows (`field_mix`, `field_decay`,
  `field_out`). The follow-on TOPs inherit their resolution from
  `velocity_field` by default, so usually only the GLSL TOP needs
  resizing — check the Common page of each TOP in the chain if in
  doubt. 512² is the sweet spot; 1024² is wasteful at `Fieldradius` < 0.2.
- Set `Lookup Texture POP` → **Interpolate: On** (already documented; double-check).
- If still chunky, shrink `Fieldradius` further (0.06 gets tight; 0.04
  reads as per-limb pinpoint). Smaller radius + higher resolution = the
  smoothest look.

## Full settings rundown

All current defaults. These live as custom parent pars on the
`velocity_controller` COMP (installed via `install_velocity_params.py`,
forcibly re-applied via `reset_velocity_params.py`).

> **Installer note:** `install_velocity_params.py` is idempotent —
> existing pars are **not** overwritten. When default values change in
> the codebase, running the installer again won't update them on
> already-installed COMPs. To apply current defaults, either: (a) run
> `reset_velocity_params.py` (forcibly overwrites every par), or (b)
> use the right-click "Reset to Default" on individual pars after
> re-running the installer.

### Sensing page

| Par | Default | Range | What it does |
| --- | --- | --- | --- |
| `Landmarks` | `left_wrist right_wrist left_ankle right_ankle nose` | — | Space/comma-separated list of MediaPipe landmark names to track. Script CHOP rebuilds state on change. |
| `Visibilitythreshold` | `0.5` | 0..1 | Output gate. Below this, `<L>:visible` is 0 and emit/burst fade. |
| `Trustthreshold` | `0.75` | 0..1 | Commit gate. Only above this does `last_good` update and velocity math run. Between gate and trust = marginal zone (output last-good, visible=1). |
| `Velocitysmooth` | `0.08` s | 0..0.5+ | One-pole EMA time constant on raw velocity. Shorter = snappier, noisier. |
| `Accelsmooth` | `0.05` s | 0..0.5+ | Same for acceleration. |
| `Speedscale` | `2.5` UV/s | 0.1..10+ | Raw speed / scale = emit (clamped 0..1). Lower = more emit at gentle motion. |
| `Accelthreshold` | `8.0` | 0..50+ | Min accel magnitude that arms a burst. |
| `Accelscale` | `40.0` | 1..200+ | Accel above threshold / scale = burst amplitude (clamped 0..1). |
| `Burstdecay` | `0.35` s | 0..2+ | Exponential tail length of burst envelope. |
| `Maxjump` | `0.30` UV/frame | 0..1 | Teleport-rejection threshold inside a trusted stream. 0 disables. |
| `Settleframes` | `5` | 0..30 | Post-dropout grace period (frames) where Maxjump is skipped to let MediaPipe lock on. |
| `Zspeedweight` | `0.35` | 0..1 | How much vz/az contribute to speed & accel magnitudes (emit/burst drivers). 1 = full 3D, 0 = z doesn't trigger emission at all. |
| `Blendtime` | `0.08` s | 0..1+ | Lag CHOP time constant for post-sensing smoothing. |

### Renderer page

| Par | Default | Range | What it does |
| --- | --- | --- | --- |
| **Emission** | | | |
| `Spawnrate` | `5000` pts/s | 0..50000 | Currently informational (Particle POP reads `w` as birth attribute; this is a reserved par for future total-rate scaling). |
| `Burstgain` | `6.0` | 0..20+ | Multiplier on `burst` when mixing into the spawn-weight `w = emit + Burstgain × burst`. |
| **Emission region (2D scatter)** | | | |
| `Spawncount` | `12` | 1..40+ | Sub-emitters per landmark within the region. 1 = single-point. Scales total particle count linearly — watch Max Particles. |
| `Spawnspread` | `0.08` UV | 0..0.3 | Max along-velocity extent at full speed (streak length). |
| `Spawnspreadref` | `0.8` UV/s | 0.1..10+ | Speed at which full `Spawnspread` is engaged. |
| `Spawnspreadmin` | `0.02` UV | 0..0.1 | Minimum extent in both axes at rest (lump size). Matches the flow-field shader's gaussian-at-rest. |
| `Spawnperpratio` | `0.3` | 0..1 | Perp/along extent ratio at speed. 0 = pure streak, 1 = square, 0.3 = elongated with width. |
| `Spawnvelscale` | `0.15` | 0..1.5+ | Multiplier on limb velocity → `StartPartvel`. 0.15 = gentle launch (flowfield does the work). 1.0 = particles fly off fast. |
| `Spawnvelfan` | `0.5` | 0..2 | Perpendicular fan on edge sub-emitters' initial velocity. 0 = parallel, 0.5 = ~27° cone, 1.0 = ~45°. |
| **Flow field** | | | |
| `Fieldradius` | `0.05` UV | 0.01..0.5 | Base gaussian sigma. 3-sigma spread = ~15% of frame at default. |
| `Fieldforce` | `0.4` | 0..10+ | Global multiplier on the velocity vector written into the field. 0.4 = water-feel (default). Raise to 1.5+ for vacuum/explosive feel; lower for barely-drift. |
| `Fielddecay` | `0.30` | 0..0.99 | Level TOP multiplier in the persistence chain. 0 = instantaneous; higher = longer force trails in the air. |
| `Zgain` | `0.2` | 0..3+ | Depth → splat size. Negative z (toward camera) scales radius up, clamped to 1.8× in shader. |
| `Zforceweight` | `0.05` | 0..1 | Scales `vz` on **both** render-side paths: (a) into the velocity-field texture (dampens z-force on live particles), and (b) into `StartPartvel.z` (dampens z-velocity on newborn particles). MediaPipe's depth is noisy even during pure horizontal motion — without this, particles would drift forward/back on sideways gestures. 0 = completely flat 2D, 1 = full 3D with raw jitter. **Separate from `Zspeedweight`** (sensing-side, emit/burst). |
| `Velstretch` | `0.8` | 0..3+ | Anisotropic kernel elongation along velocity direction. Makes fast limbs throw a longer cone of force. |
| `Stretchspeedref` | `2.0` UV/s | 0.1..10+ | Speed at which full `Velstretch` applies. |
| **Noise drift** | | | |
| `Curlgain` | `0.5` | 0..2+ | Curl noise amplitude. Bends wavefronts organically per-position. 0.5 = meaningful bending. Crank for turbulent look. |
| `Curlscale` | `0.5` | 0.05..20+ | Noise period. **Critical**: must be < particle cloud extent (~1 UV), otherwise the whole cloud samples one curl direction and drifts consistently. 0.5 gives varied curl across the cloud that averages to zero. Lower = tight micro-turbulence; higher than 1 = everything drifts together. |
| **Life** | | | |
| `Lifemin` | `0.8` s | 0.1..20+ | Minimum particle lifetime. |
| `Lifemax` | `2.0` s | 0.1..20+ | Maximum particle lifetime (drives Particle POP Life Expect + Variance). |
| **Bounding box (containment via bounds_reflect GLSL POP)** | | | |
| `Boundsminx/y/z` | `0 / 0 / -0.5` | — | Min corner of the containment box in particle space. |
| `Boundsmaxx/y/z` | `1 / 1 / +0.5` | — | Max corner. |
| `Boundsbounce` | `0.4` | 0..1 | Restitution on wall hits. 0 = stop dead, 1 = elastic, 0.4 = water-like. |
| `Boundsmargin` | `0.0` UV | 0..0.1 | Inset from walls before clamping (stops particles from clipping into walls visually). |
| **Screen-space feedback smear** | | | |
| `Feedbackenable` | `On` | toggle | Whole post-render feedback branch enabled. |
| `Feedbackfade` | `0.92` | 0..0.999 | Per-frame multiply on the feedback texture. Higher = longer ghosts. |
| `Feedbackzoom` | `1.003` | 0.95..1.05 | Per-frame zoom on the feedback texture for subtle drift. |

### Particle POP parameters (on Particle POP inside your render network, NOT on the velocity_controller COMP)

These aren't installed by `install_velocity_params.py` — you set them
manually on Particle POP itself:

| Par | Recommended value | Why |
| --- | --- | --- |
| Target Particles Update POP | `force_null` | Feedback target — closes the force chain loop. |
| Create Point Primitives | `On` | Needed for rendering. |
| Maximum Particles | `~100000` | Budget for 12 sub-emitters × 5 limbs × peak_w × 60fps × 2s life ≈ 43k alive. |
| Emission from | `Birth Attribute` | Uses per-point `w` instead of a global rate. |
| Input Birth Attribute | `w` | |
| Randomize Input Points | `On` | Otherwise particles cycle through input points mechanically. |
| Life Expect | `parent().par.Lifemax` | |
| Life Variance (Fraction) | `1 - parent().par.Lifemin / parent().par.Lifemax` | |
| Initial Velocity | `0 0 0` | Fallback only — real velocity comes from `StartPartvel` attribute. |
| Initial Mass | `1` | |
| **Initial Drag** | **`0.2`** | Per-particle drag — gives every particle a baseline friction so they slow as they travel (water feel). Raise toward `0.5` for heavier viscosity. |
| **Velocity Damping** | **`1.5`** | Per-frame multiplicative velocity reduction. This is **the** water-feel knob. `0` = vacuum (particles coast forever), `1–2` = strong viscous damping (particles decay to rest quickly), `3+` = molasses. Combine with a low `Fieldforce` for the "water" look. |
| Play | `On` | Drives per-cook integration. No separate "Time Integration" toggle. |

On the **Attributes** page: transfer `v` → `StartPartvel` (not `PartVel`
— that's reserved).

## Quick tuning checklist

1. **Hands not emitting enough particles at gentle motion.** Drop `Speedscale`
   (smaller → full emit at lower speed). Or raise `Spawnrate`.
2. **Bursts not popping on whips.** Drop `Accelthreshold` until the burst
   channel pulses visibly on a Trail CHOP; tune `Accelscale` so a hard whip
   reaches 1.0 but gentle waves stay below 0.3.
3. **Particles freeze when performer stops.** Raise `Curlgain` so idle noise
   is visible.
4. **Field feels laggy / pushes particles off-camera.** Lower `Fieldforce`
   and/or `Fielddecay`.
5. **Screen is a solid white after a few seconds.** `Feedbackfade` too high —
   pull it down toward 0.88.
6. **Particles spawn in the corner, not at the limbs.** The `P` attribute
   on `emitters_pop` is stuck at origin. Drop a Trail CHOP on
   `emitters_chop` first — you should see `P0`, `P1`, `P2` tracking live.
   If those look right but the POP is still at origin, the CHOP-to-POP's
   attribute row for `P` isn't picking up the channels — double-check
   that row has `Channel Scope = P0 P1 P2` and `Attribute Type = float
   size 3`. TD's automatic name detection doesn't work here (bracket
   naming isn't allowed in channel names), so the rows have to be set
   manually.
7. **`emitters_tex` is all zero.** Open its Viewer — pixels 0..4 on row 0
   should have non-zero R/G. If the Script TOP is erroring, check its
   textport: most likely `op('lag1')` returned None because the sensing chain
   isn't wired up yet, or a landmark name in `Landmarks` doesn't match the
   upstream channels (watch for singular/plural, e.g. `left_index` vs
   `left_index_tip`).
8. **Visibility threshold does nothing — every joint is always "visible".**
   The Script CHOP reads `<L>:visible` (blankensmithing tox convention,
   0..1 confidence). Drop a Trail CHOP on whatever's feeding `in1` and
   confirm those channels are present and actually varying. If your upstream
   tox uses a different suffix, change `f'{lm}:visible'` in
   `velocity_script_chop.py` to match.

## Forking for another experiment

Same playbook as `painting_controller`:

- Duplicate the `velocity_controller` Base COMP, rename it.
- If the new experiment needs different landmarks, edit `parent().par.Landmarks`.
  The Script CHOP rebuilds its state dict automatically. Both `emitters_tex`
  and `emitters_pop` pick up the new landmark list on the next cook — no
  wiring changes needed.
- If it needs more than velocity (e.g., relative distance between limbs,
  vertical position bands), add a helper to `velocity_logic.py` that returns
  extra fields, extend `PER_LANDMARK_CHANS` or `GLOBAL_CHANS`, and the Script
  CHOP will emit them. If the new fields should reach the renderer, also
  extend `emitters_tex_script.py` to pack them into unused channels of the
  texture (B/A of row 1 are free after `visible` and `speed` if you want to
  reuse them).
- If the visual needs to change substantially, edit the POP chain in place —
  or, if you expect to swap whole renderers frequently, expose the render
  sub-chain as a child Base COMP inside `velocity_controller` so you can
  replace the child without rewiring the sensing side.

Portable bits to lift: the state-on-COMP-via-store/fetch pattern for per-cook
memory; the `Landmarks` parent-par convention; the idempotent page installer;
the "Script TOP + Script CHOP read the same CHOP by name" idiom for turning
sparse semantic channels into dense render inputs without Shuffle CHOPs.
