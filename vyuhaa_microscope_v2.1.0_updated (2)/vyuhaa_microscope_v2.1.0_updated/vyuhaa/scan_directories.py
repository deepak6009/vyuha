"""Functionality to manage file system operations for scan directories."""

import json
import logging
import os
import re
import shutil
import threading
import zipfile
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_serializer,
    field_validator,
)

from vyuhaa.utilities import make_name_safe, requires_lock

LOGGER = logging.getLogger(__name__)

IMG_DIR_NAME = "images"
SCAN_ZERO_PAD_DIGITS = 4

STITCH_REGEX = re.compile(r"stitched\.(jpe?g|ome\.tiff?|tiff?)$")
IMAGE_REGEX = re.compile(r"(tile_)?-?[0-9]+_-?[0-9]+\.(jpe?g|tiff?)$")

SCAN_DATA_FILENAME = "scan_data.json"


class NotEnoughFreeSpaceError(IOError):
    """An exception raised if there is not enough free space on disk to scan."""


class ScanInfo(BaseModel):
    """Summary information for the UI about a scan folder."""

    name: str
    created: float
    modified: float
    duration: Optional[float]
    number_of_images: int
    stitch_available: bool
    dzi: Optional[str]


class ScanData(BaseModel):
    """Data about a scan to be saved to a JSON file in the directory.

    This serialises into a human readable format where possible with

    timestamps in %Y-%m-%d_%H:%M:%S format
    timedeltas in %H:%M:%S format

    Properties that are not known until the end have ``None`` serialised as "Unknown"
    """

    model_config = ConfigDict(extra="forbid")

    scan_name: str
    """The name of the scan i.e. scan_0001"""

    starting_position: Mapping[str, int]
    """The starting position in dictionary format."""

    overlap: float
    """The overlap between adjacent images as a fraction of the image size."""

    max_dist: int
    """The maximum distance the scan could move (in steps) from the starting position."""

    dx: int
    """The number of steps between adjacent images in x."""

    dy: int
    """The number of steps between adjacent images in y."""

    autofocus_dz: int
    """The z range used for autofocus (in steps)."""

    autofocus_on: bool
    """Whether autofocus is on."""

    start_time: datetime
    """The time the scan started."""

    skip_background: bool
    """Whether automatic background detection is on, skipping locations with no sample."""

    stitch_automatically: bool
    """Whether the scan is set to automatically stitch when complete."""

    correlation_resize: float
    """The resize factor applied to images when the stitching program is correlating."""

    save_resolution: tuple[int, int]
    """The resolution that scan images are saved at."""

    image_count: int = 0
    """The number of images taken."""

    duration: Optional[timedelta] = None
    """The duration of the scan.

    This is automatically set when ``set_final_data()`` is run.
    """

    scan_result: Optional[str] = None
    """The result of the scan.

    This should be set with ``set_final_data()`` to ensure duration is set.
    """

    def set_final_data(self, result: str) -> None:
        """Set the final data for the scan, scan duration is automatically calculated.

        :param result: A string describing the result.
        """
        self.duration = datetime.now() - self.start_time
        self.scan_result = result

    @field_validator("start_time", mode="before")
    @classmethod
    def parse_timestamp(cls, value: str | datetime) -> datetime:
        """Validate a timestamp that may be a string in Year-Month-Day_Hrs:Min:Sec format."""
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d_%H:%M:%S")
        return value

    @field_serializer("start_time")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialise timestamp to Year-Month-Day_Hrs:Min:Sec format."""
        return value.strftime("%Y-%m-%d_%H:%M:%S")

    @field_validator("duration", mode="before")
    @classmethod
    def parse_timedelta(cls, value: Optional[str | timedelta]) -> Optional[timedelta]:
        """Validate a timedelta that may be a string in Hrs:Min:Sec format or "Unknown"."""
        if isinstance(value, str):
            if value == "Unknown":
                return None
            hrs, mins, secs = map(int, value.split(":"))
            return timedelta(hours=hrs, minutes=mins, seconds=secs)
        return value

    @field_serializer("duration")
    def serialize_timedelta(self, value: Optional[timedelta]) -> str:
        """Serialise timedelta to Hrs:Min:Sec (or "Unknown" if None)."""
        if value is None:
            return "Unknown"
        # Calculate the string manually rather than use str(), as this may convert to
        # days for a very very long scan, and then it is much harder to parse.
        total_secs = value.total_seconds()
        hrs = int(total_secs // 3600)
        mins = int((total_secs % 3600) // 60)
        secs = int((total_secs % 60))
        return f"{hrs}:{mins:02}:{secs:02}"

    @field_validator("scan_result", mode="before")
    @classmethod
    def parse_unknown_as_none(cls, value: Optional[str | int]) -> Optional[str | int]:
        """Validate the string "Unknown" as None."""
        return None if value == "Unknown" else value

    @field_serializer("scan_result")
    def serialize_none_as_unknown(self, value: Optional[str | int]) -> str | int:
        """Serialise None as "Unknown" for a more human readable result."""
        return "Unknown" if value is None else value


class ScanDirectoryManager:
    """A class for managing interactions with scan directories."""

    _base_scan_dir: str

    def __init__(self, base_scan_dir: str) -> None:
        """Initialise the scan directory manager.

        :param base_scan_dir: Path of the directory that holds all scans.
        """
        self._base_scan_dir = base_scan_dir
        if not os.path.exists(self._base_scan_dir):
            os.makedirs(self._base_scan_dir)
        self._lock = threading.RLock()

    @property
    def base_dir(self) -> str:
        """The base directory scans are saved to."""
        return self._base_scan_dir

    def exists(self, scan_name: str) -> bool:
        """Return True if scan of this name exists on disk."""
        return os.path.isdir(self.path_for(scan_name))

    def path_for(self, scan_name: str) -> str:
        """Return the path for a given scan name.

        Returns the path even if it doesn't exist
        """
        return os.path.join(self._base_scan_dir, scan_name)

    def img_dir_for(self, scan_name: str) -> str:
        """Return the path for the image dir for a given scan name.

        Returns the path even if it doesn't exist
        """
        return os.path.join(self._base_scan_dir, scan_name, IMG_DIR_NAME)

    @requires_lock
    def get_file_path_from(
        self, scan_name: str, filename: str, check_exists: bool = False
    ) -> Optional[str]:
        """Return the full file path for the file within a scan directory.

        If check_exists is True then None will be returned if the file does
        not exist.
        """
        file_path = os.path.join(self.path_for(scan_name), filename)
        if check_exists and not os.path.exists(file_path):
            return None
        return file_path

    @requires_lock
    def get_file_path_from_img_dir(
        self, scan_name: str, filename: str, check_exists: bool = False
    ) -> Optional[str]:
        """Return the full file path for the file within a scan directory.

        If check_exists is True, None is returned if the file does not exist. If False
        then the path is returned anyway
        """
        file_path = os.path.join(self.img_dir_for(scan_name), filename)
        if check_exists and not os.path.exists(file_path):
            return None
        return file_path

    def get_final_stitch_path(self, scan_name: str) -> Optional[str]:
        """Return the file full path for the final stitch.

        If no final stitch is found, return None
        """
        stitch_fname = ScanDirectory(scan_name, self.base_dir).get_final_stitch_name()
        if stitch_fname is None:
            return None
        return self.get_file_path_from_img_dir(scan_name, stitch_fname)

    @requires_lock
    def get_scan_data_path(self, scan_name: str) -> Optional[str]:
        """Return the file full path scan data JSON file.

        If no scan data JSON file is found, return None
        """
        scan_data_path = ScanDirectory(scan_name, self.base_dir).scan_data_path
        if scan_data_path is None:
            return None
        if not os.path.isfile(scan_data_path):
            return None
        return scan_data_path

    def get_scan_data_dict(self, scan_name: str) -> Optional[dict[str, Any]]:
        """Return the scan data read from a JSON file as a dict.

        This is a dictionary not a base model as the data format has changed
        somewhat over time.
        """
        return ScanDirectory(scan_name, self.base_dir).get_scan_data_dict()

    @property
    @requires_lock
    def all_scans(self) -> list[str]:
        """Return a list of the scan names in the base directory."""
        return [f.name for f in os.scandir(self._base_scan_dir) if f.is_dir()]

    @requires_lock
    def all_scans_info(self, ongoing: Optional[str] = None) -> list[ScanInfo]:
        """Return a lists of ScanInfo objects for each scan."""
        all_info: list[ScanInfo] = []
        for scan_name in self.all_scans:
            # If the scan is ongoing send flag to skip reading the json data
            skip_json = scan_name == ongoing
            scan_dir = ScanDirectory(scan_name, self.base_dir)
            info = scan_dir.scan_info(skip_json=skip_json)
            all_info.append(info)
        return all_info

    @requires_lock
    def _unique_scan_name(self, scan_name: str) -> str:
        """Get the next unique scan name starting with the given name.

        For more explanation on the scan naming see `new_scan_dir`
        """
        scan_name = make_name_safe(scan_name)
        # A regex with the scan name and a group for the numbers
        scan_regex = re.compile(
            "^" + scan_name + "_([0-9]{" + str(SCAN_ZERO_PAD_DIGITS) + "})$"
        )

        matching_scans = [scan for scan in self.all_scans if scan_regex.match(scan)]
        if not matching_scans:
            scan_num = 1
        else:
            last_matching_scan = sorted(matching_scans)[-1]
            # Get the first group from the regex, turn to int, and add 1
            scan_match = scan_regex.match(last_matching_scan)

            if scan_match is None:  # pragma: no cover
                # Type narrow, we know it is a match but mypy doesn't. This code is
                # Not reachable hence the no cover.
                raise RuntimeError("Internal error: regex No longer matches")

            scan_num = int(scan_match[1]) + 1

        # Set a sensible limit for the number of scans of one name
        # based on our zero padding.
        max_scan_no = 10**SCAN_ZERO_PAD_DIGITS - 1

        if scan_num > max_scan_no:
            raise FileExistsError(
                "Could not create a new scan folder: all names in use!"
            )

        return f"{scan_name}_{scan_num:0{SCAN_ZERO_PAD_DIGITS}d}"

    @requires_lock
    def new_scan_dir(self, scan_name: str) -> "ScanDirectory":
        """Get a unique name for this scan and create a directory for it.

        The scan will be named ``{scan_name}_0001`` where the number is
        zero-padded to be SCAN_ZERO_PAD_DIGITS digits long (to allow correct sorting if the
        scans are ordered alphanumerically).

        Creates a new empty folder, into which scans are saved

        Returns the a ScanDirectory object
        """
        # if no scan name is set set to "scan". This done here as empty strings
        # get passed in otherwise.
        if not scan_name:
            scan_name = "scan"

        full_scan_name = self._unique_scan_name(scan_name)

        os.makedirs(self.path_for(full_scan_name))
        os.makedirs(self.img_dir_for(full_scan_name))
        return ScanDirectory(full_scan_name, self.base_dir)

    @requires_lock
    def delete_scan(self, scan_name: str) -> None:
        """Delete a scan."""
        shutil.rmtree(self.path_for(scan_name))

    @requires_lock
    def zip_scan(self, scan_name: str, final_version: bool = False) -> str:
        """Zips any images from the scan not yet zipped, return full path to zip.

        ``final_version`` Set true to stitch all files not just the scan images
        this should only be done at the end as it is not possible to update a file
        in a zip.
        """
        return ScanDirectory(scan_name, self.base_dir).zip_files(final_version)

    @requires_lock
    def check_free_disk_space(self, min_space: int = 500000000) -> None:
        """Raise an exception if there is not enough free disk space to continue scanning.

        :param min_space: the minimum space required in bytes.
            Default = 500,000,000 (500MB)

        :raises NotEnoughFreeSpaceError: if the remaining storage is below min_space
        """
        disk_usage = shutil.disk_usage(self._base_scan_dir)
        if disk_usage.free < min_space:
            raise NotEnoughFreeSpaceError(
                "There is not enough free disk space to continue. "
                f"(Required: {min_space >> 20}MB. Free: {disk_usage.free >> 20}MB)."
            )


class ScanDirectory:
    """A class for handling interactions with scan directories."""

    _name: str
    _base_scan_dir: str

    def __init__(self, name: str, base_scan_dir: str) -> None:
        """Initialise the scan directory.

        :param name: the name of the scan (the scan directory basename).
        :param base_scan_dir: Path of the directory that holds all scans.
        """
        self._name = name
        self._base_scan_dir = base_scan_dir
        if not os.path.isdir(self.dir_path):
            raise FileNotFoundError(
                f"The scan directory {self.dir_path} cannot be found"
            )

    @property
    def name(self) -> str:
        """The name of the scan."""
        return self._name

    @property
    def dir_path(self) -> str:
        """The full path to the scan directory."""
        return os.path.join(self._base_scan_dir, self._name)

    @property
    def images_dir(self) -> Optional[str]:
        """The path to the images directory.

        None is returned if no images directory was created.
        """
        im_path = os.path.join(self.dir_path, IMG_DIR_NAME)
        if os.path.isdir(im_path):
            return im_path
        return None

    @property
    def scan_data_path(self) -> Optional[str]:
        """The path to the scan data json file for this directory.

        Returns None if there is no images dir to write to.
        """
        if self.images_dir is not None:
            return os.path.join(self.images_dir, SCAN_DATA_FILENAME)
        return None

    @property
    def created_time(self) -> float:
        """The time the directory was created on disk."""
        return os.path.getctime(self.dir_path)

    def get_scan_files(self) -> list[str]:
        """Return a list of the files in the images dir."""
        if self.images_dir is None:
            return []
        return os.listdir(self.images_dir)

    def _extract_scan_images(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for scan images.

        :param file_list: The list of files to search. Normally this would be
            ``self.get_scan_files()``

        :returns: The list of files that match the naming convention for scan images
        """
        return [i for i in file_list if IMAGE_REGEX.search(i)]

    def _extract_final_stitches(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for final stitches.

        :param file_list: The list of files to search.

        :returns: The list of files that match the naming convention for final stitches
        """
        return [i for i in file_list if STITCH_REGEX.search(i)]

    def _extract_dzi_files(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for dzi_files.

        :param file_list: The list of files to search.

        :returns: The list of files that match the naming convention for dzi_files
        """
        return [i for i in file_list if i.endswith("dzi")]

    def get_final_stitch_name(self) -> Optional[str]:
        """Return the filename for the final stitch (in the images dir).

        If no final stitch is found, return None
        """
        stitches = self._extract_final_stitches(self.get_scan_files())
        if not stitches:
            return None
        return stitches[0]

    def get_modified_time(self) -> float:
        """Return the modified time of the directory."""
        return max(os.stat(root).st_mtime for root, _, _ in os.walk(self.dir_path))

    def get_scan_data_dict(self) -> Optional[dict[str, Any]]:
        """Return the scan data from the json file as a dictionary.

        This is safer than get_scan_data for older scans before a defined model was
        used.

        :return: The data as a dictionary or None if it couldn't be loaded.
        """
        if self.scan_data_path is None:
            return None
        try:
            with open(self.scan_data_path, "r", encoding="utf-8") as data_file:
                return json.load(data_file)
        except (json.decoder.JSONDecodeError, IOError):
            return None

    def get_scan_data(self) -> Optional[ScanData]:
        """Return the scan data from the json file as a ScanData model.

        :return: The data as a ScanData model or None if it couldn't be loaded or
            valdiated.
        """
        data_dict = self.get_scan_data_dict()
        if data_dict is None:
            LOGGER.warning(f"Could not load scan data for {self.name}.")
            return None
        try:
            return ScanData(**data_dict)
        except ValidationError:
            LOGGER.warning(f"Could not validate scan data for {self.name}.")
            return None

    def scan_info(self, skip_json: bool = False) -> ScanInfo:
        """Return the information to be used in the UI for the scan."""
        scan_files = self.get_scan_files()
        scan_images = self._extract_scan_images(scan_files)
        stitches = self._extract_final_stitches(scan_files)
        dzi_files = self._extract_dzi_files(scan_files)
        number_of_images = len(scan_images)
        stitch_available = len(stitches) > 0
        dzi = None if not dzi_files else str(dzi_files[0])

        scan_data = None if skip_json else self.get_scan_data()
        duration = (
            None
            if scan_data is None or scan_data.duration is None
            else scan_data.duration.total_seconds()
        )

        return ScanInfo(
            name=self.name,
            created=self.created_time,
            modified=self.get_modified_time(),
            duration=duration,
            number_of_images=number_of_images,
            stitch_available=stitch_available,
            dzi=dzi,
        )

    def all_files(self, skip_dirs: Optional[list[str]] = None) -> list[str]:
        """Return a list of all files in the scan dir relative to the dir.

        :param skip_dirs: Skip any file in a directory that is on this list. The list
            should be the basename of the directory. e.g. "scan_0001_files" not
            "images/scan_0001_files"
        """
        if skip_dirs is None:
            skip_dirs = []
        files = []
        for file_root, dirs, filenames in os.walk(self.dir_path, topdown=True):
            # Skip any skipped directories.
            # Note: we must use slice assignment to edit in place.
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in filenames:
                full_path = os.path.join(file_root, filename)
                files.append(os.path.relpath(full_path, self.dir_path))
        return files

    def save_scan_data(self, scan_data: ScanData) -> None:
        """Save the scan data for this scan to disk."""
        if self.scan_data_path is None:
            raise FileNotFoundError(
                "There is no images directory to save scan data into."
            )

        with open(self.scan_data_path, "w", encoding="utf-8") as f:
            f.write(scan_data.model_dump_json(indent=4))

    def zip_files(self, final_version: bool = False) -> str:
        """Zips any images from the scan not yet zipped, return full path to zip.

        ``final_version`` Set true to stitch all files not just the scan images
        this should only be done at the end as it is not possible to update a file
        in a zip.
        """
        zip_fname = os.path.join(self.dir_path, "images.zip")

        # Use noqa as converting this into a 1 liner is not more readable.
        if os.path.isfile(zip_fname):  # noqa: SIM108
            # get a list of files in the existing zip
            zip_files = get_files_in_zip(zip_fname)
        else:
            zip_files = []

        # For each `filename.dzi` we need to skip the `filename_files` directory
        dzi_files = self._extract_dzi_files(self.get_scan_files())
        dzi_dirs = [dzi[:-4] + "_files" for dzi in dzi_files]

        with zipfile.ZipFile(zip_fname, mode="a") as scan_zip:
            for file in self.all_files(skip_dirs=dzi_dirs):
                # Don't zip zipfiles, dzi files, or files already in the zip
                if file.endswith((".zip", ".dzi")) or file in zip_files:
                    continue
                # If this is not the final version, then only zip image files.
                if not final_version and not IMAGE_REGEX.search(file):
                    continue

                scan_zip.write(os.path.join(self.dir_path, file), arcname=file)
        return zip_fname


def get_files_in_zip(zip_path: str) -> list[str]:
    """List the relative paths of all files and folders in the zip folder specified."""
    scan_zip = zipfile.ZipFile(zip_path)
    return [os.path.normpath(i) for i in scan_zip.namelist()]
