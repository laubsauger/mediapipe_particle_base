# CLAUDE.md — Project Brief

TouchDesigner project using MediaPipe pose tracking as input for two independent
experiments: a particle/flow-field renderer and a Beat Saber rhythm game. Both
consume the same upstream sensing and run independently. Read this, then the
relevant `*_setup.md` for the subsystem you touch.

## Architecture

Upstream (third-party): MediaPipe tox, Logo TOP A/B, Depth TOP (realsense/noise).

```
/project1
  mask_controller (Base COMP)   — ALL mask source switching/blending/cycling/
      in_mask_a/b, in_depth,      standby. → out_mask (TOP) + out_state (CHOP, 4ch)
      in_pose → out_mask/out_state
  depth_placeholder (NoiseTOP, swap for realsenseTOP later)
  particle_system (Base COMP)   — consumes ONE mask (TOP) + 4ch state (CHOP) +
      in_mask, in_mask_state,     in_depth + in_pose_*. Decoupled from mask source.
      in_depth, in_pose_*         Depth is a SEPARATE 3D force layer.
      → out_render, out_field, out_vc_pose
  beatsaber_controller → beatsaber_renderer  — reads pose via
      particle_system/out_vc_pose; independent of the visual side.
```

Independent vertical slices. `mask_controller` owns all mask logic; `particle_system`
just consumes the resolved mask + state.

## Subsystem 1 — velocity_controller (sensing + particle render)

One Base COMP, two sub-chains:

- **Sensing**: `in_pose → (select_position + select_visibility) → merge1 → limit1 →
  velocity_script_chop → limit2 → lag1 → null1 → out1`. `velocity_script_chop` runs
  `velocity_logic.py` per cook → per-landmark position/velocity/accel/emit/burst/
  visibility. (`limit1/2` bypassed; `select_visibility` renames `visibility<N>` →
  `<lm>:visible`.)
- **Particle render**: `lag1` feeds `emitters_tex_script_top` (velocity-field texture)
  and `emitters_chop_script` (POP emitter set). `ambient_chop_script → ambient_pop`
  adds the constant **particle soup**; both merge via `merge_emitters` Merge POP into
  `particle1` (Particle POP). Force chain `p_to_uv → field_sample → curl_noise →
  add_to_force → bounds_reflect → force_null` (`bounds_reflect` GLSL POP does force
  integration + damping + position-clamp/wall-reflection). Render tee `particle1 →
  color_attr → render_null → geo1 → render1` → post-FX → `out2`.
  - **Render:** `geo1` instances a camera-facing quad (`sprite_quad`, orient=xy)
    textured with `sprite_disc` on additive Constant MAT (`particle_mat`) → soft motes.
    `color_attr` writes HDR `Cd`: soup = cyclic 3-stop position gradient cycling through
    a **palette bank** (`uSoupA/B/C` = set 0 = preset triad; sets 1–3 curated; rotates
    at `Soupsetspeed` sets/s); movement = per-limb palette + Embers age ramp.
  - **Post-FX** (`render1` 16f → … → `out2`): trails (`trail_*` feedback) → `bloom1` →
    anamorphic streaks (`streak_thresh/blur/comp`) → `grade` (grade.frag) → `lens_finish`
    (vignette+CA+grain).
  - **Look presets:** `Preset` menu + `Applypreset` pulse → `preset_exec` parexec →
    `presets.apply()` sets palette + post-FX + trail bundles (LOOK only, no physics).
  - **Force integration + velocity damping live in `bounds_reflect`**, NOT Particle POP
    (damping 0). Soup idle drift capped by `Soupmaxspeed`.

Setup: [`velocity_controller_setup.md`](./velocity_controller_setup.md)

## Subsystem 2 — beatsaber_* (game loop + renderer)

- **`beatsaber_controller`** consumes `velocity_controller/out1` (needs left/right
  wrist+elbow + `:visible`). `game_tick` Script CHOP runs the loop each cook via the
  pure-Python `beatsaber/` package; stores a snapshot read by `notes_chop`/`events_dat`.
- **`beatsaber_renderer`** — separate Base COMP, own perspective camera, renders sabers
  as lines, notes as instanced cubes, UI overlay.

Setup: [`beatsaber_controller_setup.md`](./beatsaber_controller_setup.md),
[`beatsaber_renderer_setup.md`](./beatsaber_renderer_setup.md)

## File map

**Top-level Python** (pure logic + thin TD callbacks):
- `velocity_logic.py` — sensing math (velocity/accel/burst). Self-test: `python3 velocity_logic.py`.
- `velocity_script_chop.py` — Script CHOP wrapping velocity_logic.
- `emitters_chop_script.py` — Script CHOP → N points (`p/v/w/Lid`) for Particle POP; 2D velocity-aligned scatter.
- `ambient_chop_script.py` — Script CHOP, the particle soup. Births `min(Ambientrate·dt, Ambientpoints)` pts/cook (`Ambientpoints` is a HARD per-cook cap). `Lid`=100 sentinel. Self-test.
- `presets.py` — pure-Python Look presets (Cosmic/Ember/Ink/Neon), LOOK-only bundles. Self-test.
- `apply_preset.py` — parexec callback applying a preset on pulse/menu change (deferred).
- `mask_standby.py`, `mask_cycle.py`+`mask_cycle_chop.py`, `mask_state_resolve.py` — mask gating/cycling/state-resolve inside mask_controller / particle_system.
- `body_logic.py` (skeleton JOINTS/BONES), `body_tex_script.py` (packs joints→texture), `emitters_tex_script.py` (channels→velocity-field texture).
- `audio_logic.py` (pure: envelopes/AGC/build-state, self-test) + `audio_react_chop.py` (Script CHOP `audio_react`) — turn ARE (`/project1/ARE_v1_2`) feature CHOPs into 0..1 modulation channels (kick/snare/hat/bass/breath/build/spec0..14, ×master `Audioreact`). Consumed as `base+chan·Audio<x>` on uniform exprs. See setup md "Audio reactivity".
- `install_*_params.py` (idempotent par installers), `reset_*_params.py` (force-reset to defaults), `bootstrap_*.py` (one-shot COMP builders).
- `beatsaber_game_tick.py` / `_notes.py` / `_events.py` / `_parexec.py` / `_saber_sop.py` / `_hud.py` — TD callbacks for the game.
- `painting_*` — older first experiment; reference only, don't break, don't invest.

**Shaders** (`shaders/`, each synced to its GLSL POP/TOP DAT — edit file, TD reloads):
- `velocity_field.frag` — splats per-emitter gaussians into 2D force field.
- `bounds_reflect.glsl` — GLSL POP force integrator + container: force→vel curve,
  `Velocitydamping`+`Maxspeed`, hard-clamp `P` to box + reflect `PartVel`. Adds soup
  curl drift + logo attractor (Lid≥5), body field (all), depth force. Output `PartVel P`.
- `p_to_uv.glsl` — writes `Puv` = box-UV of `P` for lookups.
- `color_attr.glsl` — per-particle `Cd`: soup palette bank + ember age ramp + velocity
  HDR boost + mask brighten. Procedural cosine palette (NO sampler — see crash warning).
- `logo_grad.frag` — GLSL TOP, mask→force+mask field. **MUST be rgba32float** (8-bit
  clamps negative gradients). Native Lookup POPs sample it at `Puv`.
- `depth_field.frag` — depth map → 3D wall-repel force (covers surface between bones).
- `body_field.frag` — skeleton BONES → soft-capsule push/drag force (must match body_logic.BONES).
- `body_viz.frag` — glowing skeleton render, composited before bloom.
- `sprite_disc.frag` — soft round particle sprite (replaced asymmetric Ramp radial).
- `grade.frag` (ACES + lift/gamma/gain), `lens_finish.frag` (CA+vignette+grain).

GLSL **TOPs** sample `sTD2DInputs[0]` — safe. The crash case is a GLSL **POP** referencing an unbound `sampler2D`.

**`beatsaber/` package** (pure Python, no TD, each self-testable via `python3 -m beatsaber.<name>`):
`saber_logic`, `timeline`, `beatmap`, `hit_detection`, `score`, `game`, `test_beatmap.json`.

## Coordinate conventions

- **Sensing space** (pre-render): `x,y ∈ [0,1]` MediaPipe-UV, `y=0` TOP. `z` ≈ [-0.5,+0.5]
  (positive = away). Webcam is **mirrored** (selfie-cam) — a feature, don't "fix" it.
- **Game render (Beat Saber)**: same x/y. `z=0` = hit plane, `z<0` = approach tunnel
  (notes spawn `z≈-10`), `z>0` = behind player. Camera at `z=+3`, rotation (0,0,0),
  looks down -Z. No lookAt. (Stale docs saying notes spawn at +z are wrong.)
- **Particle render**: same x/y; `z` carries MediaPipe depth heavily down-weighted
  (`Zforceweight=0.05`) because monocular depth is noisy.

## Conventions

1. **Pure-Python logic + thin TD callbacks.** Each brain module has no TD imports and a
   `__main__` self-test. Callbacks read CHOP channels, call the module, write output.
2. **Idempotent installers + explicit resets.** `install_*` adds pars only if missing
   (never overwrites tuning); `reset_*` force-applies every default. Pulling new defaults
   → run the matching `reset_*` (or update pars manually); the installer won't push them.
3. **Bootstrap scripts** scaffold non-trivial COMPs (idempotent).
4. **Synced Text DATs** mirror external `.py`/`.glsl` files (`File` set, `Sync File` On).
5. **Custom pars on the parent COMP**, not on Script ops; Script ops read `parent().par.X.eval()`.
6. **Single source of truth for state schema**: `_fresh_landmark_state()` / `_fresh_saber_state()`;
   `ensure_schema()` migrates old stored state.

Adding a feature: pure Python module + self-test → TD callback → installer entry → reset entry
→ document in `*_setup.md` → update this file if architecture changes.

## Quick orientation ("X is broken")

| Symptom | Look at |
| --- | --- |
| Particles flying off-screen | `Velocitydamping`/`Forcescale`/`Forcedeadzone`/`Maxspeed` (bounds_reflect), `Fieldforce`/`Lifemax`. NOT Particle POP damping (0 on purpose). |
| Particles escaping the box | `bounds_reflect` must have `P` in Output Attributes + hard-clamp pos. |
| No soup / soup ceiling | Soup births = `min(Ambientrate/60, Ambientpoints)` — **`Ambientpoints` is a hard per-cook cap**; keep it ≥ `Ambientrate/60`. Then `maxparticles` + `Lifemax` are the next ceilings. |
| Few movement particles | births = `int((emit + Burstgain·burst)·visible)`; <1 → zero. Lower `Speedscale` (emit saturates sooner) / raise `Burstgain`. |
| Soup frozen "squirt-guns" | `ambient_chop_script` not cooking every frame → must read always-cooking `lag1`. |
| No glow/bloom | `Bloom*`; needs `render1` 16f + `color_attr` HDR. |
| Static curl swirls | `curl_noise` Translate-4D `t4d` = `absTime.seconds × Curlspeed`. |
| Switch look/preset | `Preset`+`Applypreset` → `preset_exec` → `presets.py`. |
| No / wrong audio reaction | `audio_react` Script CHOP + `audio_logic.py`; Audio page (`Audioenable` toggle / `Audioreact` master). ARE wired in via `are_features` Merge (/project1) → `in_audio` In CHOPs (particle_system → velocity_controller). ARE absent → empty → defaults to 0. |
| Post-FX wrong | Chain `render1 → trail_comp → bloom1 → streak_comp → grade → lens_finish → out2`. |
| Notes not spawning | `_get_or_build_game()` must prime wall clock before start. |
| Notes flying wrong way | renderer camera must be `tz=+3`, not negative. |
| Particles drift in z on horizontal motion | `Zforceweight`. |
| Particles biased to a corner | scatter-list mean centering in `emitters_chop_script.py` + `Curlscale` < cloud extent. |

## TD gotchas (cost real time)

- **No Script POP / Source POP / Color SOP.** Use Script CHOP + CHOP-to-POP; Particle POP
  is the hub (modifier chain feeds back via Target Particles Update POP); Primitive SOP "Add" for color.
- **Channel names can't contain `[`/`]`** — use `P0/P1/P2`.
- **Cameras look down -Z** — place camera at +Z, scene at -Z.
- **Velocity damping in `bounds_reflect`, not Particle POP** (`PartVel *= 1 − Velocitydamping`).
- **`Partvel` reserved on Particle POP** — use `Start*` seed prefix.
- **Script CHOP doesn't cook every frame by default** — wire output to a cooking op or set `isTimeSlice=True`.
- **GLSL POP write syntax varies by build** — View Compiled Shader to see output vars.
- **Curl wavelength must be < cloud extent** or whole cloud drifts together (`Curlscale=0.5`).
- **Level TOP threshold makes NEGATIVE pixels — set `clamp=True`** (`trail_gate`/`streak_thresh`);
  negatives into ACES `grade` map to WHITE → frame washes out.
- **Feedback TOP**: `input`=init source, `par.top`=loop-end (back-ref via `par.top`, NOT input).
- **GLSL TOP res through feedback loop**: set explicit `custom` res (Use Input collapses to 128²).
- **GLSL POP vec uniform**: set `vecNtype` to match the shader decl (else Redeclaration error).
- **Parameter Execute DAT fires DEFERRED** (next frame) — a same-script readback sees old values.
- **Billboard sprites**: Rectangle SOP `orient=xy` faces -Z camera; `texture=face` for UVs.

**⚠️ NEVER extract a full Particle/POP point stream to a CHOP** (`poptoCHOP` extract=points on
particle1/render_null/force_null) — pulls ~30k–150k points/cook → freezes (fps 0) or crashes TD.
Use `op.numPoints()` (cheap), a TOP `numpyArray()`, or a heavily-thinned `poptoCHOP` (`thinstep`
large), and `destroy()` the temp op same call. (`fps 0` can also just mean Pause.)

**⚠️ GLSL POP sampler crash:** never let a synced GLSL POP/TOP reference a `sampler2D` before it's
bound to a real TOP — sampling an unbound sampler is a GPU fault that crashes TD, and the synced file
re-crashes on every load until fixed on disk. Bind the TOP FIRST, then edit the shader. (Soup palette
is an in-shader procedural/uniform palette for exactly this reason.) Plain float/vec uniforms are safe
unbound (default 0).

## Self-tests

```bash
cd /Users/flo/work/TD/MediaPipe_Base
python3 velocity_logic.py
python3 ambient_chop_script.py
python3 presets.py
python3 -m beatsaber.saber_logic
python3 -m beatsaber.timeline
python3 -m beatsaber.beatmap
python3 -m beatsaber.hit_detection
python3 -m beatsaber.score
python3 -m beatsaber.game
```

All print `OK — …` on success; < 1s total.
