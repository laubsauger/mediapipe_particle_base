"""
beatmap.py
==========

Note schema + beatmap file parser + test-map generator. Pure Python.

A beatmap is a list of scheduled Notes. Each Note has:
    time          : seconds from song start when the note should be HIT
    x, y          : game-world xy in 0..1 UV (MediaPipe-style)
    z_spawn       : z position at which this note spawns (typical: -10.0,
                    far down the approach tunnel away from the camera).
                    Notes travel from z_spawn to z_hit (0.0) over
                    `travel_time` seconds, approaching the player.
                    Negative by convention so TD's default camera (at
                    +Z looking -Z) renders the tunnel correctly.
    color         : "red" (left saber / left hand) or "blue" (right saber)
    cut_direction : "up", "down", "left", "right", "up_left", "up_right",
                    "down_left", "down_right", or "any" (no direction constraint)
    size          : edge length of the cube collider (typical 0.15 UV)

Beatmap file format (JSON):
    {
        "title": "test",
        "bpm": 120,
        "travel_time": 2.0,
        "z_spawn": 10.0,
        "notes": [
            {"time": 2.0, "x": 0.3, "y": 0.5, "color": "red", "cut": "down"},
            ...
        ]
    }

`travel_time` determines how long a note is visible before it should be
hit — i.e., spawn_time = time - travel_time. Notes are sorted by time
on load so spawning is O(1) lookahead.
"""

import json
import math


COLORS = ("red", "blue")
CUT_DIRECTIONS = (
    "up", "down", "left", "right",
    "up_left", "up_right", "down_left", "down_right",
    "any",
)


# Unit vectors for each cut direction in MediaPipe UV (y grows DOWNWARD,
# so "up" means -y). Used by hit detection to compare required cut vs
# actual saber motion.
CUT_VECTORS = {
    "up":         ( 0.0, -1.0,  0.0),
    "down":       ( 0.0,  1.0,  0.0),
    "left":       (-1.0,  0.0,  0.0),
    "right":      ( 1.0,  0.0,  0.0),
    "up_left":    (-0.707, -0.707, 0.0),
    "up_right":   ( 0.707, -0.707, 0.0),
    "down_left":  (-0.707,  0.707, 0.0),
    "down_right": ( 0.707,  0.707, 0.0),
    "any":        ( 0.0,  0.0,  0.0),  # no direction requirement
}


class Note:
    """A single scheduled note. Mutable: position updates each tick while
    the note is active; spawned/hit/missed lifecycle flags get set in place."""

    __slots__ = (
        "id",          # monotonically increasing; assigned by Beatmap
        "time",        # hit time in seconds
        "x", "y",      # world xy of the note (constant over lifetime)
        "z_spawn",     # where the note first appears
        "color",
        "cut",         # cut_direction string
        "size",
        # Derived / mutable:
        "spawn_time",  # time - travel_time
        "z",           # current z (updated each tick)
        "state",       # "scheduled" | "spawned" | "hit" | "missed"
        "hit_time",    # when the hit was registered (for cleanup)
    )

    def __init__(self, id, time, x, y, color, cut, z_spawn=-10.0,
                 size=0.15, spawn_time=None):
        if color not in COLORS:
            raise ValueError(f"color must be one of {COLORS}, got {color!r}")
        if cut not in CUT_DIRECTIONS:
            raise ValueError(f"cut must be one of {CUT_DIRECTIONS}, got {cut!r}")
        self.id = id
        self.time = float(time)
        self.x = float(x)
        self.y = float(y)
        self.z_spawn = float(z_spawn)
        self.color = color
        self.cut = cut
        self.size = float(size)
        self.spawn_time = float(spawn_time) if spawn_time is not None else None
        self.z = self.z_spawn
        self.state = "scheduled"
        self.hit_time = None

    def __repr__(self):
        return (f"Note(id={self.id}, t={self.time:.2f}, xy=({self.x:.2f},{self.y:.2f}), "
                f"{self.color} {self.cut}, state={self.state})")


class Beatmap:
    """A loaded beatmap — list of Notes sorted by hit time + metadata."""

    def __init__(self, notes, title="untitled", bpm=120.0,
                 travel_time=2.0, z_spawn=-10.0):
        self.title = title
        self.bpm = float(bpm)
        self.travel_time = float(travel_time)
        self.z_spawn = float(z_spawn)
        # Sort by hit time so spawning is a simple forward cursor.
        self.notes = sorted(notes, key=lambda n: n.time)
        # Assign ids in timeline order, and precompute spawn times.
        for i, n in enumerate(self.notes):
            n.id = i
            n.spawn_time = n.time - self.travel_time
            if n.spawn_time is not None and n.spawn_time < 0:
                # Early notes: spawn at t=0 but they'll appear late.
                n.spawn_time = 0.0

    @classmethod
    def from_dict(cls, d):
        travel_time = float(d.get("travel_time", 2.0))
        z_spawn = float(d.get("z_spawn", -10.0))
        notes = []
        for raw in d.get("notes", []):
            notes.append(Note(
                id=0,                      # will be reassigned in constructor
                time=raw["time"],
                x=raw["x"],
                y=raw["y"],
                color=raw["color"],
                cut=raw.get("cut", "any"),
                z_spawn=float(raw.get("z_spawn", z_spawn)),
                size=float(raw.get("size", 0.15)),
            ))
        return cls(
            notes=notes,
            title=d.get("title", "untitled"),
            bpm=float(d.get("bpm", 120.0)),
            travel_time=travel_time,
            z_spawn=z_spawn,
        )

    @classmethod
    def from_json_file(cls, path):
        with open(path, "r") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_json_string(cls, s):
        return cls.from_dict(json.loads(s))

    def duration(self):
        """End time of the last note (approximate song length)."""
        if not self.notes:
            return 0.0
        return self.notes[-1].time

    def __repr__(self):
        return (f"Beatmap(title={self.title!r}, bpm={self.bpm}, "
                f"{len(self.notes)} notes, duration≈{self.duration():.1f}s)")


# ---------------------------------------------------------------------------
# Synthesised test map — generates a small beatmap without a file.
# Useful for wiring up before you have a real map.
# ---------------------------------------------------------------------------

def make_test_beatmap():
    """A ~15-second canned beatmap for development."""
    notes = [
        # Warmup — alternating simple down-cuts.
        Note(0, time=2.0, x=0.30, y=0.50, color="red",  cut="down"),
        Note(0, time=3.0, x=0.70, y=0.50, color="blue", cut="down"),
        Note(0, time=4.0, x=0.30, y=0.50, color="red",  cut="down"),
        Note(0, time=5.0, x=0.70, y=0.50, color="blue", cut="down"),

        # Side cuts.
        Note(0, time=6.5, x=0.20, y=0.45, color="red",  cut="right"),
        Note(0, time=7.0, x=0.80, y=0.45, color="blue", cut="left"),

        # Diagonal.
        Note(0, time=8.5, x=0.35, y=0.35, color="red",  cut="down_right"),
        Note(0, time=9.0, x=0.65, y=0.35, color="blue", cut="down_left"),

        # Low / high.
        Note(0, time=10.5, x=0.30, y=0.70, color="red",  cut="up"),
        Note(0, time=11.5, x=0.70, y=0.25, color="blue", cut="down"),

        # Finale — quick sequence.
        Note(0, time=13.0, x=0.30, y=0.50, color="red",  cut="down"),
        Note(0, time=13.4, x=0.50, y=0.40, color="blue", cut="down"),
        Note(0, time=13.8, x=0.70, y=0.50, color="red",  cut="down_left"),
        Note(0, time=14.2, x=0.50, y=0.60, color="blue", cut="up_right"),
    ]
    return Beatmap(
        notes=notes,
        title="test map — development",
        bpm=120.0,
        travel_time=2.0,
        z_spawn=10.0,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bm = make_test_beatmap()
    print(bm)
    for n in bm.notes[:5]:
        print(f"  {n}")
    print(f"  ... ({len(bm.notes) - 5} more)")

    # Notes sorted by time.
    for a, b in zip(bm.notes, bm.notes[1:]):
        assert a.time <= b.time, "notes must be sorted by time"

    # Spawn times precomputed.
    for n in bm.notes:
        assert n.spawn_time is not None
        assert n.spawn_time <= n.time

    # Round-trip via JSON.
    data = {
        "title": "roundtrip",
        "bpm": 100,
        "travel_time": 1.5,
        "notes": [
            {"time": 1.0, "x": 0.5, "y": 0.5, "color": "red", "cut": "up"},
            {"time": 2.0, "x": 0.5, "y": 0.5, "color": "blue", "cut": "down"},
        ],
    }
    bm2 = Beatmap.from_json_string(json.dumps(data))
    assert bm2.title == "roundtrip"
    assert len(bm2.notes) == 2
    assert bm2.notes[0].cut == "up"
    assert abs(bm2.notes[0].spawn_time - (-0.5)) < 1e-9 or bm2.notes[0].spawn_time == 0.0

    # Bad inputs.
    try:
        Note(0, time=1.0, x=0.5, y=0.5, color="green", cut="up")
        assert False, "should have rejected bad color"
    except ValueError:
        pass
    try:
        Note(0, time=1.0, x=0.5, y=0.5, color="red", cut="sideways")
        assert False, "should have rejected bad cut"
    except ValueError:
        pass

    # CUT_VECTORS sanity.
    for d, v in CUT_VECTORS.items():
        if d == "any":
            continue
        mag = math.sqrt(sum(c * c for c in v))
        assert abs(mag - 1.0) < 1e-3, f"{d} should be unit: {v}"

    print("\nOK — beatmap load, sort, spawn-time compute, JSON round-trip,")
    print("     bad-input rejection, and cut vector magnitudes all pass.")
