"""
Vision perception layer.

Perception ONLY — never imports gimbal or modifies state machine.

Contains:
  - data_source:        Unified mock/real vision data source
  - video_stream:       reCamera SSCMA WebSocket video client
  - pose_estimator:     YOLO11n-Pose ONNX pose estimation
  - attention_engine:   Head pose attention monitor with baseline calibration
  - emotieff_adapter:   EmotiEffLib parallel emotion recognition (8-class + VA)
  - mediapipe_face:     MediaPipe 478-landmark face detector
  - eye_metrics:        EAR / blink rate / PERCLOS eye focus metrics
  - llm_reflect:        Lightweight template emotion diary + quotes
  - face_tracker_v2:    Kalman + ByteTrack multi-face tracker
  - face_crop:          Face ROI extraction from landmarks
  - mock_data_generator: Scripted scenario bbox generator
"""
