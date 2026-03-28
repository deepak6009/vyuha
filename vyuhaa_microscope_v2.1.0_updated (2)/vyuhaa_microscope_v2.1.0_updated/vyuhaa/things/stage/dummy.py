"""Functionality for mimicking a stage during simulation and testing."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Optional

# import labthings_fastapi as lt

from .base import BaseStage


class DummyStage(BaseStage):
    """A dummy stage for testing purposes.

    This stage should work similarly to a Sangaboard stage, but without any
    hardware attached.
    """

    def __init__(
        self,
        thing_server_interface: Any = None,
        step_time: float = 0.001,
        **kwargs: Any,
    ) -> None:
        """Initialise the Dummy stage, setting the step_time to adjust the speed.

        :param step_time: The time in seconds per "motor" step. The default of 0.001
            works well for the live simulation. For unit testing it is very slow
            so the speed can be increased. Increasing it too far is problematic if
            also doing computationally heavy tasks like simulated image blurring.
        """
        super().__init__(thing_server_interface, **kwargs)
        self.step_time = step_time
        self.instantaneous_position = self._hardware_position

    def __enter__(self) -> "DummyStage":
        """Register the stage position when the Thing context manager is opened."""
        self.instantaneous_position = self._hardware_position
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Nothing to do when the Thing context manager is closed."""

    axis_inverted: dict[str, bool] = {"x": True, "y": False, "z": False}
    """Used to convert coordinates between the program frame and the hardware frame."""

    def _hardware_move_relative(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a relative move. Keyword arguments should be axis names."""
        displacement = [kwargs.get(k, 0) for k in self.axis_names]
        self.moving = True
        try:
            fraction_complete = 0.0
            dt = self.step_time
            max_displacement = max(abs(v) for v in displacement)
            start_time = time.time()
            while time.time() - start_time < dt * max_displacement:
                if block_cancellation:
                    time.sleep(dt)
                else:
                    time.sleep(dt)
                fraction_complete = (time.time() - start_time) / (dt * max_displacement)
                self.instantaneous_position = {
                    ax: self._hardware_position[ax] + int(fraction_complete * disp)
                    for ax, disp in zip(self.axis_names, displacement, strict=True)
                }
            fraction_complete = 1.0
        except Exception as e:
            # Simple exception handling for removal
            raise e
        finally:
            self.moving = False
            self._hardware_position = {
                ax: self._hardware_position[ax] + int(fraction_complete * disp)
                for ax, disp in zip(self.axis_names, displacement, strict=True)
            }
            self.instantaneous_position = self._hardware_position

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make an absolute move. Keyword arguments should be axis names."""
        displacement = {
            axis: int(pos) - self._hardware_position[axis]
            for axis, pos in kwargs.items()
            if axis in self.axis_names
        }
        self._hardware_move_relative(
            block_cancellation=block_cancellation, **displacement
        )

    def set_zero_position(self) -> None:
        """Make the current position zero in all axes.

        This action does not move the stage, but resets the position to zero.
        It is intended for use after manually or automatically recentring the
        stage.
        """
        self._hardware_position = dict.fromkeys(self.axis_names, 0)
        self.instantaneous_position = self._hardware_position
