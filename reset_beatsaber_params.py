"""
reset_beatsaber_params.py
=========================

Force-reset every parent par on the `beatsaber_controller` COMP to its
current "known good" default. Unlike install_beatsaber_params.py (which
is idempotent and skips existing pars), this one FORCIBLY overwrites
every value.

Run this when:
- The defaults in the codebase have moved on and you want to apply them
  to a COMP that was installed earlier (the installer alone doesn't
  push new defaults onto existing pars).
- Your tuning has drifted somewhere unhelpful and you want a clean
  baseline before re-tuning.
- You're handing the project off and want to know the user is starting
  from documented values.

Usage
-----
- Paste into a Text DAT *inside* the beatsaber_controller COMP (same
  place install_beatsaber_params.py goes). The simplest workflow is:
    1. Add a Text DAT named `reset_beatsaber_params` whose File par
       points at this script (sibling Sync File pattern).
    2. Right-click ▸ Run Script.
- Or run it from the textport once:
    cd = op('/project1/beatsaber_controller')
    op('/project1/beatsaber_controller/install_beatsaber_params').run()
    op('/project1/beatsaber_controller/reset_beatsaber_params').run()
- The DAT can then be deleted — the par values live on the COMP.

Assumes the pars have already been created (via
install_beatsaber_params.py). Missing pars are logged and skipped so
you can run the installer + the reset back-to-back without errors.

Existing par values are overwritten with the constants below regardless
of any tuning you've done. Save a note first if you have values you
want to preserve.

To create a "preset" (e.g. a strict-scoring profile, or a renderer
profile that disables the hand basis), copy this file and tweak the
constants below. Keep the structure — the _apply() loop is generic.
"""

comp = parent()


# ---------------------------------------------------------------------------
# Sensing page
# ---------------------------------------------------------------------------
SENSING = {
    'Visibilitythreshold': 0.5,
}

# ---------------------------------------------------------------------------
# Saber page
# ---------------------------------------------------------------------------
SABER = {
    # Geometry — hilt + blade. The two sum to ~0.25 UV by default
    # (about a quarter of the frame width), with a short hilt segment
    # so the blade visibly emerges from a closed fist.
    'Hiltlength':   0.04,
    'Bladelength':  0.21,
    # Hand basis dominance vs forearm fallback. 1.0 = trust the
    # hand-knuckle basis fully when present, 0.0 = ignore the hand and
    # always use the forearm.
    'Handweight':   1.0,
    # Quaternion / palm-normal smoothing time constant. Lower = snappier
    # response to wrist twists, more knuckle jitter through. Higher =
    # smoother at the cost of perceptible roll lag on fast twists.
    'Orientsmooth': 0.03,
    # Forearm fallback -Z tilt (ignored when the hand basis is active).
    'Zextrusion':   0.3,
    # Z of the hilt base in game world. 0 = on the hit plane.
    'Hiltplanez':   0.0,
    # Legacy par from the previous geometry model. The runtime no
    # longer reads it (Hiltlength + Bladelength supersede it), but if
    # it exists on the COMP we reset it too to keep its label honest.
    'Saberlength':  0.25,
}

# ---------------------------------------------------------------------------
# Gameplay page
# ---------------------------------------------------------------------------
GAMEPLAY = {
    'Beatmapfile':       'beatsaber/test_beatmap.json',
    'Autostart':         True,
    'Loop':              True,
    'Angletolerancerad': 1.0,    # ~57° — lenient for webcam tracking noise
    'Minswingspeed':     0.02,   # UV/cook (~1.2 UV/s @ 60 fps)
    'Misswindowseconds': 0.25,
    # Pulse pars (Start/Pause/Resume/Reset) are momentary triggers, not
    # values, so we don't reset them.
}

# ---------------------------------------------------------------------------
# Debug page
# ---------------------------------------------------------------------------
DEBUG = {
    'Enableeventslog': True,
    'Trailframes':     8,
}


def _apply(mapping, page_label):
    """Set every par in `mapping` to its mapped value, logging which
    ones were applied vs missing. Boolean values are coerced to int 1/0
    since toggle pars store as ints."""
    applied = 0
    missing = []
    for name, val in mapping.items():
        p = getattr(comp.par, name, None)
        if p is None:
            missing.append(name)
            continue
        try:
            if isinstance(val, bool):
                p.val = 1 if val else 0
            else:
                p.val = val
            applied += 1
        except Exception as e:
            print(f"  FAIL {page_label}.{name}: {e}")
    print(f"{page_label}: {applied}/{len(mapping)} pars set.")
    if missing:
        print(f"  missing (run install_beatsaber_params.py first): {missing}")


_apply(SENSING,  'Sensing')
_apply(SABER,    'Saber')
_apply(GAMEPLAY, 'Gameplay')
_apply(DEBUG,    'Debug')

print("reset_beatsaber_params: done. All existing pars forced to current "
      "defaults. Save a note of your previous tuning if you had custom "
      "values — they're now overwritten.")
