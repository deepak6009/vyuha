"""Provide a LabThings-FastAPI interface to the Sangaboard motor controller."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from copy import copy
from types import TracebackType
from typing import Any, Iterator, Literal, Optional

import semver

# import labthings_fastapi as lt
from typing import Any
import sangaboard

from . import BaseStage

REQUIRED_VERSION = semver.Version.parse("1.0.0")
RECOMMENDED_VERSION = semver.Version.parse("1.0.4")


class SangaboardThing(BaseStage):
    """A Thing to manage a Sangaboard motor controller.

    Internally, this uses the ``pysangaboard`` package from PyPi. This imports
    as ``sangaboard``. As ``pysangaboard`` does not support some features added
    to the Sangaboard firmware v1 (LED flashing, aborting moves, etc) this
    functionality is accessed by directly querying the serial interface.
    """

    def __init__(
        self,
        thing_server_interface: Any = None,
        port: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Initialise SangaboardThing.

        Initialise the "Thing", but do not initialise an underlying
        ``Sangaboard`` object from ``pysangaboard`` until the Thing context
        manager is started.

        :param port: The serial port for the Sangaboard. Optional, this is used
            to stop the Sangaboard object querying available devices.
        :param ``**kwargs``: Any other keyword arguments to be passed to the
            Sangaboard class

        """
        self.sangaboard_kwargs = copy(kwargs)
        self.sangaboard_kwargs["port"] = port
        self._sangaboard_lock = threading.RLock()
        # super().__init__(thing_server_interface, **kwargs)
        super().__init__()
        # self.thing_server_interface = thing_server_interface

    def __enter__(self) -> "Sangaboard":
        """Connect to the sangaboard when the Thing context manager is opened."""
        self._sangaboard = sangaboard.Sangaboard(**self.sangaboard_kwargs)
        with self.sangaboard() as sb:
            sb.query("blocking_moves false")
        self.check_firmware()
        self.update_position()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Close the sangaboard connection when the Thing context manager is closed."""
        with self.sangaboard() as sb:
            sb.close()

    @contextmanager
    def sangaboard(self) -> Iterator[sangaboard.Sangaboard]:
        """Return the wrapped ``sangaboard.Sangaboard`` instance.

        This is protected by a ``threading.RLock``, which may change in future.
        """
        with self._sangaboard_lock:
            yield self._sangaboard

    axis_inverted: dict[str, bool] = {"x": True, "y": False, "z": True}
    """Used to convert coordinates between the program frame and the hardware frame."""

    def update_position(self) -> None:
        """Read position from the stage and set the corresponding property."""
        with self.sangaboard() as sb:
            self._hardware_position = dict(
                zip(self.axis_names, sb.position, strict=True)
            )

    def check_firmware(self) -> None:
        """Error/warn if firmware doesn't meet requirements/recommendations.

        Raise a Runtime Error if the version is below REQUIRED_VERSION

        Log a warning if the version is below RECOMMENDED_VERSION
        """
        with self.sangaboard() as sb:
            # This will raise a ValueError is if the firmware version cannot be parsed
            # this error will stop the microscope booting as we cannot ensure valid
            # firmware.
            version = semver.Version.parse(sb.firmware_version)

            # Raise an error if version is below required
            if version < REQUIRED_VERSION:
                raise RuntimeError(
                    f"Sangaboard firmware version {version} is below the required "
                    f"{REQUIRED_VERSION}."
                )

            # Warn if version is below recommended
            if version < RECOMMENDED_VERSION:
                self.logger.warning(
                    f"Sangaboard firmware version {version} is below the recommended "
                    f"{RECOMMENDED_VERSION}."
                )

    def _hardware_move_relative(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a relative move in the coordinate system used by the sangaboard."""
        displacement = [kwargs.get(axis, 0) for axis in self.axis_names]
        with self.sangaboard() as sb:
            self.moving = True
            try:
                sb.move_rel(displacement)
                if block_cancellation:
                    sb.query("notify_on_stop")
                else:
                while sb.query("moving?") == "true":
                        time.sleep(0.1)
            except Exception as e:
                # If the move has been cancelled, stop it but don't handle the exception.
                # We need the exception to propagate in order to stop any calling tasks,
                # and to mark the invocation as "cancelled" rather than stopped.
                sb.query("stop")
                raise e
            finally:
                self.moving = False
                self.update_position()

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a absolute move in the coordinate system used by the sangaboard."""
        with self.sangaboard():
            self.update_position()
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
        with self.sangaboard() as sb:
            sb.zero_position()
        self.update_position()

    def flash_led(
        self,
        number_of_flashes: int = 10,
        dt: float = 0.5,
        led_channel: Literal["cc"] = "cc",
    ) -> None:
        """Flash the LED to identify the board.

        This is intended to be useful in situations where there are multiple
        Sangaboards in use, and it is necessary to identify which one is
        being addressed.
        """
        led_command = f"led_{led_channel}"
        with self.sangaboard() as sb:
            return_value = sb.query(f"{led_command}?")
            if not return_value.startswith("CC LED:"):
                raise IOError("The sangaboard does not support LED control")

            # Reading and setting LED brightness suffers from repeated reads and writes
            # decreasing the value. Rather than use the value the code warns that the value
            # cannot be used.
            intended_brightness = float(return_value[7:])
            on_brightness = 0.32
            self.logger.warning(
                "Brightness control is not yet implemented. Desired brightness: "
                f"{intended_brightness}. Set brightness: {on_brightness}"
            )
            for _i in range(number_of_flashes):
                sb.query(f"{led_command} 0")
                time.sleep(dt)
                sb.query(f"{led_command} {on_brightness}")
                time.sleep(dt)
