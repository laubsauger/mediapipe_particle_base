"""
game.py
=======

Top-level game coordinator. Ties together:
  - timeline   (current song time)
  - beatmap    (scheduled notes)
  - saber_logic(saber positions/velocities per cook)
  - hit_detection (swept-volume collisions)
  - score      (combo, multiplier, running totals)

One call per TD cook:
    game.tick(wall_seconds, saber_samples)

The tick:
  1. Pushes wall_seconds into the timeline.
  2. Spawns any notes whose spawn_time has arrived.
  3. Advances active note z positions (notes move toward z=0).
  4. Advances saber state from current landmark samples.
  5. Runs hit detection for every saber against every active note.
  6. Dispatches hit / bad-cut / miss events to the score.
  7. Cleans up hit/missed notes so the active list stays bounded.

The game exposes snapshots so the TD Script ops can read active note
positions, saber positions, and score stats into CHOP/DAT outputs.
"""

from .timeline import Timeline, TIMELINE
from .beatmap import Beatmap, CUT_VECTORS
from .saber_logic import (
    new_state as saber_new_state,
    update as saber_update,
    default_params as saber_default_params,
    SABER_COLORS,
)
from .hit_detection import (
    check_saber_vs_note,
    default_params as hit_default_params,
    GOOD_CUT, BAD_CUT_COLOR, BAD_CUT_DIRECTION,
)
from .score import Score


# ---------------------------------------------------------------------------
# Events — the game produces these each tick for consumers (VFX, audio,
# debug overlays, score display) to react to.
# ---------------------------------------------------------------------------

class Events:
    def __init__(self):
        self.spawned = []   # list of Note ids spawned this tick
        self.hits    = []   # list of dicts: {note_id, saber, quality, hit_point, ...}
        self.misses  = []   # list of Note ids that timed out
        self.bad_cuts = []  # list of dicts: {note_id, saber, reason, ...}

    def clear(self):
        self.spawned.clear()
        self.hits.clear()
        self.misses.clear()
        self.bad_cuts.clear()


# ---------------------------------------------------------------------------
# Game coordinator
# ---------------------------------------------------------------------------

class Game:
    # Class-level defaults. These exist so that any Game instance fetched
    # from comp.store() that predates a new attribute (e.g. `loop`) still
    # finds a sensible default via attribute lookup, instead of throwing
    # AttributeError on first tick. Any new attribute added to __init__
    # should also be mirrored here.
    loop = False
    _last_wall_seconds = None

    def __init__(self, beatmap=None, params=None, timeline=None, loop=False):
        """
        beatmap  : Beatmap instance (or None for "not loaded yet")
        params   : merged dict of saber + hit params (see default_params())
        timeline : Timeline instance (shared singleton from TIMELINE by default)
        loop     : if True, auto-reset-and-restart when the beatmap ends
                   (after the last note has completed its miss window).
                   For dev/testing so you don't have to click Start repeatedly.
        """
        self.beatmap = beatmap
        self.timeline = timeline if timeline is not None else TIMELINE
        self.params = dict(default_params())
        if params:
            self.params.update(params)
        self.loop = loop

        # Saber state dict (from saber_logic.new_state())
        self.saber_state = saber_new_state()

        # Active note lookup: map of Note.id → Note.
        self.active_notes = {}

        # Index into beatmap.notes for the next note to consider spawning.
        self._spawn_cursor = 0

        # Per-cook dt is computed from successive wall_seconds passed to
        # tick(). None on first cook → seeded with a nominal frame time.
        self._last_wall_seconds = None

        self.score = Score()
        self.events = Events()

    # -- lifecycle ----------------------------------------------------------

    def load_beatmap(self, beatmap):
        self.beatmap = beatmap
        self.reset()

    def reset(self):
        """Clear active notes, scores, timeline, and saber state.
        Call this when restarting a map."""
        self.saber_state = saber_new_state()
        self.active_notes = {}
        self._spawn_cursor = 0
        self.score.reset()
        self.events.clear()
        self.timeline.reset()

    def start(self):
        self.timeline.start()

    def pause(self):
        self.timeline.pause()

    def resume(self):
        self.timeline.resume()

    # -- main per-cook tick -------------------------------------------------

    def tick(self, wall_seconds, saber_samples):
        """
        wall_seconds   : absTime.seconds (or audio playback time).
        saber_samples  : dict with keys "left" and "right"; each value is
                         {wrist_xy, elbow_xy, wrist_visible, elbow_visible,
                          hand_landmarks (optional dict),
                          hand_visible (optional bool)}.
                         hand_landmarks is {wrist, index_mcp, middle_mcp,
                          pinky_mcp} → each (x, y, z). When absent or
                         hand_visible is False, saber orientation falls
                         back to the elbow→wrist forearm axis only.

        Returns (events, state_snapshot). state_snapshot has everything a
        TD Script op would want to read (active notes, saber positions,
        score summary).
        """
        # 1. Clock advances + per-cook dt for orientation smoothing.
        # dt is computed from successive wall_seconds to keep the saber
        # orientation EMA-slerp framerate-independent. Cap runaway dt
        # (after pause/reload) so a 10s gap doesn't snap the orientation
        # in one frame.
        prev_wall = getattr(self, "_last_wall_seconds", None)
        if prev_wall is None:
            dt = 1.0 / 60.0
        else:
            dt = max(0.0, wall_seconds - prev_wall)
            if dt > 0.25:
                dt = 1.0 / 60.0
        self._last_wall_seconds = wall_seconds

        self.timeline.set_wall_clock(wall_seconds)
        t = self.timeline.song_time()

        # 1.5 Loop: if we've gone past the end of the beatmap, reset and
        # restart from t=0. The end is the last note's hit time plus
        # travel_time (for any still-in-flight notes) plus a small tail
        # for VFX cleanup.
        # `getattr` shields against stale Game instances pulled from
        # comp.store() that predate the loop attribute.
        if (getattr(self, 'loop', False) and self.beatmap is not None
                and self.timeline.is_playing()):
            miss_window = self.params.get("miss_window_seconds", 0.25)
            end_time = (self.beatmap.duration()
                        + self.beatmap.travel_time
                        + miss_window
                        + 0.5)           # small tail so the last note isn't cut off
            if t > end_time:
                self.reset()
                # Prime the timeline so the new t0 matches the current
                # wall clock — song_time resumes at ~0 after the reset.
                self.timeline.set_wall_clock(wall_seconds)
                self.start()
                t = self.timeline.song_time()

        # 2. Fresh events bucket for this tick.
        self.events.clear()

        # 3. Saber update from landmark samples.
        # Pull every saber-related param from self.params; defaults from
        # saber_default_params() backfill anything the controller hasn't
        # overridden. dt drives the quaternion EMA-slerp.
        saber_params = {
            "hilt_length":   self.params.get("hilt_length", 0.04),
            "blade_length":  self.params.get("blade_length", 0.21),
            "hilt_plane_z":  self.params.get("hilt_plane_z", 0.0),
            "z_extrusion":   self.params.get("z_extrusion", 0.3),
            "hand_weight":   self.params.get("hand_weight", 1.0),
            "orient_smooth": self.params.get("orient_smooth", 0.06),
        }
        saber_out = saber_update(self.saber_state, saber_samples,
                                 dt, saber_params)

        # 4. Spawn notes whose spawn_time has arrived.
        if self.beatmap is not None:
            while self._spawn_cursor < len(self.beatmap.notes):
                note = self.beatmap.notes[self._spawn_cursor]
                if note.spawn_time is not None and note.spawn_time <= t:
                    note.state = "spawned"
                    self.active_notes[note.id] = note
                    self.events.spawned.append(note.id)
                    self._spawn_cursor += 1
                else:
                    break   # notes sorted by time; no later ones spawn yet

        # 5. Advance active note positions.
        #
        #   note.z = z_spawn * (1 - progress)
        #
        # With z_spawn = -10 and progress in [0, 1], z animates from -10
        # (spawn, far down the tunnel) to 0 (hit plane). Past progress=1
        # the formula naturally extends z past 0 toward positive — i.e.
        # the note continues flying TOWARD the camera at z=+3 and
        # eventually past it. We keep advancing the z for *missed*
        # notes too, so they visually fly past the player instead of
        # freezing on the slash plane (which felt like they were
        # waiting there). Hit notes stop updating — they vanish
        # instantly on contact (notes_chop filters them out).
        travel = self.beatmap.travel_time if self.beatmap is not None else 2.0
        for note in self.active_notes.values():
            if note.state == "hit":
                # Don't move hit notes — they're about to be cleaned
                # up. notes_chop won't render them anyway.
                continue
            # Includes spawned + missed notes. Missed notes keep flying
            # past the hit plane until cleanup removes them.
            progress = (t - note.spawn_time) / max(travel, 1e-6)
            note.z = note.z_spawn * (1.0 - progress)

        # 6. Collision detection: each saber vs each active note.
        #    Dispatch result via score + events.
        for note_id in list(self.active_notes.keys()):
            note = self.active_notes[note_id]
            if note.state in ("hit", "missed"):
                continue
            for saber_name in ("left", "right"):
                saber_snap = dict(saber_out[saber_name])
                saber_snap["color"] = SABER_COLORS[saber_name]
                result, info = check_saber_vs_note(
                    saber_snap, note, CUT_VECTORS, self.params)
                if result is None:
                    continue

                # Hit-ish event — register and mark the note.
                if result == GOOD_CUT:
                    note.state = "hit"
                    note.hit_time = t
                    pts = self.score.register_hit(info["quality"])
                    self.events.hits.append({
                        "note_id": note.id,
                        "saber": saber_name,
                        "quality": info["quality"],
                        "hit_point": info["hit_point"],
                        "points": pts,
                        "combo_after": self.score.combo,
                        "multiplier": self.score.multiplier,
                    })
                else:
                    # Bad cut (color or direction) — this counts as a hit
                    # ATTEMPT for the purposes of note lifecycle (so it
                    # gets cleaned up) but ALSO breaks combo.
                    note.state = "hit"   # mark as consumed
                    note.hit_time = t
                    self.score.register_bad_cut()
                    self.events.bad_cuts.append({
                        "note_id": note.id,
                        "saber": saber_name,
                        "reason": result,
                        "angle_error": info.get("angle_error"),
                        "swing_speed": info.get("swing_speed"),
                    })
                break  # one saber hitting the note is enough; don't check the other

        # 7. Miss detection: notes that are past their hit time by miss_window
        #    without being contacted.
        miss_window = self.params["miss_window_seconds"]
        for note in list(self.active_notes.values()):
            if note.state in ("hit", "missed"):
                continue
            if t > note.time + miss_window:
                note.state = "missed"
                self.score.register_miss()
                self.events.misses.append(note.id)

        # 8. Cleanup: drop notes when they're done.
        #
        # HIT notes: dropped on the next cook (cleanup_age=0). They
        # disappear instantly from the rendered scene the moment the
        # saber slashes through them.
        #
        # MISSED notes: kept around long enough to fly past the camera.
        # With z_spawn=-10 and travel_time=2s, a note's z continues at
        # the same linear rate after progress=1: z(t) = z_spawn * (1 -
        # (t - spawn_time) / travel_time). At progress=1.5 (0.5s after
        # hit time) z = +5; at progress=1.6 (0.6s after) z = +6, so it
        # passes the camera at z=+3 around progress=1.3 (i.e. ~0.6s
        # past the hit window). We give 0.8s past the hit time before
        # cleanup so the note is comfortably past the camera before
        # disappearing.
        hit_cleanup_age  = 0.0   # disappear immediately on hit
        miss_cleanup_age = 0.8   # fly past the camera (z=+3) and beyond
        to_drop = []
        for note_id, note in self.active_notes.items():
            if note.state == "hit" and t - note.hit_time >= hit_cleanup_age:
                to_drop.append(note_id)
            elif note.state == "missed" and t - note.time > miss_cleanup_age:
                to_drop.append(note_id)
        for note_id in to_drop:
            del self.active_notes[note_id]

        # 9. Build the state snapshot for TD consumers.
        snapshot = {
            "song_time": t,
            "sabers": saber_out,
            "active_notes": list(self.active_notes.values()),
            "score": self.score.summary(),
            "events": {
                "spawned": list(self.events.spawned),
                "hits":    list(self.events.hits),
                "misses":  list(self.events.misses),
                "bad_cuts": list(self.events.bad_cuts),
            },
        }
        return self.events, snapshot


# ---------------------------------------------------------------------------
# Merged defaults
# ---------------------------------------------------------------------------

def default_params():
    p = {}
    p.update(saber_default_params())
    p.update(hit_default_params())
    return p


# ---------------------------------------------------------------------------
# Self-test: a short synthetic play-through
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from beatsaber.beatmap import make_test_beatmap

    bm = make_test_beatmap()
    g = Game(beatmap=bm)
    g.start()

    # Synthetic saber motion: both hands "at rest" between notes, each hand
    # does a down-cut at roughly the scheduled note time.
    #
    # We simulate 16 seconds of play at 60fps.
    fps = 60
    wall = 0.0
    dt = 1.0 / fps

    # A helper that produces a saber sample for a given hand that's at
    # rest OR doing a down-cut (wrist travels downward).
    def at_rest(x, y):
        return {"wrist_xy": (x, y), "elbow_xy": (x, y + 0.2),
                "wrist_visible": True, "elbow_visible": True}

    def down_cut(x, y, t, hit_t):
        # Linear ramp: at hit_t wrist is low, before/after it returns.
        dt_from_hit = t - hit_t
        if abs(dt_from_hit) < 0.15:
            # Swing window — wrist below rest position.
            y_swing = y + 0.3 * (1.0 - abs(dt_from_hit) / 0.15)
            return {"wrist_xy": (x, y_swing), "elbow_xy": (x, y + 0.25),
                    "wrist_visible": True, "elbow_visible": True}
        return at_rest(x, y)

    hits_log = []
    misses_log = []
    bad_cuts_log = []

    # We'll check the first few notes explicitly. Each is at a specific
    # (x, y) and color.
    frames = int(16 * fps)
    for f in range(frames):
        wall = f * dt
        t_song = wall

        # Build saber samples driven by the beatmap — synthetic "perfect"
        # player hits each note on its scheduled time.
        left_sample  = at_rest(0.30, 0.50)
        right_sample = at_rest(0.70, 0.50)
        for note in bm.notes:
            if note.color == "red" and note.cut == "down":
                # Simulated hit: route LEFT saber to the note's xy at note.time
                # ONLY if note is the nearest downcut note.
                if abs(t_song - note.time) < 0.15:
                    left_sample = down_cut(note.x, note.y - 0.15, t_song, note.time)
                    break
            if note.color == "blue" and note.cut == "down":
                if abs(t_song - note.time) < 0.15:
                    right_sample = down_cut(note.x, note.y - 0.15, t_song, note.time)
                    break

        samples = {"left": left_sample, "right": right_sample}
        evs, snap = g.tick(wall, samples)

        for h in evs.hits:
            hits_log.append((t_song, h["note_id"], h["saber"], h["quality"]))
        for m in evs.misses:
            misses_log.append((t_song, m))
        for b in evs.bad_cuts:
            bad_cuts_log.append((t_song, b["note_id"], b["reason"]))

    print("=== game.tick() integration replay ===")
    print(f"ticks: {frames}  final song_time: {g.timeline.song_time():.2f}s")
    print(f"final score: {g.score}")
    print(f"  hits: {len(hits_log)}  misses: {len(misses_log)}  bad_cuts: {len(bad_cuts_log)}")
    if hits_log:
        print("  first 3 hits:")
        for h in hits_log[:3]:
            print(f"    t={h[0]:.2f}s note_id={h[1]} saber={h[2]} quality={h[3]:.2f}")

    assert g.score.hits > 0, "synthetic player should hit at least some notes"
    # Expected 4 red down-cut + 4 blue down-cut hits from the first section.
    assert g.score.hits >= 4, f"expected ≥4 hits, got {g.score.hits}"

    print("\nOK — game coordinator integration run pass.")
