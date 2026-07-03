# Tracking Tuning SOP

This document is the Phase 1A tuning skeleton for single-person recognition,
face tracking, and gimbal stabilization. Phase 1A is observe-only: the runtime
may expose these fields, but the control behavior remains owned by
`main_phase3.py` and `core/orchestrator_v2.py`.

## Parameter Table

| Parameter | Unit | Default source | Increase effect | Decrease effect |
| --- | --- | --- | --- | --- |
| `center_deadband_x_ratio` | frame width ratio | current hardcoded deadzone | less horizontal correction near center | more responsive, more jitter risk |
| `center_deadband_y_ratio` | frame height ratio | current hardcoded deadzone | less vertical correction near center | more responsive, more jitter risk |
| `safe_roi_width_ratio` | frame width ratio | Phase 1B default placeholder | larger stable area | earlier edge correction |
| `safe_roi_height_ratio` | frame height ratio | Phase 1B default placeholder | larger stable area | earlier edge correction |
| `edge_margin_x_ratio` | frame width ratio | Phase 1B default placeholder | earlier horizontal edge correction | less edge correction |
| `edge_margin_y_ratio` | frame height ratio | Phase 1B default placeholder | earlier vertical edge correction | less edge correction |
| `max_yaw_deg_per_sec` | deg/s | current command cadence estimate | faster pan | slower pan |
| `max_pitch_deg_per_sec` | deg/s | current command cadence estimate | faster tilt | slower tilt |
| `max_yaw_delta_deg_per_tick` | deg | current hardcoded clamp | larger per-command pan | smoother but slower pan |
| `max_pitch_delta_deg_per_tick` | deg | current hardcoded clamp | larger per-command tilt | smoother but slower tilt |
| `yaw_smoothing_alpha` | 0-1 | current EMA value | follows detection faster | smoother target |
| `pitch_smoothing_alpha` | 0-1 | current EMA value | follows detection faster | smoother target |
| `lost_hold_ms` | ms | current occlusion hold | holds longer before search | searches sooner |
| `search_after_ms` | ms | current search grace | waits longer before search | searches sooner |
| `require_stable_frames` | frames | current lock confirmation | more stable lock | faster lock |
| `search_sweep_deg` | deg | current limited search sweep | wider local search | narrower local search |
| `search_yaw_deg_per_sec` | deg/s | current search command speed | faster sweep | slower sweep |
| `search_pitch_deg_per_sec` | deg/s | current search behavior | reserved for Phase 1C | reserved for Phase 1C |
| `search_timeout_ms` | ms | current timeout before standby | searches longer | returns standby sooner |

## Telemetry Fields

`tracking_state` is the UI-facing state derived from runtime phase and lock
state. `locked_track_id`, `raw_bbox`, `track_bbox`, `control_target`,
`face_center`, `frame_center`, `error_x_px`, `error_y_px`, `error_x_ratio`,
and `error_y_ratio` describe the current target geometry.

Latency fields are `frame_age_ms`, `face_detection_ms`, `embedding_ms`,
`tracker_update_ms`, and `control_loop_ms`. Rate fields are `vision_hz`,
`control_hz`, `telemetry_hz`, and `ui_push_hz`.

## Recommended Order

1. Check `frame_age_ms`.
2. Check `control_loop_ms`.
3. Check `face_detection_ms` and `embedding_ms`.
4. Confirm deadband behavior.
5. Tune `max_yaw_*` and `max_pitch_*`.
6. Tune `yaw_smoothing_alpha` and `pitch_smoothing_alpha`.
7. Tune `lost_hold_ms`.
8. Tune `search_sweep_deg` and `search_*_deg_per_sec`.

## Common Issues

If the face jitters while centered, inspect `error_x_ratio`,
`error_y_ratio`, `track_bbox`, and edge margins before changing gains.

If the system chases stale frames, reduce model frequency only after confirming
`frame_age_ms` grows continuously.

If reacquire feels too eager, adjust `require_stable_frames` in Phase 1C after
verifying the candidate is stable in telemetry.

## Performance Downgrade Order

1. Reduce dashboard and telemetry push frequency.
2. Gate emotion, gesture, MediaPipe, diary, and quote features.
3. Ensure LLM calls cannot block tracking or control.
4. Keep latest-frame/drop-old-frame behavior.
5. Reduce embedding frequency only after telemetry shows embedding is the bottleneck.

## Acceptance Checklist

- `/api/control/runtime` returns all Phase 1A telemetry fields.
- `/api/single_track/start`, `/api/single_track/stop`,
  `/api/multi_track/start`, and `/api/multi_track/stop` do not return 404.
- FastAPI remains UI/telemetry/event emitter only.
- `main_phase3.py` remains the only hardware control runtime.
- Existing control behavior and control tests are unchanged.

## Rollback

Revert the Phase 1A commit. Because the new config is observe-only and
telemetry fields default to `null` or conservative values, runtime behavior
should also remain compatible if consumers ignore the new fields.
