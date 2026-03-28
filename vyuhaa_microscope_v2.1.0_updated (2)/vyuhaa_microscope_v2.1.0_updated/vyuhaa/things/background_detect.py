"""Provide functionality to detect if the camera is imaging sample or background.

An example background image is captured by the camera and sent to classes in the module
for analysis. Information from these images is used to detect whether an image from the
current camera field of view contains sample.
"""

from typing import Optional, TypeVar, Any, List

import cv2
import numpy as np
from pydantic import BaseModel

# import labthings_fastapi as lt

from vyuhaa.ui import PropertyControl, property_control_for

SettingsType = TypeVar("SettingsType", bound=BaseModel)
BackgroundType = TypeVar("BackgroundType", bound=BaseModel)


class MissingBackgroundDataError(RuntimeError):
    """An error raised if checking for sample without background data set."""


class ChannelBlankError(RuntimeError):
    """An error raised if a channel has no measured standard deviation.

    This is not physical and usually means the camera has not yet booted or changed
    mode fully.
    """


class BackgroundDetectAlgorithm:
    """The base class for defining background detect algorithms."""

    display_name: str = "Base Detector"

    def __init__(self, thing_server_interface: Any = None) -> None:
        """Initialise and create the lock."""
        if self.display_name == "Base Detector":
            raise NotImplementedError(
                "Do not try to use the BackgroungDetectAlgorithm directly. "
                " Use a subclass"
            )
        # super().__init__(thing_server_interface)

    @property
    def ready(self) -> bool:
        """Whether the background detector is ready."""
        raise NotImplementedError(
            "Each background detect algorithm must implement a ready property."
        )

    def image_is_sample(self, image: np.ndarray) -> tuple[bool, str]:
        """Label the current image as either background or sample.

        :returns: A tuple of the result (boolean), and explanation string. The
            explanation string is formatted so it can be added into a sentence such as
            ``An action was taken because the image is {message}.``
        """
        raise NotImplementedError(
            "Each background detect algorithm must implement an image_is_sample method."
        )

    def set_background(self, image: np.ndarray) -> None:
        """Use the input image to update the background data.

        Background data must be a Pydantic BaseModel.
        """
        raise NotImplementedError(
            "Each background detect algorithm must implement an set_background method."
        )

    @property
    def settings_ui(self) -> list[PropertyControl]:
        """A list of PropertyControl objects to create the settings in the UI."""
        raise NotImplementedError(
            "Each background detect algorithm must implement an settings_ui method."
        )


class ColourChannelDetectLUV(BackgroundDetectAlgorithm):
    """Compare images with a known background in LUV colourspace.

    This uses an LUV colour space checking only the mean and standard deviation of the
    U and V channels. The LUV colourspace as it collect colours together in a human-
    intuitive way.
    """

    display_name: str = "Colour Channel (LUV)"

    background_means: Optional[list[float]] = None
    """The mean of each channel in the colourspace."""
    background_stds: Optional[list[float]] = None
    """The standard deviation of each channel in the colourspace."""

    # These are the same as those used for ChannelDeviationLUV. More detail is
    # provided there.
    min_stds = [0.5, 0.3, 0.5]

    channel_tolerance: float = 7.0
    """Channel Tolerance

    The number of standard deviations a pixel value must be from the background mean
    to be considered sample.
    """

    min_sample_coverage: float = 25
    """Sample Coverage Required (%)

    The minimum percentage of the image that needs to be identified as sample for the
    image to be labeled as containing sample.
    """

    @property
    def settings_ui(self) -> list[PropertyControl]:
        """A list of PropertyControl objects to create the settings in the UI."""
        return [
            property_control_for(self, "channel_tolerance", label="Channel Tolerance"),
            property_control_for(
                self, "min_sample_coverage", label="Sample Coverage Required (%)"
            ),
        ]

    @property
    def ready(self) -> bool:
        """Whether the background detector is ready."""
        return self.background_means is not None and self.background_stds is not None

    def background_mask(self, image: np.ndarray) -> np.ndarray:
        """Calculate a binary image, showing whether each pixel is background.

        True is background.

        The image should be in LUV format, the output will be binary with the
        same shape in the first two dimensions.
        """
        if self.background_means is None or self.background_stds is None:
            raise RuntimeError(
                "Cannot calculated background mask if no background is set."
            )
        # The ``[1:]`` selects only the U and V channels of the image.
        # Only U and V are used as brightness (L) often changes as
        # the height of the sample changes.
        # Wrapping in ``[[ ]]`` forces the colour channels to the numpy axis 2
        # (3rd axis) so they are compared to the colour channel of each pixel.
        means = np.array([[self.background_means[1:]]])
        stds = np.array([[self.background_stds[1:]]])

        # Compare each image in the pixel with the mean and standard deviation along
        # axis to (the colour channels).
        return np.all(
            np.abs(image[:, :, 1:] - means) < stds * self.channel_tolerance,
            axis=2,
        )

    def get_sample_coverage(self, image: np.ndarray) -> float:
        """Return the percentage of the input image that is background.

        Evaluate whether it is foreground or background by comparing it to the saved
        statistics for a background image on a per-pixel basis

        :returns: A value (between 0 and 100) is the percentage of the image that is
            sample.
        """
        if self.background_means is None or self.background_stds is None:
            raise MissingBackgroundDataError(
                "Background is not set: you need to calibrate background detection."
            )
        image_luv = cv2.cvtColor(image, cv2.COLOR_RGB2LUV)
        mask = self.background_mask(image_luv)

        return float(1 - np.count_nonzero(mask) / np.prod(mask.shape)) * 100

    def image_is_sample(self, image: np.ndarray) -> tuple[bool, str]:
        """Label the current image as either background or sample.

        :returns: A tuple of the result (boolean), and explanation string. The
            explanation string is formatted so it can be added into a sentence such as
            ``An action was taken because the image is {message}.``
        """
        sample_coverage = self.get_sample_coverage(image)

        # Use bool otherwise get numpy variants of True and False.
        is_sample = bool(sample_coverage > self.min_sample_coverage)
        message = f"{sample_coverage:0.1f}% sample"
        if not is_sample:
            message = "only " + message
        return is_sample, message

    def set_background(self, image: np.ndarray) -> None:
        """Use the input image to update the background distributions."""
        image_luv = cv2.cvtColor(image, cv2.COLOR_RGB2LUV)

        mu = np.mean(image_luv, axis=(0, 1))
        std = np.std(image_luv, axis=(0, 1))

        if np.any(std == 0):
            raise ChannelBlankError("Some LUV channels have no standard deviation.")
        std = np.maximum(std, self.min_stds)

        self.background_means = mu.tolist()
        self.background_stds = std.tolist()


class ChannelDeviationLUV(BackgroundDetectAlgorithm):
    """Compare the standard deviations of the LUV channels in a grid to background data.

    Using an LUV colour space, each image is divided into an 8x8 grid of images.
    The standard deviation of each channel of each image is calculated and compared
    to the median standard deviation for a grid of background images.
    """

    display_name: str = "Channel Deviation (LUV)"

    background_stds: Optional[list[float]] = None
    """The standard deviation of each channel in the colourspace."""

    # Empirically, 0.5 seems to be approximate the standard deviation for a good image
    # in L and V. U appears to be about 60% of this value. U is about 65% of V when
    # converting white-noise in RGB into LUV (note that we are using cv2's internal
    # LUV colour space not converting to the CIELUV numbers)
    min_stds = [0.5, 0.3, 0.5]

    channel_tolerance: float = 7.0
    """Channel Tolerance

    The number of standard deviations a pixel value must be from the background mean
    to be considered sample.
    """

    min_sample_coverage: float = 25
    """Sample Coverage Required (%)

    The minimum percentage of the image that needs to be identified as sample for the
    image to be labeled as containing sample.
    """

    @property
    def settings_ui(self) -> list[PropertyControl]:
        """A list of PropertyControl objects to create the settings in the UI."""
        return [
            property_control_for(self, "channel_tolerance", label="Channel Tolerance"),
            property_control_for(
                self, "min_sample_coverage", label="Sample Coverage Required (%)"
            ),
        ]

    @property
    def ready(self) -> bool:
        """Whether the background detector is ready."""
        return self.background_stds is not None

    def get_sample_coverage(self, image: np.ndarray) -> float:
        """Return the percentage of the input image that is background.

        Evaluate whether it is foreground or background by comparing the standard
        deviations of an 8x8 grid of sub-images to the median standard deviation
        from a background image.

        :returns: A value (between 0 and 100) that is the percentage of the image that is
            sample.
        """
        if self.background_stds is None:
            raise MissingBackgroundDataError(
                "Background is not set: you need to calibrate background detection."
            )
        image_luv = cv2.cvtColor(image, cv2.COLOR_RGB2LUV)
        stds = _chunked_stds(image_luv, 8, 8)

        bg_stds = self.background_stds
        l_cut = bg_stds[0] * self.channel_tolerance
        u_cut = bg_stds[1] * self.channel_tolerance
        v_cut = bg_stds[2] * self.channel_tolerance

        populated_regions = (
            (stds[:, :, 0] > l_cut) | (stds[:, :, 1] > u_cut) | (stds[:, :, 2] > v_cut)
        )

        return float(100 * np.sum(populated_regions) / 64)

    def image_is_sample(self, image: np.ndarray) -> tuple[bool, str]:
        """Label the current image as either background or sample.

        :returns: A tuple of the result (boolean), and explanation string. The
            explanation string is formatted so it can be added into a sentence such as
            ``An action was taken because the image is {message}.``
        """
        sample_coverage = self.get_sample_coverage(image)

        # Use bool otherwise get numpy variants of True and False.
        is_sample = bool(sample_coverage > self.min_sample_coverage)
        message = f"{sample_coverage:0.1f}% sample"
        if not is_sample:
            message = "only " + message
        return is_sample, message

    def set_background(self, image: np.ndarray) -> None:
        """Use the input image to update the background distributions."""
        image_luv = cv2.cvtColor(image, cv2.COLOR_RGB2LUV)
        c_stds = _chunked_stds(image_luv, 8, 8)
        channel_blank = np.all(c_stds == 0, axis=(0, 1))
        if np.any(channel_blank):
            raise ChannelBlankError("Some LUV channels have no standard devaition.")

        std = np.median(c_stds, axis=(0, 1))
        std = np.maximum(std, self.min_stds)
        self.background_stds = std.tolist()


def _chunked_stds(img: np.ndarray, n_rows: int = 8, n_cols: int = 8) -> np.ndarray:
    """Split image into a grid and calculate std of each channel in each chunk.

    :param img: The image to analyse
    :param n_rows: The number of rows in the grid
    :param n_cols: The number of cols in the grid
    :return: A numpy array of the grid of standard deviations.
    """
    h, w = img.shape[:2]
    row_height = h // n_rows
    col_width = w // n_cols
    out = np.zeros((n_rows, n_cols, 3))
    for i in range(n_rows):
        for j in range(n_cols):
            chunk = img[
                i * row_height : (i + 1) * row_height,
                j * col_width : (j + 1) * col_width,
            ]
            out[i, j, :] = np.std(chunk, axis=(0, 1))
    return out
