"""Vyuhaa Microscope Camera.

This module defines the interface for cameras. Any compatible lt.Thing
should enable the server to work.

See repository root for licensing information.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Mapping, Optional, Tuple

import numpy as np
import piexif
from PIL import Image

# import labthings_fastapi as lt
# from labthings_fastapi.types.numpy import NDArray
NDArray = np.ndarray # Direct use of numpy array

# Try to import JetsonCamera so it's available
try:
    from .jetson import JetsonCamera
except ImportError:
    pass

from vyuhaa.things.background_detect import (
    BackgroundDetectAlgorithm,
)
from vyuhaa.ui import ActionButton, PropertyControl
from vyuhaa.utilities import coerce_thing_selector


class JPEGBlob:
    """A class representing a JPEG image."""
    media_type: str = "image/jpeg"
    def __init__(self, data: bytes): self.data = data
    @classmethod
    def from_bytes(cls, data: bytes): return cls(data)
    @classmethod
    def from_temporary_directory(cls, directory, filename):
        with open(os.path.join(directory.name, filename), 'rb') as f:
            return cls(f.read())


class PNGBlob:
    """A class representing a PNG image."""
    media_type: str = "image/png"
    def __init__(self, data: bytes): self.data = data


class CaptureError(RuntimeError):
    """An error trying to capture from a CameraThing."""


class NoImageInMemoryError(RuntimeError):
    """An error called if no image is in memory when accessed."""


class CameraMemoryBuffer:
    """A class that holds images in memory. The images are by default PIL images.

    However subclasses of BaseCamera can use this class to store other object types.
    """

    _storage: dict[int, tuple[Any, Mapping[str, Any]]]

    def __init__(self) -> None:
        """Create the buffer instance."""
        # This dictionary is the main store for data. Dictionaries are ordered since
        # Python 3.6, so the order in the dictionary is the capture order
        self._storage = {}
        # A simple id system where each capture id is just the number of captures since
        # the server starts
        self._latest_id: int = 0

    def add_image(
        self,
        image: Any,
        metadata: Mapping[str, Any],
        buffer_max: int = 1,
    ) -> int:
        """Add an image to the Memory buffer.

        This will add an image to the memory buffer. By default the buffer will
        be cleared. To allow saving multiple images the buffer_max must be set
        every time an image is added.

        :param image: The image to add. A PIL image is recommended, but cameras
            can choose to use other formats
        :param metadata: Optional, a dictionary of the image metadata.
        :param buffer_max: The maximum number of images that should be in the buffer
            once this images is added. Default is 1.

        :returns: The id in the buffer for this image
        """
        self._latest_id += 1
        self._create_space(buffer_max)
        self._storage[self._latest_id] = (image, metadata)
        return self._latest_id

    def get_image(
        self, buffer_id: Optional[int] = None, remove: bool = True
    ) -> tuple[Any, Mapping[str, Any]]:
        """Return the image with the given id.

        If no id is given the most recent image is returned. However, the
        buffer is also cleared, otherwise it would be possible to accidentally
        retrieve images out of order.

        :param buffer_id: The buffer id of the image to retrieve
        :param remove: True (default) to remove this image from the buffer, False
            to leave the image in the buffer.
        """
        # No id given
        if buffer_id is None:
            # Get the latest image and metadata tuple from storage
            try:
                image_tuple = list(self._storage.values())[-1]
            except IndexError as e:
                raise NoImageInMemoryError("No image in memory to retrieve.") from e
            # Clear the storage so images don't get retrieved out of order
            self._storage.clear()
            return image_tuple

        try:
            if remove:
                return self._storage.pop(buffer_id)
            return self._storage[buffer_id]
        except KeyError as e:
            raise NoImageInMemoryError(
                "No image with matching id in memory to retrieve."
            ) from e

    def clear(self) -> None:
        """Clear all images from memory."""
        self._storage.clear()

    def _create_space(self, buffer_max: int) -> None:
        """Create space to add an image.

        :param buffer_max: The maximum number of images that should be in the buffer
            once another images is added.
        """
        # If only one image to be stored just clear the storage and return
        if buffer_max <= 1:
            self._storage.clear()
            return

        # Number to remove to get the storage down to 1 less than the buffer length
        to_remove = len(self._storage) - (buffer_max - 1)
        # If if there is space. Nothing to do, just return
        if to_remove < 1:
            return

        keys_to_remove = list(self._storage.keys())[:to_remove]
        for key in keys_to_remove:
            del self._storage[key]


class MockStream:
    """A minimal mock for LabThings MJPEGStream."""
    def __init__(self):
        self._last_frame = b""
        self._has_new = False

    def add_frame(self, frame: bytes) -> None:
        self._last_frame = frame
        self._has_new = True

    def grab_frame(self) -> bytes:
        return self._last_frame

    def next_frame_size(self) -> int:
        return len(self._last_frame)

    def stop(self) -> None:
        pass

    async def frame_async_generator(self):
        """Yield frames for monitoring."""
        import asyncio
        while True:
            if self._has_new:
                self._has_new = False
                yield self._last_frame
            await asyncio.sleep(0.05)


class BaseCamera:
    """The base class for all cameras. All cameras must directly inherit from this class.
    """

    _all_background_detectors: Mapping[str, BackgroundDetectAlgorithm] = None

    mjpeg_stream = None # Descriptor removed
    lores_mjpeg_stream = None
    _memory_buffer = CameraMemoryBuffer()

    def __init__(self, thing_server_interface: Any = None) -> None:
        """Initialise the base camera, this creates the background detectors.

        This must be run by all child camera classes.

        To add a new background detector to the server it must be added to the
        dictionary in this function. Configuration will be added at a later date.
        """
        self._thing_server_interface = thing_server_interface
        self.mjpeg_stream = MockStream()
        self.lores_mjpeg_stream = MockStream()
        # Default is never updated but is used if the value set from settings is
        # incorrect. In the future a better way to set defaults for thing slot mappings
        # would be ideal.
        self._default_background_detector = "bg_channel_deviations_luv"
        self._background_detector_name: Optional[str] = None

    def __enter__(self) -> "BaseCamera":
        """Open hardware connection when the Thing context manager is opened."""
        self._background_detector_name = coerce_thing_selector(
            thing_mapping=self._all_background_detectors,
            selected=self._background_detector_name,
            default=self._default_background_detector,
        )
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Close hardware connection when the Thing context manager is closed."""
        pass

    @property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating.

        This always returns False in BaseCamera. It should be reimplemented by child
        classes if calibration is required.
        """
        return False

    def start_streaming(
        self, main_resolution: tuple[int, int] = (800, 800), buffer_count: int = 1
    ) -> None:
        """Start (or stop and restart) the camera.

        :param main_resolution: the resolution to use for the main stream.
        :param buffer_count: number of images in the stream buffer.

        Note that the default values for both parameters should be set appropriately
        for the specific camera when defining a new Camera Thing.
        """
        raise NotImplementedError(
            "CameraThings must define their own start_streaming method"
        )

    def kill_mjpeg_streams(self) -> None:
        """Kill the streams now as the server is shutting down.

        This is called when uvicorn gets the a shutdown signal. As this is called from
        the event loop it cannot interact with the our ThingProperties or run
        ``self.mjpeg_stream.stop()`` as the portal cannot be called from this loop.

        Instead we just set the ``_streaming`` value to False. This stops the async frame
        generator when the next frame notifies.
        """
        if self.stream_active:
            self.mjpeg_stream._streaming = False
            self.lores_mjpeg_stream._streaming = False

    @property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""
        raise NotImplementedError(
            "CameraThings must define their own stream_active method"
        )

    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""
        raise NotImplementedError(
            "CameraThings must define their own discard_frames method"
        )

    def capture_array(
        self,
        stream_name: Literal["main", "lores", "raw", "full"] = "main",
        wait: Optional[float] = 5,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array."""
        raise NotImplementedError(
            "CameraThings must define their own capture_array method"
        )

    downsampled_array_factor: int = 2
    """The downsampling factor when calling capture_downsampled_array."""

    def capture_downsampled_array(self) -> NDArray:
        """Acquire one image from the camera, downsample, and return as an array.

        * The array is downsamples by the thing property `downsampled_array_factor`.
        * The default capture array arguments are used.

        This method provides the interface expected by the camera_stage_mapping.
        """
        img = self.capture_array()
        return downsample(self.downsampled_array_factor, img)

    def capture_jpeg(
        self,
        stream_name: Literal["main", "lores", "full"] = "main",
        wait: Optional[float] = None,
    ) -> JPEGBlob:
        """Acquire one image from the camera as a JPEG.

        This will use the internal capture image functionally of capture_image of
        the specific camera being used.

        :param stream_name: A stream name supported by this camera.
        :param wait: (Optional, float) Set a timeout in seconds. If None it will
            use the default for the underlying camera.
        """
        fname = datetime.now().strftime("%Y-%m-%d-%H%M%S.jpeg")
        directory = tempfile.TemporaryDirectory()
        jpeg_path = os.path.join(directory.name, fname)

        img = self.capture_image(stream_name, wait)

        capture_metadata = self._capture_metadata()

        self._save_capture(
            jpeg_path=jpeg_path,
            image=img,
            metadata=capture_metadata,
        )

        return JPEGBlob.from_temporary_directory(directory, fname)

    def grab_jpeg(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> JPEGBlob:
        """Acquire one image from the preview stream and return as blob of JPEG data.

        Note: in rare cases the JPEG stream may be broken. This can cause an OS error
        when loading the image. If loading with PIL, as long as the header data is
        complete, this error will not be raised until the data is accessed. Consider
        using ``grab_jpeg_as_array`` instead.

        This differs from ``capture_jpeg`` in that it does not pause the MJPEG
        preview stream. Instead, we simply return the next frame from that
        stream (either "main" for the preview stream, or "lores" for the low
        resolution preview). No metadata is returned.
        """
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        frame = self._thing_server_interface.call_async_task(stream.grab_frame)
        return JPEGBlob.from_bytes(frame)

    def grab_as_array(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> NDArray:
        """Acquire one image from the preview stream and return as an array.

        It works like ``grab_jpeg`` but reliably handles broken streams. Prefer using
        this method over directly grabbing the frame and converting to a numpy array
        via PIL.

        This differs from ``capture_array`` in that it does not pause the MJPEG
        preview stream.
        """
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        tries = 0
        while tries < 3:
            try:
                frame = self._thing_server_interface.call_async_task(stream.grab_frame)
                return np.asarray(Image.open(io.BytesIO(frame)))
            except OSError:
                tries += 1
        raise OSError("Could not open frames from MJPEG stream.")

    def grab_jpeg_size(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> int:
        """Acquire one image from the preview stream and return its size."""
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        return self._thing_server_interface.call_async_task(stream.next_frame_size)

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"],
        wait: Optional[float] = None,
    ) -> Image.Image:
        """Capture a PIL image from stream stream_name with timeout wait."""
        raise NotImplementedError(
            "CameraThings must define their own capture_image method"
        )

    def capture_and_save(
        self,
        jpeg_path: str,
        save_resolution: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Capture an image and save it to disk.

        :param jpeg_path: The path to save the file to
        :param save_resolution: can be set to resize the image before saving. By
            default this is None meaning that the image is saved at original resolution.
        """
        image, capture_metadata = self._robust_image_capture()

        self._save_capture(jpeg_path, image, capture_metadata, save_resolution)

    def capture_to_memory(self, buffer_max: int = 1) -> int:
        """Capture an image to memory. This can be saved later with ``save_from_memory``.

        Note that only one image is held in memory so this will overwrite any image
        in memory.

        :param buffer_max: The maximum number of images that should be in the buffer
            once this images is added. Default is 1.

        :returns: the buffer id of the image captured
        """
        image, metadata = self._robust_image_capture()
        return self._memory_buffer.add_image(image, metadata, buffer_max=buffer_max)

    def save_from_memory(
        self,
        jpeg_path: str,
        save_resolution: Optional[Tuple[int, int]] = None,
        buffer_id: Optional[int] = None,
    ) -> None:
        """Save an image that has been captured to memory.

        :param jpeg_path: The path to save the file to
        :param save_resolution: can be set to resize the image before saving. By
            default this is None meaning that the image is saved at original
            resolution.
        :param buffer_id: The buffer id of the image to save, this was returned by
            ``capture_to_memory``
        """
        image, metadata = self._memory_buffer.get_image(buffer_id)

        self._save_capture(
            jpeg_path=jpeg_path,
            image=image,
            metadata=metadata,
            save_resolution=save_resolution,
        )

    def clear_buffers(self) -> None:
        """Clear all images in memory."""
        self._memory_buffer.clear()

    def _robust_image_capture(self) -> Tuple[Image.Image, Mapping[str, Any]]:
        """Capture an image in memory and return it with metadata.

        This robust capturing method attempts to capture the image five times
        each time with a 5 second timeout set.

        :raises CaptureError: if the capture fails for any reason

        :returns: tuple with PIL Image, and dictionary of metadata.
        """
        for capture_attempts in range(5):
            try:
                capture_metadata = self._capture_metadata()

                image = self.capture_image(stream_name="main", wait=5)
                return image, capture_metadata
            except TimeoutError:
                self.logger.warning(
                    f"Attempt {capture_attempts + 1} to capture image timed out. Do you have enough RAM?"
                )
        raise CaptureError("An error occurred while capturing after 5 attempts")

    def _capture_metadata(self) -> dict:
        """Return the metadata for a capture, from the thing states, time and known names."""
        # metadata = self._thing_server_interface.get_thing_states()
        metadata = {} # LabThings interface removal
        current_time = datetime.now()
        return {
            "capture_time": current_time.timestamp(),
            "timezone": current_time.astimezone().utcoffset(),
            "make": "Vyuhaa",
            "model": "Vyuhaa Microscope",
            "things_states": metadata,
        }

    def _add_metadata_to_capture(self, jpeg_path: str, capture_metadata: dict) -> None:
        """Add the EXIF metadata for a JPEG image.

        This adds:
        - UserComment (JSON-encoded metadata from the Things)
        - Capture time (DateTimeOriginal, DateTimeDigitized, 0th DateTime)
        - Camera Make and Model
        """
        # Load existing EXIF
        exif_dict = piexif.load(jpeg_path)

        user_metadata = capture_metadata["things_states"]
        capture_time = capture_metadata["capture_time"]
        timezone = capture_metadata["timezone"]

        # Convert timezone into bytes with required formatting
        hours = int(timezone.total_seconds() // 3600)
        minutes = int((abs(timezone.total_seconds()) % 3600) // 60)
        sign = "+" if hours >= 0 else "-"
        offset_str = f"{sign}{abs(hours):02d}:{minutes:02d}"

        # Update UserComment with JSON-encoded metadata
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = json.dumps(
            user_metadata
        ).encode("utf-8")

        capture_time_str = datetime.fromtimestamp(capture_time).strftime(
            "%Y:%m:%d %H:%M:%S"
        )

        # Update the three EXIF date fields used as "created" by different platforms
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = capture_time_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = capture_time_str
        exif_dict["0th"][piexif.ImageIFD.DateTime] = capture_time_str

        exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = offset_str.encode()
        exif_dict["Exif"][piexif.ExifIFD.OffsetTimeDigitized] = offset_str.encode()

        # Update Make and Model
        exif_dict["0th"][piexif.ImageIFD.Make] = capture_metadata["make"]
        exif_dict["0th"][piexif.ImageIFD.Model] = capture_metadata["model"]

        # Write the updated EXIF back to the file
        piexif.insert(piexif.dump(exif_dict), jpeg_path)

    def _save_capture(
        self,
        jpeg_path: str,
        image: Image.Image,
        metadata: Mapping[str, Any],
        save_resolution: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Save the captured image and metadata to disk.

        A warning is logged if metadata cannot be added.

        :raises IOError: if the file cannot be saved

        nothing is returned on success
        """
        if save_resolution is not None and image.size != save_resolution:
            image = image.resize(save_resolution, Image.Resampling.BOX)
        try:
            # Per PIL documentation,
            # (https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg)
            # there are two factors when saving a JPEG. Subsampling affects the colour,
            # quality affects the pixels.
            # subsampling = 0 disables subsampling of colour
            # quality = 95 is the maximum recommended - above this, JPEG compression is
            # disabled, file size increases and quality is barely or not affected
            image.save(jpeg_path, quality=95, subsampling=0)
            try:
                self._add_metadata_to_capture(jpeg_path, dict(metadata))
            except Exception:
                # We need to capture any exception as there are many reasons metadata
                # might not be added. We warn rather than log the error.
                self.logger.exception(f"Failed to add metadata to {jpeg_path}")
        except Exception as e:
            raise IOError(f"An error occurred while saving {jpeg_path}") from e

    settling_time: float = 0.2
    """The settling time when calling the ``settle()`` method."""

    def settle(self) -> None:
        """Sleep for the settling time, ready to provide a fresh frame.
        """
        time.sleep(self.settling_time)
        self.discard_frames()

    @property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return []

    @property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return []

    @property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return []

    @property
    def background_detector_name(self) -> Optional[str]:
        """The name of the active background selector."""
        return self._background_detector_name

    @background_detector_name.setter
    def background_detector_name(self, name: str) -> None:
        """Validate and set background_detector_name."""
        if name not in self._all_background_detectors:
            self.logger.warning(f"{name} is not a valid background detector name.")
        self._background_detector_name = name

    @property
    def background_detector(self) -> Optional[BackgroundDetectAlgorithm]:
        """The active background detector instance."""
        if self.background_detector_name is None:
            return None
        return self._all_background_detectors[self.background_detector_name]

    def image_is_sample(self) -> tuple[bool, str]:
        """Label the current image as either background or sample."""
        if self.background_detector is None:
            raise RuntimeError("No background detectors available.")
        current_image = self.grab_as_array(stream_name="lores")
        return self.background_detector.image_is_sample(current_image)

    def set_background(self) -> None:
        """Grab an image, and use its statistics to set the background.

        This should be run when the microscope is looking at an empty region,
        and will calculate the mean and standard deviation of the pixel values
        in the LUV colourspace. These values will then be used to compare
        future images to the distribution, to determine if each pixel is
        foreground or background.
        """
        if self.background_detector is None:
            raise RuntimeError("No background detectors available.")
        background = self.grab_as_array(stream_name="lores")
        self.background_detector.set_background(background)

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Empty metadata dict for subclasses to populate."""
        return {}


def downsample(factor: int, image: np.ndarray) -> np.ndarray:
    """Downsample an image by taking the mean of each nxn region.

    This should be very efficient:

    * calculate each pixel as the mean of each ``factor * factor`` square without
        interpolation.
    * If the image is not an integer multiple of the resampling factor, discard
        the left-over pixels to avoid odd edge effects and keep performance quick.
    """
    if factor == 1:
        return image
    new_size = [d // factor for d in image.shape[:2]]
    # First, we ensure we have something that's an integer multiple
    # of `factor`
    cropped = image[: new_size[0] * factor, : new_size[1] * factor, ...]
    reshaped = cropped.reshape(
        (new_size[0], factor, new_size[1], factor) + image.shape[2:]
    )
    return reshaped.mean(axis=(1, 3))
