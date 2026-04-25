"""
hit_detection.py
================

Swept-volume saber vs. note AABB collision plus cut-direction scoring.
Pure Python.

Per frame, for each active note:

    1. Build the saber swept segment from (prev_tip → tip).
    2. Build the saber "blade line" as (hilt → tip).
    3. Test intersection between the swept segment (or the combined
       convex region it sweeps) and the note's AABB at its current z.
    4. If hit:
         a. Was the saber the correct color?
            Wrong color → BAD_CUT_COLOR.
         b. Was the saber moving in the required cut direction?
            If `note.cut == "any"` → direction doesn't matter.
            Otherwise compute angle_error between actual velocity
            direction and required cut vector. If error > threshold
            → BAD_CUT_DIRECTION.
         c. Compute quality (0..1) from: angle accuracy, through-center
            distance, and swing magnitude (encourages full swings).
         d. Return GOOD_CUT with quality score.
    5. If still no hit by the time `current_time > note.time + miss_window`
       → MISS.

Returns structured results so the caller (game.py) can feed into score/VFX.
"""

import math


# ---------------------------------------------------------------------------
# Result codes
# ---------------------------------------------------------------------------

GOOD_CUT            = "good"
BAD_CUT_COLOR       = "bad_color"
BAD_CUT_DIRECTION   = "bad_direction"
MISS                = "miss"


# ---------------------------------------------------------------------------
# Vector helpers (3D tuples — keep self-contained so this module has no
# dependency on saber_logic)
# ---------------------------------------------------------------------------

def _sub(a, b):   return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def _add(a, b):   return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def _scale(a, s): return (a[0]*s, a[1]*s, a[2]*s)
def _dot(a, b):   return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
def _len(a):      return math.sqrt(a[0]*a[0] + a[1]*a[1] + a[2]*a[2])
def _clamp(v, lo, hi): return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Geometry: segment vs AABB intersection
# ---------------------------------------------------------------------------

def _segment_aabb_intersect(p0, p1, aabb_min, aabb_max):
    """
    Slab-method line-segment vs axis-aligned bounding-box test.
    Returns (hit: bool, t_enter: float, t_exit: float).
    t is the 0..1 parameter along (p0 → p1) at which the segment enters/exits
    the AABB. Both -1.0 if no intersection.
    """
    tmin = 0.0
    tmax = 1.0
    d = _sub(p1, p0)
    for i in range(3):
        if abs(d[i]) < 1e-12:
            # Segment is parallel to this axis.
            if p0[i] < aabb_min[i] or p0[i] > aabb_max[i]:
                return (False, -1.0, -1.0)
        else:
            t1 = (aabb_min[i] - p0[i]) / d[i]
            t2 = (aabb_max[i] - p0[i]) / d[i]
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return (False, -1.0, -1.0)
    return (True, tmin, tmax)


def note_aabb(note):
    """Axis-aligned bounds of a note's cube at its current position."""
    h = note.size * 0.5
    return (
        (note.x - h, note.y - h, note.z - h),
        (note.x + h, note.y + h, note.z + h),
    )


# ---------------------------------------------------------------------------
# Cut-direction / angle checks
# ---------------------------------------------------------------------------

def _angle_error_2d(actual_vec, required_vec):
    """
    Angular error between two 2D vectors (we use xy of the 3D vectors).
    Returns radians; 0 = perfectly aligned, pi = opposite.
    Zero-length actual_vec returns pi (counts as no swing).
    """
    ax, ay = actual_vec[0], actual_vec[1]
    rx, ry = required_vec[0], required_vec[1]
    alen = math.sqrt(ax*ax + ay*ay)
    rlen = math.sqrt(rx*rx + ry*ry)
    if alen < 1e-6 or rlen < 1e-6:
        return math.pi
    c = _clamp((ax*rx + ay*ry) / (alen * rlen), -1.0, 1.0)
    return math.acos(c)


def _distance_segment_to_point(p0, p1, point):
    """Closest distance from a point to segment (p0, p1) in 3D."""
    d = _sub(p1, p0)
    dd = _dot(d, d)
    if dd < 1e-12:
        return _len(_sub(point, p0))
    t = _clamp(_dot(_sub(point, p0), d) / dd, 0.0, 1.0)
    closest = _add(p0, _scale(d, t))
    return _len(_sub(point, closest))


# ---------------------------------------------------------------------------
# Main collision check
# ---------------------------------------------------------------------------

def check_saber_vs_note(saber, note, cut_vectors, params):
    """
    One saber, one note — return a result tuple.

    saber        : dict with hilt, tip, prev_tip, prev_hilt, velocity, color
                   (color added by caller based on which saber)
    note         : a Note (beatmap.Note) with current position .x, .y, .z
    cut_vectors  : beatmap.CUT_VECTORS table (direction name → unit vec)
    params       : dict with
        angle_tolerance_rad : max angle error for a "good cut" (default 1.0 rad ≈ 57°)
        min_swing_speed     : min |velocity| to register a cut (typical 0.02 UV/frame)
        miss_window_seconds : ignored here (used by timeline.py for MISS detection)

    Returns (result, info_dict) where result is one of the GOOD_CUT / BAD_CUT_* codes
    or None if no contact happened. info_dict has: t_enter, hit_point, angle_error,
    center_dist, swing_speed, quality.
    """
    # 1. Segment sweep = (prev_tip → tip). We also use the blade line
    #    (hilt → tip) as a "current frame blade" collider, so fast-moving
    #    sabers don't tunnel through small notes between frames.
    prev_tip = saber["prev_tip"]
    tip      = saber["tip"]
    hilt     = saber["hilt"]

    aabb_min, aabb_max = note_aabb(note)

    # Swept tip segment.
    hit_tip, t_tip_in, t_tip_out = _segment_aabb_intersect(
        prev_tip, tip, aabb_min, aabb_max)
    # Current-frame blade segment (catches notes the tip skipped over).
    hit_blade, t_bl_in, t_bl_out = _segment_aabb_intersect(
        hilt, tip, aabb_min, aabb_max)

    if not (hit_tip or hit_blade):
        return (None, None)

    # Pick the "cleaner" intersection — earlier t on the tip sweep if present,
    # else the blade intersection.
    if hit_tip:
        t_enter = t_tip_in
        hit_point = _add(prev_tip, _scale(_sub(tip, prev_tip), t_enter))
    else:
        t_enter = t_bl_in
        hit_point = _add(hilt, _scale(_sub(tip, hilt), t_enter))

    # 2. Colour check.
    if saber["color"] != note.color:
        return (BAD_CUT_COLOR, {
            "t_enter": t_enter, "hit_point": hit_point,
            "swing_speed": _len(saber["velocity"]),
            "angle_error": None, "center_dist": None, "quality": 0.0,
        })

    # 3. Direction check (only if note specifies a direction).
    swing_speed = _len(saber["velocity"])
    angle_error = None

    if note.cut != "any":
        required_vec = cut_vectors[note.cut]
        angle_error  = _angle_error_2d(saber["velocity"], required_vec)
        if angle_error > params["angle_tolerance_rad"]:
            return (BAD_CUT_DIRECTION, {
                "t_enter": t_enter, "hit_point": hit_point,
                "angle_error": angle_error, "swing_speed": swing_speed,
                "center_dist": None, "quality": 0.0,
            })

    # 4. Minimum swing magnitude — taps shouldn't count as cuts.
    if swing_speed < params["min_swing_speed"]:
        return (BAD_CUT_DIRECTION, {
            "t_enter": t_enter, "hit_point": hit_point,
            "angle_error": angle_error, "swing_speed": swing_speed,
            "center_dist": None, "quality": 0.0,
        })

    # 5. Quality score (0..1): how accurate was the cut?
    note_center = (note.x, note.y, note.z)
    center_dist = _distance_segment_to_point(prev_tip, tip, note_center)
    # Normalise — hitting through exact center = 1.0, hitting at edge = ~0.
    center_score = 1.0 - _clamp(center_dist / (note.size * 0.7), 0.0, 1.0)

    # Angle score — 0 error = 1.0, at tolerance = 0.
    if note.cut == "any":
        angle_score = 1.0
    else:
        angle_score = 1.0 - _clamp(angle_error / params["angle_tolerance_rad"], 0.0, 1.0)

    # Swing magnitude score — at min_swing_speed = 0, at 4x = 1.0.
    swing_score = _clamp(swing_speed / (params["min_swing_speed"] * 4.0), 0.0, 1.0)

    quality = 0.5 * center_score + 0.3 * angle_score + 0.2 * swing_score

    return (GOOD_CUT, {
        "t_enter": t_enter, "hit_point": hit_point,
        "angle_error": angle_error, "swing_speed": swing_speed,
        "center_dist": center_dist, "quality": quality,
        "center_score": center_score,
        "angle_score": angle_score,
        "swing_score": swing_score,
    })


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def default_params():
    return {
        "angle_tolerance_rad": 1.0,    # ~57° lenient for webcam tracking noise
        "min_swing_speed":     0.02,   # UV/cook (~1.2 UV/s at 60 fps)
        "miss_window_seconds": 0.25,   # note ages out this long after hit time
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from beatsaber.beatmap import Note, CUT_VECTORS
    params = default_params()

    def saber(color, prev_tip, tip, hilt=None):
        if hilt is None:
            hilt = _add(tip, _scale(_sub(prev_tip, tip), 1.0))  # just something
        return {
            "color": color,
            "hilt": hilt,
            "tip": tip,
            "prev_tip": prev_tip,
            "prev_hilt": hilt,
            "velocity": _sub(tip, prev_tip),
        }

    # --- Test 1: clean down-cut through a red note ---
    note = Note(0, time=1.0, x=0.5, y=0.5, color="red", cut="down")
    note.z = 0.0
    s = saber("red",
              prev_tip=(0.5, 0.3, 0.0),
              tip=(0.5, 0.7, 0.0),
              hilt=(0.5, 0.3, 0.0))
    result, info = check_saber_vs_note(s, note, CUT_VECTORS, params)
    print(f"clean down-cut:         {result}  quality={info['quality']:.2f}")
    assert result == GOOD_CUT
    assert info["quality"] > 0.5

    # --- Test 2: wrong-colour saber ---
    s = saber("blue",
              prev_tip=(0.5, 0.3, 0.0),
              tip=(0.5, 0.7, 0.0))
    result, info = check_saber_vs_note(s, note, CUT_VECTORS, params)
    print(f"wrong colour:           {result}")
    assert result == BAD_CUT_COLOR

    # --- Test 3: correct colour, wrong direction (sideways instead of down) ---
    s = saber("red",
              prev_tip=(0.3, 0.5, 0.0),
              tip=(0.7, 0.5, 0.0))   # moving RIGHT, but note requires DOWN
    result, info = check_saber_vs_note(s, note, CUT_VECTORS, params)
    print(f"sideways on down-note:  {result}  angle_err={info['angle_error']:.2f}rad")
    assert result == BAD_CUT_DIRECTION

    # --- Test 4: too slow (tap) ---
    s = saber("red",
              prev_tip=(0.5, 0.495, 0.0),
              tip=(0.5, 0.505, 0.0))   # barely moving
    result, info = check_saber_vs_note(s, note, CUT_VECTORS, params)
    print(f"slow tap:               {result}  swing_speed={info['swing_speed']:.3f}")
    assert result == BAD_CUT_DIRECTION

    # --- Test 5: no intersection (saber far away) ---
    s = saber("red",
              prev_tip=(0.0, 0.3, 0.0),
              tip=(0.0, 0.7, 0.0))
    result, info = check_saber_vs_note(s, note, CUT_VECTORS, params)
    print(f"no intersection:        {result}")
    assert result is None

    # --- Test 6: "any" direction accepts anything fast ---
    note_any = Note(0, time=1.0, x=0.5, y=0.5, color="red", cut="any")
    note_any.z = 0.0
    s = saber("red",
              prev_tip=(0.3, 0.5, 0.0),
              tip=(0.7, 0.5, 0.0))
    result, info = check_saber_vs_note(s, note_any, CUT_VECTORS, params)
    print(f"'any' direction:        {result}  quality={info['quality']:.2f}")
    assert result == GOOD_CUT

    # --- Test 7: through-center cut scores higher than edge cut ---
    note_center = Note(0, time=1.0, x=0.5, y=0.5, color="red", cut="down")
    note_center.z = 0.0
    s_center = saber("red", prev_tip=(0.5, 0.3, 0.0), tip=(0.5, 0.7, 0.0))
    s_edge   = saber("red", prev_tip=(0.54, 0.3, 0.0), tip=(0.54, 0.7, 0.0))
    _, info_c = check_saber_vs_note(s_center, note_center, CUT_VECTORS, params)
    _, info_e = check_saber_vs_note(s_edge, note_center, CUT_VECTORS, params)
    print(f"center quality {info_c['quality']:.2f} vs edge quality {info_e['quality']:.2f}")
    assert info_c["quality"] > info_e["quality"], "center cuts should score higher"

    print("\nOK — all 7 hit-detection cases pass.")
