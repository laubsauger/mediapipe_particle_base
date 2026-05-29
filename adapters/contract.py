"""
adapters/contract.py
====================

The single source of truth for the canonical pose-channel schema. ALL adapters
emit this; ALL consumers (velocity_logic, body_logic, beatsaber) read this. The
list/indices match MediaPipe Pose so downstream `body_logic.JOINTS` /
`Landmarks` references stay valid.

Pure — no TD imports; importable from anywhere. See `docs/sensor_contract.md`
for the full contract (units, axes, normalisation, mirror convention).
"""

# (mp_index, canonical_name) — IF YOU CHANGE THIS, update docs/sensor_contract.md
# and body_logic.JOINTS' mp_index references.
LANDMARKS = [
    (0,  'nose'),
    (1,  'left_eye_inner'),
    (2,  'left_eye'),
    (3,  'left_eye_outer'),
    (4,  'right_eye_inner'),
    (5,  'right_eye'),
    (6,  'right_eye_outer'),
    (7,  'left_ear'),
    (8,  'right_ear'),
    (9,  'mouth_left'),
    (10, 'mouth_right'),
    (11, 'left_shoulder'),
    (12, 'right_shoulder'),
    (13, 'left_elbow'),
    (14, 'right_elbow'),
    (15, 'left_wrist'),
    (16, 'right_wrist'),
    (17, 'left_pinky'),
    (18, 'right_pinky'),
    (19, 'left_index'),
    (20, 'right_index'),
    (21, 'left_thumb'),
    (22, 'right_thumb'),
    (23, 'left_hip'),
    (24, 'right_hip'),
    (25, 'left_knee'),
    (26, 'right_knee'),
    (27, 'left_ankle'),
    (28, 'right_ankle'),
    (29, 'left_heel'),
    (30, 'right_heel'),
    (31, 'left_foot_index'),
    (32, 'right_foot_index'),
]
NAMES = [n for _, n in LANDMARKS]
INDEX_OF = {n: i for i, n in LANDMARKS}
NAME_OF  = {i: n for i, n in LANDMARKS}
NLM = len(LANDMARKS)

# Upper bound on simultaneously tracked people across the supported sensors.
# Kinect/Orbbec advertise up to 4–6; cap at 4 so MAX_PERSONS×132 channels stays
# manageable (528 at full saturation). See docs/sensor_contract.md.
MAX_PERSONS = 4


def channel_names():
    """Full canonical channel list (132 channels for 33 landmarks).
       4 channels per landmark: x, y, z, visibility<idx>."""
    out = []
    for i, n in LANDMARKS:
        out.extend([n + ':x', n + ':y', n + ':z', 'visibility%d' % i])
    return out


def blank_sample(name):
    """A 'this landmark is absent' value tuple (x, y, z, visibility).
       Adapters MUST emit this for joints their sensor doesn't track —
       downstream visibility-gating then cleanly ignores them."""
    return (0.0, 0.0, 0.0, 0.0)


def mirror_x(x):
    """Flip horizontal for non-selfie-cammed sources (the canonical convention
       is mirrored: performer's right hand = screen-right)."""
    return 1.0 - x


def person_prefix(person_id):
    """`'p<N>:'` — channel prefix for person `person_id`. Adapters emit each
    person's block prefixed; downstream consumers iterate over persons."""
    return 'p%d:' % int(person_id)


def channel_names_for_person(person_id):
    """The 132 channels for one person, prefixed with `p<N>:`."""
    return [person_prefix(person_id) + ch for ch in channel_names()]


def channel_names_multi(person_count=MAX_PERSONS, legacy_aliases=True):
    """Full multi-person channel list. If `legacy_aliases=True`, person 0's
    channels ALSO appear without prefix (so existing single-person code that
    reads `nose:x` still works — it sees person 0). Drop the aliases later
    once everything migrates."""
    out = []
    if legacy_aliases and person_count > 0:
        out.extend(channel_names())   # p0 aliased to legacy names
    for p in range(person_count):
        out.extend(channel_names_for_person(p))
    return out


if __name__ == '__main__':
    assert NLM == 33
    assert NAMES[0] == 'nose' and NAMES[32] == 'right_foot_index'
    assert INDEX_OF['left_wrist'] == 15 and INDEX_OF['right_wrist'] == 16
    chs = channel_names()
    assert len(chs) == 132, len(chs)
    assert chs[:4] == ['nose:x', 'nose:y', 'nose:z', 'visibility0']
    assert chs[-1] == 'visibility32'
    assert mirror_x(0.2) == 0.8
    assert blank_sample('left_wrist') == (0.0, 0.0, 0.0, 0.0)
    # multi-person
    assert person_prefix(0) == 'p0:' and person_prefix(3) == 'p3:'
    p1 = channel_names_for_person(1)
    assert len(p1) == 132 and p1[0] == 'p1:nose:x' and p1[-1] == 'p1:visibility32'
    multi = channel_names_multi(person_count=2, legacy_aliases=True)
    # 2 persons + p0 legacy aliases = 132 + 2*132 = 396 channels
    assert len(multi) == 132 + 2 * 132, len(multi)
    assert 'nose:x' in multi and 'p0:nose:x' in multi and 'p1:nose:x' in multi
    multi_no_alias = channel_names_multi(person_count=2, legacy_aliases=False)
    assert len(multi_no_alias) == 2 * 132
    assert 'nose:x' not in multi_no_alias and 'p0:nose:x' in multi_no_alias
    print("OK — contract: 33 landmarks; multi-person prefix `p<N>:` "
          "+ legacy aliases for back-compat (MAX_PERSONS=%d)." % MAX_PERSONS)
