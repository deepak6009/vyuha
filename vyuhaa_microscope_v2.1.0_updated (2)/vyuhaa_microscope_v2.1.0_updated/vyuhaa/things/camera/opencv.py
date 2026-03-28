"""Vyuhaa Microscope OpenCV Camera.

This module defines a camera Thing that uses OpenCV's
``VideoCapture``.

See repository root for licensing information.
"""

from __future__ import annotations

import logging
from threading import Thread
from types import TracebackType
from typing import Literal, Optional

import cv2
from PIL import Image

# import labthings_fastapi as lt
# from labthings_fastapi.types.numpy import NDArray
from typing import Any
from numpy.typing import NDArray

from . import BaseCamera

LOGGER = logging.getLogger(__name__)


class OpenCVCamera(BaseCamera):
    """A Thing that provides and interface to an OpenCV Camera."""

    def __init__(
        self, thing_server_interface: Any = None, camera_index: int = 0
    ) -> None:
        """Iniatilise the thing storing the index of the camera to use.

        :param camera_index: The index of the camera to use for the microscope.
        """
        # super().__init__(thing_server_interface)
        super().__init__()
        self.camera_index = camera_index
        self._capture_thread: Optional[Thread] = None
        self._capture_enabled = False

    def __enter__(self) -> "OpenCVCamera":
        """Start the capture thread when the Thing context manager is opened."""
        super().__enter__()
        self.cap = cv2.VideoCapture(self.camera_index)
        self._capture_enabled = True
        self._capture_thread = Thread(target=self._capture_frames)
        self._capture_thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Release the camera when the Thing context manager is closed.

        Before releasing the camera the capture thread is closed.
        """
        if self.stream_active:
            self._capture_enabled = False
            if self._capture_thread is not None:
                self._capture_thread.join()
        self.cap.release()
        super().__exit__(exc_type, exc_value, traceback)

    @property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""
        if self._capture_enabled and self._capture_thread:
            return self._capture_thread.is_alive()
        return False

    def _capture_frames(self) -> None:
        while self._capture_enabled:
            ret, frame = self.cap.read()
            if not ret:
                LOGGER.error(f"Failed to capture frame from camera {self.camera_index}")
                break
            jpeg = cv2.imencode(".jpg", frame)[1].tobytes()
            self.mjpeg_stream.add_frame(jpeg)
            jpeg_lores = cv2.imencode(".jpg", cv2.resize(frame, (320, 240)))[
                1
            ].tobytes()
            self.lores_mjpeg_stream.add_frame(jpeg_lores)

    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""
        self.capture_array()

    def capture_array(
        self,
        stream_name: Literal["main", "lores", "raw", "full"] = "full",
        wait: Optional[float] = None,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array.

        This function will produce a nested list containing an uncompressed RGB image.
        It's likely to be highly inefficient - raw and/or uncompressed captures using
        binary image formats will be added in due course.
        """
        if wait is not None:
            LOGGER.warning("OpenCV camera has no wait option. Use None.")
        LOGGER.warning(f"OpenCV camera doesn't respect {stream_name=}")
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError(
                f"Failed to capture frame from camera {self.camera_index}"
            )
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"] = "main",
        wait: Optional[float] = None,
    ) -> Image.Image:
        """Acquire one image from the camera and return as a PIL image.

        This function will produce a JPEG image.
        """
        if wait is not None:
            LOGGER.warning("OpenCV camera has no wait option. Use None.")
        LOGGER.warning(f"OpenCV camera doesn't respect {stream_name=}")
        return Image.fromarray(self.capture_array())
