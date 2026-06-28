"""
Unified vision data source — switchable between Mock and Real.

This is the SINGLE entry point for vision data in the system.
The control plane imports from here, never directly from detector or mock.

Usage:
    from vision.data_source import create_vision_source

    source = create_vision_source(use_mock=True)
    bboxes = source.get_bboxes()  # → List[BBox] or []

Supports:
    - Mock mode:   uses MockDataGenerator (scenarios A→B→C→D, no hardware)
    - Real mode:   uses vision.Detector (SSCMA YOLO on reCamera)
"""

from typing import List, Optional  # noqa: F401

from core.event import BBox
from utils.logger import get_logger
from vision.mock_data_generator import MockDataGenerator

logger = get_logger(__name__)


class VisionDataSource:
    """
    Abstract vision data source interface.

    All vision data flows through this abstraction so the rest of the system
    doesn't care whether data comes from mock or real hardware.
    """

    def get_bboxes(self) -> List[BBox]:
        """Return detected bounding boxes for the current frame."""
        raise NotImplementedError

    @property
    def is_mock(self) -> bool:
        raise NotImplementedError

    @property
    def frame_count(self) -> int:
        raise NotImplementedError

    def reset(self) -> None:
        """Reset the data source."""
        pass


class MockVisionSource(VisionDataSource):
    """
    Mock data source using MockDataGenerator.

    Cycles through pre-scripted scenarios:
      ENTER → IDLE → MOVE → LEAVE → ENTER ...
    """

    def __init__(self, **kwargs) -> None:
        self._gen = MockDataGenerator(**kwargs)
        logger.info("Vision source: MOCK (scenario cycling mode)")

    def get_bboxes(self) -> List[BBox]:
        bbox = self._gen.next()
        if bbox is None:
            return []
        return [bbox]

    @property
    def is_mock(self) -> bool:
        return True

    @property
    def frame_count(self) -> int:
        return self._gen.total_frames

    def reset(self) -> None:
        self._gen.reset()

    @property
    def generator(self) -> MockDataGenerator:
        """Access the underlying generator for scenario info."""
        return self._gen


class RealVisionSource(VisionDataSource):
    """
    Real data source backed by SSCMA WebSocket (VideoStream).

    Connects to a configured reCamera SSCMA WebSocket and parses detection boxes.
    Also exposes get_jpeg_bytes() for face detection downstream.
    """

    def __init__(self, sscma_url: str,
                 conf_thresh: float = 0.10) -> None:
        if not sscma_url:
            raise ValueError("sscma_url is required for RealVisionSource")
        from vision.video_stream import VideoStream
        self._stream = VideoStream(url=sscma_url)
        self._stream.start()
        self._conf_thresh = conf_thresh
        self._frame_count: int = 0
        logger.info("Vision source: REAL (SSCMA @ %s)", sscma_url)

    def get_bboxes(self) -> List[BBox]:
        self._frame_count += 1
        bboxes: List[BBox] = []
        for box in self._stream.boxes:
            if len(box) < 5:
                continue
            conf = float(box[4])
            conf = conf / 100.0 if conf > 1.0 else conf
            if conf < self._conf_thresh:
                continue
            bboxes.append(BBox(
                x1=int(box[0]), y1=int(box[1]),
                x2=int(box[2]), y2=int(box[3]),
                class_name="person",
                confidence=conf,
            ))
        return bboxes

    def get_jpeg_bytes(self) -> Optional[bytes]:
        """Latest JPEG frame as raw bytes (for FaceTrackerV2)."""
        import base64
        b64 = self._stream.frame_b64
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except Exception:
            return None

    @property
    def is_mock(self) -> bool:
        return False

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def reset(self) -> None:
        self._frame_count = 0


# ═══════════════════════════════════════════════════════════════
#  Factory
# ═══════════════════════════════════════════════════════════════

def create_vision_source(
    use_mock: bool = True,
    sscma_url: str = "",
    **mock_kwargs,
) -> VisionDataSource:
    """
    Create the appropriate vision data source.

    Args:
        use_mock:   If True, use MockVisionSource (no hardware needed).
                    If False, connect to SSCMA WebSocket for real detections.
        sscma_url:  SSCMA WebSocket URL (required when use_mock=False).
        mock_kwargs: Passed to MockDataGenerator.
    """
    if use_mock:
        return MockVisionSource(**mock_kwargs)
    if not sscma_url:
        raise ValueError("sscma_url is required when use_mock=False")
    return RealVisionSource(sscma_url=sscma_url)
