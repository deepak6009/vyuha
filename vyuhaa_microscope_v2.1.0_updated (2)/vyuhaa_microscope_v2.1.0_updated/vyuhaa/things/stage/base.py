"""Base classes for Vyuhaa translation stages."""

from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any, Literal, overload

class RedefinedBaseMovementError(RuntimeError):
    """The subclass of BaseStage has overridden ``move_relative`` or ``move_absolute``."""

class BaseStage:
    """A base stage class for Vyuhaa translation stages."""

    _axis_names = ("x", "y", "z")

    def __init__(self, thing_server_interface: Any = None) -> None:
        """Initialise the stage."""
        self._hardware_position = dict.fromkeys(self._axis_names, 0)

        # This must be the last thing the function does in case it is caught in a try.
        if (
            self.__class__.move_relative is not BaseStage.move_relative
            or self.__class__.move_absolute is not BaseStage.move_absolute
        ):
            raise RedefinedBaseMovementError(
                "move_relative and/or move_absolute has been overridden. This may "
                "cause issues as the base methods implement converting from program "
                "coordinates to hardware coordinates. Consider overriding "
                "_hardware_move_relative and/or _hardware_move_absolute instead."
            )

    @property
    def axis_names(self) -> Sequence[str]:
        """The names of the stage's axes, in order."""
        return self._axis_names

    @property
    def position(self) -> Mapping[str, int]:
        """Current position of the stage."""
        return self._apply_axis_direction(self._hardware_position)

    moving: bool = False
    """Whether the stage is in motion."""

    axis_inverted: dict[str, bool] = {"x": False, "y": False, "z": False}
    """Used to convert coordinates between the program frame and the hardware frame."""

    @overload
    def _apply_axis_direction(self, position: list[int] | tuple[int]) -> list[int]: ...

    @overload
    def _apply_axis_direction(
        self, position: Mapping[str, int]
    ) -> Mapping[str, int]: ...

    def _apply_axis_direction(
        self, position: list[int] | tuple[int] | Mapping[str, int]
    ) -> list[int] | Mapping[str, int]:
        if isinstance(position, (list, tuple)):
            return [
                -int(pos) if inverted else int(pos)
                for pos, inverted in zip(
                    position, self.axis_inverted.values(), strict=True
                )
            ]
        if isinstance(position, Mapping):
            try:
                return {
                    ax: -int(position[ax])
                    if self.axis_inverted[ax]
                    else int(position[ax])
                    for ax in position
                }
            except KeyError as e:
                raise KeyError(
                    f"One or more axis in {position.keys()} is not defined."
                ) from e
        raise TypeError(
            "Position must be a sequence of positions or a mapping from axis to position."
        )

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Summary metadata describing the current state of the stage."""
        return {"position": self.position}

    def invert_axis_direction(self, axis: Literal["x", "y", "z"]) -> None:
        """Invert the direction setting of the given axis."""
        direction = self.axis_inverted
        try:
            direction[axis] = not direction[axis]
        except KeyError as e:
            raise KeyError(f"The axis {axis} is not defined.") from e
        self.axis_inverted = direction

    def move_relative(self, block_cancellation: bool = False, **kwargs: int) -> None:
        """Make a relative move. Keyword arguments should be axis names."""
        self._hardware_move_relative(
            block_cancellation=block_cancellation,
            **self._apply_axis_direction(kwargs),
        )

    def _hardware_move_relative(
        self, block_cancellation: bool = False, **kwargs: int
    ) -> None:
        """Make a relative move in the hardware coordinate system."""
        raise NotImplementedError(
            "StageThings must define their own _hardware_move_relative method"
        )

    def move_absolute(self, block_cancellation: bool = False, **kwargs: int) -> None:
        """Make an absolute move. Keyword arguments should be axis names."""
        self._hardware_move_absolute(
            block_cancellation=block_cancellation,
            **self._apply_axis_direction(kwargs),
        )

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make an absolute move in the hardware coordinate system."""
        raise NotImplementedError(
            "StageThings must define their own move_absolute method"
        )

    def set_zero_position(self) -> None:
        """Make the current position zero in all axes."""
        raise NotImplementedError(
            "StageThings must define their own set_zero_position method"
        )

    def get_xyz_position(self) -> tuple[int, int, int]:
        """Return a tuple containing (x, y, z) position."""
        position_dict = self.position
        return (position_dict["x"], position_dict["y"], position_dict["z"])

    def move_to_xyz_position(self, xyz_pos: tuple[int, int, int]) -> None:
        """Move to the location specified by an (x, y, z) tuple."""
        self.move_absolute(x=xyz_pos[0], y=xyz_pos[1], z=xyz_pos[2])
