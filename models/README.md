# Models

Place compiled `.cvimodel`, ONNX, MediaPipe Task and ASR-backed model assets here.

## Required models

| File | Purpose |
|------|---------|
| `yolo11n_cv181x_int8.cvimodel` | Person/face detection (SSCMA YOLO) |
| `yolo11n_pose_cv181x_int8.cvimodel` | Human pose estimation (optional) |
| `gesture_recognizer.task` | MediaPipe Gesture Recognizer for low-risk companion intents |
| `emotiefflib/enet_b0_8_va_mtl.onnx` | EmotiEff 8-class emotion inference |

## Host-side models used by the dashboard

| File or cache | Source | Purpose | Validation |
|---|---|---|---|
| `models/gesture_recognizer.task` | `https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task` | Open Palm / Closed Fist / Thumb Up / Thumb Down / Victory intent detection | `python3 -m unittest tests.test_gesture_detector` |
| Hugging Face cache for `Systran/faster-whisper-tiny` | `https://huggingface.co/Systran/faster-whisper-tiny` | local CPU ASR for meeting WAV segments | instantiate `WhisperModel("Systran/faster-whisper-tiny", device="cpu", compute_type="int8")` |

The gesture model only feeds UI companion intents. It must not emit gimbal
control events. Meeting ASR is optional at runtime; `/api/meeting/summarize`
returns a clear fallback error when no speech segment or ASR result is present.

Meeting noise suppression and WebRTC VAD do not add files under `models/`.
They are optional Python dependencies (`noisereduce` and `webrtcvad-wheels`)
and automatically fall back to RMS segmentation when unavailable.

## Model conversion pipeline

ONNX → `model_transform` (→ MLIR) → `run_calibration` (INT8) → `model_deploy` (→ .cvimodel)

Refer to `OSHW-reCamera-Series/yolo11n_models/reCamera Workshop/` for step-by-step guides.

## Source

Models are compiled for CVI181x (Sophgo) processor. Original YOLO11n weights from Ultralytics.
