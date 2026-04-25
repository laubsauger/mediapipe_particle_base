"""
score.py
========

Scoring / combo / multiplier state. Pure Python.

Model (simplified Beat Saber):
- Base score for a cut = 100 * quality (0..100 per note).
- Multiplier starts at 1x, ramps to 2x / 4x / 8x as combo grows.
- Combo breaks on miss or bad cut.
- Running totals: score, combo, max_combo, hits, misses, bad_cuts.

Multiplier tiers (configurable):
    0..4  combo → 1x
    5..13 combo → 2x
    14..29 combo → 4x
    30+   combo → 8x
"""

# Multiplier tiers: list of (combo_threshold, multiplier) in ascending
# combo order. First threshold must be 0. Multiplier = last tier
# whose threshold <= combo.
DEFAULT_TIERS = [
    (0,   1),
    (5,   2),
    (14,  4),
    (30,  8),
]


class Score:
    """Game score state. Methods mutate in place."""

    def __init__(self, tiers=None, per_note_max=100):
        self.tiers = tiers if tiers is not None else list(DEFAULT_TIERS)
        self.per_note_max = per_note_max
        self.reset()

    # -- state --------------------------------------------------------------

    def reset(self):
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.hits = 0
        self.misses = 0
        self.bad_cuts = 0
        self.total_quality = 0.0      # sum of all hit qualities, for accuracy %

    @property
    def multiplier(self):
        mult = 1
        for threshold, m in self.tiers:
            if self.combo >= threshold:
                mult = m
        return mult

    @property
    def accuracy(self):
        """0..1 — average quality over all attempted notes (hits + misses +
        bad cuts). 0 if nothing attempted yet."""
        attempted = self.hits + self.misses + self.bad_cuts
        if attempted == 0:
            return 0.0
        return self.total_quality / attempted

    # -- events -------------------------------------------------------------

    def register_hit(self, quality):
        """Good cut. `quality` is 0..1 from hit_detection.
        The hit that crosses into a higher tier gets scored at the NEW
        multiplier (increment combo first, then read multiplier for scoring)."""
        self.combo += 1
        if self.combo > self.max_combo:
            self.max_combo = self.combo
        points = int(self.per_note_max * quality) * self.multiplier
        self.score += points
        self.hits += 1
        self.total_quality += quality
        return points

    def register_bad_cut(self):
        """Wrong colour or wrong direction. Breaks combo, no points."""
        self.combo = 0
        self.bad_cuts += 1
        # Bad cut contributes 0 to total_quality (brings accuracy down).

    def register_miss(self):
        """Note expired without a hit. Breaks combo, no points."""
        self.combo = 0
        self.misses += 1

    # -- display ------------------------------------------------------------

    def summary(self):
        return {
            "score":     self.score,
            "combo":     self.combo,
            "max_combo": self.max_combo,
            "multiplier": self.multiplier,
            "hits":      self.hits,
            "misses":    self.misses,
            "bad_cuts":  self.bad_cuts,
            "accuracy":  self.accuracy,
        }

    def __repr__(self):
        s = self.summary()
        return (f"Score({s['score']}, combo={s['combo']}×{s['multiplier']}, "
                f"{s['hits']}h/{s['misses']}m/{s['bad_cuts']}b, "
                f"acc={s['accuracy']*100:.1f}%)")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sc = Score()
    assert sc.score == 0
    assert sc.combo == 0
    assert sc.multiplier == 1

    # Four perfect hits: 100 each at 1x.
    for _ in range(4):
        sc.register_hit(1.0)
    assert sc.score == 400
    assert sc.combo == 4
    assert sc.multiplier == 1

    # Fifth hit crosses into 2x tier: 100 * 2 = 200. Total 600.
    sc.register_hit(1.0)
    assert sc.score == 600
    assert sc.combo == 5
    assert sc.multiplier == 2

    # Eight more perfect hits — still 2x until combo 14.
    for _ in range(8):
        sc.register_hit(1.0)
    assert sc.combo == 13
    assert sc.multiplier == 2

    # 14th hit crosses into 4x.
    sc.register_hit(1.0)
    assert sc.combo == 14
    assert sc.multiplier == 4
    # Score before the 14th: 600 + 200*8 = 2200. After: 2200 + 400 = 2600.
    assert sc.score == 2600

    # A miss breaks combo.
    sc.register_miss()
    assert sc.combo == 0
    assert sc.multiplier == 1
    assert sc.misses == 1
    assert sc.score == 2600  # no points lost, just combo broken

    # Bad cut also breaks combo (already 0, no change) and doesn't score.
    sc.register_bad_cut()
    assert sc.combo == 0
    assert sc.bad_cuts == 1

    # Lower-quality hit scores less.
    sc0 = Score()
    sc1 = Score()
    sc0.register_hit(1.0)
    sc1.register_hit(0.5)
    assert sc0.score > sc1.score

    # Accuracy.
    sc_acc = Score()
    sc_acc.register_hit(0.9)
    sc_acc.register_hit(0.8)
    sc_acc.register_miss()
    assert abs(sc_acc.accuracy - (0.9 + 0.8 + 0.0) / 3) < 1e-9

    print(sc)
    print(f"  accuracy: {sc.accuracy*100:.1f}%  max_combo: {sc.max_combo}")
    print("\nOK — scoring, tiers, combo-break, accuracy all pass.")
