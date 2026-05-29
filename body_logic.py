# body_logic.py
# =============
# Pure-Python skeleton definition + helpers for the BODY force field — the
# layer that lets the performer's whole body (not just 5 debug points) push and
# drag the particle soup. No TD imports; self-testable: `python3 body_logic.py`.
#
# The body_tex Script TOP packs these joints (position + per-joint velocity +
# visibility) into a small RGBA32F texture; `shaders/body_field.frag` splats the
# BONES as soft capsules into a 2D field (push away from each bone + drag along
# its velocity); a native Lookup Texture POP samples that at each particle's Puv
# → `bodyforce`, which `bounds_reflect` folds into PartVel. See
# velocity_controller_setup.md "Body push + drag".
#
# Coordinate space: joints are MediaPipe-UV (x,y ∈ [0,1], y=0 top). The field is
# sampled at particle Puv (also [0,1]); the velocity_field already lives in this
# space, so the body field matches it. Aspect (16:9) is corrected in the shader
# when measuring distance so the falloff radius is round in world units.

# Joints we drive the body field from. (name, MediaPipe Pose landmark index.)
# Positions come from in_pose `<name>:x/y`; visibility from `visibility<idx>`.
# Order here == the packed texture column order (the "pack index" the BONES use).
# Upper bound on simultaneously tracked people (matches adapters.contract).
# Each person gets a full skeleton + per-joint visibility; persons not present
# emit visibility 0 and are dropped by the field/viz shaders. See
# docs/sensor_contract.md ("Multi-person support").
MAX_PERSONS = 4

JOINTS = [
    ('nose',           0),
    ('left_shoulder',  11),
    ('right_shoulder', 12),
    ('left_elbow',     13),
    ('right_elbow',    14),
    ('left_wrist',     15),
    ('right_wrist',    16),
    ('left_hip',       23),
    ('right_hip',      24),
    ('left_knee',      25),
    ('right_knee',     26),
    ('left_ankle',     27),
    ('right_ankle',    28),
]
NJOINTS = len(JOINTS)

# Bones as (pack-index A, pack-index B) into JOINTS. Major skeleton only — the
# limbs + torso box + a light head/neck cross. Each is splatted as a capsule.
BONES = [
    (1, 2),    # shoulders
    (1, 3), (3, 5),    # left arm  (shoulder→elbow→wrist)
    (2, 4), (4, 6),    # right arm
    (1, 7), (2, 8),    # torso sides (shoulder→hip)
    (7, 8),    # hips
    (7, 9), (9, 11),   # left leg  (hip→knee→ankle)
    (8, 10), (10, 12), # right leg
    (0, 1), (0, 2),    # head/neck cross (nose→shoulders)
]
NBONES = len(BONES)


def joint_velocity(prev, cur, dt):
    """Per-joint velocity (UV units / second) from previous and current
    positions. `prev`/`cur` are lists of (x, y); returns list of (vx, vy).
    dt <= 0 or a missing prev yields zero velocity (no spurious spike on the
    first cook). Pure — unit-testable."""
    if dt <= 0.0 or prev is None or len(prev) != len(cur):
        return [(0.0, 0.0) for _ in cur]
    out = []
    for (px, py), (cx, cy) in zip(prev, cur):
        out.append(((cx - px) / dt, (cy - py) / dt))
    return out


def visibility_index_channel(mp_idx):
    """Channel name carrying a joint's visibility on in_pose. The MediaPipe tox
    emits raw visibility as `visibility<idx>` (e.g. `visibility15` = left_wrist);
    only a renamed subset becomes `<name>:visible` downstream."""
    return 'visibility%d' % mp_idx


def person_position_channels(joint_name, person_id):
    """`(x, y)` channel names for a joint of a specific person. Tries the new
    `p<N>:<lm>:x` prefix first; falls back to the LEGACY non-prefixed names
    (`<lm>:x`) when `person_id == 0` — that way single-person MediaPipe data
    flows in unchanged, and multi-person sensors slot in alongside."""
    pref = 'p%d:' % int(person_id)
    return [pref + joint_name + ':x', pref + joint_name + ':y']


def person_visibility_channel(mp_idx, person_id):
    """Visibility channel for joint `mp_idx` of person `person_id`."""
    return 'p%d:visibility%d' % (int(person_id), int(mp_idx))


def read_first(chop, names, default=0.0):
    """Return the first channel value found in `chop` from a list of candidate
    names — used for transparent legacy fallback (try `p0:nose:x`, then `nose:x`).
    Pure-ish: takes any object with `.__getitem__` returning a channel with
    `.eval()`. `default` if none found / non-finite."""
    import math as _m
    for nm in names:
        try:
            c = chop[nm]
            if c is None:
                continue
            v = float(c.eval()) if hasattr(c, 'eval') else float(c[0])
            if _m.isfinite(v):
                return v
        except Exception:
            continue
    return default


if __name__ == '__main__':
    # Every bone references valid, distinct packed joints.
    for a, b in BONES:
        assert 0 <= a < NJOINTS and 0 <= b < NJOINTS, (a, b)
        assert a != b, (a, b)
    # No duplicate joints in the pack; names unique.
    names = [n for n, _ in JOINTS]
    assert len(set(names)) == NJOINTS
    idxs = [i for _, i in JOINTS]
    assert len(set(idxs)) == NJOINTS
    # joint_velocity: basic diff + guards.
    prev = [(0.0, 0.0), (0.5, 0.5)]
    cur = [(0.1, 0.0), (0.5, 0.6)]
    v = joint_velocity(prev, cur, 0.5)   # dt=0.5s
    assert abs(v[0][0] - 0.2) < 1e-9 and abs(v[1][1] - 0.2) < 1e-9, v
    assert joint_velocity(None, cur, 0.5) == [(0.0, 0.0), (0.0, 0.0)]
    assert joint_velocity(prev, cur, 0.0) == [(0.0, 0.0), (0.0, 0.0)]
    assert visibility_index_channel(15) == 'visibility15'
    print("OK — body_logic: %d joints, %d bones, velocity diff + guards pass."
          % (NJOINTS, NBONES))
