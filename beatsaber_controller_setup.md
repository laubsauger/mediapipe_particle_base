# beatsaber_controller — setup guide

Beat Saber-inspired rhythm game built on top of `velocity_controller`'s
landmark sensing, with optional MediaPipe Hands knuckle landmarks for
accurate sabre orientation. Same architecture pattern as the rest of
the project: a Base COMP with custom parent pars, synced Text DATs
wrapping the pure-Python [`beatsaber/`](./beatsaber/) package, and
Script CHOPs that emit well-defined output channel sets for rendering.

Targets TD **2025.30960+**. Coexists with the particle pipeline without
touching it — Beat Saber is a consumer of `velocity_controller/out1`.

---

## Build summary

1. Make sure `velocity_controller` emits both wrist and elbow landmarks
   per side (Section 1 below).
2. Build `beatsaber_controller` (Sections 2–6 below).
3. Run `bootstrap_beatsaber_renderer.py` to build `beatsaber_renderer`.
4. Optionally wire a MediaPipe Hands source for wrist-roll-aware
   orientation (Section 6, hand inputs subsection).

The renderer is built end-to-end by the bootstrap script — including
the `sabers_geo` 4-primitive color chain, the trail Geo COMPs with
their MATs, the camera, the Worldscale binding, the HUD Text TOPs, the
flash chain, and the final composite. For `beatsaber_controller`,
follow the build steps below.

---

## Assumptions about the input

Before wiring this up, make sure `velocity_controller` is emitting the
landmarks we need. Required:

- `left_wrist`, `left_elbow`, `right_wrist`, `right_elbow` — pose-side
  landmarks, used as the hilt anchor + the forearm fallback orientation.

Optional (enables the hand-knuckle orientation):

- `left_hand_wrist`, `left_hand_index_mcp`, `left_hand_middle_mcp`,
  `left_hand_pinky_mcp` and the same four for `right_hand_*` — from a
  MediaPipe Hands tox or equivalent.

If your current `Landmarks` par on `velocity_controller` doesn't
include the elbows, add them. In the textport:

    op('/project1/velocity_controller').par.Landmarks = (
        'left_wrist right_wrist left_elbow right_elbow '
        'left_ankle right_ankle nose'
    )

Hand landmarks come from a separate source (the pose tox doesn't emit
them). Wire that source into the controller's input chain via a Merge
CHOP — see step 4 of the minimum-effort path above.

Also **assumes the webcam feed is mirrored** (selfie/front-facing cam
or a Flip TOP in the input chain). This gives the first-person illusion
where a hand on screen-right corresponds to the user's actual right
hand, with sabres attached naturally. The hand-knuckle chirality logic
is built around this convention — do not also flip x in the channel
mapping.

---

## What gets built

```
beatsaber_controller  (Base COMP, all custom pars live here)
│
├── Text DATs (synced to external .py files from /project.folder/)
│   ├── beatsaber_game_tick        → .../beatsaber_game_tick.py
│   ├── beatsaber_notes            → .../beatsaber_notes.py
│   ├── beatsaber_events           → .../beatsaber_events.py
│   ├── beatsaber_parexec          → .../beatsaber_parexec.py
│   └── install_beatsaber_params   → .../install_beatsaber_params.py
│
├── in1                 In CHOP — fed from velocity_controller/out1
│                       (optionally merged with a hand-landmark CHOP)
├── select_landmarks    Select CHOP — narrows to the channels game_tick reads
├── game_tick           Script CHOP — drives the game loop, emits saber state
├── notes_chop          Script CHOP — active notes (one sample per note)
├── events_dat          Script DAT — per-cook event log
├── parexec             Parameter Execute DAT — dispatches Start/Pause/Reset
└── out1                Out CHOP — passes game_tick's output to parents
```

---

## Topology

```mermaid
flowchart LR
    vc[/../velocity_controller/out1/]
    hands[/../mediapipe_hands_chop/<br/>(optional)/]
    merge[merge_landmarks<br/>Merge CHOP]
    in1([in1<br/>In CHOP])
    sel[select_landmarks<br/>*_wrist:* *_elbow:* *_hand_*]
    tick[game_tick<br/>Script CHOP<br/>reads CHOP, runs Game.tick]
    notes[notes_chop<br/>Script CHOP<br/>reads snapshot from storage]
    events[events_dat<br/>Script DAT]
    parexec[parexec<br/>ParExec DAT]
    out1([out1<br/>Out CHOP])

    vc --> merge
    hands --> merge
    merge --> in1 --> sel --> tick --> out1
    tick -. stores 'beatsaber_snapshot' .-> notes
    tick -. stores 'beatsaber_snapshot' .-> events
    parexec -. pulses Start/Pause/Resume/Reset .-> tick
```

`game_tick` is the only op that advances the game. It stores the latest
snapshot as COMP storage; `notes_chop` and `events_dat` are read-only
consumers of that snapshot.

---

## Build steps (full-from-scratch)

### 1. Add required landmarks upstream

Make sure `velocity_controller`'s `Landmarks` par includes both wrists
and both elbows. Textport, once:

    op('/project1/velocity_controller').par.Landmarks = (
        'left_wrist right_wrist left_elbow right_elbow '
        'left_ankle right_ankle nose'
    )

Verify via a Trail CHOP on `velocity_controller/out1` that
`left_elbow:x`, `right_elbow:x` etc. are present and tracking.

### 2. Create the Base COMP

1. Right-click the project root, **Add Operator ▸ COMP ▸ Base**. Rename
   to `beatsaber_controller`.
2. Drop into it.

### 3. Sync the Text DATs

Five Text DATs, each with **File** parameter set to the relative path
from `project.folder` and **Sync File** = On. Same pattern as
`velocity_controller`:

| DAT name | File par |
| --- | --- |
| `beatsaber_game_tick` | `beatsaber_game_tick.py` |
| `beatsaber_notes` | `beatsaber_notes.py` |
| `beatsaber_events` | `beatsaber_events.py` |
| `beatsaber_parexec` | `beatsaber_parexec.py` |
| `install_beatsaber_params` | `install_beatsaber_params.py` |
| `reset_beatsaber_params` | `reset_beatsaber_params.py` |

After creating them, right-click each and hit **Force Reload** to pull
content from disk immediately.

The two `*_params` DATs are a matched pair:
- **`install_beatsaber_params`** is **idempotent** — it only adds pars
  that aren't already there. Existing values are preserved. Run it
  once during initial setup, and again after pulling a new code drop
  that adds new pars.
- **`reset_beatsaber_params`** is **destructive** — it forcibly
  overwrites every par's value with the current codebase defaults.
  Run it when the defaults have moved on, when your tuning has drifted,
  or when handing the project to someone who needs a known baseline.
  Save a note of any custom values first; they're not preserved.

### 4. Install custom pars

Right-click the `install_beatsaber_params` DAT ▸ **Run Script**. That
creates the four custom pages (Sensing, Saber, Gameplay, Debug) on the
COMP. Textport should print
`beatsaber_controller: custom pages installed (16 params total).`

(Optional) immediately follow with right-click `reset_beatsaber_params`
▸ **Run Script** to confirm every par is at its documented default —
useful if you're rebuilding from a known good state and want zero
drift from the codebase. Output looks like
`Sensing: 1/1 pars set. Saber: 7/7 pars set. Gameplay: 6/6 pars set.
Debug: 2/2 pars set.`

### 5. Wire the CHOPs

Inside `beatsaber_controller`:

- **In CHOP** named `in1`. Leave as-is.
- **Select CHOP** named `select_landmarks`. Channel Names pattern:
  `left_wrist:* right_wrist:* left_elbow:* right_elbow:* *_hand_*`.
  The `*_hand_*` segment is forward-looking — when you eventually wire
  the hand tracker, the relevant channels will already pass through.
  Connect `in1 → select_landmarks`.
- **Script CHOP** named `game_tick`. Callbacks DAT: `beatsaber_game_tick`.
  Connect `select_landmarks → game_tick`.
- **Script CHOP** named `notes_chop`. Callbacks DAT: `beatsaber_notes`.
  No inputs — reads purely from parent storage.
- **Script DAT** named `events_dat`. Callbacks DAT: `beatsaber_events`.
  No inputs.
- **Out CHOP** named `out1`. Connect `game_tick → out1`.
- **Parameter Execute DAT** named `parexec`. Callbacks DAT:
  `beatsaber_parexec`. **OPs**: `.` (this COMP). **Parameters**:
  `Start Pause Resume Reset`. **On Pulse**: On.

### 6. Wire the input(s)

At the project level (parent of `beatsaber_controller`):

**Pose only (forearm-orientation fallback):**
- Connect `velocity_controller/out1` → `beatsaber_controller/in1`.

**Pose + hand landmarks (full hand-orientation):**
1. Create a **Merge CHOP** at the project level called `merge_landmarks`.
   Input 0 = `velocity_controller/out1`. Input 1 = your hand-landmark
   source CHOP (output of a MediaPipe Hands tox, post-rename to the
   `<side>_hand_<joint>:<axis>` naming pattern if needed).
2. Connect `merge_landmarks → beatsaber_controller/in1`.

Naming convention for the hand source: each joint emits up to four
channels following the pattern `<side>_hand_<joint>:<axis>`, where
`<side>` is `left` or `right`, `<joint>` is one of
`wrist | index_mcp | middle_mcp | pinky_mcp`, and `<axis>` is `x`,
`y`, `z`, or `visible`. So the eight channels we read are:
`left_hand_wrist:x`, `left_hand_wrist:y`, `left_hand_wrist:z`,
`left_hand_index_mcp:x`, ..., and the four `right_hand_*` equivalents.
The `:visible` channel per joint is optional; treated as visible when
absent. If your tox emits a different schema, drop a Rename CHOP
between it and the Merge to map names.

### 7. Verify

1. Right-click `game_tick` ▸ **View Viewer Active** (or open an Info
   popup). Within a couple seconds you should see channels like
   `left_hilt_x`, `left_hilt_top_x`, `left_tip_y`, `left_up_z`,
   `left_hand_active`, `song_time`, `score`, etc. all populating.
   `song_time` should start near 0 and tick up each cook.
2. Right-click `notes_chop` ▸ View Viewer. Once the first beatmap note
   spawns (default test map: t = 2.0), you should see one sample per
   active note.
3. Right-click `events_dat` ▸ View Viewer. Shows a row per
   spawned/hit/missed/bad-cut event fired this cook.
4. Move a hand in front of the camera and confirm `left_hand_active`
   reads `1` whenever the hand-tracking is contributing to the basis,
   and `0` when it falls back to the forearm.

If `game_tick`'s textport shows `beatsaber_game_tick: import failed`,
the `beatsaber/` package isn't visible on Python's `sys.path`. The
callback pushes `project.folder` onto `sys.path` at import time; this
usually only happens if `project.folder` isn't set (unsaved .toe).
Save the project once and force-reload the DAT.

---

## Orientation pipeline

The sabre's pose each cook is built from one or two sources, fused, and
smoothed:

1. **Hand-knuckle basis (preferred when present).** Wrist + the three
   knuckle landmarks (index_MCP, middle_MCP, pinky_MCP) define a plane:
   the forward axis is the wrist-to-middle-MCP vector (the long axis
   of the palm), and the palm normal comes from the chirality-aware
   cross product of the cross-palm vector with the forward vector. We
   never read fingertip landmarks — they're too jittery. Knuckles only.
2. **Forearm fallback.** When the hand isn't visible (or its landmarks
   collapse onto each other), we use the elbow→wrist vector for the
   forward axis with a default palm-normal hint of `+Z` (toward camera).
   This is the same orientation source the previous version used.
3. **Confidence-weighted blend.** When both are available, the forward
   axis is blended (controlled by `Handweight`, default 1.0 = trust the
   hand). The up axis (palm normal) always comes from the hand basis
   when present, since the forearm has no roll information.
4. **Gram-Schmidt re-orthonormalization.** Whatever combination we end
   up with, the up axis is projected to be exactly perpendicular to the
   forward axis and renormalized, then `right = up × forward`. This
   guarantees a valid rotation matrix every frame, even when individual
   landmarks wobble.
5. **Temporal smoothing on the up axis (palm normal) only.** EMA-lerp
   toward the new target with time constant `Orientsmooth` (default
   0.03 s). The forward axis is taken instantaneously each cook so
   fast swings aren't dragged by smoothing. (We tried full quaternion
   slerp; it interpolates through the 4D shortest arc, which during a
   180°-in-one-cook forearm flip — which happens during a hard down-swing
   — produces visible sideways twists. Splitting the smoothing avoids
   that.)

If the sabre wobbles too much at rest, raise `Orientsmooth` (try 0.05).
If hard wrist twists feel laggy, lower it (try 0.015). If you don't
have hand tracking and want to disable the hand path entirely, set
`Handweight` to 0.

---

## Output contract — what game_tick emits

Per-saber channels (each `<side>` ∈ `{left, right}`):

| Channel | What |
| --- | --- |
| `<side>_hilt_<x/y/z>` | Hilt base position at the wrist |
| `<side>_hilt_top_<x/y/z>` | Hilt-blade junction (where the blade emerges from the fist) |
| `<side>_tip_<x/y/z>` | Far end of the blade (used for swept-volume collision) |
| `<side>_dir_<x/y/z>` | Forward unit vector (hilt → tip) |
| `<side>_up_<x/y/z>` | Palm-normal unit vector (saber roll axis) |
| `<side>_vel_<x/y/z>` | Tip velocity over 1 cook |
| `<side>_tip_speed` | Magnitude of `vel` (for trail intensity etc.) |
| `<side>_hand_active` | 1 when the hand-knuckle basis contributed, 0 otherwise |

Globals:

| Channel | What |
| --- | --- |
| `song_time` | Seconds from song start |
| `score`, `combo`, `max_combo`, `multiplier` | Score state |
| `hits`, `misses`, `bad_cuts`, `accuracy` | Running totals |
| `hit_this_frame`, `miss_this_frame`, `bad_cut_this_frame`, `spawned_this_frame` | 0/1 flags for VFX triggers |
| `active_notes` | Count of currently alive notes |

UI feedback (persistent across cooks; held until the next event):

| Channel | What |
| --- | --- |
| `last_hit_quality` | Quality (0..1) of the most recent good hit |
| `last_swing_speed` | Magnitude of the swing on the most recent hit/bad-cut |
| `last_hit_saber` | 0 = none, 1 = left, 2 = right |
| `last_event_kind` | 0 = none, 1 = hit, 2 = miss, 3 = bad cut |
| `time_since_event` | Seconds elapsed since the most recent event (drives fades) |

Upcoming-note hints (next-not-yet-hit note, or zeros if none):

| Channel | What |
| --- | --- |
| `upcoming_time` | Hit time of the soonest active note |
| `upcoming_x`, `upcoming_y` | World position |
| `upcoming_color` | 0 = none, 1 = red, 2 = blue |
| `upcoming_cut_x`, `upcoming_cut_y` | Required cut direction (unit vec, xy only) |
| `upcoming_dt` | Seconds until hit time (negative = past hit) |

`notes_chop` continues to emit one sample per active note as before
(see existing channel list there).

---

## UI feedback wiring (renderer side)

These are recipes for hooking the new feedback channels into the
renderer. They live in `beatsaber_renderer` (or wherever you composite
the final image), not in the controller. Operator-level instructions —
no Python in this section.

### Score / combo / accuracy HUD

The renderer ships with five Text TOPs (`text_score`, `text_combo`,
`text_accuracy`, `text_song_time`, `text_eventlog`) whose `text`
parameters call helper functions in `beatsaber_hud.py` to format
`score`, `combo`, `multiplier`, `accuracy`, `song_time`, and the event
log. See `beatsaber_renderer_setup.md` Section 7 for the full operator
table.

To extend the HUD with additional readouts, drop another Text TOP and
bind its `text` parameter to a Python expression like
`mod('beatsaber_hud').<your_helper>()`, then composite into `comp_out`.

### Hit / miss / bad-cut bursts

For per-event color flashes localized at the hit point:

1. Add a **Constant TOP** named `flash_color`. Its RGB is driven by
   `last_event_kind`:
   - Add a CHOP-to-DAT or a Lookup TOP keyed on the channel.
   - Mapping: 1 (hit) → green, 2 (miss) → grey, 3 (bad cut) → red.
2. Add a **Lag CHOP** on `time_since_event` with a fast attack
   (~5 ms) and slow release (~500 ms). Output is a fade envelope that
   peaks at the moment of an event and decays.
3. Multiply `flash_color` by the lag envelope (Math TOP, multiply).
4. Composite over `render_scene` via a Composite TOP set to **Add**.

For a localized burst at the actual hit position, route the composite's
position from `last_hit_saber`'s corresponding `<side>_tip_x/y`.

### Cut-direction arrow on each note

Instance an arrow billboard (Box SOP scaled to `(0.05, 0.05, 0.001)` is
fine for a debug version) inside `notes_geo` alongside the cube. On
the Geometry COMP's Instance page, drive its rotation from a CHOP
expression `degrees(atan2(cut_y, cut_x))` reading from `notes_chop`.

### Slash-strength trail

Add a **Trail CHOP** in `beatsaber_renderer` reading
`game_tick`'s `left_tip_x/y/z right_tip_x/y/z` channels with a window
length of `parent('beatsaber_controller').par.Trailframes`. Pipe via a
**CHOP-to-SOP** ("Channels are Points" mode) into a **Line SOP**, then
into the existing `sabers_geo` graph (or a parallel line-only Geometry
COMP). Color the trail by mixing the saber's blade color with a
brightness factor driven by `<side>_tip_speed` (use a Math TOP /
Math CHOP to remap speed → 0..1 brightness). A fast swing produces a
bright thick streak; a slow drift produces a faint trace.

### Move-expected indicator

Render a small ghost cube at `(upcoming_x, upcoming_y, 0)` colored by
`upcoming_color` (red or blue), pulsing in alpha based on
`upcoming_dt` (full opacity at `upcoming_dt = 0.5`s before hit, fade
to 0 by `upcoming_dt = 0`). One Constant SOP cube with a Geometry COMP
+ a CHOP reference to drive translation/color/alpha is enough.

---

## Camera / coordinate convention

Same as before. The renderer's `game_cam` sits at `(0.5, 0.5, +3.0)`
with zero rotation, looking down its local -Z into the approach
tunnel. Notes spawn at `z ≈ -10` and travel toward `z = 0` (the hit
plane). Sabres live on the hit plane with their hilt at `z = 0` and
the blade extending forward (mostly into -z, depending on user
posture). See `beatsaber_renderer_setup.md` for the camera
verification checklist.

---

## Gameplay controls (parexec)

Pulse buttons on the Gameplay page trigger game state transitions via
the `beatsaber_parexec` Parameter Execute DAT:

- **Start** — reset and begin from t=0
- **Pause** — freeze `song_time` (notes stop moving, score frozen)
- **Resume** — unfreeze
- **Reset** — clear notes + score, game enters "not started" state

If you want keyboard shortcuts, create a Keyboard In DAT and have it
pulse the corresponding par on the COMP.

---

## Switching beatmaps

Change `Beatmapfile` on the Gameplay page — the `game_tick` Script CHOP
notices the par value change, loads the new JSON, and rebuilds the
`Game` object on the next cook. Old progress is cleared.

Beatmap files are JSON in the format described by
[`beatsaber/beatmap.py`](./beatsaber/beatmap.py):

    {
        "title": "my map",
        "bpm": 120,
        "travel_time": 2.0,
        "z_spawn": -10.0,
        "notes": [
            {"time": 2.0, "x": 0.3, "y": 0.5, "color": "red",  "cut": "down"},
            {"time": 3.0, "x": 0.7, "y": 0.5, "color": "blue", "cut": "down"}
        ]
    }

`travel_time` controls lead time: a note with `time: 5.0` and
`travel_time: 2.0` spawns at `song_time = 3.0`, travels from
`z = z_spawn` to `z = 0` over 2 seconds, and arrives for the hit at
`song_time = 5.0`. **Note time is the HIT time, not the spawn time.**

### Audio-driven beatmaps (planned, see `beatsaber.beatmap_gen`)

A standalone `beatsaber.beatmap_gen` module is being added that takes
an audio file and produces a beatmap automatically. Timing model:

- Onset / beat detection (librosa) gives per-beat target HIT times.
- The generator subtracts `lead_time = travel_time` from each hit
  time to produce the spawn time the game expects.
- A single `audio_visual_offset_ms` knob (default 0) globally shifts
  hit times to compensate for end-to-end latency (audio buffer +
  MediaPipe inference lag). **If hits feel consistently early, raise
  this value by 10 ms; if they feel late, lower it.** Calibrate by
  ear with one note repeating on every beat.
- Generator and game must read the same monotonic clock — usually the
  audio playback clock, not wall time. When you hook up an Audio
  File In TOP for music playback, replace `absTime.seconds` in
  `beatsaber_game_tick.py` with that TOP's time channel (already
  documented in `beatsaber_renderer_setup.md`).

CLI: `python -m beatsaber.beatmap_gen <track.mp3> <out.json>` once the
module lands. Required pip installs: `librosa`, `numpy`, `soundfile`.

---

## Parameter rundown

### Sensing

| Par | Default | Role |
| --- | --- | --- |
| `Visibilitythreshold` | 0.5 | Gate on `<L>:visible` — when elbow/wrist drop below this the saber direction holds last-good |

### Saber

| Par | Default | Role |
| --- | --- | --- |
| `Hiltlength` | 0.04 UV | Hilt segment length (wrist to blade emergence) |
| `Bladelength` | 0.21 UV | Blade segment length |
| `Handweight` | 1.0 | Blend weight for hand basis vs forearm fallback (0 = forearm only) |
| `Orientsmooth` | 0.03 s | EMA-lerp time constant on the palm-normal axis |
| `Zextrusion` | 0.3 | Forearm-fallback `-Z` tilt (hand basis ignores this) |
| `Hiltplanez` | 0.0 | World z of the hilt base (0 = hit plane) |

### Gameplay

| Par | Default | Role |
| --- | --- | --- |
| `Beatmapfile` | `beatsaber/test_beatmap.json` | Path to the current beatmap |
| `Autostart` | On | Start the timeline on first cook |
| `Loop` | On | Auto-restart when the beatmap finishes |
| `Angletolerancerad` | 1.0 rad (~57°) | Max direction error for a good cut |
| `Minswingspeed` | 0.02 UV/cook | Below this, cuts don't count (avoids tap-hits) |
| `Misswindowseconds` | 0.25 s | How long past note.time we wait before calling miss |
| `Start/Pause/Resume/Reset` | — | Pulse buttons (dispatched via parexec) |

### Debug

| Par | Default | Role |
| --- | --- | --- |
| `Enableeventslog` | On | Toggle events_dat cook (no cost when off) |
| `Trailframes` | 8 | Length of saber trail in frames (for the swept-volume debug render) |

---

## Coexistence with velocity_controller / particles

The only shared resource is `velocity_controller/out1`.
`beatsaber_controller` is pure consumer — it subscribes to the landmark
channels and runs its own game loop in parallel. Adding elbow landmarks
to the upstream `Landmarks` par doesn't affect particle behavior
(particle's `emitters_chop_script` only reads the landmarks named in
its own `Landmarks` par, which is independent).

You can run:
- Only particles (don't create `beatsaber_controller`)
- Only the game (connect `velocity_controller` → `beatsaber_controller`
  and disable the particle render tree)
- Both simultaneously (particle TOP and game TOP composited)

For a "particles react to saber cuts" feature later: subscribe the
particle emission to `game_tick`'s `hit_this_frame` channel and blast a
burst at the hit note's `(x, y, z)`. That's a one-wire addition.

---

## Verifying the full loop

Simple smoke test:

1. Project saved, `velocity_controller` wired and emitting channels.
2. `beatsaber_controller` built per the steps above.
3. (Optional) hand-landmark source merged into the input.
4. Stand in front of camera with both hands visible.
5. Check `game_tick`'s Info popup: `left_hilt_x` ≈ your left wrist x,
   `left_tip_y` offset above/below it based on forearm orientation,
   `left_up_z` ≈ +1 if you're holding the sabre upright with palm
   facing the camera.
6. Wait for `song_time` to reach 2.0s — first note spawns. Watch
   `notes_chop`'s sample count go from 0 to 1.
7. Do a vertical down-swing with your left hand around
   `(x=0.3, y=0.5)`. Watch `score`, `combo`, `hits` increment.
   `events_dat` should show a row with `event=hit`, `saber=left`,
   `quality > 0.5`.
8. Let a note pass without hitting. After ~0.25s past its hit time,
   `misses` increments.

If you see score register on clean cuts and miss on no-cuts, the loop
is working end-to-end.

---

## Troubleshooting

### "AttributeError: 'Game' object has no attribute 'loop'"

A stale `Game` instance from a previous TD session was cached in
`comp.store()` and is missing the attribute. Fixed by force-reloading
`beatsaber_game_tick` after the latest update; the new code keeps
`Game.loop` as a class-level default and uses `getattr` to defend
against this. If you still see it: `op('beatsaber_controller').unstore('beatsaber_game')`
in the textport, then save and reload.

### Sabre orientation seems flipped

Check that the upstream image is mirrored (selfie cam style) — the
hand-knuckle chirality logic assumes this. If your input ISN'T mirrored,
either add a Flip TOP to your input chain (preferred), OR set
`Handweight` to 0 to fall back to the rotation-agnostic forearm path.

### Sabre doesn't roll when I twist my wrist

`left_hand_active` reads 0 → hand tracking isn't reaching the
controller. Check (a) the hand source is connected, (b) the channel
names match the `<side>_hand_<joint>:<axis>` pattern, (c) the
`select_landmarks` Select CHOP includes `*_hand_*` in its pattern,
(d) the per-landmark `:visible` channels (if present) clear the
`Visibilitythreshold`.

### Sabre is jumpy / wobbles a lot

Raise `Orientsmooth` to 0.05 or 0.06. The default 0.03 favors
responsiveness over noise rejection — fine in good lighting, twitchy
in dim or cluttered backgrounds.

### Hard swings produce bad cuts (wrong direction)

Lower `Orientsmooth` to 0.015. Default 0.03 is fast enough for typical
swings but noticeably lags very fast wrist twists.

### Score not registering

Open `game_tick`'s Info popup. If `song_time` is 0 → game isn't started
(press the Gameplay page's `Start` pulse). If `song_time` is huge
(thousands of seconds) → `_get_or_build_game` should be priming the
timeline's wall clock; pull the latest `beatsaber_game_tick.py`.

### `notes_chop` sample count never goes above 0

Check `Beatmapfile` exists at the path shown. Default
`beatsaber/test_beatmap.json` should exist next to the .toe. The
test map's first note is at `time = 2.0`, so wait until
`song_time ≥ 2.0` before expecting samples.
