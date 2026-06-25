#!/usr/bin/env python3
"""
Check emotion_classifier.onnx: load, inspect, verify, dummy inference.
"""
import sys
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "emotion_classifier.onnx"


def main():
    print(f"Model: {MODEL_PATH}")
    if not MODEL_PATH.is_file():
        print(f"❌ Model file not found at {MODEL_PATH}")
        sys.exit(1)
    print(f"   Size: {MODEL_PATH.stat().st_size:,} bytes\n")

    # ── 1. Load ──
    try:
        import onnxruntime as ort
    except ImportError:
        print("❌ onnxruntime not installed. Run: pip install onnxruntime")
        sys.exit(1)

    try:
        session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        print("✅ Model loaded successfully\n")
    except Exception as e:
        print(f"❌ Failed to load model: {e}")
        sys.exit(1)

    # ── 2. Inputs ──
    print("─" * 50)
    print("Inputs:")
    for inp in session.get_inputs():
        print(f"   name : {inp.name}")
        print(f"   shape: {inp.shape}")
        print(f"   type : {inp.type}")
    print()

    # ── 3. Outputs ──
    print("─" * 50)
    print("Outputs:")
    for out in session.get_outputs():
        print(f"   name : {out.name}")
        print(f"   shape: {out.shape}")
        print(f"   type : {out.type}")
    print()

    # ── 4. External data check ──
    print("─" * 50)
    print("External data check:")
    model_dir = MODEL_PATH.parent
    for f in sorted(model_dir.iterdir()):
        if f.suffix in (".bin", ".data", ".weights") and "emotion" in f.name.lower():
            print(f"   ✅ External data file: {f.name} ({f.stat().st_size:,} bytes)")
    # Check if model metadata references external data
    meta = session.get_modelmeta()
    if meta.custom_metadata_map:
        for k, v in meta.custom_metadata_map.items():
            print(f"   Metadata: {k} = {v}")
    print()

    # ── 5. Dummy inference ──
    print("─" * 50)
    print("Dummy inference:")
    import numpy as np

    input_info = session.get_inputs()[0]
    shape = [1 if isinstance(d, str) or d == -1 else d for d in input_info.shape]
    dummy = np.random.randn(*shape).astype(np.float32)
    print(f"   Input shape : {dummy.shape}")

    try:
        outputs = session.run(None, {input_info.name: dummy})
        for i, out_val in enumerate(outputs):
            out_info = session.get_outputs()[i]
            arr = np.array(out_val)
            print(f"   Output[{i}]  : {out_info.name}")
            print(f"   Shape       : {arr.shape}")
            print(f"   Min / Max   : {arr.min():.4f} / {arr.max():.4f}")
            print(f"   Mean ± Std  : {arr.mean():.4f} ± {arr.std():.4f}")
            if arr.ndim == 2 and arr.shape[1] <= 10:
                print(f"   Row 0       : {arr[0]}")
        print("\n✅ Dummy inference passed")
    except Exception as e:
        print(f"❌ Inference failed: {e}")
        sys.exit(1)

    print("\n" + "─" * 50)
    print("All checks passed.")


if __name__ == "__main__":
    main()
