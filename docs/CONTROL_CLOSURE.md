# Gimbal Control Closure

Production safety is conditional on deploying the matching Node-RED bridge in
`deploy/node_red/recamera_control_bridge.json`.

The control runtime consumes session-bound `vision/observation` events from
FastAPI instead of running a second face detector. Observations include normalized
coordinates, frame dimensions, current track IDs, observation IDs and timestamps.
Stale, out-of-order and foreign-session observations fail closed.

Every motor command carries a session ID, sequence, issue time and expiry. The
device bridge rejects commands without a live lease and runs a 2000 ms watchdog.
Explicit stop, lease expiry, page teardown and process shutdown revoke authority.
Transient device-lease failures pause motion without clearing the selected feature;
later heartbeats re-authorize the same session, while the device watchdog remains
responsible for fail-safe motor stop.

Single-person tracking locks one current face, holds through short occlusion,
uses a deadband, searches within 35 degrees for eight seconds, then returns to
180/90 standby and ceases commands. Multi-person mode uses stable DOA for coarse
yaw before the selected face controls both axes.
