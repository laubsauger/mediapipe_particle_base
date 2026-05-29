"""Sensor adapter layer. See docs/sensor_contract.md.

The canonical channel schema is defined in `contract.py`. One module per
sensor source maps that source's native data into the canonical schema:

  - `mediapipe_adapter` : near-passthrough (MediaPipe emits the schema natively)
  - `kinect_adapter`    : stub — map Kinect Azure body joints
  - `orbbec_adapter`    : stub — map Orbbec body tracking joints
  - `osc_adapter`       : stub — receive landmarks over OSC

Adapters are intentionally pure-ish modules + small TD COMPs; the COMP's
output is the single CHOP matching `contract.channel_names()`.
"""
