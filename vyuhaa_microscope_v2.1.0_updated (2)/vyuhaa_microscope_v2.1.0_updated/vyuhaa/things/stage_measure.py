"""File contains all the functions used to measure the range of motion.

The range of motion is measured by first taking 5 'medium' sized steps which gives
enough positions to predict future z positions. Next, one 'big' step is taken followed
by 3 'small' steps to test that the stage is still moving as expected and has not
reached the edge. Once the edge has been found and to account for the possibility that
the edge was reached during a "big" step, the stage is moved in a sequence of steps
in the opposite direction until motion is detected. This is position is taken as the
true final position.

Throughout the test, parasitic motion (motion in the axis not being measured)
is tracked and an error is raised if it exceeds a minimum amount. Currently
this is 10% of the expected motion is the measured axis.
"""

import time
from copy import copy
from threading import Lock
from typing import Any, Literal, Mapping, Optional, overload

import numpy as np

# import labthings_fastapi as lt
from camera_stage_mapping import fft_image_tracking

# Things
from .autofocus import AutofocusThing
from .camera import BaseCamera
from .camera_stage_mapping import CameraStageMapper
from .stage import BaseStage

## Size of movement in percentage of field of view
SMALL_STEP = 20
MEDIUM_STEP = 50
BIG_STEP = 200

PARASITIC_MOTION_TOL = 0.1
DETECT_MOTION_TOL = 0.65


class RomDataTracker:
    """Class for tracking range of motion data.

    The attributes storing data are:

    * ``stage_coords`` A list of the the stage coordinates at each measurement location
    * ``displacements`` A list of the displacement calculated after each movement
    """

    def __init__(self) -> None:
        """Define useful data tracked throughout test."""
        self.stage_coords: list[Mapping[str, int]] = []
        self.offsets: list[Mapping[str, float]] = []

    def record_movement(
        self, current_pos: Mapping[str, int], offset: Mapping[str, float]
    ) -> None:
        """Record the current position and the measured offset of the last move."""
        self.stage_coords.append(current_pos)
        self.offsets.append(offset)

    @property
    def final_position(self) -> Mapping[str, int]:
        """The last stage coordinate recorded."""
        return copy(self.stage_coords[-1])

    def fit_axis(self, axis: Literal["x", "y"]) -> np.poly1d:
        """Quadratic fit stage z against x or y and return poly1d of fit.

        Note that this considers only one of x and y, and ignores the other coordinate. If
        this function is used on moves where both x and y are changing, it will give misleading
        results.
        """
        lateral_positions = [i[axis] for i in self.stage_coords]
        z_positions = [i["z"] for i in self.stage_coords]
        fit_params = np.polyfit(lateral_positions, z_positions, 2)
        return np.poly1d(fit_params)

    def predict_z_displacement(
        self,
        axis: Literal["x", "y"],
        stage_movement: Mapping[str, int],
        stage_position: Mapping[str, int],
    ) -> int:
        """Predict the z-displacement needed for a given movement.

        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :param stage_movement: The movement to be performed in stage coordinates.
        :param stage_position: The current stage position in stage coordinates.
        :return: The predicted relative z displacement needed to stay in focus.
        """
        fit_func = self.fit_axis(axis)
        z_dest = fit_func(stage_position[axis] + stage_movement[axis])

        return int(z_dest - stage_position["z"])

    def find_turning_point(self, axis: Literal["x", "y"]) -> Mapping[str, int]:
        """Find the turing point from the recorded coordinates."""
        fit_func = self.fit_axis(axis)
        turning_loc = fit_func.deriv().roots[0]
        turning_z = fit_func(turning_loc)

        # As we only move in 1 direction, pull the other axis from the coords.
        other_axis = "x" if axis == "y" else "y"
        other_coord = self.stage_coords[-1][other_axis]
        return {axis: int(turning_loc), other_axis: other_coord, "z": int(turning_z)}


class ParasiticMotionError(Exception):
    """Custom exception raised when parasitic motion is detected.

    Parasitic motion is when motion in the direction not being measured
    is too high.
    """


class RangeofMotionThing:
    """A class used to measure the range of motion of the stage in X and Y."""

    _autofocus: AutofocusThing = None
    _cam: BaseCamera = None
    _csm: CameraStageMapper = None
    _stage: BaseStage = None

    calibrated_range: Optional[tuple[int, int]] = None

    def __init__(self, thing_server_interface: Any = None) -> None:
        """Initialise and create the lock."""
        # super().__init__(thing_server_interface)
        self._lock = Lock()
        self._stream_resolution: Optional[tuple[int, int]] = None
        self._rom_data = RomDataTracker()

    def perform_rom_test(self) -> Mapping[str, Any]:
        """Measures the range of motion of the stage across the x and y axes.

        :return: Results dictionary separated into keys of each axis and direction.
        """
        got_lock = self._lock.acquire(blocking=False)
        if not got_lock:
            raise RuntimeError("Trying to run ROM test when a test is already running.")

        try:
            self.logger.info(
                "Using the stage to measure the Range of Motion. "
                "Please ensure you are using a sample that covers the whole range of "
                "motion. This should be approximately 12 x 12 mm."
            )
            start_time = time.time()

            self._set_stream_resolution()

            # The total range for the two axes under test
            step_range = []

            # Strictly typed definitions to iterate over for MyPys sake.
            axes: tuple[Literal["x"], Literal["y"]] = ("x", "y")
            directions: tuple[Literal[1], Literal[-1]] = (1, -1)

            for axis in axes:
                # The final position of the axis under test in the given direction
                axis_limits = []
                for axis_dir in directions:
                    # Create a new tracker at start of measurement.
                    self._rom_data = RomDataTracker()
                    self._move_until_edge(axis=axis, direction=axis_dir)
                    # Append final position from tracker
                    axis_limits.append(self._rom_data.final_position[axis])
                # Calculate step range.
                step_range.append(abs(axis_limits[0] - axis_limits[1]))

            total_time = time.time() - start_time
            self.logger.info(
                f"Range of motion is {step_range[0]} x {step_range[1]} steps"
            )
            self.calibrated_range = (step_range[0], step_range[1])
        finally:
            self._lock.release()
        return {
            "Time": total_time,
            "CSM Matrix": self._csm.image_to_stage_displacement_matrix,
            "Step Range": step_range,
        }

    def perform_recentre(self) -> None:
        """Measures the curvature of the motion to centre x and y axes."""
        got_lock = self._lock.acquire(blocking=False)
        if not got_lock:
            raise RuntimeError("Trying to run recentre when a test is already running.")

        try:
            self.logger.info("Recentring the stage.")

            self._set_stream_resolution()

            # Strictly typed definitions to iterate over for MyPys sake.
            axes: tuple[Literal["x"], Literal["y"]] = ("x", "y")

            for axis in axes:
                self._recentre_axis(axis)

            centre = self._stage.position
            centre_str = f"({centre['x']}, {centre['y']})"
            self.logger.info(f"Centre is estimated at {centre_str}.")
            # Set the central position to (0,0,0)
            self._stage.set_zero_position()
            self.logger.info("Position reset to (0, 0, 0).")

        finally:
            self._lock.release()

    def _set_stream_resolution(self) -> None:
        """Set the self._stream_resolution attribute by reading camera."""
        stream_shape = self._cam.grab_as_array().shape
        # Swap axes as numpy is [y, x]
        self._stream_resolution = (stream_shape[1], stream_shape[0])

    def _move_until_edge(
        self, axis: Literal["x", "y"], direction: Literal[1, -1]
    ) -> None:
        """Move in one direction until movement per step decreases significantly.

        This should move until the edge of the stage. Once the edge is reached there
        will be some movement as there is no hard stop, but it will reduce
        significantly.

        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :param direction: The direction which is being measured. This must be 1 or -1.
        :return: Results dictionary containing stage positions,
           correlations and the final position.
        """
        # Start by performing an autofocus and recording starting position
        self._autofocus.looping_autofocus(dz=1000)
        starting_position = self._stage.position
        self._rom_data.stage_coords.append(starting_position)

        try:
            dir_str = "positive" if direction == 1 else "negative"
            self.logger.info(f"Beginning the {axis}-axis in the {dir_str} direction")

            self.logger.info("Moving the stage in 5 medium sized steps.")
            self._moves_for_z_prediction(axis=axis, direction=direction)

            still_moving = True
            # Loop taking 1 big step followed by 3 small steps to check if the
            # stage is moving as expected or has reached end of range of motion.
            # Stop once end of range of motion is detected.
            while still_moving:
                self._big_z_corrected_movement(axis=axis, direction=direction)
                # Autofocus and record position
                self._autofocus.looping_autofocus(dz=800)
                self._rom_data.stage_coords.append(self._stage.position)

                # Perform small moves to check stage is still moving.
                still_moving = self._stage_still_moves(axis=axis, direction=direction)

            # Stage may have crashed during a large move. Move stage back until motion
            # is detected
            self.logger.info("Moving stage back until motion is detect")
            self._move_back_until_motion_detected(axis=axis, direction=direction)

            # Replace final position.
            self._rom_data.stage_coords[-1] = self._stage.position

        finally:
            self._stage.move_absolute(**starting_position, block_cancellation=True)

    def _recentre_axis(self, axis: Literal["x", "y"]) -> None:
        """Recentre a single axis.

        :param axis: The axis to recentre
        """
        self.logger.info(f"Finding centre in {axis}.")
        # A new tracker for this axis.
        self._rom_data = RomDataTracker()
        # Direction is in image coords, initial assumption is that centre is at (0,0).
        direction = self._img_dir_from_stage_coords({"x": 0, "y": 0, "z": 0}, axis)

        i = 0
        while True:
            i += 1
            # Find z then make a number of moves (autofocussing and logging position)
            # 5 moves initially (2 subsequently).
            self._autofocus.looping_autofocus(dz=1000)
            self._rom_data.stage_coords.append(self._stage.position)
            self._moves_for_z_prediction(
                axis=axis,
                direction=direction,
                n_moves=5 if i == 1 else 2,
            )

            centred, direction = self._recentre_decision(axis)
            if centred:
                break

            # If still going at iteration 9 exit
            if i > 9:
                raise RuntimeError(f"Couldn't find centre of {axis}-axis")

            # Make a big z-corrected move towards estimate of centre.
            self._big_z_corrected_movement(axis, direction)

    def _recentre_decision(
        self, axis: Literal["x", "y"]
    ) -> tuple[bool, Literal[1, -1]]:
        """Decide what to do next during recentreing.

        The algorithm here:

        * Estimate turning point location
        * Check if distance to point is further than a "BIG_STEP"
        * If smaller, move to estimated centre, and return that it is centred
        * If larger, return that it is not centred, and the direction to move in.

        :param axis: The axis to recentre
        :return: A tuple. The first value is True for "the stage is now centred" and
            False for "the stage is not centred". The second value is the direction to
            move in, this only has meaning if the first value is False.
        """
        estimate = self._rom_data.find_turning_point(axis=axis)
        img_perc = self._distance_in_img_percentage(estimate, axis)

        # if the distance is less than 1 big step away then move to it and exit
        if abs(img_perc) < BIG_STEP:
            self.logger.info(f"Estimated centre of {axis}-axis is {estimate[axis]}")
            self._stage.move_absolute(**estimate, block_cancellation=False)
            # Note the second return, the direction, is meaningless here.
            return True, 1
        self.logger.info(
            f"Estimated centre {abs(img_perc):.0f}% of a field of view away, "
            "that is too far to move in one move."
        )
        # Else calculate the desired direction in image coordinates.
        direction = self._img_dir_from_stage_coords(estimate, axis)
        return False, direction

    def _img_percentage_to_img_coords(
        self, fov_perc: int, axis: Literal["x", "y"]
    ) -> float:
        """For a given image percentage and axis return the distance in img coords.

        :param fov_perc: The percentage of field of view the stage should move by.
        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :return: Distance in image coordinates (pixels)
        """
        if self._stream_resolution is None:
            raise RuntimeError(
                "Stream resolution must be set before generating movement in coords"
            )

        img_index = 0 if axis == "x" else 1
        return (fov_perc / 100) * self._stream_resolution[img_index]

    def _img_dir_from_stage_coords(
        self, target: Mapping[str, int], axis: Literal["x", "y"]
    ) -> Literal[1, -1]:
        """For a target location in stage coords, return the direction in image coordinates.

        :param target: The target poisiton in stage coordinates
        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :return: Direction to move in image coordinates.
        """
        target_im_coords = self._csm.convert_stage_to_image_coordinates(**target)
        current_loc = self._stage.position
        current_loc_im_coords = self._csm.convert_stage_to_image_coordinates(
            **current_loc
        )
        return -1 if target_im_coords[axis] < current_loc_im_coords[axis] else 1

    def _distance_in_img_percentage(
        self, target: Mapping[str, int], axis: Literal["x", "y"]
    ) -> float:
        """For a target location in stage coords return the distance in percentage of FOV.

        :param target: The target poisiton in stage coordinates
        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :return: Percentage of field of view the stage should move by.
        """
        if self._stream_resolution is None:
            raise RuntimeError(
                "Stream resolution must be set before converting coords to percentage"
            )

        current_loc = self._stage.position
        move_stage = {key: target[key] - current_loc[key] for key in target}
        move_img = self._csm.convert_stage_to_image_coordinates(**move_stage)

        img_index = 0 if axis == "x" else 1
        return (move_img[axis] / self._stream_resolution[img_index]) * 100

    def _movement_in_img_coords(
        self,
        fov_perc: int,
        axis: Literal["x", "y"],
        direction: Literal[1, -1],
    ) -> Mapping[str, float]:
        """Return the dictionary for a move in image coordinates.

        This dictionary can be passed directly to csm.move_in_image_coordinates

        :param fov_perc: The percentage of field of view the stage should move by.
        :param axis: The resolution of the stream from the camera.
        :param direction: The direction the stage moves.

        :return: The movement size in image coordinates
        """
        distance = self._img_percentage_to_img_coords(fov_perc=fov_perc, axis=axis)
        if axis == "x":
            return {"x": distance * direction, "y": 0}
        return {"x": 0, "y": distance * direction}

    def _moves_for_z_prediction(
        self, axis: Literal["x", "y"], direction: Literal[1, -1], n_moves: int = 5
    ) -> None:
        """Perform medium sized moves with autofocus for z feed-forward.

        z-feed forward allows prediction of the z-position as the stage moves. For the
        feed forward calculation to work an initial number of measurements must be
        taken.

        :param direction: The direction the stage moves.
        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :param n_moves: Number of moves to make. Default is 5 which is enough for an
            initial z estimate.
        """
        movement = self._movement_in_img_coords(
            fov_perc=MEDIUM_STEP, axis=axis, direction=direction
        )
        for _loop in range(n_moves):
            offset = self._move_and_measure(movement=movement)

            self._rom_data.record_movement(self._stage.position, offset)
            if _parasitic_motion_detected(movement, offset):
                raise ParasiticMotionError(
                    "Parasitic motion detected during images to calculate z-curvature. "
                    "This may indicate you have started at the end of the range of "
                    "travel, or that camera stage mapping is poorly calibrated."
                )

    def _big_z_corrected_movement(
        self, axis: Literal["x", "y"], direction: Literal[1, -1]
    ) -> None:
        """Take one big move with feed-forward z-correction.

        The size is defined by the BIG_STEP constant.

        :param axis: The axis to move in.
        :param direction: The direction to move in.
        """
        movement = self._movement_in_img_coords(
            fov_perc=BIG_STEP, axis=axis, direction=direction
        )
        # Convert to stage coordinates
        stage_movement = self._csm.convert_image_to_stage_coordinates(**movement)

        z_disp = self._rom_data.predict_z_displacement(
            axis=axis,
            stage_movement=stage_movement,
            stage_position=self._stage.position,
        )
        self._stage.move_relative(z=z_disp)
        self._csm.move_in_image_coordinates(**movement)

    def _stage_still_moves(
        self,
        axis: Literal["x", "y"],
        direction: Literal[1, -1],
    ) -> bool:
        """Carry out 3 small moves in a given direction and axis.

        An image is taken before and after each move to check the stage has moved as
        far as it should. If the offset (calculated by cross correlation) is less than
        expected, 3 attempts are made to refocus the image to ensure that image quality
        is not causing the offset to mistakenly be reported as a low value. If the
        calculated offset is still too low then this is taken as an indication that the
        edge has been found.

        :param axis: The axis which is being measured. This must be 'x' or 'y'.
        :param direction: The direction the stage moves.
        """
        movement = self._movement_in_img_coords(
            fov_perc=SMALL_STEP, axis=axis, direction=direction
        )
        abs_min_offset = abs(movement[axis] * DETECT_MOTION_TOL)

        for _loop in range(3):
            offset = self._move_and_measure(
                movement=movement,
                # Don't autofocus initially
                perform_autofocus=False,
                # But retry focus 3 times if detected motion is too small or parasitic
                max_autofocus_repeats=3,
                abs_min_offset=abs_min_offset,
            )
            parasitic_motion = _parasitic_motion_detected(movement, offset)
            self.logger.info(f"Offset measured as {offset[axis]}")
            self._rom_data.record_movement(self._stage.position, offset)

            if abs(offset[axis]) < abs_min_offset or parasitic_motion:
                # If the offset is below the abs min offset, or significant
                # parasitic motion is detected then the edge has been found.
                self.logger.info("Edge has been found.")
                return False
        # If we reached here the stage is still moving fine
        return True

    def _move_back_until_motion_detected(
        self,
        axis: Literal["x", "y"],
        direction: Literal[1, -1],
    ) -> None:
        """Move the stage against the direction of test unil motion is detected.

        In the case that the stage has reached the end of the motion, this method moves
        the motor in the opposite direction until reliable motion is detected.

        :param axis: The axis in which the stage is moving. This must be 'x' or 'y'.
        :param direction: The direction in which the stage was moving during the test,
            this method will move in the opposite direction.
        """
        movement = self._movement_in_img_coords(
            fov_perc=SMALL_STEP, axis=axis, direction=-direction
        )

        max_moves = int(1.5 * BIG_STEP / SMALL_STEP)
        # minimum number of pixels for motion to be detected as reliable
        motion_minimum = abs(movement[axis] * DETECT_MOTION_TOL)

        for i in range(max_moves):
            # Increment movement
            self.logger.info(f"Testing with step size {movement[axis]}")

            # Run an autofocus every 3 steps
            run_autofocus = i % 3 == 2
            offset = self._move_and_measure(
                movement=movement, perform_autofocus=run_autofocus
            )

            self.logger.info(f"Offset measured as {abs(offset[axis])}")
            if abs(offset[axis]) > motion_minimum:
                self.logger.info("Motion detected.")
                return
        # If this point is reached then motion is never detected
        raise RuntimeError("Cannot detect motion again after reaching end of range.")

    def _move_and_measure(
        self,
        movement: Mapping[str, float],
        perform_autofocus: bool = True,
        max_autofocus_repeats: int = 0,
        abs_min_offset: float = 0.0,
    ) -> Mapping[str, float]:
        """Move the stage and measure the offset between the two positions.

        :param movement: A dictionary containing the distance to move in image coords.
        :param perform_autofocus: Set to False to disable autofocus after move.
            Default is  True
        :param max_autofocus_repeats: The number of times to repeat the focus if the
            detected (on-axis) offset is below ``abs_min_offset``. This will only work
            if ``abs_min_offset`` is also set
        :param abs_min_offset: The absolute minimum (on-axis) offset, under which the
            autofocus is repeated.
        :return: The calculated offset from cross correlation.
        """
        if max_autofocus_repeats > 0 and abs_min_offset <= 0:
            raise ValueError(
                "abs_min_offset must be positive if max_autofocus_repeats > 0."
            )

        # Take image before move
        before_img = self._cam.grab_as_array()
        # Move and autofocus if required
        self._csm.move_in_image_coordinates(**movement)
        if perform_autofocus:
            self._autofocus.looping_autofocus(dz=800)
        # Take image and calculate offset
        offset = self._offset_from(before_img)

        if max_autofocus_repeats > 0:
            axis = _axis_from_movement_dict(movement)
            af_repeats = 0
            parasitic_motion = _parasitic_motion_detected(movement, offset)
            while abs(offset[axis]) < abs_min_offset or parasitic_motion:
                af_repeats += 1
                self.logger.info(
                    f"Motion not detected. Refocusing to check. Attempt {af_repeats}/3."
                )
                self._autofocus.looping_autofocus(dz=800)
                # Re-take image and calculate offset
                offset = self._offset_from(before_img)
                parasitic_motion = _parasitic_motion_detected(movement, offset)
                if af_repeats >= max_autofocus_repeats:
                    # break if the maximum number of tries are exceeded.
                    break

        return offset

    def _offset_from(self, before_img: np.ndarray) -> Mapping[str, float]:
        """Take an image and calculate the offset from an input image.

        :param before_img: The image the offset should be calculated with respect to.
        :return: The calculated offset as a dictionary in pixels
        """
        is_sample, _bg_message = self._cam.image_is_sample()
        if not is_sample:
            raise RuntimeError(
                "No sample detected. Sample must be densely featured and cover the "
                "whole range of motion."
            )
        after_img = self._cam.grab_as_array()
        offset = fft_image_tracking.displacement_between_images(
            image_0=before_img,
            image_1=after_img,
            sigma=10,
            fractional_threshold=0.1,
            pad=True,
        )
        return {"x": offset[1], "y": offset[0]}


# The number of return arguments depends on if return_other is True or False
# let MyPy know with overloads typed to Literal[False] and Literal[True].
@overload
def _axis_from_movement_dict(
    movement: Mapping[str, float], return_other: Literal[False]
) -> str: ...
@overload
def _axis_from_movement_dict(
    movement: Mapping[str, float], return_other: Literal[True]
) -> tuple[str, str]: ...


# Overload the case where return_other is not specified, this is needed
# because MyPy doesn't see that return other's default is `False` and use
# the `Literal[False]` overload.
@overload
def _axis_from_movement_dict(movement: Mapping[str, float]) -> str: ...


# Finally The function.
def _axis_from_movement_dict(
    movement: Mapping[str, float], return_other: bool = False
) -> str | tuple[str, str]:
    """Return the axis that a given movement dictionary moves in.

    For example: ``_axis_from_movement_dict({"x": 10, "y":0})`` will return ``x``.

    :param movement: The movement dictionary.
    :param return_other: If True return a tuple with the first value being the movement
        axis and the second being the other axis. Default is False
    :return: The movement axis (and optionally the other axis) as strings.
    """
    # Not exclusive or, this errors if both axes are zero or neither axes are zero.
    if not ((movement["x"] == 0) ^ (movement["y"] == 0)):
        raise ValueError("Either x or y movement should be zero, but not both.")

    if return_other:
        return ("y", "x") if movement["x"] == 0 else ("x", "y")
    return "y" if movement["x"] == 0 else "x"


def _parasitic_motion_detected(
    movement: Mapping[str, float], offset: Mapping[str, float]
) -> bool:
    """Compare desired movement to measured offset, report if parasitic motion was detected.

    :param movement: The movement dictionary in image coordinates.
    :param offset: The offset calculated from correlation.
    :return: True if too much parasitic motion is detects. False if movement is as
        expected.
    """
    movement_axis, other_axis = _axis_from_movement_dict(movement, return_other=True)
    parasitic_motion = abs(offset[other_axis])
    max_parasitic_motion = abs(movement[movement_axis] * PARASITIC_MOTION_TOL)
    return parasitic_motion > max_parasitic_motion
