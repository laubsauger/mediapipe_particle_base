# Pose Sensor Contract

A single channel contract for ANY pose source. Drop in the MediaPipe tox,
plug in a Kinect/Orbbec, or feed pose over OSC — the rest of the pipeline
(velocity sensing, particle controller, beatsaber game) doesn't change.

The pipeline expects ONE COMP that outputs a CHOP matching this schema. We
keep that "input port" inside `velocity_controller` as the existing `in_pose`
CHOP. An ADAPTER COMP (one per sensor) feeds it.

---

## Canonical schema

We use **MediaPipe Pose** as the lingua franca (33 landmarks). Other sensors
map TO this. If a sensor doesn't track a joint (e.g. Kinect Azure has no
ear/eye/mouth detail), the adapter emits **`x=0, y=0, z=0, visibility=0`** for
that landmark — the downstream visibility-gate skips it cleanly.

### Per-landmark channels (33 × 4 = 132 channels)

For each landmark `lm` (the 33 names below), emit:

| Channel | Type | Range | Meaning |
| --- | --- | --- | --- |
| `<lm>:x` | float | `[0, 1]` (mirror selfie convention) | normalised image x — `0` = left edge, `1` = right edge |
| `<lm>:y` | float | `[0, 1]` | normalised image y — `0` = top, `1` = bottom |
| `<lm>:z` | float | rough `[-1, 1]` | depth relative to the hip midpoint; `-z` = toward camera. Sensors that have NO depth must emit `0`. Noisy — downstream applies `Zspeedweight`/`Zforceweight` to tame it. |
| `visibility<idx>` | float | `[0, 1]` | per-landmark confidence — used by visibility-gating and standby detection. `<idx>` = the integer MediaPipe index (0..32, see table below). |

Coordinate convention notes:
- **x is normalised by image WIDTH, y by HEIGHT.** On a 16:9 feed the physical
  scale differs — `velocity_logic` aspect-corrects `vx` so velocity is isotropic
  (see `aspect` param). Adapters MUST follow this same width/height normalisation.
- **Mirrored (selfie) view** is the default: the performer's right hand reads
  on screen-right. Adapters from non-mirrored sources should flip x → `1 - x`.
- **`y = 0` is TOP** of the image (image convention, not OpenGL).

### The 33 landmarks (MediaPipe Pose indices)

```
 0 nose              11 left_shoulder      23 left_hip
 1 left_eye_inner    12 right_shoulder     24 right_hip
 2 left_eye          13 left_elbow         25 left_knee
 3 left_eye_outer    14 right_elbow        26 right_knee
 4 right_eye_inner   15 left_wrist         27 left_ankle
 5 right_eye         16 right_wrist        28 right_ankle
 6 right_eye_outer   17 left_pinky         29 left_heel
 7 left_ear          18 right_pinky        30 right_heel
 8 right_ear         19 left_index         31 left_foot_index
 9 mouth_left        20 right_index        32 right_foot_index
10 mouth_right       21 left_thumb
                     22 right_thumb
```

The minimum we actually USE downstream is much smaller — see `body_logic.JOINTS`
(13 joints, the major skeleton) and `install_velocity_params.Landmarks` (5 by
default: nose, wrists, ankles). Adapters can emit only those for cheaper sensors;
fill the rest with zeros + visibility 0.

---

## Adapter pattern

An adapter is a TD COMP whose output CHOP matches the schema above. The COMP's
internal layout is the adapter's business; the external port is the contract.

### Existing: MediaPipe (`adapters/mediapipe_adapter.py`)

The MediaPipe tox already emits the schema natively — the "adapter" is a
near-passthrough Base COMP that wraps the tox + exposes a stable
`out_pose` CHOP. (The tox itself is huge and not redistributed — drop into
`toxes/` manually; see `.gitignore`.)

### Future: Kinect Azure (`adapters/kinect_adapter.py`)

Kinect has 32 body joints with its own naming + a different coordinate space
(meters in 3D, depth camera origin). Map them into the canonical schema:

- Project the 3D joints to the depth-camera image plane → `(x_px, y_px)`,
  then normalise: `x = x_px / width`, `y = y_px / height`.
- Mirror x if the source isn't selfie-cammed: `x = 1 - x`.
- `z` = `(depth_metres - hip_depth_metres)` scaled to roughly `[-1, 1]`.
- Map Kinect's joint names → MediaPipe names (head→nose, shoulder/elbow/wrist
  pairs match, ankle/heel/foot map directly). Joints with no equivalent stay 0.
- Visibility: Kinect emits a confidence per joint — clamp into `[0, 1]`.

### Future: Orbbec (`adapters/orbbec_adapter.py`)

Similar to Kinect — Orbbec Body Tracking SDK exposes a 21-joint skeleton.
Project + normalise + remap names. Joints with no equivalent emit 0.

### Future: OSC / generic (`adapters/osc_adapter.py`)

Any external pipeline (a custom CV model, an external mocap server, …) can push
landmark data over OSC. Define an address pattern, e.g.:

```
/pose/<lm>/x      <float 0..1>
/pose/<lm>/y      <float 0..1>
/pose/<lm>/z      <float -1..1>
/pose/visibility/<idx>   <float 0..1>
```

A TD OSC In CHOP + a Rename CHOP turns this into the canonical schema.

---

## Plumbing it in (planned, not yet wired)

A `sensor` Base COMP at `/project1/sensor` with a `Source` menu par
(`MediaPipe / Kinect / Orbbec / OSC / Custom`) selects ONE adapter to be the
active output. The COMP's single output is `out_pose` matching the contract.

`velocity_controller/in_pose` is set to read `/project1/sensor/out_pose` — so
nothing inside `velocity_controller` cares which sensor is upstream. Adapters
can be added/swapped without touching the rest of the project.

Until that's wired, MediaPipe stays the direct upstream (the existing path).
This doc + `adapters/` already exist as the foundation.
