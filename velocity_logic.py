"""
velocity_logic.py
=================

Pure-Python logic for the velocity-driven particle controller. No TouchDesigner
imports in here, so you can run `python velocity_logic.py` outside TD and
see the state evolve through a synthetic trajectory as a sanity check.

Place this file in TD as a Text DAT named `velocity_logic` (extension set to
.py or not — both work when accessed via `mod`). It's the sibling of a
Script CHOP whose callback DAT is `velocity_script_chop`.

Design
------
The controller senses N landmarks (default: left_wrist, right_wrist,
left_ankle, right_ankle, nose) and, for each one, emits a small bundle of
per-landmark channels that a downstream particle renderer consumes:

    <L>:x        <L>:y         normalized position (pass-through, 0..1)
    <L>:vx       <L>:vy        smoothed velocity, units/sec in 0..1 space
    <L>:speed                  |v|
    <L>:accel                  |Δv|/Δt magnitude, smoothed
    <L>:emit                   emission rate 0..1  (driven by speed)
    <L>:burst                  burst gate 0..1    (driven by accel spikes)
    <L>:visible                visibility gate 0/1 (visibility > threshold)

Plus a handful of globals:

    total_motion, total_burst, frame_dt

Smoothing & burst semantics
---------------------------
Velocity is computed with a finite difference on position, then low-passed
with a one-pole EMA whose time constant is `velocity_smooth` seconds.

Acceleration is the EMA-smoothed finite difference of velocity. The burst
channel is an envelope that snaps to `|a| / accel_scale` whenever that
exceeds `accel_threshold`, then decays exponentially with time constant
`burst_decay` seconds. That gives you a "spike then tail" pulse per whip
motion rather than a noisy instantaneous accel reading.

The Script CHOP calls `update()` once per cook with (dt, positions, vis).
Everything user-tunable arrives as kwargs; no parameter lives inside this
module. Same parent-pars-only convention as painting_controller.
"""

import math

# ---------------------------------------------------------------------------
# Default landmark set
# ---------------------------------------------------------------------------
# You can override LANDMARKS at call time; this is just the default the
# Script CHOP and install_velocity_params.py agree on. Names match
# blankensmithing's pose channel prefixes.
LANDMARKS = (
    "left_wrist",
    "right_wrist",
    "left_ankle",
    "right_ankle",
    "nose",
)

# ---------------------------------------------------------------------------
# Per-landmark channel suffixes (emitted in this fixed order per landmark)
# ---------------------------------------------------------------------------
PER_LANDMARK_CHANS = (
    "x", "y",
    "vx", "vy",
    "speed",
    "accel",
    "emit",
    "burst",
    "visible",
)

GLOBAL_CHANS = (
    "total_motion",
    "total_burst",
    "frame_dt",
)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _fresh_landmark_state():
    """Per-landmark state template. Keep this as the single source of truth
    for the inner-dict schema — `new_state` and `ensure_schema` both use it
    so adding a new field never leaves old sessions in an inconsistent shape."""
    return {
        "prev_x":      None,  # previous position, None = uninitialised
        "prev_y":      None,
        "vx":          0.0,   # smoothed velocity
        "vy":          0.0,
        "prev_vx":     0.0,   # previous smoothed velocity (for accel)
        "prev_vy":     0.0,
        "accel":       0.0,   # smoothed |a|
        "burst":       0.0,   # burst envelope (0..1, decays)
        "last_good_x": None,  # last trusted position, held on dropout
        "last_good_y": None,
    }


def new_state(landmarks=LANDMARKS):
    """
    Build a fresh state dict. Keyed by landmark name. The Script CHOP
    stashes this on the parent COMP via op.store() so it survives
    cook-to-cook and is reset cleanly on reload.
    """
    return {lm: _fresh_landmark_state() for lm in landmarks}


def ensure_schema(state, landmarks):
    """
    Migrate a state dict forward when this module adds new fields. Called
    by the Script CHOP every cook — cheap and idempotent.

    - Adds landmarks that are in `landmarks` but not in `state`.
    - Leaves landmarks in `state` that aren't in `landmarks` alone (they're
      harmless stale data; the caller decides whether to rebuild).
    - Backfills any missing inner-dict keys with defaults so old sessions
      don't KeyError after a schema bump.

    Mutates `state` in place and returns it.
    """
    template = _fresh_landmark_state()
    for lm in landmarks:
        if lm not in state or not isinstance(state[lm], dict):
            state[lm] = _fresh_landmark_state()
        else:
            for k, v in template.items():
                state[lm].setdefault(k, v)
    return state


def reset_state(state):
    """Clear all sampling history; call when the source drops out for a while."""
    for lm in state:
        s = state[lm]
        s["prev_x"] = None
        s["prev_y"] = None
        s["vx"] = 0.0
        s["vy"] = 0.0
        s["prev_vx"] = 0.0
        s["prev_vy"] = 0.0
        s["accel"] = 0.0
        s["burst"] = 0.0
        # Keep last_good_x/y across resets — they're the whole point of
        # holding position on dropout, and are harmless if stale.


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _ema_alpha(dt, tau):
    """
    One-pole EMA coefficient for a target time constant `tau` (seconds).
    alpha=1 passes raw, alpha=0 freezes. Clamped so dt > tau still behaves.
    """
    if tau <= 0.0:
        return 1.0
    # 1 - exp(-dt/tau) is the exact form; small-dt approximation dt/tau
    # is also fine but we pay for the exp to keep big frame hitches sane.
    a = 1.0 - math.exp(-dt / tau)
    if a < 0.0:
        return 0.0
    if a > 1.0:
        return 1.0
    return a


def _clamp01(v):
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# ---------------------------------------------------------------------------
# Core update: called once per cook per landmark
# ---------------------------------------------------------------------------

def update_landmark(sample, x, y, visible, trusted, dt, params):
    """
    Advance one landmark's state by one frame.

    sample   : dict entry from state[lm] — mutated in place
    x, y     : new position in 0..1 (MediaPipe-space)
    visible  : bool — True if MediaPipe confidence ≥ Visibilitythreshold
                      (output gate — drives the `visible` channel).
    trusted  : bool — True if MediaPipe confidence ≥ Trustthreshold,
                      i.e. high enough to cache this position as last-good.
                      Always implies `visible` (Trustthreshold ≥ Visibilitythreshold).
    dt       : seconds since previous cook
    params   : dict with keys
                 velocity_smooth, accel_smooth, speed_scale,
                 accel_threshold, accel_scale, burst_decay, max_jump

    Three zones
    -----------
    1. trusted=True:        use raw (x, y), update last_good, compute velocity,
                            output visible=1.
    2. visible=True, trusted=False (marginal zone):
                            output last_good, NOT raw. last_good is NOT updated.
                            Envelopes still update normally. visible=1 so the
                            emitter stays on, but pinned to the last trusted
                            position. Prevents the "drift to garbage" slide
                            during MediaPipe's confidence ramp-down.
    3. visible=False:       output last_good, visible=0, envelopes decay.
                            Net: blob fades out in place.

    Also: even in the trusted zone, if (x, y) jumps by more than `max_jump`
    from last_good, the frame is rejected (treated as zone 2) — belt-and-
    suspenders against single-frame teleports that are somehow still flagged
    trusted.

    Returns a dict of per-landmark output values:
      x, y, vx, vy, speed, accel, emit, burst, visible
    """
    # ---- Burst envelope decays every cook regardless of visibility. -------
    decay_a = _ema_alpha(dt, params["burst_decay"])
    sample["burst"] *= (1.0 - decay_a)

    # ---- Jump rejection: treat a huge one-frame position jump as untrusted --
    # even if MediaPipe still flags the landmark trusted. Common when the
    # joint leaves the frame boundary one cook before confidence has caught up.
    jumped = False
    if (trusted
            and sample["last_good_x"] is not None
            and params.get("max_jump", 0.0) > 0.0):
        dx = x - sample["last_good_x"]
        dy = y - sample["last_good_y"]
        if (dx * dx + dy * dy) > (params["max_jump"] * params["max_jump"]):
            jumped = True

    # A jumped frame is demoted out of the trusted zone but keeps its
    # `visible` flag — so it lands in the "marginal" branch below.
    if jumped:
        trusted = False

    # ---- Zone 2 & 3: not trusted (either marginal-visible or invisible) ---
    # Fall through to the fade-in-place path. In both cases we output
    # last_good (never raw), so the blob doesn't slide during confidence
    # ramp-downs. In zone 3 the output `visible` is 0 so downstream gates
    # kill spawning; in zone 2 it's still 1 so emitter stays on, just pinned.
    if not trusted or dt <= 0.0:
        if not visible:
            # Invisible — decay everything toward 0.
            alpha_v = _ema_alpha(dt, params["velocity_smooth"])
            sample["vx"] *= (1.0 - alpha_v)
            sample["vy"] *= (1.0 - alpha_v)
            sample["prev_vx"] = sample["vx"]
            sample["prev_vy"] = sample["vy"]
            alpha_a = _ema_alpha(dt, params["accel_smooth"])
            sample["accel"] *= (1.0 - alpha_a)
            sample["prev_x"] = None
            sample["prev_y"] = None
        else:
            # Marginal — pin position, but don't aggressively decay velocity
            # (the limb might still be moving; we just don't trust these
            # specific samples). Blank prev_x/y so the next trusted frame
            # doesn't compute velocity against the stale-held position.
            sample["prev_x"] = None
            sample["prev_y"] = None

        # Output last_good if we have one, else raw (first-frame fallback).
        if sample["last_good_x"] is not None:
            out_x = sample["last_good_x"]
            out_y = sample["last_good_y"]
        else:
            out_x, out_y = x, y
        return _emit(sample, out_x, out_y, visible, params)

    # ---- Zone 1: trusted. Commit last_good and run normal velocity math. --
    sample["last_good_x"] = x
    sample["last_good_y"] = y

    # ---- Velocity via finite diff + EMA smooth ----------------------------
    if sample["prev_x"] is None:
        # First valid sample: seed, don't diff (would produce a huge spike
        # because prev == 0).
        sample["prev_x"] = x
        sample["prev_y"] = y
        sample["vx"] = 0.0
        sample["vy"] = 0.0
        sample["prev_vx"] = 0.0
        sample["prev_vy"] = 0.0
        sample["accel"] = 0.0
        return _emit(sample, x, y, visible, params)

    raw_vx = (x - sample["prev_x"]) / dt
    raw_vy = (y - sample["prev_y"]) / dt

    alpha_v = _ema_alpha(dt, params["velocity_smooth"])
    new_vx = sample["vx"] + alpha_v * (raw_vx - sample["vx"])
    new_vy = sample["vy"] + alpha_v * (raw_vy - sample["vy"])

    # ---- Acceleration magnitude via diff of smoothed velocity -------------
    raw_ax = (new_vx - sample["prev_vx"]) / dt
    raw_ay = (new_vy - sample["prev_vy"]) / dt
    raw_a_mag = math.sqrt(raw_ax * raw_ax + raw_ay * raw_ay)

    alpha_a = _ema_alpha(dt, params["accel_smooth"])
    smoothed_a = sample["accel"] + alpha_a * (raw_a_mag - sample["accel"])

    # ---- Burst detection --------------------------------------------------
    # Normalised magnitude above the threshold arms the envelope.
    if smoothed_a > params["accel_threshold"]:
        # How much over? Map to 0..1 via accel_scale.
        over = (smoothed_a - params["accel_threshold"]) / max(params["accel_scale"], 1e-6)
        new_burst = _clamp01(over)
        # Use max(existing, new) so overlapping snaps don't cancel the tail.
        if new_burst > sample["burst"]:
            sample["burst"] = new_burst

    # ---- Commit state for next cook ---------------------------------------
    sample["prev_x"] = x
    sample["prev_y"] = y
    sample["prev_vx"] = sample["vx"]
    sample["prev_vy"] = sample["vy"]
    sample["vx"] = new_vx
    sample["vy"] = new_vy
    sample["accel"] = smoothed_a

    return _emit(sample, x, y, visible, params)


def _emit(sample, x, y, visible, params):
    speed = math.sqrt(sample["vx"] * sample["vx"] + sample["vy"] * sample["vy"])
    emit = _clamp01(speed / max(params["speed_scale"], 1e-6))
    return {
        "x": x,
        "y": y,
        "vx": sample["vx"],
        "vy": sample["vy"],
        "speed": speed,
        "accel": sample["accel"],
        "emit": emit,
        "burst": sample["burst"],
        "visible": 1.0 if visible else 0.0,
    }


# ---------------------------------------------------------------------------
# Batch update — called once per cook by the Script CHOP with all landmarks
# ---------------------------------------------------------------------------

def update(state, samples, dt, params):
    """
    state   : dict from new_state()
    samples : dict {landmark_name: (x, y, visible_bool, trusted_bool)}
              `visible` gates the output (emit/burst can still fire).
              `trusted` governs whether to update last_good and run velocity
              math on this frame — should be stricter than `visible`.
              For back-compat, a 3-tuple (x, y, visible) is also accepted;
              trusted is then assumed equal to visible.
              Missing landmarks are skipped (their state still decays).
    dt      : seconds since previous cook
    params  : dict — see update_landmark()

    Returns:
      per_landmark : {landmark_name: {...output fields...}}
      globals      : {total_motion, total_burst, frame_dt}
    """
    per_landmark = {}
    total_motion = 0.0
    total_burst = 0.0
    for lm, st in state.items():
        if lm in samples:
            s = samples[lm]
            if len(s) == 3:
                x, y, vis = s
                trust = vis
            else:
                x, y, vis, trust = s
        else:
            # No sample — decay envelopes, emit zeros.
            x, y, vis, trust = 0.0, 0.0, False, False
        out = update_landmark(st, x, y, vis, trust, dt, params)
        per_landmark[lm] = out
        total_motion += out["speed"]
        total_burst  += out["burst"]

    return per_landmark, {
        "total_motion": total_motion,
        "total_burst":  total_burst,
        "frame_dt":     dt,
    }


# ---------------------------------------------------------------------------
# Default params helper — matches the custom-page defaults in installer
# ---------------------------------------------------------------------------

def default_params():
    return {
        "velocity_smooth": 0.08,   # s — shorter = snappier velocity
        "accel_smooth":    0.05,   # s — shorter = crisper bursts, more noise
        "speed_scale":     2.5,    # 1/s — speed/scale is the emit rate
        "accel_threshold": 8.0,    # 1/s² — below this, no burst
        "accel_scale":     40.0,   # 1/s² — full-burst accel above the threshold
        "burst_decay":     0.35,   # s — burst tail length
        "max_jump":        0.30,   # UV units per frame — over this = teleport
    }


# ---------------------------------------------------------------------------
# Self-test: synthetic trajectory. Run `python velocity_logic.py`.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate a wrist being still, then doing a fast whip, then stopping.
    # Landmark: single 'left_wrist'.
    import random
    random.seed(0)

    params = default_params()
    state = new_state(("left_wrist",))
    dt = 1.0 / 60.0

    def feed(traj_fn, n_frames, label):
        print(f"\n--- {label} ---")
        print(f"{'f':>3} {'x':>5} {'y':>5} {'spd':>6} {'acc':>7} {'emit':>5} {'brst':>5}")
        for i in range(n_frames):
            x, y = traj_fn(i)
            per, glb = update(
                state, {"left_wrist": (x, y, True)}, dt, params
            )
            o = per["left_wrist"]
            if i % 5 == 0 or i == n_frames - 1:
                print(f"{i:>3} {x:>5.2f} {y:>5.2f} "
                      f"{o['speed']:>6.2f} {o['accel']:>7.2f} "
                      f"{o['emit']:>5.2f} {o['burst']:>5.2f}")

    # 30 frames of stillness at (0.5, 0.5)
    feed(lambda i: (0.5, 0.5), 30, "still at center")

    # 20 frames of a sharp horizontal whip from 0.2 → 0.8 then stop
    def whip(i):
        # Ease-in, snap to rest: cubic hermite-ish
        if i < 8:
            u = i / 7.0
            # fast acceleration
            return (0.2 + (0.8 - 0.2) * (u * u), 0.5)
        return (0.8, 0.5)
    feed(whip, 20, "whip right then stop (expect burst spike around frames 5-10)")

    # 10 frames visible=False — envelopes decay, but position should HOLD
    # at the last good value (0.8, 0.5) rather than use the passed-in garbage.
    print("\n--- landmark dropped (visible=False, input garbage (0, 1)) ---")
    print("    expected: out.x=0.80 out.y=0.50  (last good, not garbage)")
    for i in range(10):
        per, glb = update(state, {"left_wrist": (0.0, 1.0, False)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: out=({o['x']:.2f},{o['y']:.2f}) "
              f"burst={o['burst']:.3f} accel={o['accel']:.3f} "
              f"visible={o['visible']}")
        assert abs(o["x"] - 0.8) < 1e-6, "position should be held at last good"
        assert abs(o["y"] - 0.5) < 1e-6, "position should be held at last good"

    # Sanity: burst strictly non-negative, emit in 0..1
    assert 0.0 <= o["burst"] <= 1.0
    assert 0.0 <= o["emit"] <= 1.0

    # Teleport test: claim visible=True but throw in a huge jump. Logic
    # should reject the jump and hold last_good.
    print("\n--- visible=True but big teleport (should be rejected) ---")
    print("    expected: out stays at (0.80, 0.50), no burst spike")
    burst_before = o["burst"]
    for i in range(5):
        per, glb = update(state, {"left_wrist": (0.05, 0.95, True)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: out=({o['x']:.2f},{o['y']:.2f}) "
              f"speed={o['speed']:.3f} accel={o['accel']:.3f} "
              f"burst={o['burst']:.3f}")
        assert abs(o["x"] - 0.8) < 1e-6, "teleport should be rejected"
        assert o["burst"] <= burst_before + 1e-6, "teleport should NOT fire a burst"

    # Hysteresis test: marginal-zone frames with drifting position should
    # NOT update last_good. The blob stays pinned while visibility ramps down.
    print("\n--- marginal-zone drift (visible=True, trusted=False, position drifting) ---")
    print("    expected: out stays at last trusted (~0.80, 0.50) despite input drifting")
    # Reset state to a known-trusted position.
    state2 = new_state(("left_wrist",))
    for _ in range(5):
        update(state2, {"left_wrist": (0.8, 0.5, True, True)}, dt, params)
    # Now feed marginal frames where raw x drifts from 0.8 toward 0.1.
    drift_xs = [0.70, 0.55, 0.40, 0.25, 0.10]
    for i, dx_val in enumerate(drift_xs):
        per, _ = update(state2,
                        {"left_wrist": (dx_val, 0.95, True, False)},
                        dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: raw_in=({dx_val:.2f}, 0.95)  out=({o['x']:.2f},{o['y']:.2f})  "
              f"visible={o['visible']}")
        assert abs(o["x"] - 0.8) < 1e-6, "marginal frame leaked drifting x"
        assert abs(o["y"] - 0.5) < 1e-6, "marginal frame leaked drifting y"
        assert o["visible"] == 1.0, "marginal frame should still be visible=1"

    # Then fully invisible — position should still be 0.8, visible=0
    per, _ = update(state2, {"left_wrist": (0.0, 1.0, False, False)}, dt, params)
    o = per["left_wrist"]
    print(f"  invisible: out=({o['x']:.2f},{o['y']:.2f}) visible={o['visible']}")
    assert abs(o["x"] - 0.8) < 1e-6 and o["visible"] == 0.0

    print("\nOK — invisible hold, envelope decay, teleport rejection,")
    print("     and marginal-zone position freeze all pass.")
