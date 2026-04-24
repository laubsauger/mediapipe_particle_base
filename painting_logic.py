"""
painting_logic.py
=================

Pure-Python logic for the interactive painting controller. No TouchDesigner
imports in here, so you can run `python painting_logic.py` outside TD and
see the full state matrix printed as a sanity check.

Place this file in TD as a Text DAT named `painting_logic` (extension set to
.py or not — both work when accessed via `mod`).

The controller has three inputs:
    1. Wrist coordinates from MediaPipe (normalized 0..1 in the source frame)
    2. A scene slider (0 or 1; can be analog with a ramp — we blend)
    3. A set of border thresholds that define the playable area

It outputs target weights for:
    prompt3, prompt4, prompt5, prompt6, model0, model1, model2, butterfly

The caller (Script CHOP) emits instantaneous targets. Smoothing (crossfade
between states) is done downstream with a Lag CHOP — that keeps the blend
time as a single tweakable knob in the network, not buried in code.

Aspect-ratio note
-----------------
MediaPipe outputs wrist x,y in 0..1 of its *source image* frame. If your
installation viewport has the same aspect ratio as the camera feed, the
border thresholds below act directly on that space and are correct.

If your source (camera) and viewport aspect ratios differ, map the coords
upstream with a Math/Stretch CHOP before feeding this module, OR pass
`source_aspect` and `view_aspect` into `wrists_in_bounds` and we'll
letterbox/pillarbox-correct the point before the bounds test.
"""

# Output names in a fixed order (same order the Script CHOP emits them).
OUTPUT_NAMES = (
    "prompt3",
    "prompt4",
    "prompt5",
    "prompt6",
    "model0",
    "model1",
    "model2",
    "butterfly",
)

# ---------------------------------------------------------------------------
# State matrix  --  edit these numbers to retune the installation.
# Keys: (scene, hands_in_bounds)
#   scene ∈ {0, 1}
#   hands_in_bounds ∈ {True, False}
# ---------------------------------------------------------------------------
STATE_MATRIX = {
    "prompt3":   {(0, False): 1.0, (0, True): 0.3, (1, False): 0.0, (1, True): 0.0},
    "prompt4":   {(0, False): 0.0, (0, True): 1.0, (1, False): 0.0, (1, True): 0.0},
    "prompt5":   {(0, False): 0.0, (0, True): 0.0, (1, False): 1.0, (1, True): 0.3},
    "prompt6":   {(0, False): 0.0, (0, True): 0.0, (1, False): 0.0, (1, True): 1.0},
    "model0":    {(0, False): 0.0, (0, True): 0.3, (1, False): 0.0, (1, True): 0.0},
    "model1":    {(0, False): 0.0, (0, True): 0.0, (1, False): 1.0, (1, True): 1.0},
    "model2":    {(0, False): 1.0, (0, True): 1.0, (1, False): 0.0, (1, True): 0.0},
    "butterfly": {(0, False): 0.0, (0, True): 1.0, (1, False): 0.0, (1, True): 0.0},
}


# ---------------------------------------------------------------------------
# Bounds test
# ---------------------------------------------------------------------------

def _remap_for_aspect(x, y, source_aspect, view_aspect):
    """
    Remap a point from `source_aspect` (w/h) into `view_aspect` space using
    a 'fit-inside' letterbox (preserves the full source content, adds
    symmetric dead-zones on the axis that has to shrink).

    Returns (x', y', visible) where `visible` is False if the point ended up
    outside 0..1 in the view space (i.e., the point was in the letterbox bar).

    If either aspect is None or 0, this is a no-op and the input is returned.
    """
    if not source_aspect or not view_aspect or source_aspect == view_aspect:
        return x, y, True
    if source_aspect > view_aspect:
        # Source is wider. Fit width -> letterbox on top/bottom in view.
        scale = view_aspect / source_aspect  # < 1
        y2 = 0.5 + (y - 0.5) * scale
        return x, y2, 0.0 <= y2 <= 1.0
    else:
        # Source is taller. Fit height -> pillarbox on left/right.
        scale = source_aspect / view_aspect  # < 1
        x2 = 0.5 + (x - 0.5) * scale
        return x2, y, 0.0 <= x2 <= 1.0


def is_point_in_bounds(x, y,
                       border_left, border_right, border_top, border_bottom):
    """
    x, y in 0..1 of the viewport (origin top-left, MediaPipe convention).
    Each border_* is a 0..1 fraction of that axis excluded from the playable
    area on that side.
    """
    return (border_left <= x <= (1.0 - border_right)
            and border_top <= y <= (1.0 - border_bottom))


def wrists_in_bounds(left_xy, right_xy,
                     left_vis, right_vis,
                     border_left, border_right, border_top, border_bottom,
                     visibility_threshold=0.5,
                     mode="or",
                     source_aspect=None, view_aspect=None):
    """
    Decide whether hands are in the playable area.

    left_xy / right_xy: (x, y) tuples in 0..1 from MediaPipe. Either may be
        None to mean "not tracked".
    left_vis / right_vis: MediaPipe visibility/confidence (0..1). None is
        treated as fully visible (useful if you don't route visibility in).
    mode: "or"  -> hands=True if EITHER wrist qualifies (more forgiving)
          "and" -> hands=True only if BOTH wrists qualify (stricter)
    source_aspect / view_aspect: optional aspect correction (see module docs).
    """
    def wrist_in(xy, vis):
        if xy is None:
            return False
        if vis is not None and vis < visibility_threshold:
            return False
        x, y = xy
        if source_aspect is not None and view_aspect is not None:
            x, y, visible = _remap_for_aspect(x, y, source_aspect, view_aspect)
            if not visible:
                return False
        return is_point_in_bounds(x, y, border_left, border_right,
                                  border_top, border_bottom)

    l_in = wrist_in(left_xy, left_vis)
    r_in = wrist_in(right_xy, right_vis)
    if mode == "and":
        return l_in and r_in
    return l_in or r_in


# ---------------------------------------------------------------------------
# Target weights
# ---------------------------------------------------------------------------

def compute_targets(scene, hands):
    """
    Hard-snapped scene lookup.
    scene: 0 or 1 (any value >= 0.5 is treated as 1)
    hands: bool
    Returns {output_name: weight}
    """
    scene_int = 1 if float(scene) >= 0.5 else 0
    key = (scene_int, bool(hands))
    return {name: STATE_MATRIX[name][key] for name in OUTPUT_NAMES}


def compute_targets_blended_scene(scene_f, hands):
    """
    Analog scene crossfade. If your scene slider smoothly ramps between 0
    and 1 (instead of hard-switching), this returns a weighted blend between
    scene=0 and scene=1 targets. For a hard 0/1 slider, the result is
    identical to compute_targets().
    """
    s0 = compute_targets(0, hands)
    s1 = compute_targets(1, hands)
    t = max(0.0, min(1.0, float(scene_f)))
    return {name: s0[name] * (1.0 - t) + s1[name] * t
            for name in OUTPUT_NAMES}


# ---------------------------------------------------------------------------
# Self-test: run `python painting_logic.py` to print the full matrix and
# validate a few edge cases. Useful before you drop this into TD.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("State matrix (rows = outputs, cols = state):")
    print(f"{'output':<10} | {'s0/no':>6} | {'s0/yes':>6} | {'s1/no':>6} | {'s1/yes':>6}")
    print("-" * 50)
    for name in OUTPUT_NAMES:
        m = STATE_MATRIX[name]
        print(f"{name:<10} | {m[(0, False)]:>6} | {m[(0, True)]:>6} | "
              f"{m[(1, False)]:>6} | {m[(1, True)]:>6}")
    print()

    # Spot-checks
    cases = [
        ((0.5, 0.5), (0.5, 0.5), 1.0, 1.0, 0, True,  "both wrists centered, scene 0"),
        ((0.05, 0.5), (0.5, 0.5), 1.0, 1.0, 0, True,  "left wrist in left deadzone, OR mode -> still hands"),
        ((0.05, 0.5), (0.05, 0.5), 1.0, 1.0, 0, False, "both wrists in deadzone -> no hands"),
        ((0.5, 0.5), (0.5, 0.5), 0.1, 0.1, 0, False, "both wrists low visibility -> no hands"),
        ((0.5, 0.5), (0.5, 0.5), 1.0, 1.0, 1, True,  "both wrists centered, scene 1"),
    ]
    border = dict(border_left=0.15, border_right=0.15,
                  border_top=0.15, border_bottom=0.15)
    print("Bounds test spot-checks:")
    for l_xy, r_xy, l_v, r_v, scene, expected, label in cases:
        got = wrists_in_bounds(l_xy, r_xy, l_v, r_v,
                               border["border_left"], border["border_right"],
                               border["border_top"], border["border_bottom"],
                               visibility_threshold=0.5, mode="or")
        ok = "OK " if got == expected else "FAIL"
        w = compute_targets_blended_scene(scene, got)
        print(f"  [{ok}] {label}: hands={got}  -> "
              f"prompt3={w['prompt3']} prompt5={w['prompt5']} "
              f"butterfly={w['butterfly']} model1={w['model1']}")
