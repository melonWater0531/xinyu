# reCamera Control Bridge

This flow is the hardware-side adapter for the single control plane. It exposes
dual-axis command, stop, and real motor readback APIs on Node-RED port 1880.
The bundled flow reads yaw and then pitch serially at 1 Hz. Speed reads are
intentionally excluded because concurrent motor queries can stall the
device-side Node-RED event loop when CAN readback is unhealthy. `/status` only
reads cached values and reports command acceptance separately from verified
motor readback.

## Install

1. Open `http://<RECAMERA_IP>:1880` and confirm the
   `node-red-contrib-seeed-recamera` palette is installed.
2. Import `recamera_control_bridge.json` into a new flow and deploy it.
3. Verify the bridge before starting `main_phase3.py`:

```bash
export RECAMERA_DEVICE_IP=<RECAMERA_IP>
SID="bridge-test-$(date +%s)"

curl -q --noproxy "$RECAMERA_DEVICE_IP" -sS -i --max-time 3 \
  "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/status"
curl -q --noproxy "$RECAMERA_DEVICE_IP" -sS -i --max-time 3 \
  -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/session/start" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\",\"lease_ms\":5000}"
NOW=$(date +%s)
curl -q --noproxy "$RECAMERA_DEVICE_IP" -sS -i --max-time 3 \
  -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/command" \
  -H 'Content-Type: application/json' \
  -d "{\"mode\":\"absolute\",\"yaw\":180,\"pitch\":90,\"yaw_speed\":180,\"pitch_speed\":180,\"session_id\":\"$SID\",\"sequence\":1,\"issued_at\":$NOW,\"expires_at\":$((NOW+5))}"
curl -q --noproxy "$RECAMERA_DEVICE_IP" -sS -i --max-time 3 \
  -X POST "http://$RECAMERA_DEVICE_IP:1880/recamera-control/v1/stop" \
  -H 'Content-Type: application/json' \
  -d "{\"stop\":true,\"session_id\":\"$SID\"}"
```

The status endpoint returns HTTP 503 until both motor angles have been read.

After deployment, run the repository's read-only verifier:

```bash
python3 tools/verify_control_bridge.py "$RECAMERA_DEVICE_IP"
```

`latency_ms.max` should remain below 100 ms. `verified=false` means the bridge
accepted a command but has not yet observed the requested absolute angle.
`last_error` records motor node, CAN, stop, or calibration failures caught by
the flow. The device lease is five seconds and must be renewed once per second.
