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

from typing import List, Optional

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
    Real data source wrapping vision.Detector.

    Requires reCamera hardware with SSCMA YOLO model loaded.
    Currently STUB — will be filled in when hardware is available.
    """

    def __init__(self, detector=None) -> None:
        self._detector = detector
        self._frame_count: int = 0
        logger.info("Vision source: REAL (detector mode) — STUB")

    def get_bboxes(self) -> List[BBox]:
        self._frame_count += 1
        if self._detector is None:
            return []

        # TODO: capture frame from camera, then:
        # frame = camera.read()
        # return self._detector.detect(frame)
        return []

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
    detector=None,
    **mock_kwargs,
) -> VisionDataSource:
    """
    Create the appropriate vision data source.

    Args:
        use_mock:    If True, use MockVisionSource. If False, use RealVisionSource.
        detector:    Detector instance (required if use_mock=False).
        mock_kwargs: Passed to MockDataGenerator (e.g., frames_enter, jitter_range).

    Returns:
        VisionDataSource instance.
    """
    if use_mock:
        return MockVisionSource(**mock_kwargs)
    else:
        return RealVisionSource(detector=detector)
