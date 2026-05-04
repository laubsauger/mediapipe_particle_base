# beatsaber_game_tick.py
# ======================
# Script CHOP callback for the Beat Saber game tick.
#
# Same pattern as velocity_script_chop: thin wrapper around pure-Python
# logic. Reads wrist+elbow landmark channels from the input (routed from
# velocity_controller/out1), builds saber_samples, calls Game.tick(),
# stores the result snapshot on the parent COMP so sibling Script ops
# can read it, and emits the most useful state as output channels.
#
# Required siblings inside the beatsaber_controller Base COMP:
#   - Text DATs `beatsaber_game_tick` (this file) and friends
#   - A Script CHOP with this DAT as its Callbacks DAT, named `game_tick`
#   - An input CHOP connected to velocity_controller/out1 (or equivalent)
#
# Input channel contract (on the Script CHOP's input CHOP):
#   left_wrist:x, left_wrist:y    (required, 0..1 MediaPipe UV)
#   left_elbow:x, left_elbow:y    (required, for forearm direction)
#   right_wrist:x, right_wrist:y
#   right_elbow:x, right_elbow:y
#   <L>:visible                   (optional, 0/1 — we gate on it)
#
# If the upstream `Landmarks` par on velocity_controller doesn't include
# `left_elbow` / `right_elbow`, add them before wiring this up:
#   op('/project1/velocity_controller').par.Landmarks = \
#       'left_wrist right_wrist left_elbow right_elbow left_ankle right_ankle nose'
# (nose/ankles are optional for gameplay but keep particle experiments fed.)
#
# Output channels:
#   <side>_hilt_<axis>      hilt base position at the wrist          (x, y, z per side)
#   <side>_hilt_top_<axis>  hilt-blade junction (where blade emerges)
#   <side>_tip_<axis>       far end of the blade
#   <side>_dir_<axis>       forward unit vector (hilt → tip)
#   <side>_up_<axis>        palm-normal unit vector (saber roll axis)
#   <side>_vel_<axis>       tip velocity over 1 cook
#   <side>_hand_active      1 if hand-knuckle basis contributed this cook, 0 otherwise
#   song_time
#   score, combo, multiplier
#   hits, misses, bad_cuts                     (running totals)
#   hit_this_frame, miss_this_frame, bad_cut_this_frame, spawned_this_frame
#                                              (0/1 flags for VFX triggers)
#   active_notes                               (count of currently-alive notes)
#
# Hand-tracking input channels (all OPTIONAL — saber falls back to forearm-only
# when missing or visibility-gated). Same per-landmark naming pattern as the
# existing pose channels:
#   <side>_hand_wrist:x/y/z            preferred hilt anchor when present
#   <side>_hand_wrist:visible
#   <side>_hand_index_mcp:x/y/z
#   <side>_hand_index_mcp:visible
#   <side>_hand_middle_mcp:x/y/z
#   <side>_hand_middle_mcp:visible
#   <side>_hand_pinky_mcp:x/y/z
#   <side>_hand_pinky_mcp:visible
#
# The full event list + active note list are stored via comp.store() for
# sibling ops (notes_chop, events_dat, etc.) to consume.

import os
import sys


# ---------------------------------------------------------------------------
# Module-load-time path setup. Make sure the project folder is on sys.path so
# `import beatsaber` works for the package with its relative imports.
# ---------------------------------------------------------------------------
def _ensure_beatsaber_on_path():
    try:
        pf = project.folder
    except Exception:
        pf = None
    if pf and pf not in sys.path:
        sys.path.insert(0, pf)


_ensure_beatsaber_on_path()

# Imports inside try so a Reload-on-broken-path doesn't hard-crash the DAT.
try:
    import beatsaber.game as _bs_game
    import beatsaber.beatmap as _bs_beatmap
except Exception as e:
    # Script CHOPs don't surface import-time exceptions nicely, so log and
    # let onCook noop. The user will see the error in the textport.
    debug(f"beatsaber_game_tick: import failed: {e}")
    _bs_game = None
    _bs_beatmap = None

# beatmap_gen is OPTIONAL — only needed when an audio file drives the
# beatmap. Loaded lazily because librosa pulls a lot of binaries.
_bs_beatmap_gen = None
def _get_beatmap_gen():
    global _bs_beatmap_gen
    if _bs_beatmap_gen is None:
        try:
            import beatsaber.beatmap_gen as bmg
            _bs_beatmap_gen = bmg
        except Exception as e:
            debug(f"beatsaber_game_tick: beatmap_gen import failed: {e}")
            _bs_beatmap_gen = False
    return _bs_beatmap_gen if _bs_beatmap_gen else None


STORAGE_KEY_GAME     = 'beatsaber_game'
STORAGE_KEY_SNAPSHOT = 'beatsaber_snapshot'
STORAGE_KEY_BEATMAP  = 'beatsaber_beatmap_path'  # track which beatmap is loaded


# ---------------------------------------------------------------------------
# Channel helpers (same style as velocity_script_chop)
# ---------------------------------------------------------------------------

def _find_chan(scriptOp, name):
    for cin in scriptOp.inputs:
        if cin is None:
            continue
        c = cin[name]
        if c is not None:
            return c
    return None


def _read(scriptOp, name, default=0.0):
    c = _find_chan(scriptOp, name)
    if c is None:
        return default
    try:
        return float(c[0])
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Game construction / beatmap hot-reload
# ---------------------------------------------------------------------------

STORAGE_KEY_BEATMAP_MTIME = 'beatsaber_beatmap_mtime'


def _get_or_build_game(comp):
    """Fetch the singleton Game from COMP storage, building it lazily.
    Rebuilds when:
      - the Beatmapfile par's path changes, OR
      - the file's modification time has changed since we last loaded it.

    The mtime check is what makes 'edit JSON, save, see effect on next
    cook' work without forcing the user to unstore the cached Game by
    hand. Without it, an in-place edit to the JSON is invisible because
    the cached Game still holds the pre-edit Beatmap object."""
    if _bs_game is None or _bs_beatmap is None:
        return None

    # Audio path takes precedence — when set, generate (or load cached)
    # beatmap from the audio file via beatsaber.beatmap_gen.
    audio_par   = getattr(comp.par, 'Audiofile', None)
    audio_rel   = audio_par.eval() if audio_par is not None else ''
    use_audio   = bool(audio_rel)

    if use_audio:
        audio_abs = os.path.join(project.folder, audio_rel) \
                    if not os.path.isabs(audio_rel) else audio_rel
        beatmap_abs = audio_abs        # cache key — audio path drives identity
    else:
        beatmap_par = getattr(comp.par, 'Beatmapfile', None)
        beatmap_rel = beatmap_par.eval() if beatmap_par is not None else 'beatsaber/test_beatmap.json'
        beatmap_abs = os.path.join(project.folder, beatmap_rel) \
                      if not os.path.isabs(beatmap_rel) else beatmap_rel

    game = comp.fetch(STORAGE_KEY_GAME, None)
    cached_path = comp.fetch(STORAGE_KEY_BEATMAP, None)
    cached_mtime = comp.fetch(STORAGE_KEY_BEATMAP_MTIME, None)

    # Stat the file. If it's missing the load below will fail loudly.
    try:
        current_mtime = os.path.getmtime(beatmap_abs)
    except OSError:
        current_mtime = None

    needs_rebuild = (game is None
                     or cached_path != beatmap_abs
                     or (current_mtime is not None
                         and cached_mtime != current_mtime))
    if needs_rebuild:
        # Audio-driven path: build (or load sidecar) via beatmap_gen.
        if use_audio:
            bmg = _get_beatmap_gen()
            if bmg is None:
                debug("beatsaber_game_tick: audio-driven beatmap requested "
                      "but beatmap_gen unavailable (install librosa). "
                      "Falling back to test beatmap.")
                bm = _bs_beatmap.Beatmap.from_json_file(
                    os.path.join(project.folder, 'beatsaber/test_beatmap.json'))
            else:
                offset_par = getattr(comp.par, 'Audiooffsetms', None)
                offset_ms = float(offset_par.eval()) if offset_par is not None else 0.0
                snap_par = getattr(comp.par, 'Audiosnapms', None)
                snap_ms = float(snap_par.eval()) if snap_par is not None else 30.0
                try:
                    bm_dict = bmg.load_or_generate_beatmap(
                        audio_abs,
                        audio_visual_offset_ms=offset_ms,
                        snap_threshold_ms=snap_ms,
                        log=lambda *a, **k: debug(' '.join(str(x) for x in a)),
                    )
                except Exception as e:
                    debug(f"beatsaber_game_tick: audio analysis failed: {e}")
                    return None
                bm = _bs_beatmap.Beatmap.from_dict(bm_dict)
        else:
            try:
                bm = _bs_beatmap.Beatmap.from_json_file(beatmap_abs)
            except Exception as e:
                debug(f"beatsaber_game_tick: beatmap load failed ({beatmap_abs}): {e}")
                return None
        # Side-lock: with mirrored webcam the user's left hand lands on
        # screen-right. To prevent cross-overs we squash each note into
        # its colour's half — red notes always x >= 0.5, blue always
        # x < 0.5. Same transform applied to display (notes_chop) AND
        # to the in-game collision position so swings register.
        mirror_par = getattr(comp.par, 'Mirrorsides', None)
        if mirror_par is not None and bool(mirror_par.eval()):
            for note in bm.notes:
                if note.color == 'red':
                    note.x = 0.5 + 0.5 * note.x
                else:
                    note.x = 0.5 * note.x
        game = _bs_game.Game(beatmap=bm)
        # IMPORTANT: prime the timeline's wall clock with the current
        # absTime.seconds BEFORE calling start(). Without this,
        # timeline._wall = 0.0 when start() latches _t0 = _wall, then the
        # first tick pushes the real wall clock (say 3600s) and song_time
        # jumps to 3600 immediately — every note spawns, misses, and
        # cleans up in a single cook, leaving 0 active notes forever.
        try:
            game.timeline.set_wall_clock(absTime.seconds)
        except Exception:
            pass
        auto_start = getattr(comp.par, 'Autostart', None)
        if auto_start is None or auto_start.eval():
            game.start()
        comp.store(STORAGE_KEY_GAME, game)
        comp.store(STORAGE_KEY_BEATMAP, beatmap_abs)
        comp.store(STORAGE_KEY_BEATMAP_MTIME, current_mtime)
    return game


# ---------------------------------------------------------------------------
# Main cook
# ---------------------------------------------------------------------------

def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    comp = parent()
    par = comp.par

    game = _get_or_build_game(comp)
    if game is None:
        # Emit a safe empty CHOP so downstream ops don't explode.
        scriptOp.numSamples = 1
        scriptOp.appendChan('song_time')[0] = 0.0
        return

    # Push latest parameter values into the Game instance each cook.
    # Cheap enough, and keeps the user's tweaks live. Direct par
    # access — if any of these AttributeErrors, the controller's
    # custom pars haven't been installed; run install_beatsaber_params
    # then reset_beatsaber_params and reload.
    game.params["hilt_length"]          = par.Hiltlength.eval()
    game.params["blade_length"]         = par.Bladelength.eval()
    game.params["z_extrusion"]          = par.Zextrusion.eval()
    game.params["hilt_plane_z"]         = par.Hiltplanez.eval()
    game.params["hand_weight"]          = par.Handweight.eval()
    game.params["orient_smooth"]        = par.Orientsmooth.eval()
    game.params["forward_lock"]         = bool(par.Forwardlock.eval())
    game.params["thrust_scale"]         = par.Thrustscale.eval()
    game.params["angle_tolerance_rad"]  = par.Angletolerancerad.eval()
    game.params["min_swing_speed"]      = par.Minswingspeed.eval()
    game.params["miss_window_seconds"]  = par.Misswindowseconds.eval()
    game.loop                           = bool(par.Loop.eval())

    vis_thresh = par.Visibilitythreshold.eval()

    # Visibility gate — we treat <L>:visible as a 0/1 already thresholded
    # by velocity_controller, but we still let the user tighten it further.
    def _visible(channel_base, fallback=True):
        c = _find_chan(scriptOp, f'{channel_base}:visible')
        if c is None:
            return fallback
        return float(c[0]) >= vis_thresh

    # Position-validity gate — now that the upstream limit1 clamp is
    # off, MediaPipe pose y/z values can be arbitrary signed floats
    # (the model emits them in a hip-centered range, not [0, 1]).
    # We only reject NaN/Inf; out-of-range values just mean the user
    # is partially out of frame, and the saber math still copes.
    import math as _math
    def _pos_valid(x, y):
        try:
            return _math.isfinite(float(x)) and _math.isfinite(float(y))
        except Exception:
            return False

    # Helper: read all 4 hand-knuckle landmarks for one side, returning
    # None if any required channel is missing OR if the per-landmark
    # visibility gate fails. The saber falls back to forearm-only when
    # this returns None.
    def _read_hand(side):
        # Per-landmark visibility check; if a landmark's :visible channel
        # is missing we treat it as visible (some hand toxes don't emit
        # one). Required channels: wrist, index_mcp, middle_mcp, pinky_mcp,
        # each with x, y, z.
        keys = ("wrist", "index_mcp", "middle_mcp", "pinky_mcp")
        out = {}
        all_visible = True
        for k in keys:
            base = f'{side}_hand_{k}'
            cx = _find_chan(scriptOp, f'{base}:x')
            cy = _find_chan(scriptOp, f'{base}:y')
            cz = _find_chan(scriptOp, f'{base}:z')
            if cx is None or cy is None:
                return None, False  # required channel missing → no hand
            x = float(cx[0])
            y = float(cy[0])
            z = float(cz[0]) if cz is not None else 0.0
            out[k] = (x, y, z)
            if not _visible(base, fallback=True):
                all_visible = False
        return out, all_visible

    left_hand,  left_hand_vis  = _read_hand('left')
    right_hand, right_hand_vis = _read_hand('right')

    # Hilt anchor: prefer the hand-tracker's wrist (more accurate during
    # rapid motion) over the pose wrist when available. Fall back to pose
    # wrist when hand isn't connected or is not visible.
    #
    # Returns a 3-tuple (x, y, z). Z is from MediaPipe's depth signal —
    # negative when the wrist is closer to the camera. Used by
    # saber_logic's `thrust_scale` to translate the hilt forward into
    # the tunnel on a hand thrust (POV feel). When `thrust_scale=0`
    # this z is ignored.
    def _hilt_xyz(side, hand, hand_vis):
        if hand is not None and hand_vis:
            return (hand["wrist"][0], hand["wrist"][1], hand["wrist"][2])
        x = _read(scriptOp, f'{side}_wrist:x', 0.3 if side == 'left' else 0.7)
        y_pose = _read(scriptOp, f'{side}_wrist:y', 0.0)
        z = _read(scriptOp, f'{side}_wrist:z', 0.0)
        return (x, 0.5 - y_pose, z)

    # MediaPipe pose y is HIP-CENTERED with +y pointing UP (head is
    # positive, feet negative). The renderer expects image-normalized
    # y where 0 = top of frame, 1 = bottom (Beat Saber / image
    # convention) so the existing sy=-Worldscale flip on the geo COMPs
    # produces the right screen orientation.
    #
    # Convert: y_image = 0.5 - y_pose
    #   pose y = +0.3 (hand raised) → y_image = 0.2 (upper image)
    #   pose y =  0.0 (hip)         → y_image = 0.5 (middle)
    #   pose y = -0.3 (hand low)    → y_image = 0.8 (lower image)
    def _read_landmark_xy(name):
        x = _read(scriptOp, f'{name}:x', 0.5)
        y_pose = _read(scriptOp, f'{name}:y', 0.0)
        return (x, 0.5 - y_pose)

    raw = {}
    for side in ('left', 'right'):
        wx, wy = _read_landmark_xy(f'{side}_wrist')
        ex, ey = _read_landmark_xy(f'{side}_elbow')
        raw[side] = (wx, wy, ex, ey)

    # No more synthetic Z — real wrist:z flows from MediaPipe pose
    # depth (hip-centered, negative = closer to camera). saber_logic's
    # `thrust_scale` path multiplies wrist_z to translate the hilt
    # into the tunnel. The synth-from-speed hack is gone (it locked
    # the blade onto a Z-axis line during any motion).
    def _synth_z(side):
        return 0.0

    def _build_sample(side, default_elbow_x):
        wx, wy, ex, ey = raw[side]
        wrist_ok = _visible(f'{side}_wrist') and _pos_valid(wx, wy)
        elbow_ok = _visible(f'{side}_elbow') and _pos_valid(ex, ey)

        if side == 'left':
            hand, hand_vis = left_hand, left_hand_vis
        else:
            hand, hand_vis = right_hand, right_hand_vis
        # Replace y in the hilt anchor with the offset-corrected value.
        wrist_xyz_raw = _hilt_xyz(side, hand, hand_vis)
        wrist_xyz = (wrist_xyz_raw[0], wy,
                     wrist_xyz_raw[2] + _synth_z(side))

        # Read elbow Z too — required for real 3D forearm direction.
        # Without it the blade direction is locked to the screen plane
        # plus a constant -z tilt, which makes Z-axis swings impossible.
        elbow_z = _read(scriptOp, f'{side}_elbow:z', 0.0)
        elbow_xyz = (ex if elbow_ok else default_elbow_x,
                     ey if elbow_ok else 0.5,
                     elbow_z)

        return {
            "wrist_xy": wrist_xyz,
            "elbow_xy": elbow_xyz,    # now 3D (kept name for back-compat)
            "wrist_visible": wrist_ok,
            "elbow_visible": elbow_ok,
            "hand_visible": hand_vis,
            "hand_landmarks": hand,
        }

    samples = {
        "left":  _build_sample('left',  0.3),
        "right": _build_sample('right', 0.7),
    }

    # Advance the game one cook.
    events, snapshot = game.tick(absTime.seconds, samples)

    # Stash the full snapshot for sibling Script ops.
    comp.store(STORAGE_KEY_SNAPSHOT, snapshot)

    # Per-cook hit list — beatsaber_slices.py reads this to spawn the
    # falling-halves slice particles. Each entry includes the note
    # position + cut direction + saber color so the slice script can
    # build a physics-driven shower without re-deriving any of it.
    hit_records = []
    if events.hits:
        try:
            from beatsaber.beatmap import CUT_VECTORS as _CV
        except Exception:
            _CV = {}
        for h in events.hits:
            note = h.get('note')
            saber = h.get('saber', 'left')
            if note is None:
                continue
            cv = _CV.get(getattr(note, 'cut', None), (0.0, 0.0, 0.0))
            sb = snapshot['sabers'].get(saber, {})
            sv = sb.get('velocity', (0.0, 0.0, 0.0))
            hit_records.append({
                'x': float(getattr(note, 'x', 0.5)),
                'y': float(getattr(note, 'y', 0.5)),
                'z': 0.0,                       # hit plane
                'size': float(getattr(note, 'size', 0.15)),
                'cut_x': float(cv[0]),
                'cut_y': float(cv[1]),
                'saber_vx': float(sv[0]),
                'saber_vy': float(sv[1]),
                'saber_vz': float(sv[2]),
                'color': getattr(note, 'color', 'red'),
                'song_time': float(snapshot['song_time']),
            })
    comp.store('beatsaber_last_hits', hit_records)

    # Per-cook event records for the slash-log overlay. ACTUAL
    # game-judged events (hit / bad_cut / miss). Build a note id →
    # note lookup so we can pull cut direction + color.
    note_by_id = {n.id: n for n in snapshot.get('active_notes', [])}
    event_records = []
    for h in events.hits:                 # list of dicts {note_id, saber, ...}
        note = note_by_id.get(h.get('note_id'))
        event_records.append({
            'kind':  'hit',
            'saber': h.get('saber', 'left'),
            'cut':   getattr(note, 'cut', 'any') if note is not None else 'any',
            'song_time': float(snapshot['song_time']),
        })
    for bc in events.bad_cuts:            # list of dicts
        note = note_by_id.get(bc.get('note_id'))
        event_records.append({
            'kind':  'bad',
            'saber': bc.get('saber', 'left'),
            'cut':   getattr(note, 'cut', 'any') if note is not None else 'any',
            'song_time': float(snapshot['song_time']),
        })
    for note_id in events.misses:         # list of bare note ids (int)
        note = note_by_id.get(note_id)
        # Pick side from the note's expected colour (red → left).
        saber = 'left' if (note is not None and getattr(note, 'color', '') == 'red') else 'right'
        event_records.append({
            'kind':  'miss',
            'saber': saber,
            'cut':   getattr(note, 'cut', 'any') if note is not None else 'any',
            'song_time': float(snapshot['song_time']),
        })
    comp.store('beatsaber_last_events', event_records)

    # ----- Emit output channels -----------------------------------------------
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate

    # Per-saber state.
    #   hilt     : hilt base at the wrist (xyz)
    #   hilt_top : hilt-blade junction (xyz) — where the blade emerges
    #   tip      : far end of the blade (xyz)
    #   dir      : forward unit vector (xyz)
    #   up       : palm-normal unit vector (xyz) — saber roll axis
    #   vel      : tip velocity over 1 cook (xyz)
    #   hand_active : 1.0 if hand-knuckle basis contributed, 0.0 otherwise
    #   blade_p<0..4>_<x/y/z> : five sample points along the blade,
    #       at fractions 0/0.25/0.5/0.75/1.0 from hilt_top to tip.
    #       p0 == hilt_top, p4 == tip. Designed for a Trail CHOP +
    #       CHOP-to-SOP "ribbon" visualization in the renderer that
    #       shows the WHOLE blade's recent motion, not just the tip.
    BLADE_TRAIL_SAMPLES = 5
    for side in ('left', 'right'):
        s = snapshot['sabers'][side]
        for axis_i, axis in enumerate(('x', 'y', 'z')):
            scriptOp.appendChan(f'{side}_hilt_{axis}')[0]     = float(s['hilt'][axis_i])
            scriptOp.appendChan(f'{side}_hilt_top_{axis}')[0] = float(s['hilt_top'][axis_i])
            scriptOp.appendChan(f'{side}_tip_{axis}')[0]      = float(s['tip'][axis_i])
            scriptOp.appendChan(f'{side}_dir_{axis}')[0]      = float(s['dir'][axis_i])
            scriptOp.appendChan(f'{side}_up_{axis}')[0]       = float(s['up'][axis_i])
            scriptOp.appendChan(f'{side}_vel_{axis}')[0]      = float(s['velocity'][axis_i])
        scriptOp.appendChan(f'{side}_hand_active')[0] = float(s.get('hand_active', 0.0))
        scriptOp.appendChan(f'{side}_tracking_active')[0] = float(s.get('tracking_active', 0.0))

        # Blade sample points — interpolate from hilt_top to tip so the
        # trail covers ONLY the bright glowing part, not the hilt stub.
        ht = s['hilt_top']
        tp = s['tip']
        for i in range(BLADE_TRAIL_SAMPLES):
            f = i / (BLADE_TRAIL_SAMPLES - 1) if BLADE_TRAIL_SAMPLES > 1 else 0.0
            for axis_i, axis in enumerate(('x', 'y', 'z')):
                v = ht[axis_i] + f * (tp[axis_i] - ht[axis_i])
                scriptOp.appendChan(f'{side}_blade_p{i}_{axis}')[0] = float(v)

    # Globals.
    scriptOp.appendChan('song_time')[0]      = float(snapshot['song_time'])
    sc = snapshot['score']
    scriptOp.appendChan('score')[0]          = float(sc['score'])
    scriptOp.appendChan('combo')[0]          = float(sc['combo'])
    scriptOp.appendChan('max_combo')[0]      = float(sc['max_combo'])
    scriptOp.appendChan('multiplier')[0]     = float(sc['multiplier'])
    scriptOp.appendChan('hits')[0]           = float(sc['hits'])
    scriptOp.appendChan('misses')[0]         = float(sc['misses'])
    scriptOp.appendChan('bad_cuts')[0]       = float(sc['bad_cuts'])
    scriptOp.appendChan('accuracy')[0]       = float(sc['accuracy'])

    # Per-cook event flags — 1 this frame if any event of that kind fired.
    # Useful for driving VFX triggers via Trail CHOP or Beat CHOP.
    scriptOp.appendChan('hit_this_frame')[0]     = 1.0 if len(events.hits) > 0 else 0.0
    scriptOp.appendChan('miss_this_frame')[0]    = 1.0 if len(events.misses) > 0 else 0.0
    scriptOp.appendChan('bad_cut_this_frame')[0] = 1.0 if len(events.bad_cuts) > 0 else 0.0
    scriptOp.appendChan('spawned_this_frame')[0] = 1.0 if len(events.spawned) > 0 else 0.0

    # Active note count — handy for debugging + UI.
    scriptOp.appendChan('active_notes')[0]   = float(len(snapshot['active_notes']))

    # ----- UI feedback telemetry ----------------------------------------------
    # `last_*` channels are persistent: they hold the most recent hit/event
    # values across cooks so a HUD or fade-out animation has something to
    # read after the per-frame pulse channels have returned to 0. Stored on
    # the COMP via fetch/store so they survive reloads.
    last_hit_quality   = comp.fetch('last_hit_quality',   0.0)
    last_swing_speed   = comp.fetch('last_swing_speed',   0.0)
    last_hit_saber     = comp.fetch('last_hit_saber',     0.0)   # 0=none, 1=left, 2=right
    last_event_kind    = comp.fetch('last_event_kind',    0.0)   # 0=none, 1=hit, 2=miss, 3=bad_cut
    last_event_time    = comp.fetch('last_event_time',    0.0)
    if events.hits:
        h = events.hits[-1]
        last_hit_quality = float(h.get('quality',  0.0))
        last_swing_speed = float(h.get('points',   0.0)) and last_swing_speed or last_swing_speed
        # Capture swing magnitude from the saber that hit.
        sb = snapshot['sabers'].get(h['saber'], {})
        v = sb.get('velocity', (0.0, 0.0, 0.0))
        last_swing_speed = float((v[0]**2 + v[1]**2 + v[2]**2) ** 0.5)
        last_hit_saber   = 1.0 if h['saber'] == 'left' else 2.0
        last_event_kind  = 1.0
        last_event_time  = float(snapshot['song_time'])
    elif events.bad_cuts:
        bc = events.bad_cuts[-1]
        last_swing_speed = float(bc.get('swing_speed') or 0.0)
        last_hit_saber   = 1.0 if bc['saber'] == 'left' else 2.0
        last_event_kind  = 3.0
        last_event_time  = float(snapshot['song_time'])
    elif events.misses:
        last_event_kind  = 2.0
        last_event_time  = float(snapshot['song_time'])
    comp.store('last_hit_quality', last_hit_quality)
    comp.store('last_swing_speed', last_swing_speed)
    comp.store('last_hit_saber',   last_hit_saber)
    comp.store('last_event_kind',  last_event_kind)
    comp.store('last_event_time',  last_event_time)

    # Time since the most recent event — UI fade-out can use this to
    # ramp alpha from 1.0 immediately after a hit down to 0 over ~0.5s.
    time_since_event = max(0.0, float(snapshot['song_time']) - last_event_time)

    scriptOp.appendChan('last_hit_quality')[0] = float(last_hit_quality)
    scriptOp.appendChan('last_swing_speed')[0] = float(last_swing_speed)
    scriptOp.appendChan('last_hit_saber')[0]   = float(last_hit_saber)
    scriptOp.appendChan('last_event_kind')[0]  = float(last_event_kind)
    scriptOp.appendChan('time_since_event')[0] = float(time_since_event)

    # Live tip-speed channels for trail-intensity rendering. Per-cook
    # magnitude of each saber's tip velocity. Already implicit in vel_*,
    # but having it as a single magnitude per side saves the renderer
    # the sqrt and is convenient for driving Bloom strength etc.
    for side in ('left', 'right'):
        v = snapshot['sabers'][side]['velocity']
        spd = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) ** 0.5
        scriptOp.appendChan(f'{side}_tip_speed')[0] = float(spd)

    # Upcoming-note hints — what's the soonest unhit note, what's its
    # color, where will it land, what cut direction does it want? Lets
    # the UI flash a "next move" indicator before the cube reaches the
    # slash plane. Empty values when nothing is queued.
    upcoming = None
    upcoming_t = float('inf')
    for note in snapshot['active_notes']:
        if note.state in ('hit', 'missed'):
            continue
        if note.time < upcoming_t:
            upcoming_t = note.time
            upcoming = note
    if upcoming is not None:
        scriptOp.appendChan('upcoming_time')[0]  = float(upcoming.time)
        scriptOp.appendChan('upcoming_x')[0]     = float(upcoming.x)
        scriptOp.appendChan('upcoming_y')[0]     = float(upcoming.y)
        scriptOp.appendChan('upcoming_color')[0] = 1.0 if upcoming.color == 'red' else 2.0
        # cut_x / cut_y from the CUT_VECTORS table, if available; default
        # to (0, 0) for "any" direction.
        try:
            from beatsaber.beatmap import CUT_VECTORS as _CV
            cv = _CV.get(upcoming.cut, (0.0, 0.0, 0.0))
            scriptOp.appendChan('upcoming_cut_x')[0] = float(cv[0])
            scriptOp.appendChan('upcoming_cut_y')[0] = float(cv[1])
        except Exception:
            scriptOp.appendChan('upcoming_cut_x')[0] = 0.0
            scriptOp.appendChan('upcoming_cut_y')[0] = 0.0
        # Time-to-hit (negative when the note is past its hit window).
        scriptOp.appendChan('upcoming_dt')[0] = float(upcoming.time - snapshot['song_time'])
    else:
        # No active note — emit zeros so downstream channel ordering is stable.
        for ch in ('upcoming_time', 'upcoming_x', 'upcoming_y',
                   'upcoming_color', 'upcoming_cut_x',
                   'upcoming_cut_y', 'upcoming_dt'):
            scriptOp.appendChan(ch)[0] = 0.0

    return

