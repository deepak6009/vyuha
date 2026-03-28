"""Vyuhaa Microscope OpenCV Camera.

This module defines a Thing that is responsible for using the stage and
camera together to perform an autofocus routine.

See repository root for licensing information.
"""

from __future__ import annotations

import io
import logging
import re
import time
from threading import Thread
from types import TracebackType
from typing import Any, Literal, Optional, overload

import numpy as np
from PIL import Image, ImageFilter

# import labthings_fastapi as lt
# from labthings_fastapi.types.numpy import NDArray
from numpy.typing import NDArray

from vyuhaa.ui import (
    ActionButton,
    PropertyControl,
    action_button_for,
    property_control_for,
)

from ..stage.dummy import DummyStage
from ..stage.base import BaseStage
from . import BaseCamera

LOGGER = logging.getLogger(__name__)

# The ratio between "motor" steps and pixels in (x, y, z)
# higher related to a faster movement
RATIO = (2, 2, 0.2)

# Some colour variation, for bg detect.
BG_COLOR = [220, 215, 217]

# Random Number Generator
RNG = np.random.default_rng()

DOWNSAMPLE = 2
# Upsample for sprites and then downsample to create sharp edges for each sprite
# as these are small and calculated once there is almost no performance penalty
# for a nice gain in quality.
SPRITE_UPSAMPLE = 4

# A list of 6 digit hex colour codes separated by ;. Allow a trailing ;
# For example, Vyuhaa pink would be #C5247F;
COLOUR_LIST_REGEX = re.compile(
    r"^\s*(#[0-9a-fA-F]{6})\s*(?:;\s*(#[0-9a-fA-F]{6})\s*)*;?\s*$"
)
# regex to separate R, G and B from a 6 digit hex code with preceding #
COLOUR_REGEX = re.compile(r"^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$")


@overload
def _downsample_shape(shape: tuple[int, int]) -> tuple[int, int]: ...


@overload
def _downsample_shape(shape: tuple[int, int, int]) -> tuple[int, int, int]: ...


def _downsample_shape(
    shape: tuple[int, int] | tuple[int, int, int],
) -> tuple[int, int] | tuple[int, int, int]:
    if len(shape) == 2:
        return (shape[0] // DOWNSAMPLE, shape[1] // DOWNSAMPLE)
    if len(shape) == 3:
        return (shape[0] // DOWNSAMPLE, shape[1] // DOWNSAMPLE, shape[2])
    raise ValueError("Shape should be a 2 or 3 element tuple.")


def colour_str_to_colour(colour_str: str) -> tuple[int, int, int]:
    """Convert a colour string into RGB colour values.

    :param colour_str: Should be a hex colour such as #33aa33 or a list of hex
        colours separated by semicolons (with optional spaces).
    :return: The colour as a tuple of 3 integers from 0 to 255 in value
    :raises ValueError: If the hex string is not valid. This should never happen if the
        user enters a bad colour string as the colour property setter checks the
        whole string regex.
    """
    if ";" in colour_str:
        colours = colour_str.split(";")
        if len(colours) > 1 and colours[-1].strip() == "":
            colours.pop(-1)
        single_colour_str = colours[RNG.integers(0, len(colours))]
    else:
        single_colour_str = colour_str
    single_colour_str = single_colour_str.lower().strip()
    colour_match = COLOUR_REGEX.match(single_colour_str)
    if colour_match is None:
        raise ValueError(
            f"{colour_str} is not a valid colour. Please use HTML hex notation."
        )

    r = int("0x" + colour_match.group(1), 16)
    g = int("0x" + colour_match.group(2), 16)
    b = int("0x" + colour_match.group(3), 16)
    return r, g, b


class SimulatedCamera(BaseCamera):
    """A simulated camera that produces images of moving blobs."""

    _stage: BaseStage = None

    _show_sample: bool = True

    def __init__(
        self,
        thing_server_interface: Any = None,
        shape: tuple[int, int, int] = (616, 820, 3),
        canvas_shape: tuple[int, int, int] = (1500, 2000, 3),
        frame_interval: float = 0.1,
    ) -> None:
        """Initialise the simulated with settings for how images are generated.

        :param shape: The shape (size) of the generated image.
        :param canvas_shape: The shape (size) of the canvas generated on initialisation
            that images are cropped from. If this is too large the it uses resources,
            but its size limits the range of motion of the simulation.
        :param frame_interval: Nominally the time between frames on the MJPEG stream,
            however the rate may be slower due to calculation time for focus.
        """
        super().__init__(thing_server_interface)
        self.shape = shape
        self.ds_shape = _downsample_shape(shape)
        self.glyph_size = 105 // DOWNSAMPLE
        self.canvas_shape = _downsample_shape(canvas_shape)

        self.frame_interval = frame_interval
        self._capture_thread: Optional[Thread] = None
        self._capture_enabled = False
        self.generate_sprites()

    repeating: bool = False

    _blob_density: int = 400

    @property
    def blob_density(self) -> int:
        """The number of blobs per million pixels."""
        return self._blob_density

    @blob_density.setter
    def blob_density(self, value: int) -> None:
        self._blob_density = value
        if self._capture_enabled:
            self.generate_canvas()

    _colour: str = "#b937b9"

    @property
    def colour(self) -> str:
        """The colour of the blobs as a HTML hex string.

        The string can either be a single colour (e.g. "#c5247f") or a list of
        colours separated by semicolons (e.g. "#c5247f; #b937b9"). Additional
        spaces are allowed between colours.
        """
        return self._colour

    @colour.setter
    def colour(self, colour_value: str) -> None:
        if COLOUR_LIST_REGEX.match(colour_value) is None:
            self.logger.warning(f"{colour_value} is not a valid colour string.")
            return

        self._colour = colour_value
        if self._capture_enabled:
            self.generate_canvas()

    @property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating."""
        if self.background_detector is None:
            return True
        return not self.background_detector.ready

    def generate_sprites(self) -> None:
        """Generate sprites to populate the image."""
        sprite_sizes = [10, 21, 36, 40, 50]
        sprite_sizes = [s * SPRITE_UPSAMPLE for s in sprite_sizes]
        self.sprites = []

        block_size = self.glyph_size * DOWNSAMPLE * SPRITE_UPSAMPLE
        channel_block = np.zeros((block_size, block_size))
        x = np.arange(channel_block.shape[0])
        y = np.arange(channel_block.shape[1])
        # 2D grid of radii
        r_coord = np.sqrt(
            (x[:, None] - np.mean(x)) ** 2 + (y[None, :] - np.mean(y)) ** 2
        )

        for sprite_size in sprite_sizes:
            # Mask of where this sprite is
            sprite_mask = r_coord < sprite_size
            # Calculate a sharp edged circle with value varying from 0 in centre to 255
            # at the edge
            sprite_px = r_coord[sprite_mask]
            sprite_px -= np.min(sprite_px)
            sprite_px /= np.max(sprite_px)
            sprite = channel_block.copy()
            sprite[sprite_mask] = 255 * sprite_px

            # Convert to uint8
            sprite = sprite.astype(np.uint8)
            # Convert to PIL (and back) to resize then append to list of sprites
            sprite_pil = Image.fromarray(sprite)
            sprite_pil = sprite_pil.resize(
                (self.glyph_size, self.glyph_size), Image.Resampling.BILINEAR
            )
            # Convert back and ensure all edges are zero as these are repeated at sample
            # edge
            sprite = np.array(sprite_pil)
            sprite[0, :] = 0
            sprite[-1, :] = 0
            sprite[:, 0] = 0
            sprite[:, -1] = 0
            self.sprites.append(sprite)

    def generate_blobs(self, n_blobs: int = 1000) -> None:
        """Generate coordinates of blobs and their sizes, centered around (0,0).

        Note that blob density is determined by sample size and n_blobs, and for larger
        samples n_blobs will need increasing to keep a high level of sample coverage per
        field of view.

        :param n_blobs: The number of blobs to generate.
        """
        self.blobs = np.zeros((n_blobs, 3))
        w = self.glyph_size

        self.blobs[:, 0] = RNG.uniform(w // 2, self.canvas_shape[1] - w // 2, n_blobs)
        self.blobs[:, 1] = RNG.uniform(w // 2, self.canvas_shape[0] - w // 2, n_blobs)
        self.blobs[:, 2] = RNG.choice(len(self.sprites), n_blobs)

    def generate_canvas(self) -> None:
        """Generate a canvas with generated blobs centered at the middle.

        Canvas is int16 so that random noise can be added to simulation image before
        changing to unit8 to stop wrapping.
        """
        n_pixels = self.canvas_shape[0] * self.canvas_shape[1] * DOWNSAMPLE**2
        self.generate_blobs(int(self.blob_density * 1e-6 * n_pixels))
        self.blank_canvas = np.ones(self.canvas_shape, dtype=np.int16)
        self.blank_canvas[:, :, 0] *= BG_COLOR[0]
        self.blank_canvas[:, :, 1] *= BG_COLOR[1]
        self.blank_canvas[:, :, 2] *= BG_COLOR[2]
        new_canvas = self.blank_canvas.copy()

        for blob_x, blob_y, sprite_index in self.blobs:
            self.draw_sprite_on_canvas(
                new_canvas, self.sprites[int(sprite_index)], int(blob_y), int(blob_x)
            )
        self.canvas = np.clip(new_canvas, 0, 255)

    def draw_sprite_on_canvas(
        self, canvas: np.ndarray, sprite: np.ndarray, centre_y: int, centre_x: int
    ) -> None:
        """Place one sprite on canvas at given centre coordinates.

        Note that self.canvas is modified in place.

        :param sprite: The sprite array to place on the canvas.
        :param centre_y: The y coordinate to place the centre of the sprite.
        :param centre_x: The x coordinate to place the centre of the sprite.
        """
        canvas_h, canvas_w, _ = canvas.shape
        sprite_h, sprite_w = sprite.shape

        sprite_f = sprite.astype(float) / 255
        r, g, b = colour_str_to_colour(self.colour)
        sprite_r = (255 - r) * sprite_f
        sprite_g = (255 - g) * sprite_f
        sprite_b = (255 - b) * sprite_f
        sprite_rgb = np.stack([sprite_r, sprite_g, sprite_b], axis=2)

        # Canvas region containing the sprite
        top = max(centre_y - sprite_h // 2, 0)
        left = max(centre_x - sprite_w // 2, 0)
        bottom = min(centre_y + (sprite_h - sprite_h // 2), canvas_h)
        right = min(centre_x + (sprite_w - sprite_w // 2), canvas_w)

        canvas[top:bottom, left:right] -= sprite_rgb.astype("int16")

    def generate_image(self, pos: tuple[int, int, int]) -> Image.Image:
        """Generate an image with blobs based on supplied coordinates.

        :param pos: a 3-item tuple containing the x,y,z coordinates of the 'stage'
        """
        canvas_width, canvas_height, _ = self.canvas_shape
        image_width, image_height, _ = self.ds_shape
        im_pos = (
            pos[0] * RATIO[0] / DOWNSAMPLE,
            pos[1] * RATIO[1] / DOWNSAMPLE,
            pos[2] * RATIO[2],
        )

        top_left = (
            int(im_pos[0]) - image_width // 2 + self.canvas_shape[0] // 2,
            int(im_pos[1]) - image_height // 2 + self.canvas_shape[1] // 2,
        )

        x_indices = np.arange(top_left[0], top_left[0] + image_width)
        y_indices = np.arange(top_left[1], top_left[1] + image_height)

        if self.repeating:
            # Create index list with modulo rather than slicing to handle wrapping at the
            # canvas edge.
            x_indices = x_indices % canvas_width
            y_indices = y_indices % canvas_height
        else:
            # Rather than use a modulo for the index list, as above when wrapping,
            # this uses np.clip to coerce all out of bound indices to repeat the
            # first or last pixel in the canvas. This works because no sprite touches
            # the very edge of the canvas (to prevent partial sprites).
            x_indices = np.clip(x_indices, 0, canvas_width - 1)
            y_indices = np.clip(y_indices, 0, canvas_height - 1)

        z_indices = np.arange(self.ds_shape[2])
        canvas = self.canvas if self._show_sample else self.blank_canvas
        # Use npx to make each 1d index list 3D
        focused_np_img = canvas[np.ix_(x_indices, y_indices, z_indices)]

        np_img = fast_pil_blur(focused_np_img, sigma=np.abs(im_pos[2]) / 5)

        # Add noise and convert to uint8
        np_img += RNG.normal(scale=self.noise_level, size=self.ds_shape).astype("int16")
        np.clip(np_img, 0, 255, out=np_img)
        pl_img = Image.fromarray(np_img.astype("uint8"))
        return pl_img.resize((self.shape[1], self.shape[0]), Image.Resampling.BILINEAR)

    def generate_frame(self) -> Image.Image:
        """Generate a frame with blobs based on the stage coordinates."""
        if hasattr(self._stage, "instantaneous_position"):
            pos = self._stage.instantaneous_position
        else:
            pos = self._stage.position
        return self.generate_image((pos["y"], pos["x"], pos["z"]))

    def __enter__(self) -> "SimulatedCamera":
        """Start the capture thread when the Thing context manager is opened."""
        super().__enter__()
        self.generate_canvas()
        self.start_streaming()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the capture thread when the Thing context manager is closed."""
        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_enabled = False
            self._capture_thread.join()
        super().__exit__(exc_type, exc_value, traceback)

    def start_streaming(
        self, main_resolution: tuple[int, int] = (820, 616), buffer_count: int = 1
    ) -> None:
        """Start the live stream.

        The start_streaming method is used a camera ``Thing`` to begin streaming
        images or to adjust the stream resolution if streaming is already active.

        The simulation camera does not currently support the resolution argument.
        It will always issue a warning that the resolution is not respected.
        If called while already streaming, the warning will be emitted and no other
        action will be taken.

        :param main_resolution: Currently ignored, this argument exists to ensure consistent API across camera Things.
        :param buffer_count: Currently ignored, this argument exists to ensure consistent API across camera Things.
        """
        # LOGGER.warning(
        #     f"Simulation camera doesn't respect {main_resolution=} or {buffer_count=} "
        #     "arguments."
        # )
        if not self.stream_active:
            self._capture_enabled = True
            self._capture_thread = Thread(target=self._capture_frames)
            self._capture_thread.start()

    @property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""
        if self._capture_enabled and self._capture_thread:
            return self._capture_thread.is_alive()
        return False

    noise_level: float = 2.0

    def _capture_frames(self) -> None:
        last_frame_t = time.time()
        while self._capture_enabled:
            wait_time = self.frame_interval - (time.time() - last_frame_t)
            if wait_time > 0:
                time.sleep(wait_time)
            last_frame_t = time.time()

            frame = self.generate_frame()
            self.mjpeg_stream.add_frame(_frame2bytes(frame))
            ds_frame = frame.resize((320, 240), resample=Image.Resampling.NEAREST)
            self.lores_mjpeg_stream.add_frame(_frame2bytes(ds_frame))

    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh.

        There is nothing to do as this is a simulation!
        """

    def capture_array(
        self,
        stream_name: Literal["main", "lores", "raw", "full"] = "full",
        wait: Optional[float] = None,
    ) -> np.ndarray:
        """Acquire one image from the camera and return as an array."""
        # Silencing spammy warnings for a cleaner terminal
        # if wait is not None:
        #     LOGGER.warning("Simulation camera has no wait option. Use None.")
        # LOGGER.warning(f"Simulation camera camera doesn't respect {stream_name=}")
        return np.array(self.generate_frame())

    def capture_image(
        self,
        stream_name: Literal["main", "lores", "full"],
        wait: Optional[float] = None,
    ) -> Image.Image:
        """Capture to a PIL image. This is not exposed as a ThingAction."""
        # Silencing spammy warnings for a cleaner terminal
        # if wait is not None:
        #     LOGGER.warning("Simulation camera has no wait option. Use None.")
        # LOGGER.warning(f"Simulation camera camera doesn't respect {stream_name=}")
        return self.generate_frame()

    def full_auto_calibrate(self) -> None:
        """Perform a full auto-calibration.

        For the simulation microscope the process is:

        * ``remove_sample``
        * ``set_background``
        * ``load_sample``
        """
        self.remove_sample()
        time.sleep(0.2)
        if self.background_detector is not None:
            self.set_background()
        time.sleep(0.2)
        self.load_sample()

    def remove_sample(self) -> None:
        """Show the simulated background with no sample."""
        if not self._show_sample:
            raise RuntimeError("Sample is already removed.")
        self._show_sample = False

    def load_sample(self) -> None:
        """Show the simulated sample."""
        if self._show_sample:
            raise RuntimeError("Sample is already in place.")
        self._show_sample = True

    @property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return [
            action_button_for(
                self, "full_auto_calibrate", submit_label="Full Auto-Calibrate"
            ),
        ]

    @property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return [
            action_button_for(self, "load_sample", submit_label="Load Sample"),
            action_button_for(self, "remove_sample", submit_label="Remove Sample"),
        ]

    @property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return [
            property_control_for(self, "repeating", label="Infinite Sample"),
            property_control_for(self, "blob_density", label="Sample Density"),
            property_control_for(self, "colour", label="Sample Colour"),
            property_control_for(self, "noise_level", label="Noise Level"),
        ]


def _frame2bytes(frame: Image.Image) -> bytes:
    """Convert frame to bytes."""
    with io.BytesIO() as buf:
        # Save in low quality for speed.
        frame.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


def fast_pil_blur(array: np.ndarray, sigma: float) -> np.ndarray:
    """Apply Gaussian blur using PIL (faster than scipy)."""
    if sigma < 0.5:
        return array  # no visible blur needed

    img_pil = Image.fromarray(array.astype(np.uint8))
    img_pil = img_pil.filter(ImageFilter.GaussianBlur(radius=sigma))

    # Convert back to NumPy array
    return np.array(img_pil, dtype=array.dtype)
