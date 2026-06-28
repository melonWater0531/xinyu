# reCamera Control Bridge

This flow is the hardware-side adapter for the single control plane. It exposes
dual-axis command, stop, and real motor readback APIs on Node-RED port 1880.

## Install

1. Open `http://<RECAMERA_IP>:1880` and confirm the
   `node-red-contrib-seeed-recamera` palette is installed.
2. Import `recamera_control_bridge.json` into a new flow and deploy it.
3. Verify the bridge before starting `main_phase3.py`:

```bash
curl "http://<RECAMERA_IP>:1880/recamera-control/v1/status"
curl -X POST "http://<RECAMERA_IP>:1880/recamera-control/v1/command" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"absolute","yaw":180,"pitch":90,"yaw_speed":180,"pitch_speed":180}'
curl -X POST "http://<RECAMERA_IP>:1880/recamera-control/v1/stop" \
  -H 'Content-Type: application/json' -d '{"stop":true}'
```

The status endpoint returns HTTP 503 until both motor angles have been read.
