# CLAUDE.md — Project Brief for Continuing in Claude Code

This is a TouchDesigner project that uses MediaPipe pose tracking as the
input layer for two experiments: a particle/flow-field renderer and a
Beat Saber-inspired rhythm game. Both consume the same upstream sensing
COMP and run independently of each other.

If you're picking this up cold, read this whole file first, then the
two `*_setup.md` guides for whichever subsystem you're touching.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────┐
│  Upstream (third-party)                                          │
│  ┌───────────────────┐                                           │
│  │ MediaPipe tox     │  emits <landmark>:x/y/z/visible channels │
│  │ (blankensmithing) │  in MediaPipe-UV space (0..1)            │
│  └─────────┬─────────┘                                           │
└────────────┼─────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Sensing (this project)                                          │
│  ┌──────────────────────────────────┐                            │
│  │ velocity_controller (Base COMP)   │ smooths landmarks, emits  │
│  │   in_pose → selects → merge1 →    │ velocity, accel, burst,   │
│  │   velocity_script_chop → lag1 →   │ visibility-gated holds    │
│  │   null1 → out1                    │                           │
│  └────────┬─────────────────────────┘                            │
│           │                                                      │
│           ├────────────────┬─────────────────┐                   │
│           ▼                ▼                 ▼                   │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐    │
│  │ Particle    │  │ beatsaber_       │  │ Other consumers  │    │
│  │ render-     │  │ controller       │  │ via out1         │    │
│  │ subgraph    │  │ (Base COMP)      │  │                  │    │
│  │ (lives      │  │ runs Game.tick() │  │                  │    │
│  │ inside      │  └────────┬─────────┘  │                  │    │
│  │ velocity_   │           │            │                  │    │
│  │ controller) │           ▼            │                  │    │
│  │             │  ┌──────────────────┐  │                  │    │
│  │             │  │ beatsaber_       │  │                  │    │
│  │             │  │ renderer         │  │                  │    │
│  │             │  │ (Base COMP)      │  │                  │    │
│  │             │  └──────────────────┘  │                  │    │
│  └─────────────┘                        └──────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

Independent vertical slices. The two consumer COMPs (particle render +
beatsaber) read `velocity_controller/out1` by channel name and never
touch each other.

## The two subsystems

### 1. velocity_controller — sensing + particle render

Inside a single `velocity_controller` Base COMP, two sub-chains:

- **Sensing chain** (live op names): `in_pose → (select_position +
  select_visibility) → merge1 → limit1 → velocity_script_chop → limit2 →
  lag1 → null1 → out1`. `velocity_script_chop` runs `velocity_logic.py` per
  cook to produce per-landmark position, velocity, acceleration, emit, burst,
  and visibility channels. (`limit1`/`limit2` are bypassed safety CHOPs;
  `select_visibility` renames the upstream `visibility<N>` channels to
  `<lm>:visible`.)
- **Particle render chain**: `lag1` feeds two Script ops
  (`emitters_tex_script_top` Script TOP and `emitters_chop_script` Script
  CHOP) that fan the channel data into a velocity-field texture and a POP
  emitter set. A second emitter, `ambient_chop_script` → `ambient_pop`, adds a
  constant **particle soup**; both merge via `merge_emitters` Merge POP into
  `particle1` (Particle POP). Force chain `p_to_uv → field_sample → curl_noise
  → add_to_force → bounds_reflect → force_null` (the `bounds_reflect` GLSL POP
  does force integration + damping + position-clamp/wall-reflection); render
  tee `particle1 → color_attr → render_null → geo1` → `render1` → post-FX → `out2`.
  - **Render:** `geo1` instances a **camera-facing quad** (`sprite_quad`, orient=xy)
    textured with a soft radial gradient (`sprite_grad`) on an **additive Constant
    MAT** (`particle_mat`) → soft glowing "light" motes (no lit spheres/lights).
    `Particlesize` scales the quad. `color_attr` writes HDR `Cd`: soup = cyclic
    3-stop position gradient (`uSoupA/B/C`, color-cycling), movement = per-limb +
    **Embers** age ramp (`uEmberHot/Mid/Old`); palette is uniforms so presets recolor.
  - **Post-FX chain** (`render1` 16f → … → `out2`, each with enable+intensity pars):
    motion trails (`trail_*` feedback smear) → `bloom1` → anamorphic streaks
    (`streak_thresh/blur/comp`) → `grade` (GLSL TOP grade.frag) → `lens_finish`
    (GLSL TOP, vignette+CA+grain).
  - **Look presets:** `Preset` menu + `Applypreset` pulse (Look page) → `preset_exec`
    parexec → `presets.apply()` sets palette + post-FX + motion bundles.
  - **Force integration and velocity damping live in `bounds_reflect` GLSL POP**,
    NOT Particle POP (damping 0). Soup idle drift is capped by `Soupmaxspeed`.

The render side has had a long debug history — see "Known issues" below.

**Setup guide**: [`velocity_controller_setup.md`](./velocity_controller_setup.md)

### 2. beatsaber_* — rhythm-game game loop + dedicated renderer

Two Base COMPs:

- **`beatsaber_controller`** consumes `velocity_controller/out1` (it
  needs `left_wrist/right_wrist/left_elbow/right_elbow` plus their
  `:visible` channels). Inside, a `game_tick` Script CHOP runs the
  game loop each cook via the Pure-Python `beatsaber/` package.
  Stores a snapshot on the COMP that sibling Script ops (`notes_chop`,
  `events_dat`) read.
- **`beatsaber_renderer`** is a dedicated, separate Base COMP that
  consumes `beatsaber_controller`'s outputs and produces a TOP. It
  has its own perspective camera (NOT shared with anything else),
  renders the sabers as colored lines, the notes as instanced cubes,
  and a UI overlay for score/combo/event flashes.

**Setup guides**: [`beatsaber_controller_setup.md`](./beatsaber_controller_setup.md),
[`beatsaber_renderer_setup.md`](./beatsaber_renderer_setup.md)

## File map

### Top-level Python (TD callbacks + bootstraps + installers)

| File | Role |
| --- | --- |
| `velocity_logic.py` | Pure-Python sensing logic (velocity/accel/burst from raw landmarks). Self-testable: `python3 velocity_logic.py`. |
| `velocity_script_chop.py` | Script CHOP callback that wraps `velocity_logic`. |
| `install_velocity_params.py` | Idempotent param installer for `velocity_controller` (Sensing + Renderer pages). |
| `reset_velocity_params.py` | Force-resets every par on `velocity_controller` to current defaults (use after pulling new defaults from disk). |
| `bootstrap_velocity_controller.py` | One-shot builder for the whole `velocity_controller` COMP skeleton. |
| `emitters_chop_script.py` | Script CHOP — turns `lag1`'s channels into N points (`p/v/w/Lid` attribs on the POP) for the Particle POP. Has 2D scatter logic with bias correction. |
| `ambient_chop_script.py` | Script CHOP — emits the constant "particle soup": `Ambientpoints` points scattered through the bounds volume each cook, birthing `Ambientrate` pts/s (fractional accumulator), `Lid`=5 sentinel. Merged with `emitters_pop` via `merge_emitters` Merge POP into `particle1`, so the soup is advected/displaced by the same force chain. Self-testable: `python3 ambient_chop_script.py`. |
| `presets.py` | Pure-Python Look presets (`Cosmic`/`Ember`/`Ink`/`Neon`) — each a bundle of **LOOK-only** par values: palette (soup colors + ember colors + tint) + post-FX (bloom / streak / grade / lens) + trail length. **Deliberately touches NO physics** (soup speed/turbulence, curl, rate, particle size, field force, spawn) so a mood switch never undoes hand-tuned motion. `apply(comp, name)` writes them (RGB tuples → `<name>r/g/b`). Self-testable: `python3 presets.py`. |
| `apply_preset.py` | Parameter Execute DAT callback (`preset_exec`) — on the COMP's `Applypreset` pulse or `Preset` menu change, imports `presets` and applies the bundle. Fires deferred (next frame). |
| `logo_amt.py` | Script CHOP callback — outputs channel `amt` (0..1) gating the logo attractor + brighten. `Logomode`: `Off`→0, `Always`→1, `Standby`→fades to 1 when no pose present (sum of `lag1` `*:visible` < 0.5), 0 when a person appears; exponentially smoothed over `Logofade` seconds. Read as a uniform by both `bounds_reflect` (force) and `color_attr` (brightness). |
| `body_logic.py` | Pure-Python skeleton definition for the body force field: `JOINTS` (13 = head/shoulders/elbows/wrists/hips/knees/ankles with MediaPipe indices) + `BONES` (14 edges as pack-index pairs) + `joint_velocity()` diff helper. Self-testable: `python3 body_logic.py`. |
| `body_tex_script.py` | Script TOP callback — packs the skeleton from `in_pose` (`<name>:x/y` + `visibility<idx>`) into an NJOINTS×2 RGBA32F texture (row0 = pos+vis, row1 = per-joint velocity, differenced from a stored prev). Feeds `body_field`. `in_pose` shares `lag1`'s coordinate convention, so the field is particle-aligned. |
| `emitters_tex_script.py` | Script TOP — packs `lag1`'s channels into an N×2 RGBA32F texture for the velocity-field shader. |
| `painting_logic.py`, `painting_script_chop.py`, `install_painting_params.py` | The original first experiment ("painting controller") — predates this work. Kept as reference for the architectural conventions. |
| `beatsaber_game_tick.py` | Script CHOP callback that runs the Beat Saber game loop. Reads landmark channels, calls `Game.tick()`, stores snapshot. |
| `beatsaber_notes.py` | Script CHOP callback — emits one sample per active note. |
| `beatsaber_events.py` | Script DAT callback — emits per-cook events (spawn/hit/miss/bad-cut) as a Table. |
| `beatsaber_parexec.py` | Parameter Execute DAT — dispatches Start/Pause/Resume/Reset pulses to the Game singleton. |
| `beatsaber_saber_sop.py` | Script SOP callback — emits two uncolored line polygons (left/right). Color is applied downstream via Primitive SOPs. |
| `beatsaber_hud.py` | Helper module — HUD text formatters (score/combo/accuracy/song_time) + event-log accumulator state machine. Called via expressions on the renderer's Text TOP `text` parameters. No PIL. |
| `install_beatsaber_params.py` | Idempotent param installer for `beatsaber_controller`. |
| `reset_beatsaber_params.py` | Force-resets every par on `beatsaber_controller` to current defaults (partner to the installer; use after pulling a new code drop or to recover a known baseline). |
| `bootstrap_beatsaber_renderer.py` | One-shot builder for `beatsaber_renderer` COMP. |

### Shaders (`shaders/`)

| File | Role |
| --- | --- |
| `velocity_field.frag` | GLSL TOP — splats per-emitter gaussians with anisotropic kernel into a 2D RGBA force field. |
| `bounds_reflect.glsl` | GLSL POP — the force integrator + container: folds `PartForce` into `PartVel` via a nonlinear deadzone/ref/gamma curve, applies `Velocitydamping` + `Maxspeed`, then **hard-clamps `P` to the box AND reflects `PartVel`** on wall hits (Output Attributes = `PartVel P`). The P-clamp is what stops fast particles overshooting/escaping the box — velocity reflection alone lagged a frame and leaked. For soup (`Lid>=5`) it also adds the 2-layer curl drift and the **logo attractor** (`logodata.xy · Logoattract · Logoamt`, capped by `Soupmaxspeed`) so the soup condenses into the logo shape in standby. For ALL particles it adds the **body field** (`bodyforce.xy · Bodypush` repel + `bodyforce.zw · Bodydrag` advect) so the skeleton parts/drags the soup. Uses real `TDIn_*()` syntax. Synced to `bounds_reflect_compute`. |
| `p_to_uv.glsl` | GLSL POP — writes `Puv` = `P` remapped into box UV (aspect-correct) for `field_sample` to index. Synced to `p_to_uv_compute`. |
| `color_attr.glsl` | GLSL POP — writes per-particle `Cd`: per-limb palette (`Lid` 0..4) or cool soup base (`Lid>=5`), velocity warm-accent, **Embers age ramp** (white-hot at birth → warm → ember → dark, via `PartAge/PartLifeSpan`), and a velocity HDR boost so fast/young particles bloom. Also reads `TDIn_logodata().w` (logo luma mask, supplied by `c_logo_lookup` in the render chain) → brightens soup sitting on the logo by `Logobright·Logoamt`. Synced to `color_attr_compute`. |
| `logo_grad.frag` | GLSL **TOP** — turns the logo into a force+mask field for the soup. Input0 = `/project1/null_logo` (SHARP, for the mask + close snap), input1 = `/project1/logo_blur` (heavily blurred, for a FAR-reaching broad gather gradient — blur radius = `Logoreach`). RGB = combined ∇(luma) (broad gB + sharp gS) = attractor toward the shape; A = sharp luma (mask). **The TOP MUST be `rgba32float`** — `useinput` (8-bit) clamps negative gradient directions to 0 and half the pull is lost (cost real debug time). Native Lookup POPs (`logo_force_pop` force chain, `c_logo_lookup` render chain) sample it at `Puv` → 4-comp `logodata` (`.xy` = attract dir, `.w` = mask). `bounds_reflect` pulls soup up the gradient (uncapped, after the soup speed cap), **traps** it on the mask (`logo.w·Logotrap` damping) so particles fill the shape, and treats the shape as a **3D vessel**: the gradient is a soft wall (≈0 inside, strong at edges → contains), while `Logovigor` injects un-capped 3D curl swirl ONLY inside the mask so the contents keep tumbling instead of freezing (0 = static decal, 1 = churning vessel). Synced to `logo_grad_pixel`. |
| `body_field.frag` | GLSL **TOP** — splats the skeleton's BONES (hardcoded MediaPipe edges, must match `body_logic.BONES`) as soft capsules from the `body_tex` joint texture: RG = push (away from nearest bone × falloff × visibility = repel), BA = drag (bone velocity × falloff = advect). Distance is aspect-corrected so `Bodyradius` is round in world units. `body_force` Lookup POP samples it at `Puv` → 4-comp `bodyforce` attribute, read by `bounds_reflect`. Synced to `body_field_pixel`. |
| `body_viz.frag` | GLSL **TOP** — the elegant glowing render of the skeleton (soft capsule bones + joint nodes + a flowing energy pulse), same `body_tex` joints + bone list as `body_field` so it sits exactly on the displacement. Additive HDR (`rgba16float`) → `body_comp` composites it onto `trail_out` BEFORE `bloom1`, so it blooms like the particles. Our own body visualization (replaces MediaPipe's debug circles at `/project1/pose_tracking/point_render`). Pars: `Bodyviz`/`Bodyvizwidth`/`Bodyvizglow`/`Bodyvizflow`/`Bodyviztint`. Synced to `body_viz_pixel`. |
| `sprite_disc.frag` | GLSL **TOP** — the particle sprite: a centered soft round disc (`1 - smoothstep(0.45,0.80,dist)`). Replaced the Ramp TOP radial (`sprite_grad`), whose TD radial normalization rendered asymmetric/mostly-white → textured quads read as lit rectangles + washed to white. `particle_mat` (additive Constant MAT) samples this as its color map; black surround = round motes, not squares. Synced to `sprite_disc_pixel`. |

| `grade.frag` | GLSL **TOP** — cinematic color grade: ACES tonemap (HDR→display) + lift/gamma/gain + saturation/contrast/tint. `uEnable` passthrough toggle. Synced to `grade_pixel`. |
| `lens_finish.frag` | GLSL **TOP** — final lens finish: chromatic aberration + vignette + animated film grain. `uEnable` passthrough. Synced to `lens_pixel`. |

All shaders above are real files under `shaders/`, each synced to its
GLSL POP/TOP DAT (`Sync File` On) — edit the file, TD reloads. **GLSL TOPs**
(velocity_field, grade, lens_finish) are fragment shaders sampling
`sTD2DInputs[0]` — safe. The crash-prone case is a GLSL **POP** referencing an
unbound `sampler2D` (never do that).

### Beat Saber package (`beatsaber/`)

Pure-Python game logic, no TD imports, every module self-testable via
`python3 -m beatsaber.<name>`.

| Module | Role |
| --- | --- |
| `saber_logic.py` | Per-saber state: hilt, tip, dir, velocity, prev_tip (for swept-volume collision). Forearm direction from elbow→wrist with -Z extrusion. |
| `timeline.py` | Abstract `song_time()` accessor. start/pause/resume/reset. Decoupled from the underlying clock so test-clock vs audio-time is a one-line swap. |
| `beatmap.py` | Note schema + JSON loader + canned `make_test_beatmap()`. CUT_VECTORS table. |
| `hit_detection.py` | Swept-volume vs note AABB intersection (slab method). Cut direction error, through-center distance, swing-magnitude scoring. Returns GOOD / BAD_COLOR / BAD_DIRECTION / None. |
| `score.py` | Combo, multiplier tiers (1× / 2× / 4× / 8×), running totals, accuracy. |
| `game.py` | Coordinator — `Game.tick(wall_seconds, samples) → (events, snapshot)`. Spawns, advances, collides, scores, cleans up. Has a `loop` flag for auto-restart. |
| `test_beatmap.json` | 14-note dev test map covering all cut directions and colors. |

### Setup guides (read before touching the corresponding subsystem)

| File | What it covers |
| --- | --- |
| `velocity_controller_setup.md` | Sensing pipeline + particle render. Long. Has ASCII history of debug iterations. |
| `beatsaber_controller_setup.md` | Beat Saber controller setup, channel contracts, troubleshooting. |
| `beatsaber_renderer_setup.md` | Dedicated renderer setup with explicit camera-verification checklist. |

## Coordinate conventions

### Sensing space (everything pre-render)

- `x`, `y` ∈ `[0, 1]` in MediaPipe-UV. `y = 0` is TOP, `y = 1` is BOTTOM.
- `z` is MediaPipe's depth, ~`[-0.5, +0.5]`. Positive = away from camera in MediaPipe's convention.
- The webcam is assumed to be **mirrored** (selfie-cam style) so the user's
  hand on their right appears on screen-right. This is what makes the
  first-person illusion work.

### Game render space (Beat Saber)

- Same `x` / `y` as sensing.
- `z = 0` is the **hit plane** where sabers live.
- `z < 0` is the approach tunnel. Notes spawn at `z ≈ -10` and travel
  to `z = 0`.
- `z > 0` is "behind the player". The game camera sits at `z = +3`
  with default rotation `(0, 0, 0)` looking down its local `-Z` axis
  into the tunnel. **No lookAt needed.**

This convention was deliberately chosen to match TD's default camera
orientation. Older versions of the code had `+z` as the tunnel and
required a lookAt or rotation hack — that's gone. If you see code or
docs implying notes spawn at `+z`, it's stale.

### Particle render space (velocity_controller's particle subgraph)

- Same `x` / `y` as sensing.
- `z` carries MediaPipe's depth signal but is heavily down-weighted
  (`Zforceweight = 0.05` default) because MediaPipe's monocular depth
  estimate is noisy. Without that, particles drift in z on purely
  horizontal motion.

## Architectural conventions

These show up everywhere and you should follow them when adding new code.

### 1. Pure Python logic + thin TD callbacks

Every "module of brain" is a pure Python file with NO TD imports
(`velocity_logic.py`, `beatsaber/*.py`, etc.). Each is self-testable
via `python3 -m <module>` (or just `python3 <file>` for the top-level
ones). TD callbacks (Script CHOP/SOP/TOP/DAT) are thin wrappers that
read CHOP channels, call into the Python module, and write back to the
op output.

This pattern matters because:
- Logic is testable without launching TD.
- TD callback bugs (channel name typos, op name conflicts) don't
  pollute the logic.
- New TD versions changing op APIs don't require rewriting the math.

### 2. Idempotent installers + explicit reset scripts

`install_*_params.py` adds custom pars to a COMP only if they don't
already exist. Re-running never overwrites tuning. When defaults need
to be force-applied, `reset_*_params.py` exists as a separate explicit
script that overwrites every par.

### 3. Bootstrap scripts for COMP scaffolding

For non-trivial COMPs (`velocity_controller`, `beatsaber_renderer`),
there's a `bootstrap_*.py` that programmatically creates the COMP,
its child ops, wires the connections, and applies sensible defaults.
Idempotent. The bootstrap leaves manual touches (specific instance
configurations, material assignments) for the user but takes care of
the mechanical 80%.

### 4. Synced Text DATs mirror external .py files

Every TD callback DAT has its `File` parameter set to the relative
path of its `.py` file under `project.folder` and `Sync File` On.
This means edits to `.py` files on disk flow into TD on reload, and
git/diff tools work normally. No copy-paste of code into the
`.toe` file.

### 5. Custom pars live on the parent COMP, not on Script ops

All user-tunable knobs are custom parent pars on the enclosing Base
COMP. The Script ops inside read `parent().par.<name>.eval()`. This
gives a clean per-COMP control surface and makes the COMP portable.

### 6. Single source of truth for state schema

The `_fresh_landmark_state()` (in `velocity_logic.py`) and
`_fresh_saber_state()` (in `beatsaber/saber_logic.py`) functions
return the canonical per-element state dict. Adding a new field
means updating that one function; `ensure_schema()` migrates older
stored state forward on the next cook.

## How to work on this

### Picking up where we left off

1. Read this file top-to-bottom (you're doing it).
2. Read the relevant `*_setup.md` for the subsystem you're touching.
3. Run the self-tests for the relevant Python module(s) to confirm
   the logic side is healthy:
   - `python3 velocity_logic.py` — sensing math
   - `python3 -m beatsaber.saber_logic` — saber state
   - `python3 -m beatsaber.timeline` — clock
   - `python3 -m beatsaber.beatmap` — note schema
   - `python3 -m beatsaber.hit_detection` — collision + scoring math
   - `python3 -m beatsaber.score` — scoring tiers
   - `python3 -m beatsaber.game` — full integration replay
4. Open `MediaPipe_Base.toe` (or `.12.toe`) in TouchDesigner.

### Making a change

1. Edit the `.py` file on disk.
2. In TD, force-reload the synced Text DAT (or save the project — sync
   triggers).
3. Verify behavior in the COMP's Info popup or downstream viewers.
4. If you changed defaults that the user has already installed, mention
   in the response that they need to either run `reset_*_params.py` or
   manually update the par values. The installer alone won't push new
   defaults onto an existing COMP.

### Adding a new TD callback

Follow the pattern of an existing one (`velocity_script_chop.py` is a
good reference for Script CHOPs, `beatsaber_saber_sop.py` for Script
SOPs, etc.). Key things:

- `import sys, os` at top, push `project.folder` onto `sys.path` if you
  need to import the `beatsaber` package or any other local module.
- Wrap imports in `try/except` and log with `debug()` so the DAT
  doesn't hard-fail on first load.
- Read parent pars via `parent().par.<Name>.eval()`.
- For state across cooks, use `parent().store(key, value)` /
  `parent().fetch(key, default)`.

### Running self-tests in CI / from outside TD

All `.py` files under `beatsaber/` and the top-level `velocity_logic.py`
have `if __name__ == "__main__": ...` self-test blocks. They print "OK
— …" on success and assert on failure. Wire these into a CI script if
you want; nothing else is required.

## Known issues / open items

### Particle render

Has had many rounds of tuning. Current state (best of memory):

- **Z-axis sensitivity** had two leak paths (sensing-side `Zspeedweight`
  and renderer-side `Zforceweight`). Both are now tamed but z is still
  the most fragile axis.
- **Particles flying too far** — controlled by the `bounds_reflect`
  force-integration knobs (all COMP pars): `Velocitydamping` (0.15 live),
  `Forcescale`, `Forcedeadzone`/`Forceref`/`Forcegamma` (nonlinear response
  that squashes rest-drift), and `Maxspeed`. Damping is NOT on Particle POP
  anymore — its Velocity Damping / Initial Drag are 0. To tame runaway
  particles, raise `Velocitydamping` / lower `Fieldforce`, don't touch
  Particle POP.
- **Bounding-box reflection + force integration** (`bounds_reflect.glsl`) —
  landed and stable; uses real `TDIn_*()` read syntax for this build (the old
  "four flagged placeholder lines" caveat is gone). Same op also integrates
  the field/curl force and applies damping.
- **Camera aspect/zoom** for the particle render — separate issue from
  the Beat Saber camera. Particle render uses the user's existing
  camera setup (whatever they wired in `velocity_controller`'s
  rendering side, not necessarily a clean `game_cam`).

### Beat Saber

- **Notes flying wrong direction** — was caused by old camera at `tz = -3`
  combined with new `z_spawn = -10`. Fixed by moving camera to `tz = +3`.
  If user reports this again, check camera position first
  (`beatsaber_renderer_setup.md` has a verification checklist).
- **Auto-start timing bug** — when `Game.start()` was called before the
  timeline had received a wall clock, `_t0` latched to 0 and song_time
  jumped to `absTime.seconds` (huge number) on the first cook. Every
  note immediately spawned + missed + cleaned up. Fixed by priming
  `timeline.set_wall_clock(absTime.seconds)` before `start()`. Watch
  for this pattern in any new "build a Game" code path.
- **Loop** — added on user request, default On. Resets and restarts
  when `song_time > beatmap.duration() + travel + miss_window + 0.5s`.
- **Renderer not yet polished** — basic saber lines + cube notes + UI
  overlay work, but no:
  - Bloom for the neon look (one Bloom TOP between `render_scene` and
    `comp_out`)
  - Saber swept-volume trails (Trail CHOP + CHOP-to-SOP recipe is in
    the setup guide but not wired)
  - Cut-direction arrow on each note (would need a GLSL MAT reading
    instance attribs)
  - Music sync (currently uses `absTime.seconds`; setup guide explains
    the one-line swap to Audio File In TOP time)

### Other

- The bootstrap scripts try several common TD parameter naming
  conventions per op (different TD versions rename pars). When adding
  new bootstrap logic, defensively `try/except` around par sets.
- `painting_controller` is the older first experiment and is largely
  ignored by current development. Don't break it but don't put effort
  into improving it either unless asked.

## TD-specific gotchas accumulated

A non-exhaustive list of things that wasted real time and shouldn't
again:

| Gotcha | What to remember |
| --- | --- |
| **Script POP doesn't exist** in any TD build. Use Script CHOP + CHOP-to-POP for emitter generation. |
| **Source POP doesn't exist.** Particle POP is the hub op; the modifier chain feeds back via "Target Particles Update POP". |
| **Color SOP doesn't exist.** Use Primitive SOP with "Add" color mode. |
| **Channel names can't contain `[` or `]`** in TD CHOPs. Sanitised to `_`. Use `P0/P1/P2` not `P[0]/P[1]/P[2]`. |
| **Cameras default to looking down -Z**, not +Z. Place the camera at +Z and the scene at -Z, no lookAt needed. |
| **Particle POP has no separate "Time Integration" toggle.** `Play: On` is the equivalent. |
| **Velocity damping lives in the `bounds_reflect` GLSL POP, not Particle POP.** It reads the `Velocitydamping` COMP par (installable) and applies `PartVel *= 1 − Velocitydamping` per cook. Particle POP's own Velocity Damping / Initial Drag are kept at 0 so the two stages don't stack. (This is a change from older docs that said to crank Particle POP's damping.) |
| **`Partvel` etc. are reserved on Particle POP.** Use `Start*` prefix for seed values (`StartPartvel`, `StartPartmass`). Renaming an input attribute to `PartVel` triggers an auto-rename to `StartPartvel` with a warning. |
| **Script CHOP doesn't cook every frame by default** — only on output demand. If you need per-cook execution, either wire the output to something that does cook (an Out CHOP that's read by another COMP) or set `isTimeSlice = True`. |
| **Script SOP `Point.Cd` requires `createAttribute(...)`** before assignment, with a version-dependent signature. Sidestep by emitting uncolored geometry and applying color via a downstream Primitive SOP. |
| **GLSL POP attribute read/write syntax** uses `TDIndex()`, `TDNumElements()`, `TDIn_<AttribName>()`, and writes via something like `TDOutAttrib_<Name>` — but the exact write syntax varies by build. Right-click the GLSL POP ▸ View Compiled Shader to see what the auto-generated output variables are. |
| **Random number generators with small N have empirical bias**. The `_SCATTER` list in `emitters_chop_script.py` had a 7% directional bias that caused particles to consistently drift toward one corner. Always center the actually-used subset by subtracting its empirical mean. |
| **Curl noise wavelength must be < cloud extent** or the whole cloud experiences the same gradient and drifts together. Default `Curlscale = 0.5` keeps the noise period below the 1-UV particle volume. |
| **`install_*_params.py` is intentionally idempotent** — it doesn't update existing par values when defaults change. Use the matching `reset_*_params.py` for force-updates. |
| **Mirrored webcam is a feature, not a bug.** It's what makes the saber-on-hand visual feel like first-person. Don't add a flip somewhere "to fix" it. |

## Self-test command summary

```bash
cd /Users/flo/work/TD/MediaPipe_Base

# Sensing math
python3 velocity_logic.py

# Ambient particle soup (scatter + birth accumulator)
python3 ambient_chop_script.py

# Beat Saber game logic (each independent)
python3 -m beatsaber.saber_logic
python3 -m beatsaber.timeline
python3 -m beatsaber.beatmap
python3 -m beatsaber.hit_detection
python3 -m beatsaber.score
python3 -m beatsaber.game     # full integration: 16s synthetic playthrough
```

All print `OK — …` on success. They take less than a second total.

## Quick orientation map

If the user says "X is broken", here's where to start looking:

| Symptom | Likely file |
| --- | --- |
| Visibility threshold not gating | `velocity_script_chop.py` (channel name match) |
| Particles flying off-screen | `Velocitydamping` / `Forcescale` / `Forcedeadzone` / `Maxspeed` COMP pars (consumed by `bounds_reflect` GLSL POP), plus `Fieldforce`/`Spawnvelscale`/`Lifemax`. NOT Particle POP damping (it's 0 on purpose). |
| Particles escaping/teleporting outside the box | `bounds_reflect` must have `P` in its Output Attributes and hard-clamp `pos` (not just reflect velocity). See `shaders/bounds_reflect.glsl`. |
| No ambient soup / soup too dense | `Ambientrate` (pts/s) + `Ambientpoints` on the COMP; `ambient_chop_script` → `ambient_pop` → `merge_emitters`. Steady alive ≈ `Ambientrate × avg-life`. `Soupbright` sets its glow. |
| Soup looks like fixed "squirt-gun" fountains | `ambient_chop_script` (Script CHOP) isn't cooking every frame → frozen scatter. It must read an always-cooking op (`lag1`) to register a per-frame cook dependency. Check `totalCooks` advances 1:1 with frames. |
| Particles too big / too few | `Particlesize` (drives `geo1/sphere1` radius); particle count = `Spawncount` + `Ambientrate` vs Particle POP `Maximum Particles`. |
| No glow / bloom | `Bloomenable`/`Bloomstrength`/`Bloomthreshold`; needs `render1` format = 16-bit float + `color_attr` HDR (`Velbloom`, Embers `kEmberHot`). |
| Age gradient wrong | `color_attr.glsl` Embers ramp (`Agegradient`/`Agefalloff`), normalized by `PartAge/PartLifeSpan`. |
| Static / frozen curl swirls | `curl_noise` Translate-4D (`t4d`) must be animated = `absTime.seconds × Curlspeed` (Simplex-4D's 4th axis is time). `t4d=0` → frozen field. `Curlgain` (bound to `amp0`) sets curl amount; 0 = none. |
| Switch the whole look / preset | `Preset` menu + `Applypreset` pulse (Look page) → `preset_exec` parexec → `presets.py` `apply()`. Edit/add looks in `presets.py` (live `importlib.reload`). |
| Post-FX wrong (bloom/streaks/grade/vignette/grain/trails) | Chain `render1 → trail_comp → bloom1 → streak_comp → grade → lens_finish → out2`. Pars on Look page (+ `Feedback*` on Renderer for trails). `grade`/`lens_finish` = GLSL TOPs (`grade.frag`/`lens_finish.frag`). |
| Particles hard / want soft glow | `geo1` instances `sprite_quad` (camera-facing) + `sprite_grad` (soft radial tex) on additive `particle_mat`. Not the old Phong spheres. |
| Post-FX TOPs render tiny/low-res | Set their `Output Resolution` to explicit `custom` 1280×720 — `Use Input` collapses inside the trail feedback loop. |
| Bloom flickers / unstable even at idle | Soup brightness straddling `Bloomthreshold`: particles cross it as they fade in/out + churn. Keep `Soupbright × palette-peak` (≈0.86) **below** `Bloomthreshold` (1.1) so the calm soup never blooms — reserve bloom for HDR movement embers. |

**Production-pass TD gotchas (post-FX build):**
- **Level TOP `blacklevel`/threshold makes NEGATIVE pixels — always set `clamp=True`.** A Level used as a brightness gate (`trail_gate`, `streak_thresh`) subtracts the black level, so sub-threshold pixels go negative. Negative luminance fed into `grade` (ACES tonemap) maps to **white** → the whole frame washes out (mean ~0.98). Both `trail_gate` AND `streak_thresh` need Clamp on. Symptom: out2 blown white while `bloom1` looks normal — check the streak chain (`streak_thresh`/`streak_comp` going negative).
- **Feedback TOP wiring:** `input` = passthrough/init source (e.g. `render1`), `par.top` = the loop-end TOP to feed back (1-frame delayed). The back-reference MUST be via `par.top` — wiring the loop-end into the Feedback TOP's *input* closes the cycle forward → "cook dependency loop." (Took 3 tries; see `trail_*`.)
- **GLSL TOP resolution through a feedback loop:** `Output Resolution = Use Input` collapses to a tiny default (128²) inside a feedback loop (circular res dependency). Set the post-FX TOPs to explicit `custom` resolution (matched 1280×720).
- **GLSL POP vec uniform type:** when binding a vec3 uniform on the Vectors page, set `vecNtype='vec3'` to match the shader's `uniform vec3` decl — otherwise TD auto-declares it `float` → `Redeclaration` compile error.
- **Parameter Execute DAT fires DEFERRED** (next frame), not synchronously — a same-script readback right after setting the watched par sees pre-callback values. Check the log / re-read next call.
- **Billboard sprites:** Rectangle SOP `orient=xy` faces the −Z camera (simplest, no per-instance billboard needed for a thin z-slab); `orient=cam` mis-transformed the instances. `texture=face` needed to generate UVs for the colormap.

**⚠️ NEVER extract a full Particle/POP point stream to a CHOP (hangs/crashes TD).**
A `poptoCHOP` (or CHOP-to-DAT, etc.) with `extract=points` on `particle1`/
`render_null`/`force_null` pulls ALL ~30k–150k points × attributes back from the
GPU each cook → a massive synchronous readback that **freezes TD (fps 0) or
crashes it** (this caused a real crash + several apparent "borked soup" scares;
the `maxpoints` par doesn't exist on `poptoCHOP`, so there's no quick cap). For
diagnostics use instead: `op.numPoints` (cheap), a **TOP** `numpyArray()` (one
readback of a render/field), or if you truly need point values, a `poptoCHOP`
with heavy **thinning** (`thinstep` large, or `thinrandom`) so it grabs ≤a few
hundred. Always `destroy()` the temp op in the SAME call. Symptom: `fps 0
critical` right after creating a temp `_`-prefixed POP op. (Also: `fps 0` can
just mean the user pressed **Pause** — check before assuming a hang.)

**⚠️ GLSL POP sampler crash (cost a TD crash + unloadable saves):** never let a synced GLSL POP/TOP reference a `sampler2D` (or `texture()`) before that sampler is bound to a real TOP on the op's Samplers page. Sampling an **unbound** sampler in a compute POP is a GPU device fault that crashes TD — and because the shader is a synced file, every save then re-crashes on load until the file is fixed on disk. If you want a Ramp TOP palette, create + bind it FIRST, then edit the shader to sample it. (The soup color ramp is currently an **in-shader procedural cosine palette** in `color_attr.glsl` — no sampler — for exactly this reason.) Plain float/vec uniforms are safe unbound (default 0).
| Particles drifting in z when only moving horizontally | `Zforceweight` (renderer side, scales `vz` in both `emitters_tex_script.py` and `emitters_chop_script.py`) |
| Particles biased toward one corner | scatter-list mean centering in `emitters_chop_script.py` + `Curlscale` (must be < cloud extent) |
| Notes not spawning | `_get_or_build_game()` in `beatsaber_game_tick.py` (must prime wall clock before start), or game not started |
| Notes flying away from camera | `bootstrap_beatsaber_renderer.py` camera config (must be `tz = 3.0`, not negative) |
| Sabers wrong color or invisible | Primitive SOP color chain inside `sabers_geo` |
| Sabers crash with "Cd attribute" error | `beatsaber_saber_sop.py` shouldn't be setting `Cd`; old version of the file |
| Score/combo not updating | `beatsaber_game_tick.py` — verify it's actually cooking each frame |

## Conventions for new work

When adding a new feature in this style:

1. **Start with pure Python.** Write a module under either the project
   root (for sensing-style features) or `beatsaber/` (for game-style).
   Add a `if __name__ == "__main__":` self-test that asserts behavior.
2. **Then write the TD callback** that wraps it. Read pars, call the
   Python, write CHOP/DAT/etc output.
3. **Add an installer entry** for any new pars on the relevant
   `install_*_params.py`.
4. **Add to the matching `reset_*_params.py`** so the new default
   propagates when the user runs the reset.
5. **Document in the relevant `*_setup.md`** — at minimum: par
   description in the parameter table, and a tuning hint.
6. **Update this CLAUDE.md** if you add a new file or significantly
   change architecture.
