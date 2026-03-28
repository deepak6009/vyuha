"""Functionality for planning scan routes.

A scan route can be planned by a ScanPlanner class currently there
is only one type the SmartSpiral. More can be added using by
subclassing the ScanPlanner
"""

# Future annotations needed for typhinting same class in __eq__ method. Other option
# would be to import Union and use a string.
from __future__ import annotations

import logging
from copy import copy
from typing import Any, Optional, TypeAlias

import numpy as np

LOGGER = logging.getLogger(__name__)

XYPos: TypeAlias = tuple[int, int]
XYZPos: TypeAlias = tuple[int, int, int]
XYPosList: TypeAlias = list[XYPos]
XYZPosList: TypeAlias = list[XYZPos]


def enforce_xy_tuple(value: XYPos) -> XYPos:
    """Check input is a tuple and is of length 2.

    If possible it will coerce the value to a tuple.

    :raises ValueError: if the input cannot be coerced to a tuple of length 2.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError("2 value tuple expected")
    if not len(value) == 2:
        raise ValueError("2 value tuple expected")
    if isinstance(value, list):
        return tuple(value)
    return value


def enforce_xyz_tuple(value: XYZPos) -> XYZPos:
    """Check input is a tuple and is of length 3.

    If possible it will coerce the value to a tuple.

    :raises ValueError: if the input cannot be coerced to a tuple of length 3.
    """
    if not isinstance(value, (list, tuple)):
        raise TypeError("3 value tuple expected")
    if not len(value) == 3:
        raise ValueError("3 value tuple expected")
    if isinstance(value, list):
        return tuple(value)
    return value


# Disable PLW1641, this warns against hash not being set when equals is. But the
# class shouldn't be hashable.
class FutureScanLocation:  # noqa PLW1641
    """Data for information on future locations to scan.

    This object is only used for internal calculation and data storage it shouldn't
    be an input or output of public methods.
    """

    def __init__(self, xy_pos: XYPos, **kwargs: Any) -> None:
        """Initialise FutureScanLocation with an xy position.

        :param xy_pos: The (x, y) position to scan.
        :param kwargs: Any other information about the location. This will be passed
            through to the VisitedScanLocation object.
        """
        self._xy_pos = enforce_xy_tuple(xy_pos)
        self.planner_data = copy(kwargs)

    @property
    def xy_tuple(self) -> XYPos:
        """The xy position tuple."""
        return self._xy_pos

    def __eq__(self, other: Any) -> bool:
        """Check for equality, only checks the xy position.

        Will check against tuple or other FutureScanLocation object.
        """
        if isinstance(other, FutureScanLocation):
            return self._xy_pos == other.xy_tuple
        if isinstance(other, tuple) and len(other) == 2:
            return self._xy_pos[:2] == other
        return NotImplemented


# Disable PLW1641, this warns against hash not being set when equals is. But the
# class shouldn't be hashable.
class VisitedScanLocation:  # noqa PLW1641
    """Data for information on locations already visited during a scan.

    This object is only used for internal calculation and data storage it shouldn't
    be an input or output of public methods.
    """

    def __init__(
        self, xyz_pos: XYZPos, imaged: bool, focused: bool, **kwargs: Any
    ) -> None:
        """Initialise VisitedScanLocation with an xyz-position, and whether imaged/focused.

        :param xyz_pos: The (x, y, z) position visited.
        :param imaged: True if an image was taken, False if not (due to background
            detect)
        :param focused: True if autofocus completed successfully
        :param kwargs: Any other information about the location. This should be passed
            through to from the FutureScanLocation object that requested the location
            to be scanned.
        """
        self._xyz_pos = enforce_xyz_tuple(xyz_pos)
        self.imaged = imaged
        self.focused = focused
        self.planner_data = copy(kwargs)

    @property
    def xyz_tuple(self) -> XYZPos:
        """The xyz position tuple."""
        return self._xyz_pos

    @property
    def xy_tuple(self) -> XYPos:
        """The xy position tuple."""
        return self._xyz_pos[:2]

    def __eq__(self, other: Any) -> bool:
        """Check for equality, only checks the xyz-position or xy-position if z isn't available.

        Will check xyz-position against 3-value tuples and other VisitedScanLocation
        objects.

        Will check xy-position against 2-value tuples and FutureScanLocation objects.
        """
        if isinstance(other, VisitedScanLocation):
            return self._xyz_pos == other.xyz_tuple
        if isinstance(other, FutureScanLocation):
            return self._xyz_pos[:2] == other.xy_tuple
        if isinstance(other, tuple):
            if len(other) == 2:
                return self._xyz_pos[:2] == other
            if len(other) == 3:
                return self._xyz_pos == other
        return NotImplemented


class ScanPlanner:
    """A base class for a scan planner.

    This should never be used directly for a scan, it should be subclassed.

    Each subclass should implement at least the methods with NotImplementedError
    set:

    * ``_parse()`` - to parse the planner_settings dictionary, saving values to class
        variables
    * ``_initial_location_list()`` - Sets the list of locations for the scan to follow

    For a simple scan pattern this should be sufficient. For more complex ones that
    dynamically adjust the path it is suggested to override ``mark_location_visited()``
    calling ``super().mark_location_visited()`` at the start of the method so that all
    locations are adjusted.

    When subclassing be sure to use ``enforce_xy_tuple`` and ``enforce_xyz_tuple`` on
    any user data before running.
    """

    def __init__(
        self, initial_position: XYPos, planner_settings: Optional[dict] = None
    ) -> None:
        """Set up lists for the path planning, and scan history."""
        self._initial_position = enforce_xy_tuple(initial_position)
        self._parse(planner_settings)

        self._remaining_locations: list[FutureScanLocation] = (
            self._initial_location_list()
        )
        self._path_history: list[VisitedScanLocation] = []

    @property
    def scan_complete(self) -> bool:
        """Return True if there are no locations left to scan."""
        return not self._remaining_locations

    @property
    def remaining_locations(self) -> XYPosList:
        """Property to access a copy of the remaining_locations."""
        return [loc.xy_tuple for loc in self._remaining_locations]

    @property
    def imaged_locations(self) -> XYZPosList:
        """Property to access a copy of the imaged_locations."""
        return [loc.xyz_tuple for loc in self._path_history if loc.imaged]

    @property
    def focused_locations(self) -> XYZPosList:
        """Property to access a copy of the focused_locations."""
        return [loc.xyz_tuple for loc in self._path_history if loc.focused]

    @property
    def path_history(self) -> XYPosList:
        """Property to access a copy of the path_history."""
        return [loc.xy_tuple for loc in self._path_history]

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        """Parse any settings sent to this planner and store them if needed."""
        raise NotImplementedError("Did you call the ScanPlanner base class?")

    def _initial_location_list(self) -> list[FutureScanLocation]:
        """Set the initial list of locations for this scan planner.

        This is called on initialisation.

        For a simple grid scan/snake scan this would be all locations to move to.

        :return: A list of FutureScanLocation objects with all planned locations.
        """
        raise NotImplementedError("Did you call the ScanPlanner base class?")

    def position_visited(self, position: XYPos | FutureScanLocation) -> bool:
        """Return True if input scan position has been visited before."""
        # Ensure tuple for correct matching!
        return position in self._path_history

    def get_visited_location(
        self, position: XYPos | XYZPos | FutureScanLocation
    ) -> VisitedScanLocation:
        """Return the scan location from the history that matches the input position."""
        # Ignoring type as self._path_history has type List[VisitedScanLocation], and
        # VisitedScanLocation implements __eq__ for XYPos & XYZPos & FutureScanLocation
        # however this is not statically detectable by MyPy
        index = self._path_history.index(position)  # type: ignore[arg-type]
        return self._path_history[index]

    def position_planned(self, position: XYPos | FutureScanLocation) -> bool:
        """Return True if input scan position position is planned."""
        # Ensure tuple for correct matching!
        return position in self._remaining_locations

    def get_next_location_and_z_estimate(self) -> tuple[XYPos, Optional[int]]:
        """Return the next location to scan and its estimated z-position.

        Note z-position may be None! This indicates that the current z, position
        should be used.
        """
        if self.scan_complete:
            raise RuntimeError("Can't get next position, scan is complete")

        next_location = self._remaining_locations[0].xy_tuple

        # If focussed locations exist return closest location, favouring most recent
        closest_pos = self.closest_focus_site(next_location)
        z = None if closest_pos is None else closest_pos[2]

        return next_location, z

    def closest_focus_site(self, xy_pos: XYPos) -> Optional[XYZPos]:
        """Return the xyz position of the closest site where focus was achieved.

        The most recently taken image is returned in the case of a tie.

        :param xy_pos: The xy_position which the returned position should be closest
            to.

        Returns None if there if no focussed locations are present
        """
        # save to variable rather than search for focussed sites each time.
        focused_locations = self.focused_locations
        if not focused_locations:
            return None

        # must be float64 (double precision) to deal with the huge numbers involved!
        current_pos = np.array(xy_pos, dtype="float64")
        path_pos = np.array(focused_locations, dtype="float64")[:, :2]

        # Use linalg.norm to calculate the direct distance bweween the points
        # Note linalg.norm always uses float64
        dists = np.linalg.norm((path_pos - current_pos), axis=1)

        # Get indices of all minima.
        # Note np.where always returns a tuple of arrays, hence the trailing [0]
        indices = np.where(dists == np.min(dists))[0]

        # The last index is most recent
        return focused_locations[indices[-1]]

    def mark_location_visited(
        self, xyz_pos: XYZPos, imaged: bool, focused: bool
    ) -> None:
        """Mark the location as visited.

        :param xyz_pos: the x_y_z position
        :param imaged: true if an image was taken, false if not (due to background detect)
        :param focused: true if autofocus completed successfully
        """
        # ensure is tuple!
        xyz_pos = enforce_xyz_tuple(xyz_pos)

        # Remove the expected position from the remaining locations list
        # and check it's correct
        expected_pos = self._remaining_locations.pop(0)
        if xyz_pos[:2] != expected_pos.xy_tuple:
            raise RuntimeError("Wrong scan location visited!")

        # Append xy position for path_history
        self._path_history.append(
            VisitedScanLocation(
                xyz_pos=xyz_pos,
                imaged=imaged,
                focused=focused,
                **expected_pos.planner_data,
            )
        )


class SmartSpiral(ScanPlanner):
    """A scan planner that spirals outward from the centre, prioritising short moves.

    This planner spirals out from the centre, but prioritises short moves over rigidly
    sticking to minimising radius from the centre of the scan.

    Each time and image is taken the four neighbouring images are added
    to the list of positions to image (unless they are already listed or
    tried). However, if a location is not imaged due no sample being detected,
    then neighbouring positions are not imaged.

    The next image taken is the fewest scan sites (moves in dx and dy) from the current
    site, with ties broken by minimising the moves away from the start of the scan.
    Final tiebreak is the distance to each site, in motor steps rather than multiple of
    dx and dy.
    """

    _max_dist: int = 0
    _dx: int = 0
    _dy: int = 0

    def __init__(
        self, initial_position: XYPos, planner_settings: Optional[dict] = None
    ) -> None:
        """Set up the lists inherited from ScanPlanner, plus a distance cutoff.

        Use the supplied _dx and _dy to set a distance cutoff for an image to be
        considered neighbouring another
        """
        super().__init__(initial_position, planner_settings)
        self._distance_cutoff: float = max([self._dx, self._dy]) * 1.1

    def _is_primary_location(
        self, location: FutureScanLocation | VisitedScanLocation
    ) -> bool:
        """Return True if input is a primary location not a secondary (intermediate) location."""
        return location.planner_data["primary"]

    @property
    def secondary_locations(self) -> XYZPosList:
        """A list of all secondary (intermediate) locations."""
        return [
            loc.xyz_tuple
            for loc in self._path_history
            if not self._is_primary_location(loc)
        ]

    def _parse(self, planner_settings: Optional[dict] = None) -> None:
        """Parse SmartSpiral Settings dictionary.

        * ``dx`` - the movement size in x
        * ``dy`` - the movement size in y
        * ``max_dist`` - The maximum distance to a location can be from the centre.
        """
        expected_keys = ["max_dist", "dx", "dy"]
        invalid_msg = "SmartSpiral requires a planner_settings dictionary with keys: "
        if not planner_settings:
            raise ValueError(invalid_msg + ",".join(expected_keys))
        if not all(keys in planner_settings for keys in expected_keys):
            raise KeyError(invalid_msg + ",".join(expected_keys))

        self._dx = int(planner_settings["dx"])
        self._dy = int(planner_settings["dy"])
        self._max_dist = int(planner_settings["max_dist"])

    def _initial_location_list(self) -> list[FutureScanLocation]:
        """Set the initial list of locations for this scan planner.

        This is salled on initialisation.

        For smart spiral this is just the first point
        """
        return [FutureScanLocation(self._initial_position, primary=True)]

    def mark_location_visited(
        self, xyz_pos: XYZPos, imaged: bool = True, focused: bool = True
    ) -> None:
        """Mark the location as visited.

        :param xyz_pos: the x_y_z position
        :param imaged: true if an image was taken, false if not (due to background detect)
        :param focused: true if autofocus completed successfully
        """
        # First call the base class to update the positions
        super().mark_location_visited(xyz_pos, imaged, focused)

        xy_pos = enforce_xy_tuple(xyz_pos[:2])
        if self._is_primary_location(self._path_history[-1]):
            if imaged:
                self._add_surrounding_positions(xy_pos)
            else:
                self._add_intermediate_positions(xy_pos)
            # Don't re-sort after imaging a secondary location or it can cause scan
            # direction to reverse, breaking the spiral.
            self._re_sort_remaining_locations(xy_pos)

    def _add_surrounding_positions(self, xy_pos: XYPos) -> None:
        """Add the 4 surrounding positions to the list of remaining locations to visit.

        This adds the surrounding positions (with 4 point connectivity) to the
        remaining locations list if they are not:

        * too far away
        * already planned
        * already visited

        If the already visited position was not imaged then an intermediate location is
        added. See also self._add_intermediate_positions() for adding intermediate
        locations after visiting a location that was not imaged.
        """
        new_positions = self._adjacent_positions(xy_pos)

        for new_pos in new_positions:
            # Skip position if already planned
            if self.position_planned(new_pos):
                continue
            if self.position_visited(new_pos):
                # Get the VisitedScanLocation object if already visited
                visited = self.get_visited_location(new_pos)
                if visited.imaged:
                    # If this adjacent position was imaged successfully already then skip.
                    continue
                # If it wasn't imaged add an intermediate location between the
                # last imaged position and this one.
                i_pos = self._intermediate_position(xy_pos, new_pos)
                # Set primary=False surrounding images are not added once imaged.
                i_loc = FutureScanLocation(i_pos, primary=False)
                # Append instantly without checking max_distance as this is between
                # imaged points.
                self._remaining_locations.append(i_loc)
                continue

            dist = distance_between(new_pos, self._initial_position)
            if dist > self._max_dist:
                LOGGER.debug("Rejected moving to %s as it is out of range", new_pos)
                continue
            new_loc = FutureScanLocation(new_pos, primary=True)
            self._remaining_locations.append(new_loc)

    def _add_intermediate_positions(self, xy_pos: XYPos) -> None:
        """Add intermediate points after locating a background location.

        This is called after an image is recorded that was background. Intermediate
        locations are added between any adjacent locations that were successfully
        imaged due to being labelled as containing sample.

        Note that in the case that an imaged location has an adjacent background image
        then adding the intermediate image will be handled by
        _add_surrounding_positions().
        """
        surrounding_positions = self._adjacent_positions(xy_pos)

        for surr_pos in surrounding_positions:
            if self.position_visited(surr_pos):
                # Get the VisitedScanLocation object if already visited
                visited = self.get_visited_location(surr_pos)
                if not visited.imaged:
                    # If it wasn't imaged then skip this position
                    continue
                # If surrounding location was imaged add an intermediate location
                # between the most recent position and this imaged position.
                i_pos = self._intermediate_position(xy_pos, surr_pos)
                # Set primary=False surrounding images are not added once imaged.
                i_loc = FutureScanLocation(i_pos, primary=False)
                # Append instantly without checking max_distance as this is between
                # imaged points.
                self._remaining_locations.append(i_loc)

    def _adjacent_positions(self, xy_pos: XYPos) -> XYPosList:
        """Return 4 points +/-dx and +/-dy from the input location."""
        return [
            (xy_pos[0] - self._dx, xy_pos[1]),
            (xy_pos[0] + self._dx, xy_pos[1]),
            (xy_pos[0], xy_pos[1] - self._dy),
            (xy_pos[0], xy_pos[1] + self._dy),
        ]

    def _intermediate_position(self, xy_pos1: XYPos, xy_pos2: XYPos) -> XYPos:
        """Return an (x,y) position halfway between two input positions."""
        x = (xy_pos1[0] + xy_pos2[0]) // 2
        y = (xy_pos1[1] + xy_pos2[1]) // 2
        return (x, y)

    def _re_sort_remaining_locations(self, current_pos: XYPos) -> None:
        """Sort the remaining positions based on the current location."""

        # Defined rather than use a lambda for readability
        def sort_key(pos: FutureScanLocation) -> tuple[bool, float, float, float]:
            return (
                self._is_primary_location(pos),  # False sorts low
                self.moves_between(current_pos, pos),
                self.moves_between(self._initial_position, pos),
                distance_between(current_pos, pos),
            )

        self._remaining_locations.sort(key=sort_key)

    def get_next_location_and_z_estimate(self) -> tuple[XYPos, Optional[int]]:
        """Return the next location to scan and its estimated z-position.

        This overrides the default behaviour of ScanPlanner to take the lowest value of
        nearest neighbours as this works best for smart stack.

        Note z-position may be None! This indicates that the current z position
        should be used.
        """
        if self.scan_complete:
            raise RuntimeError("Can't get next position, scan is complete")

        next_location = self._remaining_locations[0].xy_tuple

        # If focused locations exist, return the neighbour with the lowest z position
        closest_pos = self.select_nearby_focus_site(next_location)
        z = None if closest_pos is None else closest_pos[2]

        return next_location, z

    def select_nearby_focus_site(self, xy_pos: XYPos) -> Optional[XYZPos]:
        """Return the xyz position of the nearby site with the lowest z position.

        Lowest position is best, as starting too high causes smart stacking to
        autofocus and restart. Starting too low just requires extra movements in +z.
        Nearby is defined as within 1.1 times the larger of the x and y scan offsets.

        If no focused sites are within this range, use the height of the nearest
        focused site.

        Returns None if no focused locations are present
        """
        # save to variable rather than search for focussed sites each time.
        focused_locations = self.focused_locations
        if not focused_locations:
            return None

        # must be float64 (double precision) to deal with the huge numbers involved!
        current_pos = np.array(xy_pos, dtype="float64")
        path_pos = np.array(focused_locations, dtype="float64")[:, :2]

        # Use linalg.norm to calculate the direct distance between the points
        # Note linalg.norm always uses float64
        dists = np.linalg.norm((path_pos - current_pos), axis=1)

        # Get indices of all focused sites within distance_cutoff.
        # Note np.where always returns a tuple of arrays, hence the trailing [0]
        indices = np.where(dists <= self._distance_cutoff)[0]

        # Handle the case that no focused positions are within this range, and
        # instead use the nearest focused position. This will always return a
        # height, due to the check that self._focused_locations exists.
        if len(indices) == 0:
            distance_cutoff = min(dists)
            indices = np.where(dists <= distance_cutoff)[0]

        # Turning into an array allows slicing based on a list
        focused_locations_array = np.array(focused_locations)

        # Choose the lowest (smallest z) of the neighbouring sites. Smart stack works best
        # if started too low, so the lowest z will perform best
        chosen_focused_site = min(focused_locations_array[indices], key=lambda x: x[-1])

        # Convert back into list so values are of type int instead of np.int32
        return tuple(chosen_focused_site.tolist())

    def moves_between(
        self,
        starting_pos: XYPos | np.ndarray | FutureScanLocation,
        ending_pos: XYPos | np.ndarray | FutureScanLocation,
    ) -> float:
        """Return the larger of x moves or y moves between two xy positions.

        :param starting_pos: the position to measure from
        :param ending_pos: the position to measure to

        """
        if isinstance(starting_pos, FutureScanLocation):
            starting_pos = starting_pos.xy_tuple
        if isinstance(ending_pos, FutureScanLocation):
            ending_pos = ending_pos.xy_tuple
        move_size = np.array([self._dx, self._dy])

        starting_pos = np.array(starting_pos, dtype="float64")
        ending_pos = np.array(ending_pos, dtype="float64")

        displacement_in_moves = (ending_pos - starting_pos) / move_size

        return np.max(np.abs(displacement_in_moves))


def distance_between(
    current_pos: XYPos | np.ndarray | FutureScanLocation,
    next_pos: XYPos | np.ndarray | FutureScanLocation,
) -> float:
    """Calculate the distance between the two xy positions.

    This was previously called ``distance_to_site``
    """
    if isinstance(current_pos, FutureScanLocation):
        current_pos = current_pos.xy_tuple
    if isinstance(next_pos, FutureScanLocation):
        next_pos = next_pos.xy_tuple
    next_pos = np.array(next_pos, dtype="float64")
    current_pos = np.array(current_pos, dtype="float64")
    return float(np.linalg.norm(next_pos - current_pos))
