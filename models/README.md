# Models

Place compiled `.cvimodel` files here.

## Required models

| File | Purpose |
|------|---------|
| `yolo11n_cv181x_int8.cvimodel` | Person/face detection (SSCMA YOLO) |
| `yolo11n_pose_cv181x_int8.cvimodel` | Human pose estimation (optional) |

## Model conversion pipeline

ONNX → `model_transform` (→ MLIR) → `run_calibration` (INT8) → `model_deploy` (→ .cvimodel)

Refer to `OSHW-reCamera-Series/yolo11n_models/reCamera Workshop/` for step-by-step guides.

## Source

Models are compiled for CVI181x (Sophgo) processor. Original YOLO11n weights from Ultralytics.
