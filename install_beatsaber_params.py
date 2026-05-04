"""
install_beatsaber_params.py
===========================

Idempotent parameter installer for the `beatsaber_controller` Base COMP.
Run once from a Text DAT *inside* the Base COMP: right-click ▸ Run Script.

Mirrors the pattern of install_velocity_params: idempotent, pars live on
the COMP itself, multiple pages for organisational clarity.

Pages:
  Sensing    — landmark input gate
  Saber      — saber geometry / direction
  Gameplay   — beatmap, hit detection, timing
  Debug      — toggles for debug readouts / visualisation

Re-running is safe; existing values are preserved. See
reset_beatsaber_params.py (if ever needed) for a force-reset equivalent.
"""

comp = parent()


def _page(name):
    for p in comp.customPages:
        if p.name == name:
            return p
    return comp.appendCustomPage(name)


def _has(name):
    return getattr(comp.par, name, None) is not None


def add_float(page, name, label, default, rmin, rmax,
              clamp_min=True, clamp_max=True):
    if _has(name):
        return
    pg = page.appendFloat(name, label=label)
    p = pg[0]
    p.default = default
    p.val = default
    p.normMin = rmin
    p.normMax = rmax
    p.clampMin = clamp_min
    p.clampMax = clamp_max


def add_str(page, name, label, default):
    if _has(name):
        return
    pg = page.appendStr(name, label=label)
    p = pg[0]
    p.default = default
    p.val = default


def add_toggle(page, name, label, default):
    if _has(name):
        return
    pg = page.appendToggle(name, label=label)
    p = pg[0]
    p.default = 1 if default else 0
    p.val = p.default


def add_pulse(page, name, label):
    if _has(name):
        return
    page.appendPulse(name, label=label)


# ---------------------------------------------------------------------------
# Page 1: Sensing
# ---------------------------------------------------------------------------
sensing = _page('Sensing')

# Same idea as velocity_controller's Visibilitythreshold — applies to the
# <L>:visible gate channel we read from upstream. Since velocity_controller
# has already thresholded at its own value, this can be 0.5 to pass through.
add_float(sensing, 'Visibilitythreshold', 'Visibility Threshold (gate)',
          0.5, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Page 2: Saber
# ---------------------------------------------------------------------------
saber = _page('Saber')

# Hilt segment length — short stub from the wrist to where the blade
# emerges. Visually, the hilt is what's "in the fist". 0.08 UV ≈ 8% of
# the frame width is roughly a closed-fist's worth of offset.
add_float(saber, 'Hiltlength', 'Hilt Length (UV)',
          0.08, 0.0, 0.3, clamp_max=False)

# Blade segment length — the bright glowing part. 0.55 UV with the
# default 0.08 hilt sums to 0.63 UV total — visually substantial
# at the default camera distance.
add_float(saber, 'Bladelength', 'Blade Length (UV)',
          0.55, 0.1, 1.5, clamp_max=False)

# Hand-vs-forearm orientation blend. 1.0 = trust the hand-knuckle basis
# fully when the hand tracker provides data; 0.0 = ignore the hand,
# always use the forearm fallback. Useful to dial down if hand
# tracking is glitchy in your lighting setup.
add_float(saber, 'Handweight', 'Hand Basis Weight (0=forearm only)',
          1.0, 0.0, 1.0)

# Quaternion / palm-normal smoothing time constant. Lower = snappier
# response to wrist roll, more knuckle jitter through. Higher = smoother
# at the cost of a perceptible lag on fast wrist twists. 0.03 s
# (~half-life ≈ 20 ms) is the sweet spot for typical webcam tracking.
add_float(saber, 'Orientsmooth', 'Orientation Smooth (s)',
          0.03, 0.0, 0.3, clamp_max=False)

# Z-extrusion: how much the saber tilts into +z (toward approaching notes).
# 0 = saber flat in the hit plane, 1 = saber pointing straight into the lane.
# 0.1 keeps the blade close to z=0 so notes (which sit on the hit plane
# at z=0 with a small ±0.075 thickness) actually intersect the blade
# during a swing; a larger value with the default blade length would
# leave the tip too far into the tunnel and notes would miss.
# Only used by the FOREARM FALLBACK basis — when the hand tracker is
# providing landmarks the forward axis comes from wrist→middle_MCP and
# this par doesn't contribute.
add_float(saber, 'Zextrusion', 'Z Extrusion (forearm fallback only)',
          0.1, 0.0, 1.0)

# POV / thrust feel.
#
# Forwardlock: when ON, the blade's forward axis is clamped to the z ≤ 0
# half-space, so the blade can NEVER rotate toward the camera. This
# stops the visually-jarring "blade swooping out of the screen" effect
# when a forward thrust pose puts the wrist past the elbow on the
# camera-facing axis. Default ON.
add_toggle(saber, 'Forwardlock', 'Forward Lock (blade tilts away from camera)',
           True)

# Thrustscale: maps the wrist's MediaPipe depth (negative when wrist
# is closer to the camera) into hilt z so a forward-thrust motion
# translates the saber INTO the tunnel — the POV expectation. 0 =
# locked to the hit plane (no thrust translation, default behavior
# from earlier versions). 1.5 = a 0.3-UV thrust toward the camera
# pushes the hilt 0.45 units deeper into the tunnel. Tune up if
# thrusts feel too small, down if you'd rather keep the saber on
# the hit plane for easier note-hitting.
add_float(saber, 'Thrustscale', 'Thrust Scale (wrist-z → hilt-z)',
          1.5, 0.0, 5.0, clamp_max=False)

# Which z the saber hilt sits at. 0 = hit plane (notes arrive here).
# Negative = hilt in front of hit plane, positive = behind.
add_float(saber, 'Hiltplanez', 'Hilt Plane Z',
          0.0, -1.0, 1.0, clamp_max=False, clamp_min=False)


# ---------------------------------------------------------------------------
# Page 3: Gameplay
# ---------------------------------------------------------------------------
game = _page('Gameplay')

# Beatmap file, relative to project.folder. The Script CHOP handles both
# relative and absolute paths; leave this as a relative path for project
# portability.
add_str(game, 'Beatmapfile', 'Beatmap File',
        'beatsaber/test_beatmap.json')

# Auto-start on first cook. If off, you'd start the game via a Pulse or
# by calling op('game_tick').par.Start.pulse() in Python.
add_toggle(game, 'Autostart', 'Auto-start on First Cook', True)

# Loop — automatically reset + restart the beatmap when it finishes.
# Useful for dev/testing and ambient/background modes. Turn off for
# scored single-play sessions where you want a final score screen.
add_toggle(game, 'Loop', 'Loop beatmap', True)

# Cut angle tolerance in radians. 1.0 rad ≈ 57° — fairly lenient for
# webcam-tracking noise. Tighten to 0.5 for strict direction matching.
add_float(game, 'Angletolerancerad', 'Cut Angle Tolerance (rad)',
          1.0, 0.1, 3.14, clamp_max=False)

# Minimum saber-tip swing magnitude (UV per cook) for a cut to count.
# At 60fps, 0.02 UV/cook = ~1.2 UV/s. Taps below this don't count.
add_float(game, 'Minswingspeed', 'Min Swing Speed (UV/cook)',
          0.02, 0.0, 0.5, clamp_max=False)

# How long past a note's hit_time we wait before marking it missed.
# 0.25s is forgiving; 0.1s is strict.
add_float(game, 'Misswindowseconds', 'Miss Window (s)',
          0.25, 0.0, 1.0, clamp_max=False)

# Manual controls — pulses that call methods on the Game singleton. Wire
# them via the Parameter Execute DAT (see setup guide) so pressing these
# triggers game.start() / game.pause() / game.reset().
add_pulse(game, 'Start', 'Start')
add_pulse(game, 'Pause', 'Pause')
add_pulse(game, 'Resume', 'Resume')
add_pulse(game, 'Reset', 'Reset')


# ---------------------------------------------------------------------------
# Page 4: Debug
# ---------------------------------------------------------------------------
debug_page = _page('Debug')

# Toggle the events DAT (saves a small amount of cook cost when off).
add_toggle(debug_page, 'Enableeventslog', 'Enable Events Log', True)

# Scale for the debug saber/trail line render (if you wire one up).
add_float(debug_page, 'Trailframes', 'Saber Trail Length (frames)',
          8, 1, 60, clamp_max=False)


print("beatsaber_controller: custom pages installed "
      "({} params total).".format(
          len([pr for pr in comp.customPars
               if pr.page.name in ('Sensing', 'Saber', 'Gameplay', 'Debug')])))
