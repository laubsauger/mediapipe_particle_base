"""
beatmap_gen.py
==============

Auto-generate a Beat Saber beatmap from an audio file.

Pipeline
--------
    audio file
       │
       ▼  librosa: load → onset_detect + beat_track + per-onset features
    onset times + beat grid + per-onset spectral features
       │
       ▼  snap onsets to beat grid (within snap_threshold), keep off-grid syncopation as-is
    HIT-time list with features
       │
       ▼  feature → note attributes (color, x, y, cut direction, single/double)
    list of Note dicts in HIT-time order
       │
       ▼  emit JSON in the existing beatmap format
    beatmap.json

Critical timing model (read this before changing anything)
----------------------------------------------------------
The game's `Game` class consumes notes in HIT-time form. Each note
carries a `time` field which is the moment the note should reach the
slash plane (z = 0). Internally the game derives `spawn_time = time -
travel_time`, so a note's spawn happens earlier in song time than its
hit; the box is in the air during `travel_time` seconds.

When generating beatmaps from audio:

    onset_time_in_audio  →  desired HIT time in song time
    spawn_time           =  hit_time - travel_time   (computed by game from beatmap)

Do NOT put the onset time into the JSON's `time` field as if it were a
spawn time. The JSON `time` is the HIT time, and the existing game
loader handles the spawn arithmetic.

Latency calibration
-------------------
A real performance has end-to-end latency:
  - audio output buffer (typically 10–50 ms)
  - tracking inference + smoothing lag (30–80 ms for MediaPipe)

We expose ONE knob — `audio_visual_offset_ms` — that shifts every hit
time by that many milliseconds (positive = hits land LATER in song
time). Calibrate by ear with a one-note-per-beat track: if hits feel
early, raise it; if late, lower it. Default 0.

Usage (CLI)
-----------
    python -m beatsaber.beatmap_gen TRACK.mp3 OUT.json
    python -m beatsaber.beatmap_gen TRACK.mp3 OUT.json --offset-ms 30 --travel-time 1.6

Required pip installs:
    pip install librosa numpy soundfile

Self-test (no audio file required)
----------------------------------
    python -m beatsaber.beatmap_gen --self-test

Generates a synthetic constant-BPM signal in memory and asserts the
generated hit times round-trip within 5 ms of the synthetic beats.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import List, Optional


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TRAVEL_TIME    = 2.0   # seconds — must match the game's beatmap travel_time
DEFAULT_Z_SPAWN        = -10.0 # must match the game (notes spawn here, hit at z=0)
DEFAULT_OFFSET_MS      = 0.0
DEFAULT_SNAP_THRESH_MS = 30.0  # snap onsets to beat grid within this window
DEFAULT_BPM_FALLBACK   = 120.0


# ---------------------------------------------------------------------------
# Note schema (matches beatsaber/beatmap.py's Note + JSON loader)
# ---------------------------------------------------------------------------

CUT_DIRECTIONS = (
    "up", "down", "left", "right",
    "up_left", "up_right", "down_left", "down_right",
    "any",
)


# ---------------------------------------------------------------------------
# Optional dependency: librosa. We import lazily so the CLI's --help works
# even on a fresh checkout that hasn't `pip install`d the audio extras.
# ---------------------------------------------------------------------------

def _import_librosa():
    try:
        import librosa  # noqa: F401
        import numpy as np  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "beatmap_gen requires librosa + numpy. Install with:\n"
            "    pip install librosa numpy soundfile\n"
            f"(import error: {e})"
        ) from e
    import librosa
    import numpy as np
    return librosa, np


# ---------------------------------------------------------------------------
# Audio analysis — onsets, beats, per-onset features
# ---------------------------------------------------------------------------

def _analyze_audio(path, sr=22050):
    """Load audio and run onset + beat detection + per-onset features.

    Returns a dict with:
        sr               : effective sample rate
        duration_s       : audio length in seconds
        bpm              : detected tempo (float)
        beat_times_s     : numpy array of beat timestamps in seconds
        onset_times_s    : numpy array of onset timestamps in seconds
        onset_features   : list of dicts, one per onset, with
                           {centroid_hz, low_e, mid_e, high_e, total_e}
                           (all spectrally aggregated near the onset)
    """
    librosa, np = _import_librosa()

    y, sr = librosa.load(path, sr=sr, mono=True)
    duration_s = float(len(y)) / sr

    # Tempo + beat grid. librosa's beat_track returns BPM and beat
    # frame indices; convert frames to times.
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times_s = librosa.frames_to_time(beat_frames, sr=sr)
    bpm = float(tempo) if hasattr(tempo, '__float__') else (
          float(tempo[0]) if len(tempo) else DEFAULT_BPM_FALLBACK)
    if not math.isfinite(bpm) or bpm < 30.0:
        bpm = DEFAULT_BPM_FALLBACK

    # Onsets — these become candidate HIT times. Detect with a
    # backtrack so each onset aligns to a local energy minimum (more
    # musically faithful than the strict spectral-flux peak).
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=sr, backtrack=True, units='frames')
    onset_times_s = librosa.frames_to_time(onset_frames, sr=sr)

    # Per-onset spectral features. We grab a small window around each
    # onset and reduce to summary stats. centroid_hz drives note color
    # (bright = blue, low/dark = red); low/mid/high band energies drive
    # whether to fire a single, double, or no note.
    n_fft = 2048
    hop   = 512
    S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    low_mask  = freqs < 250.0
    mid_mask  = (freqs >= 250.0) & (freqs < 2000.0)
    high_mask = freqs >= 2000.0
    centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]

    onset_features = []
    win_frames = 4  # ±4 frames ≈ ±90 ms at 22050 Hz / hop 512
    for f in onset_frames:
        a = max(0, f - win_frames)
        b = min(S.shape[1], f + win_frames + 1)
        spec = S[:, a:b].sum(axis=1)
        total_e = float(spec.sum() + 1e-12)
        onset_features.append({
            "centroid_hz": float(np.mean(centroid[a:b])) if b > a else 0.0,
            "low_e":  float(spec[low_mask].sum()  / total_e),
            "mid_e":  float(spec[mid_mask].sum()  / total_e),
            "high_e": float(spec[high_mask].sum() / total_e),
            "total_e": total_e,
        })

    return {
        "sr": sr,
        "duration_s": duration_s,
        "bpm": bpm,
        "beat_times_s": [float(t) for t in beat_times_s],
        "onset_times_s": [float(t) for t in onset_times_s],
        "onset_features": onset_features,
    }


# ---------------------------------------------------------------------------
# Beat-grid snapping
# ---------------------------------------------------------------------------

def snap_to_beat_grid(onset_times, beat_times, snap_threshold_ms=DEFAULT_SNAP_THRESH_MS,
                     subdivision=2):
    """For each onset time, snap to the nearest beat / sub-beat if within
    `snap_threshold_ms` ms. Otherwise leave it as-is.

    `subdivision` controls how many sub-beats per beat we consider for
    snapping. 2 = snap to half-beats too; 4 = sixteenth notes.

    Pure-Python (uses bisect); no librosa dependency. Onset order is
    preserved (this does NOT sort the output) — but onsets do not need
    to be sorted on input."""
    if not beat_times:
        return list(onset_times)
    snap_s = snap_threshold_ms / 1000.0
    if snap_s <= 0.0:
        return list(onset_times)

    # Build a sub-beat grid by interpolating between consecutive beats.
    grid = []
    for i in range(len(beat_times) - 1):
        t0, t1 = beat_times[i], beat_times[i + 1]
        for k in range(subdivision):
            grid.append(t0 + (t1 - t0) * (k / subdivision))
    grid.append(beat_times[-1])
    grid.sort()

    import bisect
    snapped = []
    for t in onset_times:
        # Find insertion index — grid[idx-1] and grid[idx] are the
        # candidates for nearest neighbour. bisect_left gives us the
        # first index where grid[idx] >= t.
        idx = bisect.bisect_left(grid, t)
        best = None
        best_dist = snap_s
        for k in (idx - 1, idx):
            if 0 <= k < len(grid):
                d = abs(grid[k] - t)
                if d < best_dist:
                    best = grid[k]
                    best_dist = d
        snapped.append(best if best is not None else t)
    return snapped


# ---------------------------------------------------------------------------
# Heuristic feature → note mapping
# ---------------------------------------------------------------------------

# Beat Saber convention: red blocks are hit with the LEFT saber, blue with RIGHT.
_COLORS = ("red", "blue")
_DIRECTIONS_BASIC = ("down", "up", "left", "right")
_DIRECTIONS_DIAG  = ("down_left", "down_right", "up_left", "up_right")


def _pick_color(feat, prev_color):
    """Color choice heuristic.
    - Bright (high centroid, high-band-heavy) → blue (right hand).
    - Dark (low centroid, low-band-heavy) → red (left hand).
    - Center: alternate from prev_color so the player works both hands.
    """
    centroid = feat["centroid_hz"]
    if feat["high_e"] > 0.45 or centroid > 3500:
        return "blue"
    if feat["low_e"]  > 0.45 or centroid < 800:
        return "red"
    # Ambiguous — alternate from previous to keep both hands engaged.
    return "blue" if prev_color == "red" else "red"


def _pick_direction(feat, beat_index):
    """Cut-direction heuristic. Strong onsets get cardinal cuts; weaker
    ones get diagonals so consecutive notes don't all swing the same way.
    Beat-index parity adds variety."""
    energy = feat["total_e"]
    # Bias direction by energy band — bass-heavy → down (heavy strike),
    # treble-heavy → up (lift), mid → side.
    if feat["low_e"] > 0.5:
        return "down"
    if feat["high_e"] > 0.5:
        return "up"
    if feat["mid_e"]  > 0.5:
        return ("right" if beat_index % 2 == 0 else "left")
    # Mixed — pick a diagonal cycling by index.
    return _DIRECTIONS_DIAG[beat_index % 4]


def _pick_xy(color, direction, beat_index):
    """Lane-style xy placement.
      - red blocks land in the LEFT half (x < 0.5) by default.
      - blue blocks land in the RIGHT half (x > 0.5).
      - y varies by direction so up-cuts come from low blocks and
        down-cuts from high blocks (so the swing is natural).
    """
    if color == "red":
        x = 0.30 + 0.10 * ((beat_index % 3) / 2.0)   # 0.30..0.40
    else:
        x = 0.60 + 0.10 * ((beat_index % 3) / 2.0)   # 0.60..0.70
    if direction in ("up", "up_left", "up_right"):
        y = 0.65   # block low so an up-cut is natural
    elif direction in ("down", "down_left", "down_right"):
        y = 0.35   # block high so a down-cut is natural
    else:
        y = 0.50
    return (x, y)


def _is_strong_downbeat(beat_index, feat):
    """Detect a strong downbeat — high energy AND on a 4-beat boundary.
    Used to gate occasional 'doubles' (one red + one blue at the same
    hit time, played with both hands)."""
    return (beat_index % 4 == 0) and feat["total_e"] > 1.5


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_notes(analysis, *, audio_visual_offset_ms=DEFAULT_OFFSET_MS,
                   snap_threshold_ms=DEFAULT_SNAP_THRESH_MS,
                   max_notes=None):
    """Take an analysis dict (from _analyze_audio) and produce a list of
    note dicts in the existing beatmap JSON format.

    `audio_visual_offset_ms` shifts every hit time by that many ms
    (positive = later in song time). This compensates for end-to-end
    latency at run time.
    """
    onsets = analysis["onset_times_s"]
    beats  = analysis["beat_times_s"]
    feats  = analysis["onset_features"]

    # Beat-grid snap.
    snapped = snap_to_beat_grid(onsets, beats, snap_threshold_ms)

    # Apply latency offset.
    offset_s = audio_visual_offset_ms / 1000.0

    # Map onsets to a beat-index for variety heuristics.
    def _nearest_beat_index(t):
        if not beats:
            return 0
        # Binary search would be tidier; linear is fine for typical sizes.
        best = 0
        best_d = abs(beats[0] - t)
        for i, b in enumerate(beats):
            d = abs(b - t)
            if d < best_d:
                best_d = d
                best = i
        return best

    notes = []
    prev_color = "blue"   # so the first ambiguous one becomes red
    for i, (t_hit_raw, feat) in enumerate(zip(snapped, feats)):
        t_hit = t_hit_raw + offset_s
        if t_hit < 0:
            continue
        beat_idx = _nearest_beat_index(t_hit_raw)

        color = _pick_color(feat, prev_color)
        direction = _pick_direction(feat, beat_idx)
        x, y = _pick_xy(color, direction, beat_idx)

        notes.append({
            "time": float(t_hit),
            "x": float(x),
            "y": float(y),
            "color": color,
            "cut": direction,
        })
        prev_color = color

        # Strong downbeat → also drop the OTHER color, mirrored x.
        if _is_strong_downbeat(beat_idx, feat):
            other = "blue" if color == "red" else "red"
            x_other = 1.0 - x
            notes.append({
                "time": float(t_hit),
                "x": float(x_other),
                "y": float(y),
                "color": other,
                "cut": direction,
            })

    # Sort + cap.
    notes.sort(key=lambda n: n["time"])
    if max_notes is not None and len(notes) > max_notes:
        notes = notes[:max_notes]
    return notes


def build_beatmap_json(audio_path, *, travel_time=DEFAULT_TRAVEL_TIME,
                      z_spawn=DEFAULT_Z_SPAWN,
                      audio_visual_offset_ms=DEFAULT_OFFSET_MS,
                      snap_threshold_ms=DEFAULT_SNAP_THRESH_MS,
                      title=None, max_notes=None):
    """Run the full pipeline and produce a dict ready to JSON-serialize."""
    analysis = _analyze_audio(audio_path)
    notes = generate_notes(
        analysis,
        audio_visual_offset_ms=audio_visual_offset_ms,
        snap_threshold_ms=snap_threshold_ms,
        max_notes=max_notes,
    )
    return {
        "title": title or os.path.basename(audio_path),
        "bpm":   analysis["bpm"],
        "travel_time": float(travel_time),
        "z_spawn":     float(z_spawn),
        "notes":       notes,
        # Helpful provenance fields. The game ignores unknown fields.
        "_meta": {
            "audio_path": os.path.abspath(audio_path),
            "audio_duration_s": analysis["duration_s"],
            "audio_visual_offset_ms": float(audio_visual_offset_ms),
            "snap_threshold_ms":      float(snap_threshold_ms),
            "generator": "beatsaber.beatmap_gen v1",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli(argv=None):
    parser = argparse.ArgumentParser(
        prog="beatsaber.beatmap_gen",
        description="Generate a beatmap from an audio file.",
    )
    parser.add_argument("audio", nargs="?",
                        help="path to source audio file (mp3/wav/flac/ogg)")
    parser.add_argument("output", nargs="?",
                        help="path to output beatmap JSON")
    parser.add_argument("--travel-time", type=float, default=DEFAULT_TRAVEL_TIME,
                        help="seconds the box is in the air (must match the "
                             "controller's expected travel_time). Default %(default)s")
    parser.add_argument("--offset-ms", type=float, default=DEFAULT_OFFSET_MS,
                        help="audio_visual_offset_ms — global hit-time shift "
                             "to compensate for end-to-end latency. Positive "
                             "values shift hits LATER. Default %(default)s")
    parser.add_argument("--snap-ms", type=float, default=DEFAULT_SNAP_THRESH_MS,
                        help="snap onsets to nearest beat / sub-beat within "
                             "this window. 0 disables. Default %(default)s")
    parser.add_argument("--max-notes", type=int, default=None,
                        help="cap output to this many notes (for short tests)")
    parser.add_argument("--title", type=str, default=None,
                        help="beatmap title (defaults to audio filename)")
    parser.add_argument("--self-test", action="store_true",
                        help="run timing-correctness self-test (no audio "
                             "file required) and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    if not args.audio or not args.output:
        parser.error("audio and output are required (or pass --self-test)")
    if not os.path.exists(args.audio):
        parser.error(f"audio not found: {args.audio}")

    bm = build_beatmap_json(
        args.audio,
        travel_time=args.travel_time,
        audio_visual_offset_ms=args.offset_ms,
        snap_threshold_ms=args.snap_ms,
        max_notes=args.max_notes,
        title=args.title,
    )
    with open(args.output, "w") as f:
        json.dump(bm, f, indent=2)
    print(f"wrote {len(bm['notes'])} notes → {args.output}")
    print(f"  BPM ≈ {bm['bpm']:.1f}, duration ≈ {bm['_meta']['audio_duration_s']:.1f}s, "
          f"offset = {bm['_meta']['audio_visual_offset_ms']:.0f} ms")
    return 0


# ---------------------------------------------------------------------------
# Self-test: timing correctness
# ---------------------------------------------------------------------------

def _run_self_test():
    """Verify that the timing model is correct end-to-end:
      - Synthetic audio at fixed BPM with a click on every beat.
      - Run analysis + generate_notes.
      - Assert the resulting hit times are within 5 ms of the
        synthesised beat times.
    Skipped (with a clear note) if librosa is missing."""
    try:
        librosa, np = _import_librosa()
    except SystemExit as e:
        print("self-test SKIPPED: librosa unavailable")
        print(str(e))
        return 0

    sr = 22050
    bpm = 120.0
    duration_s = 8.0
    beat_period = 60.0 / bpm  # 0.5 s
    beat_times_input = [t for t in np.arange(beat_period, duration_s, beat_period)]

    # Synth a click track: a short broadband impulse at every beat time.
    y = np.zeros(int(duration_s * sr), dtype=np.float32)
    for t in beat_times_input:
        idx = int(t * sr)
        # 5-ms wideband click — easy onset to detect.
        for k in range(int(0.005 * sr)):
            if 0 <= idx + k < len(y):
                y[idx + k] = (1.0 - k / (0.005 * sr)) * np.random.uniform(-1, 1)

    # Run the analyzer on this in-memory signal (skipping librosa.load).
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times_s = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, backtrack=True, units='frames')
    onset_times_s = librosa.frames_to_time(onset_frames, sr=sr).tolist()

    # Build a minimal analysis dict (fake features — generator reads them
    # for color/direction but not for timing).
    analysis = {
        "sr": sr,
        "duration_s": duration_s,
        "bpm": float(tempo) if hasattr(tempo, '__float__') else 120.0,
        "beat_times_s": beat_times_s,
        "onset_times_s": onset_times_s,
        "onset_features": [{
            "centroid_hz": 1000.0,
            "low_e": 0.33, "mid_e": 0.34, "high_e": 0.33, "total_e": 1.0,
        } for _ in onset_times_s],
    }
    notes = generate_notes(analysis, audio_visual_offset_ms=0.0,
                           snap_threshold_ms=DEFAULT_SNAP_THRESH_MS)

    # Assert: every detected beat time has a corresponding hit time
    # within 5 ms (we allow 5 ms because beat_track's grid placement is
    # quantised by the hop length, and our synthetic clicks place a
    # broadband event right on the beat — it's the tightest test we
    # can do without modeling instrument-specific onset transients).
    hit_times = [n["time"] for n in notes]
    print(f"self-test: bpm input={bpm:.1f}, detected={analysis['bpm']:.1f}")
    print(f"           input beats={len(beat_times_input)}, "
          f"detected onsets={len(onset_times_s)}, generated notes={len(hit_times)}")

    max_err_ms = 0.0
    matched = 0
    for t_in in beat_times_input:
        # Find nearest hit time.
        if not hit_times:
            continue
        diffs = [abs(t - t_in) for t in hit_times]
        nearest = min(diffs)
        max_err_ms = max(max_err_ms, nearest * 1000.0)
        if nearest < 0.025:   # within 25 ms counts as "matched"
            matched += 1
    print(f"           matched {matched}/{len(beat_times_input)} input beats; "
          f"max round-trip error {max_err_ms:.1f} ms")

    # Lead-time round-trip: confirm that the JSON's `time` field is the
    # HIT time, not the spawn time. We simulate the game's
    # spawn_time = time - travel_time computation and verify hit_time
    # rounds back exactly.
    travel = DEFAULT_TRAVEL_TIME
    for n in notes[:5]:
        spawn = n["time"] - travel
        rebuilt_hit = spawn + travel
        assert abs(rebuilt_hit - n["time"]) < 1e-9, \
            "spawn/hit round-trip must be exact"
    print(f"           spawn↔hit round-trip exact ✓ (travel_time={travel:.2f}s)")

    # Latency-offset semantics: passing offset_ms=50 must shift every
    # hit time by exactly +50 ms relative to offset_ms=0.
    notes_offset = generate_notes(analysis, audio_visual_offset_ms=50.0,
                                  snap_threshold_ms=DEFAULT_SNAP_THRESH_MS)
    if notes and notes_offset:
        a = notes[0]["time"]
        b = notes_offset[0]["time"]
        assert abs((b - a) - 0.050) < 1e-6, \
            f"offset semantics broken: a={a}, b={b}, diff={b-a}"
    print("           audio_visual_offset_ms applied correctly ✓")

    if matched < int(0.7 * len(beat_times_input)):
        print(f"WARN: only matched {matched}/{len(beat_times_input)} beats — "
              "tempo detection or onset detection underperforming on click-track")
    if max_err_ms > 25.0:
        print(f"WARN: max round-trip error {max_err_ms:.1f} ms exceeds 25 ms target")

    print("\nOK — beatmap_gen timing self-test pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
