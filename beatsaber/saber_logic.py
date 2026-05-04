"""
saber_logic.py
==============

Pure-Python saber state module for the Beat Saber sub-game. No TD
imports — runnable as `python -m beatsaber.saber_logic` for a self-test.

What it computes
----------------
Given per-cook landmark positions from the sensing side, it produces
per-saber world-space pose:

    hilt       : 3D position of the saber's hilt base (at the wrist)
    hilt_top   : 3D position of the hilt-blade junction (where the blade emerges)
    tip        : 3D position of the blade's far tip
    dir        : forward unit vector (hilt_base → tip)
    up         : palm-normal unit vector (the "top" of the hilt; rotates with wrist roll)
    right      : forward × up (third basis axis)
    velocity   : per-cook tip velocity (for swept-volume collision)
    prev_tip   : previous frame's tip
    orient_quat: smoothed orientation as a unit quaternion
    hand_active: 1.0 if hand-knuckle landmarks contributed this frame, else 0.0

Orientation pipeline (the part that matters)
--------------------------------------------
The fragile thing here is *orientation* — the saber's roll. Pose-only
inputs (elbow + wrist) tell you which way the forearm points but not
how the wrist is rotated. MediaPipe Hands gives 21 per-hand landmarks
including the four knuckle positions (index/middle/ring/pinky MCPs)
which let us recover roll. But finger landmarks are jittery, so we
fuse cautiously:

  1. **Wrist + KNUCKLES ONLY for the instantaneous frame.**
     Fingertips are the worst landmarks (high occlusion + tracking
     drift); we don't read them. The back of the hand is rigid, so
     wrist + index_MCP + middle_MCP + pinky_MCP form a stable plane.
       - forward axis = wrist → middle_MCP   (long axis of the palm)
       - cross-palm   = chirality-aware index↔pinky vector
       - palm normal  = forward × cross-palm  (saber's "up")
       - Gram-Schmidt re-orthonormalizes so the basis is always a
         valid rotation, even if one knuckle wobbles.

  2. **Elbow → wrist as a low-frequency anchor.**
     When hand-tracking confidence is low (occlusion, hand half off
     screen), the forearm vector is still reliable. We blend the
     hand's forward axis with the elbow→wrist forward axis weighted
     by hand confidence, so the saber's pointing direction degrades
     gracefully instead of snapping to garbage.

  3. **Quaternion-level temporal smoothing.**
     Smoothing landmarks before computing the basis would distort
     it (different landmarks lag at different rates → twisted basis).
     Instead we build the instantaneous basis from current-frame
     landmarks, convert to quaternion, then EMA-slerp the stored
     quaternion toward it. Cutoff time constant = `orient_smooth`.
     Fast swings come through; jitter at rest is suppressed.

  4. **Held last-good** when both hand AND forearm drop out — the
     saber freezes in place rather than twitching.

All of this is computed per-side; left/right hand chirality flips
the cross-palm direction so the palm normal consistently points
toward the user (matching a normal sword grip).

Game-world coordinates
----------------------
- x, y are in MediaPipe-UV (0..1 each).
- z = 0  is the hit plane (sabers live here).
- z < 0  is the approach tunnel (notes spawn at z ≈ -10).
- z > 0  is behind the player (camera at +Z, looking -Z).

Sabre geometry
--------------
A saber is rendered as a hilt segment + a blade segment:
    hilt_base (at wrist) ────► hilt_top ────────────────────► tip
                  (hilt_length)        (blade_length)

`hilt_top` is offset from the wrist along the forward axis by
`hilt_length`, so the blade visually emerges from the front of a
closed fist instead of starting at the wrist origin. The collision
endpoint stays `tip` (the far end of the blade) for swept-volume
detection.
"""

import math


# ---------------------------------------------------------------------------
# Saber identity
# ---------------------------------------------------------------------------

SABER_NAMES = ("left", "right")
SABER_COLORS = {"left": "red", "right": "blue"}  # Beat Saber convention


# ---------------------------------------------------------------------------
# Per-saber state
# ---------------------------------------------------------------------------

def _fresh_saber_state():
    """Per-saber state template. Single source of truth for the inner-dict
    schema. ensure_schema() backfills missing keys on stored state so old
    sessions don't KeyError after a schema bump."""
    return {
        # Geometry (world coords).
        "hilt":       (0.5, 0.5, 0.0),    # hilt base (at wrist)
        "hilt_top":   (0.5, 0.5, 0.0),    # hilt-blade junction
        "tip":        (0.5, 0.7, -0.2),   # far end of blade
        "dir":        (0.0, 1.0, -0.3),   # forward unit vector (hilt → tip)
        "up":         (0.0, 0.0, 1.0),    # palm-normal unit vector
        "right":      (1.0, 0.0, 0.0),    # cross-palm unit vector (forward × up)
        "velocity":   (0.0, 0.0, 0.0),    # tip velocity over last cook
        "prev_tip":   (0.5, 0.7, -0.2),   # previous frame's tip (for sweep)
        "prev_hilt":  (0.5, 0.5, 0.0),    # previous frame's hilt

        # Orientation smoothing state.
        # `smoothed_up` is the EMA-lerped palm-normal axis (the noisy
        # one). Forward axis is recomputed instantaneously each cook
        # from the source landmarks and orthonormalized against the
        # smoothed_up. Quat fields are derived for any consumer that
        # wants a single rotation handle.
        "smoothed_up":    None,           # None on first cook → seeded
        "last_good_up":   None,           # last valid up axis (held on dropout)
        "orient_quat":    None,           # derived smoothed-basis quat
        "last_good_quat": None,           # derived from last_good basis

        # Forward axis EMA state — separate from `smoothed_up` because
        # the two axes need different time constants.
        "smoothed_forward": None,

        # Diagnostic — was the hand basis active this frame?
        "hand_active": 0.0,
        # 1.0 when the wrist+elbow had a usable position this cook,
        # 0.0 when tracking degraded and we held the last good pose.
        # The renderer reads this to hide the saber when tracking is
        # lost (otherwise the saber snaps to MediaPipe's (0, 0) garbage).
        "tracking_active": 0.0,
    }


def new_state():
    """Build state for both sabers. Stored on the Script CHOP's parent COMP
    via op.store(); survives cook-to-cook, resets on reload."""
    return {name: _fresh_saber_state() for name in SABER_NAMES}


def ensure_schema(state):
    """Backfill any missing inner-dict keys with current defaults so older
    stored state migrates forward cleanly. Mutates state in place."""
    template = _fresh_saber_state()
    for name in SABER_NAMES:
        if name not in state or not isinstance(state[name], dict):
            state[name] = _fresh_saber_state()
        else:
            for k, v in template.items():
                state[name].setdefault(k, v)
    return state


# ---------------------------------------------------------------------------
# Vec3 helpers (3D, plain tuples — no numpy)
# ---------------------------------------------------------------------------

def _sub(a, b):    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b):    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _scale(a, s):  return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b):    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _len(a):       return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _normalize(a, fallback=(0.0, 1.0, 0.0)):
    m = _len(a)
    if m < 1e-6:
        return fallback
    inv = 1.0 / m
    return (a[0] * inv, a[1] * inv, a[2] * inv)


# ---------------------------------------------------------------------------
# Quaternion helpers (w, x, y, z)
# ---------------------------------------------------------------------------

def _q_normalize(q):
    m = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if m < 1e-9:
        return (1.0, 0.0, 0.0, 0.0)
    inv = 1.0 / m
    return (q[0] * inv, q[1] * inv, q[2] * inv, q[3] * inv)


def _q_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def _q_neg(q):
    return (-q[0], -q[1], -q[2], -q[3])


def _q_slerp(a, b, t):
    """Spherical linear interpolation between two unit quaternions.
    Takes the short arc by flipping `b`'s sign if dot(a, b) < 0 — without
    that, smoothing across the antipodal switch produces a 360° spin."""
    d = _q_dot(a, b)
    if d < 0.0:
        b = _q_neg(b)
        d = -d
    if d > 0.9995:
        # Very close — linear blend is numerically safer than slerp.
        result = (a[0] + t * (b[0] - a[0]),
                  a[1] + t * (b[1] - a[1]),
                  a[2] + t * (b[2] - a[2]),
                  a[3] + t * (b[3] - a[3]))
        return _q_normalize(result)
    # Acos clamp guards against d > 1.0 due to FP error.
    if d > 1.0:
        d = 1.0
    theta_0 = math.acos(d)
    sin_theta_0 = math.sin(theta_0)
    theta = theta_0 * t
    sin_theta = math.sin(theta)
    s_a = math.cos(theta) - d * sin_theta / sin_theta_0
    s_b = sin_theta / sin_theta_0
    return (s_a * a[0] + s_b * b[0],
            s_a * a[1] + s_b * b[1],
            s_a * a[2] + s_b * b[2],
            s_a * a[3] + s_b * b[3])


def _basis_to_quat(forward, up, right):
    """Convert an orthonormal right-handed basis (each a unit 3-vector) to
    a unit quaternion (w, x, y, z). Uses the standard branchless
    matrix-to-quat extraction with the largest-component branch picked
    for numerical stability."""
    # Rotation matrix M whose columns are the basis vectors. Internally
    # m[row][col]: M[i][j] = basis_j[i].
    m00, m10, m20 = right[0], right[1], right[2]
    m01, m11, m21 = up[0],    up[1],    up[2]
    m02, m12, m22 = forward[0], forward[1], forward[2]

    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0  # s = 4w
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif (m00 > m11) and (m00 > m22):
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0  # s = 4x
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0  # s = 4y
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0  # s = 4z
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return _q_normalize((w, x, y, z))


def _quat_to_basis(q):
    """Inverse of _basis_to_quat: extract (forward, up, right) from quat."""
    w, x, y, z = q
    # Standard quat → 3x3 rotation matrix. Columns are (right, up, forward).
    right   = (1.0 - 2.0 * (y * y + z * z),
               2.0 * (x * y + w * z),
               2.0 * (x * z - w * y))
    up      = (2.0 * (x * y - w * z),
               1.0 - 2.0 * (x * x + z * z),
               2.0 * (y * z + w * x))
    forward = (2.0 * (x * z + w * y),
               2.0 * (y * z - w * x),
               1.0 - 2.0 * (x * x + y * y))
    return forward, up, right


# ---------------------------------------------------------------------------
# Orthonormalization (Gram-Schmidt)
# ---------------------------------------------------------------------------

def _orthonormalize(forward_raw, up_hint):
    """Build a right-handed orthonormal basis (forward, up, right) where:
      - `forward` is the unit-normalized `forward_raw`
      - `up` is `up_hint` projected to be perpendicular to `forward`, then
        renormalized
      - `right` = forward × up (already unit, since forward ⟂ up are unit)

    Falls back to a sensible default if the inputs are degenerate
    (zero-length forward, or up_hint parallel to forward)."""
    f = _normalize(forward_raw, fallback=(0.0, -1.0, -0.3))
    f = _normalize(f)  # in case fallback wasn't unit-length

    # Project up_hint onto plane perpendicular to f.
    proj = _dot(up_hint, f)
    u_proj = _sub(up_hint, _scale(f, proj))
    u_mag = _len(u_proj)
    if u_mag < 1e-4:
        # up_hint was (nearly) parallel to f. Pick a default perpendicular.
        candidate = (0.0, 0.0, 1.0) if abs(f[2]) < 0.95 else (0.0, -1.0, 0.0)
        proj2 = _dot(candidate, f)
        u_proj = _sub(candidate, _scale(f, proj2))
        u_mag = _len(u_proj)
        if u_mag < 1e-6:
            # Truly degenerate — return identity-aligned basis.
            return ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    inv_u = 1.0 / u_mag
    u = (u_proj[0] * inv_u, u_proj[1] * inv_u, u_proj[2] * inv_u)

    # right = up × forward — gives a right-handed basis where
    # right × up = forward (the convention that _basis_to_quat expects so
    # the resulting rotation matrix has determinant +1). If you swap the
    # cross order to forward × up you get a LEFT-handed basis and the
    # quaternion round-trip produces a mirrored / flipped orientation.
    r = _cross(u, f)
    return f, u, r


# ---------------------------------------------------------------------------
# Instantaneous bases — hand-knuckle and forearm
# ---------------------------------------------------------------------------

def _hand_basis_instantaneous(wrist_xyz, index_mcp_xyz, middle_mcp_xyz,
                              pinky_mcp_xyz, side):
    """Compute a right-handed orthonormal basis from wrist + 3 knuckle
    landmarks. Returns (forward, up, right) or None if landmarks are
    degenerate (collapsed onto a point or colinear).

    `side` is "left" or "right". The chirality flip ensures the palm-
    normal axis (`up`) points toward the camera (+Z) when the user's
    palm faces the camera, regardless of which hand."""
    # Forward: wrist → middle_MCP. The long axis of the palm. Most
    # stable axis because both endpoints are on the rigid back-of-hand.
    forward_raw = _sub(middle_mcp_xyz, wrist_xyz)
    if _len(forward_raw) < 1e-4:
        return None

    # Cross-palm vector. Chirality-aware so palm normal comes out the
    # palm-facing-user side regardless of which hand.
    #
    # Mirrored-selfie webcam convention: when the user holds their hand
    # up palm-toward-camera with fingers up, on screen the thumb is on
    # the inside (toward body center) and the pinky is on the outside.
    #   - LEFT hand on screen: thumb (index_MCP) is on the screen-RIGHT.
    #     across = pinky → index points RIGHTWARDS (+x).
    #   - RIGHT hand on screen: thumb (index_MCP) is on the screen-LEFT.
    #     across = index → pinky points RIGHTWARDS (+x).
    # In both cases we want the same world-space direction so that
    # forward × across produces a palm-normal pointing TOWARD camera (+z).
    if side == "left":
        across_raw = _sub(index_mcp_xyz, pinky_mcp_xyz)
    else:
        across_raw = _sub(pinky_mcp_xyz, index_mcp_xyz)
    if _len(across_raw) < 1e-4:
        return None

    # Palm normal = forward × across. With the chirality flip above,
    # this points toward the camera when the palm faces the camera.
    palm_normal_raw = _cross(forward_raw, across_raw)
    if _len(palm_normal_raw) < 1e-4:
        return None  # forward and across were colinear (briefly possible)

    # Re-orthonormalize: lock forward, project palm_normal_raw to be
    # exactly perpendicular to it. This is the Gram-Schmidt step that
    # guarantees the result is a valid rotation matrix even when
    # individual landmarks wobble slightly.
    return _orthonormalize(forward_raw, palm_normal_raw)


def _forearm_basis(wrist_xy, elbow_xy, z_extrusion, forearm_strength=1.0):
    """Fallback basis from elbow → wrist + a default palm-normal hint.

    `forearm_strength` ∈ [0, 1] decays the screen-XY contribution to the
    forward direction. When the forearm becomes foreshortened (the user
    thrusts the arm toward the camera), its on-screen length shrinks
    and the elbow→wrist 2D direction becomes noisy — at the limit
    (arm pointing straight at the camera) the screen vector is a single
    point and useless. Fading the XY weight as forearm length shrinks
    lets the -Z extrusion dominate, so the blade naturally swings out
    of the screen plane and into the tunnel for forward thrusts.

    No roll info from the forearm alone, so we use world +Z (toward
    camera) as the up hint."""
    fx = wrist_xy[0] - elbow_xy[0]
    fy = wrist_xy[1] - elbow_xy[1]
    fmag2 = fx * fx + fy * fy
    if fmag2 < 1e-8:
        # Degenerate — default to pointing up + into the tunnel.
        forward_raw = (0.0, -1.0, -max(z_extrusion, 0.5))
    else:
        # Scale screen-direction by forearm_strength so a foreshortened
        # forearm doesn't twist the blade sideways. -Z extrusion stays
        # at full magnitude.
        inv = forearm_strength / math.sqrt(fmag2)
        forward_raw = (fx * inv, fy * inv, -z_extrusion)
    up_hint = (0.0, 0.0, 1.0)  # default: palm normal toward camera
    return _orthonormalize(forward_raw, up_hint)


# ---------------------------------------------------------------------------
# EMA alpha helper (same shape as velocity_logic._ema_alpha)
# ---------------------------------------------------------------------------

def _ema_alpha(dt, tau):
    """One-pole EMA coefficient targeting a given time constant in seconds."""
    if tau <= 1e-6:
        return 1.0
    a = 1.0 - math.exp(-dt / tau)
    if a < 0.0:
        return 0.0
    if a > 1.0:
        return 1.0
    return a


# ---------------------------------------------------------------------------
# Per-saber update
# ---------------------------------------------------------------------------

def update_saber(sample, wrist_xy, elbow_xy, wrist_visible, elbow_visible,
                 hand_landmarks, hand_visible, side, dt, params):
    """Advance one saber's state by one cook.

    sample           : state[name] dict, mutated in place.
    wrist_xy         : (x, y) wrist position from velocity_controller (smoothed).
                       Used as the hilt anchor in world space.
    elbow_xy         : (x, y) elbow position. Used for forearm fallback basis.
    wrist_visible    : bool — pose wrist confidence above gate.
    elbow_visible    : bool — pose elbow confidence above gate.
    hand_landmarks   : optional dict with keys 'wrist', 'index_mcp',
                       'middle_mcp', 'pinky_mcp', each an (x, y, z) tuple
                       in MediaPipe-UV space. Pass None if the hand
                       tracker isn't connected.
    hand_visible     : bool — overall hand-tracking confidence above gate.
                       If False, the hand basis is skipped this cook.
    side             : "left" or "right" — for chirality-aware palm normal.
    dt               : seconds since previous cook.
    params           : dict with keys
                         hilt_length        (UV) : hilt segment length
                         blade_length       (UV) : blade segment length
                         hilt_plane_z       (UV) : world z of the hilt base
                         z_extrusion        (0..1): forearm-fallback -z tilt
                         hand_weight        (0..1): hand-vs-forearm forward blend
                         orient_smooth      (s)   : quaternion EMA time constant

    Returns the updated sample dict."""
    # Roll old positions for swept-volume collision continuity.
    sample["prev_tip"]  = sample["tip"]
    sample["prev_hilt"] = sample["hilt"]

    # Tracking-active flag. When BOTH wrist and elbow drop, hold the
    # previous geometry rather than letting the renderer snap the
    # saber to (0, 0). A flag on state lets the renderer hide the
    # saber until tracking recovers.
    if not (wrist_visible or elbow_visible):
        sample["tracking_active"] = 0.0
        # Don't touch geometry — keep last-good values.
        sample["velocity"] = (0.0, 0.0, 0.0)
        return sample
    sample["tracking_active"] = 1.0

    # 1. Forearm fallback basis. Computed whenever pose data is usable.
    #
    # No XY-fade-out anymore — the previous scheme (forearm_strength =
    # ratio²) was the cause of the "blade snaps between X-plane and
    # Z-plane" feel: small motion changes pushed the strength across
    # the threshold and the dominant axis flipped. Beat-Saber-style
    # kinematics want a CONTINUOUS blade direction that follows the
    # wrist+forearm vector smoothly, with a fixed -Z tilt that doesn't
    # depend on forearm length.
    #
    # We instead handle the foreshortening case in the smoother below:
    # when the on-screen forearm shrinks below `forearm_baseline_len`,
    # the "forearm signal weight" drops, so the EMA-smoothed forward
    # holds its previous value rather than letting the noisy short
    # vector whip the blade around. The result is: visible swing →
    # blade follows; arm pointed at camera → blade holds last good
    # direction.
    forearm_basis = None
    forearm_signal_weight = 0.0
    if wrist_visible and elbow_visible:
        fx0 = wrist_xy[0] - elbow_xy[0]
        fy0 = wrist_xy[1] - elbow_xy[1]
        forearm_len_2d = math.sqrt(fx0 * fx0 + fy0 * fy0)
        baseline_len   = params.get("forearm_baseline_len", 0.16)
        ratio = max(0.0, min(1.0, forearm_len_2d / max(1e-4, baseline_len)))
        # Linear falloff so half-length forearm still gets half-weight.
        forearm_signal_weight = ratio
        forearm_basis = _forearm_basis(wrist_xy, elbow_xy,
                                       params["z_extrusion"],
                                       forearm_strength=1.0)

    # 2. Hand-knuckle basis. Only attempted if hand is visible AND all four
    #    required landmarks are present. Knuckles only — no fingertips.
    hand_basis = None
    if (hand_visible and hand_landmarks is not None
            and all(k in hand_landmarks
                    for k in ("wrist", "index_mcp",
                              "middle_mcp", "pinky_mcp"))):
        hand_basis = _hand_basis_instantaneous(
            hand_landmarks["wrist"],
            hand_landmarks["index_mcp"],
            hand_landmarks["middle_mcp"],
            hand_landmarks["pinky_mcp"],
            side,
        )

    # 3. Pick / blend a target basis.
    #    - Both available → blend forward axes (weighted by hand_weight),
    #      use hand's palm-normal as the up hint.
    #    - Only one available → use it.
    #    - Neither → hold last-good orientation.
    hand_weight = max(0.0, min(1.0, params.get("hand_weight", 1.0)))
    if hand_basis is not None and forearm_basis is not None:
        f_hand, u_hand, _ = hand_basis
        f_forearm, _, _   = forearm_basis
        w = hand_weight
        forward_blend = (w * f_hand[0] + (1 - w) * f_forearm[0],
                         w * f_hand[1] + (1 - w) * f_forearm[1],
                         w * f_hand[2] + (1 - w) * f_forearm[2])
        # Up hint always comes from the hand's palm normal — that's the
        # only roll-bearing signal we have. Forearm has no roll info.
        target_basis = _orthonormalize(forward_blend, u_hand)
        hand_active = 1.0
    elif hand_basis is not None:
        target_basis = hand_basis
        hand_active = 1.0
    elif forearm_basis is not None:
        target_basis = forearm_basis
        hand_active = 0.0
    else:
        target_basis = None
        hand_active = 0.0

    # 4. Temporal smoothing strategy.
    #
    # We split the basis into two parts that need very different treatment:
    #
    #   forward axis  — taken INSTANTANEOUSLY each cook. It's derived from
    #                   rigid landmarks (wrist↔middle_MCP for hand basis,
    #                   elbow↔wrist for forearm fallback) and already
    #                   smoothed upstream by velocity_controller's Lag CHOP.
    #                   Smoothing it *here* would lag the swing.
    #
    #   up axis (palm normal) — EMA-lerped toward target. This is the
    #                   noisy axis (driven by knuckle jitter when the
    #                   hand basis is active) so we suppress per-frame
    #                   wobble. Smoothing acts on the up VECTOR, then
    #                   we re-orthonormalize.
    #
    # We deliberately do NOT slerp the full quaternion. Slerp computes
    # the shortest 4D arc between two orientations, which during fast
    # swings (forearm direction can flip ~180° in one cook when the
    # wrist crosses the elbow) interpolates through arbitrary 3D
    # orientations and visibly twists the saber sideways. Lerping the
    # up axis only and re-deriving the basis keeps the saber tracking
    # the wrist+forearm cleanly while still suppressing roll jitter.
    if target_basis is not None:
        f_target, u_target, _ = target_basis
        # Lerp stored up-hint toward target up. EMA alpha is per-cook,
        # framerate-independent via _ema_alpha(dt, tau).
        prev_up = sample.get("smoothed_up")
        prev_forward = sample.get("smoothed_forward")
        tau = params.get("orient_smooth", 0.03)

        if prev_up is None:
            smoothed_up = u_target
        else:
            alpha = _ema_alpha(dt, tau)
            smoothed_up = (prev_up[0] + alpha * (u_target[0] - prev_up[0]),
                           prev_up[1] + alpha * (u_target[1] - prev_up[1]),
                           prev_up[2] + alpha * (u_target[2] - prev_up[2]))
        sample["smoothed_up"] = smoothed_up
        sample["last_good_up"] = u_target

        # Smooth the forward axis too. EMA alpha is scaled by the
        # forearm-signal weight so a foreshortened arm (low weight)
        # barely contributes to the smoothed forward — effectively
        # holding the last good direction. A well-extended forearm
        # (high weight) contributes strongly. Hand-basis path always
        # gets full weight.
        if hand_basis is not None:
            forward_signal_weight = 1.0
        else:
            forward_signal_weight = forearm_signal_weight
        if prev_forward is None:
            smoothed_forward = f_target
        else:
            tau_fwd = params.get("forward_smooth", 0.06)
            alpha_fwd = _ema_alpha(dt, tau_fwd) * forward_signal_weight
            smoothed_forward = (
                prev_forward[0] + alpha_fwd * (f_target[0] - prev_forward[0]),
                prev_forward[1] + alpha_fwd * (f_target[1] - prev_forward[1]),
                prev_forward[2] + alpha_fwd * (f_target[2] - prev_forward[2]),
            )
        sample["smoothed_forward"] = smoothed_forward

        # Gram-Schmidt makes (forward, up) orthonormal; right = up × fwd.
        f_smooth, u_smooth, r_smooth = _orthonormalize(smoothed_forward,
                                                       smoothed_up)
        sample["orient_quat"]    = _basis_to_quat(f_smooth, u_smooth, r_smooth)
        sample["last_good_quat"] = sample["orient_quat"]
    else:
        # Nothing trustworthy this cook — hold the last smoothed basis.
        # On the very first cook with no data, default to identity.
        if sample.get("smoothed_up") is None:
            sample["smoothed_up"] = (0.0, 0.0, 1.0)
        if sample.get("orient_quat") is None:
            sample["orient_quat"] = (sample.get("last_good_quat")
                                     or (1.0, 0.0, 0.0, 0.0))
        f_smooth, u_smooth, r_smooth = _quat_to_basis(sample["orient_quat"])

    # 6. Forward-lock — POV constraint.
    #
    # The game is designed as a first-person view: camera is the
    # player's eye, sabres extend OUT from the hands toward the
    # approaching notes (down the −Z tunnel). The blade must NEVER
    # rotate to point at the camera (positive forward.z) — that would
    # read as "slashing toward myself" instead of "slashing into the
    # scene", breaking the POV illusion.
    #
    # Without this clamp, normal real-world poses (forward thrust,
    # elbow-above-wrist mid-swing) can put forward.z slightly
    # positive, which would visually flip the blade out of the screen
    # for a frame. Clamp forward.z to at most -FORWARD_LOCK_MIN_Z so
    # the blade is *always* tilted at least slightly into the tunnel.
    #
    # We CLAMP, not MIRROR, because mirroring (-0.5 → +0.5) creates a
    # discontinuity right at the threshold. Clamp gives a smooth
    # "floor" — small wobbles around z=0 stay near the floor; large
    # camera-ward rotations also stop at the floor.
    if params.get("forward_lock", True):
        FORWARD_LOCK_MIN_Z = 0.05   # blade always tilts at least this far
                                    # into the tunnel (in unit-vector units)
        if f_smooth[2] > -FORWARD_LOCK_MIN_Z:
            clamped_forward = (f_smooth[0], f_smooth[1], -FORWARD_LOCK_MIN_Z)
            f_smooth, u_smooth, r_smooth = _orthonormalize(clamped_forward,
                                                            u_smooth)

    # 7. Build geometry: hilt_base at the wrist, hilt_top forward by
    #    hilt_length, tip forward by hilt_length + blade_length.
    #
    # Optional thrust-z mapping: when `thrust_scale > 0`, use the
    # wrist's MediaPipe-z (closer-to-camera = negative) to push the
    # hilt INTO the tunnel. POV mental model: hand forward in real
    # life → blade extends into the scene. The mapping is
    #   hilt_z = hilt_plane_z + wrist_z_mediapipe * thrust_scale
    # Both signs match (MediaPipe z negative when wrist is closer to
    # camera; we want hilt_z negative = deeper into the tunnel), so
    # the multiplication preserves direction.
    hilt_length  = params.get("hilt_length", 0.04)
    blade_length = params.get("blade_length", 0.21)
    plane_z      = params["hilt_plane_z"]
    thrust_scale = params.get("thrust_scale", 0.0)
    if thrust_scale != 0.0:
        # wrist_z is in the optional 4th tuple slot if the upstream
        # passed it; otherwise treat as 0 (no thrust contribution).
        wrist_z = wrist_xy[2] if len(wrist_xy) > 2 else 0.0
        plane_z = plane_z + wrist_z * thrust_scale
    hilt_base = (wrist_xy[0], wrist_xy[1], plane_z)
    hilt_top  = _add(hilt_base, _scale(f_smooth, hilt_length))
    tip       = _add(hilt_top,  _scale(f_smooth, blade_length))

    # Tip velocity over one cook (for swept-volume collision in hit_detection).
    velocity = _sub(tip, sample["prev_tip"])

    sample["hilt"]        = hilt_base
    sample["hilt_top"]    = hilt_top
    sample["tip"]         = tip
    sample["dir"]         = f_smooth
    sample["up"]          = u_smooth
    sample["right"]       = r_smooth
    sample["velocity"]    = velocity
    sample["hand_active"] = hand_active
    return sample


# ---------------------------------------------------------------------------
# Batch update
# ---------------------------------------------------------------------------

def update(state, samples, dt, params):
    """Advance both sabers one cook.

    state   : dict from new_state()
    samples : dict {saber_name: {wrist_xy, elbow_xy,
                                  wrist_visible, elbow_visible,
                                  hand_landmarks (optional dict),
                                  hand_visible (optional bool)}}
    dt      : seconds since previous cook
    params  : dict — see update_saber()

    Returns dict of per-saber output snapshots so callers can read the
    current cook's state without re-reading `state`."""
    out = {}
    for name in SABER_NAMES:
        s = samples.get(name)
        if s is None:
            out[name] = dict(state[name])
            continue
        update_saber(
            state[name],
            s["wrist_xy"],
            s["elbow_xy"],
            s.get("wrist_visible", True),
            s.get("elbow_visible", True),
            s.get("hand_landmarks"),
            s.get("hand_visible", False),
            name,
            dt,
            params,
        )
        out[name] = dict(state[name])
    return out


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def default_params():
    return {
        # Sabre geometry — hilt + blade. Defaults sum to ~0.65 UV which
        # at the default camera distance (cam at z=+3, sabre at z=0,
        # fov=50°) renders the blade at roughly 22% of frame width —
        # visually substantial without dominating. Hilt is a short
        # stub so the blade appears to emerge from a closed fist.
        "hilt_length":   0.08,
        "blade_length":  0.55,
        "hilt_plane_z":  0.0,
        # Forearm fallback tilt — saber points into the tunnel when
        # only the elbow→wrist signal is available. Higher values let
        # the user execute a forward thrust: as the arm straightens
        # toward the camera the 2D forearm shrinks (foreshortening),
        # `forearm_strength` decays in update_saber, and the -Z extrusion
        # dominates → blade rotates out of the screen plane and into
        # the approach tunnel where the notes live.
        "z_extrusion":   0.45,
        # Forearm length (in UV) at which the screen-XY contribution
        # to the forward axis is at full strength. Below this, the
        # contribution decays quadratically. Tune up if the user is
        # close to the camera (longer apparent forearm); down if far.
        "forearm_baseline_len": 0.16,
        # Orientation fusion + smoothing.
        "hand_weight":   1.0,    # 1 = trust hand basis fully when present;
                                 # 0 = ignore hand, use forearm only.
        "orient_smooth": 0.03,   # EMA time constant on the up axis (s).
                                 # Suppresses per-frame palm-roll jitter.
        "forward_smooth": 0.02,  # EMA time constant on the forward axis
                                 # (s). Short — we want the blade to
                                 # FOLLOW the hand snappily, not lag
                                 # behind. The forearm-signal-weight
                                 # gate already suppresses junk during
                                 # foreshortening; the EMA only knocks
                                 # off per-frame jitter.
        # POV / thrust feel.
        "forward_lock":  True,   # clamp forward.z ≤ 0 so the blade always
                                 # tilts AWAY from camera; without this, a
                                 # forward-thrust pose can rotate the blade
                                 # toward the camera and read as "slashing
                                 # toward me" instead of into the scene.
        "thrust_scale":  1.5,    # MediaPipe wrist-z → hilt-z multiplier.
                                 # 0 = hilt locked to hilt_plane_z (old
                                 # behavior); 1.5 = a hand thrust 0.3 UV
                                 # closer to the camera in real life pushes
                                 # the hilt 0.45 units deeper into the
                                 # tunnel. Requires the upstream sample
                                 # tuple to include wrist z (3rd element).
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    params = default_params()
    state = new_state()
    dt = 1.0 / 60.0

    def _round(t, n=3):
        return tuple(round(x, n) for x in t)

    # ----- Case 1: forearm-only resting posture (no hand landmarks). -------
    samples_rest = {
        "left":  {"wrist_xy": (0.30, 0.50), "elbow_xy": (0.30, 0.70),
                  "wrist_visible": True, "elbow_visible": True},
        "right": {"wrist_xy": (0.70, 0.50), "elbow_xy": (0.70, 0.70),
                  "wrist_visible": True, "elbow_visible": True},
    }
    out = update(state, samples_rest, dt, params)
    print("=== forearm-only resting posture ===")
    for n in SABER_NAMES:
        o = out[n]
        print(f"  {n:5} hilt={_round(o['hilt'])}  "
              f"tip={_round(o['tip'])}  dir={_round(o['dir'])}  "
              f"hand_active={o['hand_active']:.0f}")
    # Saber should point upward in screen space (dy < 0 since y grows down)
    # and tilt into -z (toward approaching notes).
    assert out["left"]["dir"][1]  < 0, "left saber should point up the screen"
    assert out["right"]["dir"][1] < 0
    assert out["left"]["dir"][2]  < 0, "saber should tilt into -z (tunnel)"
    assert out["left"]["hand_active"] == 0.0, "no hand landmarks → forearm-only"

    # ----- Case 2: hand landmarks present, palm facing camera. -------------
    # Synthesise a left hand: wrist at (0.3, 0.5, 0), middle_MCP slightly
    # above (0.3, 0.4, 0) → hand pointing up-screen. Index_MCP on the
    # screen-RIGHT (x = 0.34) and pinky_MCP on the screen-LEFT (x = 0.26)
    # — the natural mirrored-webcam layout for a left hand held palm-out.
    samples_hand = {
        "left": {
            "wrist_xy": (0.30, 0.50),
            "elbow_xy": (0.30, 0.70),
            "wrist_visible": True, "elbow_visible": True,
            "hand_visible": True,
            "hand_landmarks": {
                "wrist":      (0.30, 0.50, 0.00),
                "index_mcp":  (0.34, 0.42, 0.00),
                "middle_mcp": (0.30, 0.40, 0.00),
                "pinky_mcp":  (0.26, 0.42, 0.00),
            },
        },
        # Right hand: mirror image. Index_MCP on screen-LEFT, pinky_MCP
        # on screen-RIGHT.
        "right": {
            "wrist_xy": (0.70, 0.50),
            "elbow_xy": (0.70, 0.70),
            "wrist_visible": True, "elbow_visible": True,
            "hand_visible": True,
            "hand_landmarks": {
                "wrist":      (0.70, 0.50, 0.00),
                "index_mcp":  (0.66, 0.42, 0.00),
                "middle_mcp": (0.70, 0.40, 0.00),
                "pinky_mcp":  (0.74, 0.42, 0.00),
            },
        },
    }
    # Reset state so the smoothing seeds cleanly from the hand-basis frame.
    state = new_state()
    # Pump a few frames so the EMA settles.
    for _ in range(20):
        out = update(state, samples_hand, dt, params)
    print("\n=== hand-basis (palm facing camera) ===")
    for n in SABER_NAMES:
        o = out[n]
        print(f"  {n:5} dir={_round(o['dir'])}  up={_round(o['up'])}  "
              f"hand_active={o['hand_active']:.0f}")
    # Both palm normals should point toward +Z (out of screen, toward camera).
    assert out["left"]["up"][2]  > 0.5, \
        f"left palm normal should point +Z, got up={out['left']['up']}"
    assert out["right"]["up"][2] > 0.5, \
        f"right palm normal should point +Z, got up={out['right']['up']}"
    assert out["left"]["hand_active"]  == 1.0
    assert out["right"]["hand_active"] == 1.0

    # ----- Case 3: wrist roll changes "up" without changing forward. -------
    # Rotate the LEFT hand 90° around its forward axis. We rotate the
    # knuckles such that:
    #   index_MCP : (+X, 0)  →  ( 0, +Z)   moves toward camera
    #   pinky_MCP : (-X, 0)  →  ( 0, -Z)   moves away from camera
    # This is a -90° rotation around +Y (or equivalently +90° around -Y).
    # Under that rotation the palm normal swings: (0, 0, +1) → (-1, 0, 0).
    # So we expect `up` to swing toward -X — *not* +X. (The exact sign
    # depends on the chirality convention; what matters is that |up.x|
    # is large and forward is unchanged.)
    rolled_hand = dict(samples_hand)
    rolled_hand["left"] = dict(samples_hand["left"])
    rolled_hand["left"]["hand_landmarks"] = {
        "wrist":      (0.30, 0.50, 0.00),
        "middle_mcp": (0.30, 0.40, 0.00),
        "index_mcp":  (0.30, 0.42,  0.04),
        "pinky_mcp":  (0.30, 0.42, -0.04),
    }
    state = new_state()
    for _ in range(30):
        out = update(state, rolled_hand, dt, params)
    o = out["left"]
    print("\n=== left hand rolled 90° around forward axis ===")
    print(f"  dir={_round(o['dir'])}  up={_round(o['up'])}")
    # Forward should still be ~ (0, -1, 0). Up should now be along ±X
    # (it's swung 90° from +Z, so it's mostly horizontal).
    assert abs(o["dir"][1] + 1.0) < 0.1, \
        f"forward should stay along -y, got {o['dir']}"
    assert abs(o["up"][0]) > 0.7, \
        f"up should swing to mostly ±x after wrist roll, got {o['up']}"
    assert abs(o["up"][2]) < 0.3, \
        f"up's z-component should drop near zero after a 90° roll, got {o['up']}"

    # ----- Case 4: jitter rejection — knuckles wobble, smoothed up holds. --
    import random
    random.seed(42)
    state = new_state()
    # Settle.
    for _ in range(30):
        update(state, samples_hand, dt, params)
    settled_up = state["left"]["up"]
    # Inject one frame of large knuckle jitter on the left hand.
    jittered = dict(samples_hand)
    jittered["left"] = dict(samples_hand["left"])
    jittered["left"]["hand_landmarks"] = dict(samples_hand["left"]["hand_landmarks"])
    jittered["left"]["hand_landmarks"]["index_mcp"] = (
        0.34 + 0.05, 0.42 - 0.05, 0.05)
    jittered["left"]["hand_landmarks"]["pinky_mcp"] = (
        0.26 - 0.05, 0.42 + 0.05, -0.05)
    out = update(state, jittered, dt, params)
    jit_up = out["left"]["up"]
    print("\n=== single-frame knuckle jitter ===")
    print(f"  settled up={_round(settled_up)}  jittered up={_round(jit_up)}")
    # Quaternion smoothing should keep the up axis from snapping fully to
    # the jittered orientation.
    delta = math.sqrt(sum((jit_up[i] - settled_up[i]) ** 2 for i in range(3)))
    assert delta < 0.5, \
        f"single-frame jitter shouldn't move up by more than 0.5, got {delta}"

    # ----- Case 5: hand drops out → blends back to forearm basis. ----------
    state = new_state()
    for _ in range(30):
        update(state, samples_hand, dt, params)
    # Drop the hand visibility but keep forearm.
    drop_hand = dict(samples_hand)
    drop_hand["left"] = dict(samples_hand["left"])
    drop_hand["left"]["hand_visible"] = False
    drop_hand["left"]["hand_landmarks"] = None
    # Pump frames so the EMA-slerp converges to the forearm basis.
    for _ in range(60):
        out = update(state, drop_hand, dt, params)
    print("\n=== hand drop → forearm fallback ===")
    print(f"  left dir={_round(out['left']['dir'])}  "
          f"up={_round(out['left']['up'])}  "
          f"hand_active={out['left']['hand_active']:.0f}")
    assert out["left"]["hand_active"] == 0.0
    # Forearm forward is (0, -1, 0) plus -z extrusion → up axis defaulted
    # to +Z hint. Saber should still be pointing up the screen.
    assert out["left"]["dir"][1] < -0.5

    # ----- Case 6: total dropout → orientation held. -----------------------
    state = new_state()
    for _ in range(30):
        update(state, samples_hand, dt, params)
    held_dir = state["left"]["dir"]
    held_up  = state["left"]["up"]
    blackout = {
        "left":  {"wrist_xy": (0.30, 0.50), "elbow_xy": (0.30, 0.70),
                  "wrist_visible": False, "elbow_visible": False,
                  "hand_visible": False, "hand_landmarks": None},
        "right": {"wrist_xy": (0.70, 0.50), "elbow_xy": (0.70, 0.70),
                  "wrist_visible": False, "elbow_visible": False,
                  "hand_visible": False, "hand_landmarks": None},
    }
    out = update(state, blackout, dt, params)
    print("\n=== total dropout → orientation held ===")
    print(f"  left dir={_round(out['left']['dir'])}  up={_round(out['left']['up'])}")
    for i in range(3):
        assert abs(out["left"]["dir"][i] - held_dir[i]) < 1e-6
        assert abs(out["left"]["up"][i]  - held_up[i])  < 1e-6

    # ----- Case 7: swing produces nonzero tip velocity. --------------------
    state = new_state()
    # Frame N: wrist at rest position.
    swing_pre = {
        "left":  {"wrist_xy": (0.30, 0.50), "elbow_xy": (0.30, 0.70),
                  "wrist_visible": True, "elbow_visible": True},
        "right": {"wrist_xy": (0.70, 0.50), "elbow_xy": (0.70, 0.70),
                  "wrist_visible": True, "elbow_visible": True},
    }
    for _ in range(5):
        update(state, swing_pre, dt, params)
    # Frame N+1: right wrist swung down-right.
    swing_post = dict(swing_pre)
    swing_post["right"] = {"wrist_xy": (0.85, 0.65),
                            "elbow_xy": (0.65, 0.70),
                            "wrist_visible": True, "elbow_visible": True}
    out = update(state, swing_post, dt, params)
    print("\n=== forearm-only down-right swing ===")
    print(f"  right tip={_round(out['right']['tip'])}  "
          f"vel={_round(out['right']['velocity'])}")
    assert _len(out["right"]["velocity"]) > 0.05, \
        "swing should produce meaningful tip velocity"

    # ----- Case 8: chirality — both hands neutral grip → both palm normals +Z.
    # (Already covered by case 2's assertion, but sanity-check explicitly
    # that we don't accidentally invert one side.)
    state = new_state()
    for _ in range(30):
        out = update(state, samples_hand, dt, params)
    same_sign = (out["left"]["up"][2] > 0) == (out["right"]["up"][2] > 0)
    assert same_sign, ("left and right palm normals must point the same "
                       "direction in world for a neutral grip")

    # ----- Case 9: degenerate landmarks → returns None, falls back. --------
    degen = {
        "left": {
            "wrist_xy": (0.30, 0.50), "elbow_xy": (0.30, 0.70),
            "wrist_visible": True, "elbow_visible": True,
            "hand_visible": True,
            "hand_landmarks": {
                "wrist":      (0.30, 0.50, 0.00),
                "index_mcp":  (0.30, 0.50, 0.00),  # collapsed onto wrist
                "middle_mcp": (0.30, 0.50, 0.00),  # collapsed
                "pinky_mcp":  (0.30, 0.50, 0.00),  # collapsed
            },
        },
        "right": samples_rest["right"],
    }
    state = new_state()
    out = update(state, degen, dt, params)
    print("\n=== degenerate hand landmarks → forearm fallback ===")
    print(f"  left hand_active={out['left']['hand_active']:.0f}  "
          f"dir={_round(out['left']['dir'])}")
    assert out["left"]["hand_active"] == 0.0, \
        "degenerate landmarks must trigger fallback"

    print("\nOK — forearm fallback, hand basis, wrist roll, jitter rejection,")
    print("     hand drop, total dropout, swing velocity, chirality, and")
    print("     degenerate landmarks all pass.")
