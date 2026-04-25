"""
timeline.py
===========

Abstract song-time accessor. The whole game reads current time via
`timeline.song_time()` regardless of whether the underlying source is
a test clock or an audio track. That lets us swap backends without
touching spawn/hit/scoring logic.

Usage in TD
-----------
On each cook, the `game_tick` Execute DAT calls:

    timeline.set_wall_clock(absTime.seconds)   # or audio_top.time

then inside the game loop, any code that needs current time calls:

    t = timeline.song_time()

The Timeline owns start/pause/reset state so callers don't have to.
"""


class Timeline:
    """
    One-way clock.
    - start() latches the current wall time as t0.
    - song_time() returns (wall - t0) while playing, paused_at while paused.
    - pause()/resume() freeze/unfreeze without resetting.
    - reset() clears state; next song_time() returns 0 until start().
    """

    def __init__(self):
        self._wall = 0.0         # externally pushed wall clock
        self._t0 = None          # wall time at which playback started
        self._paused_at = None   # song time captured on pause()
        self._offset = 0.0       # manual offset (latency calibration)

    # -- wall-clock injection -------------------------------------------------

    def set_wall_clock(self, wall_seconds):
        """Called once per cook from TD with absTime.seconds (or audio.time)."""
        self._wall = float(wall_seconds)

    # -- lifecycle ------------------------------------------------------------

    def start(self):
        """Start or restart playback from song_time = 0 at the current wall."""
        self._t0 = self._wall
        self._paused_at = None

    def pause(self):
        if self._paused_at is None and self._t0 is not None:
            self._paused_at = self._wall - self._t0

    def resume(self):
        if self._paused_at is not None and self._t0 is not None:
            # Shift t0 forward by the pause duration so song_time resumes
            # cleanly from paused_at.
            elapsed_paused = self._wall - (self._t0 + self._paused_at)
            self._t0 += elapsed_paused
            self._paused_at = None

    def reset(self):
        self._t0 = None
        self._paused_at = None
        self._offset = 0.0

    def is_playing(self):
        return self._t0 is not None and self._paused_at is None

    def is_paused(self):
        return self._paused_at is not None

    # -- the one query everyone else uses -------------------------------------

    def song_time(self):
        """
        Current song time in seconds.
        - Before start(): 0.0
        - While playing: wall - t0 + offset
        - While paused: paused_at + offset
        """
        if self._t0 is None:
            return 0.0
        if self._paused_at is not None:
            return self._paused_at + self._offset
        return (self._wall - self._t0) + self._offset

    # -- latency calibration --------------------------------------------------

    def set_offset(self, seconds):
        """Shift the reported song time by this many seconds. Positive =
        song is "ahead" of wall time (useful if audio has output latency)."""
        self._offset = float(seconds)


# ---------------------------------------------------------------------------
# Default singleton — the Script ops in TD grab this so every op sees the
# same clock. Unit tests can build their own Timeline() instead.
# ---------------------------------------------------------------------------
TIMELINE = Timeline()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tl = Timeline()

    # Before start: song_time = 0 regardless of wall.
    tl.set_wall_clock(100.0)
    assert tl.song_time() == 0.0
    assert not tl.is_playing()

    # Start at wall=100. song_time advances with wall.
    tl.start()
    tl.set_wall_clock(100.5)
    assert abs(tl.song_time() - 0.5) < 1e-9
    tl.set_wall_clock(103.0)
    assert abs(tl.song_time() - 3.0) < 1e-9
    assert tl.is_playing()

    # Pause at wall=103 → paused_at should be 3.0.
    tl.pause()
    assert tl.is_paused()
    assert abs(tl.song_time() - 3.0) < 1e-9

    # Advance wall while paused — song_time stays frozen.
    tl.set_wall_clock(110.0)
    assert abs(tl.song_time() - 3.0) < 1e-9

    # Resume — song_time resumes from 3.0.
    tl.resume()
    tl.set_wall_clock(111.0)
    assert abs(tl.song_time() - 4.0) < 1e-9

    # Reset.
    tl.reset()
    assert tl.song_time() == 0.0
    assert not tl.is_playing()

    # Latency offset.
    tl.start()
    tl.set_wall_clock(112.0)
    assert abs(tl.song_time() - 1.0) < 1e-9
    tl.set_offset(0.1)
    assert abs(tl.song_time() - 1.1) < 1e-9

    print("OK — timeline start, pause, resume, reset, offset all pass.")
