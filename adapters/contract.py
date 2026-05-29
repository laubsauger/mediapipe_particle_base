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
    print("OK — contract: 33 landmarks, 132 channels, mirror + blank helpers.")
