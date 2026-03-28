"""Utility functions and classes."""

import json
import logging
import os
import re
import sys
import sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None
from functools import wraps
from importlib.metadata import version
from typing import (
    Any,
    Callable,
    Concatenate,
    Literal,
    Mapping,
    Optional,
    ParamSpec,
    TypeAlias,
    TypeVar,
    overload,
)

from pydantic import BaseModel

# import labthings_fastapi as lt

T = TypeVar("T")
P = ParamSpec("P")
LockableClass = TypeVar("LockableClass")

JSONScalar: TypeAlias = str | int | float | bool | None
JSONType: TypeAlias = dict[str, "JSONType"] | list["JSONType"] | JSONScalar

LOGGER = logging.getLogger(__name__)

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# Regex for a full commit hash
COMMIT_REGEX = re.compile(r"[0-9a-f]{40}")
# Regex for a reference in the git HEAD file. Group 1 is the path.
REF_REGEX = re.compile(r"^ref:\s(.*)$")


def _is_lock_like(obj: Any) -> bool:
    """Check if an object is a lock.

    Cannot use ``isinstance(obj, threading.RLock)``, may be possible to use
    ``isinstance(obj, threading._RLock)``. But this uses a private method and may
    break. Instead making the check that both "acquire" and "release" exist.
    """
    return hasattr(obj, "acquire") and hasattr(obj, "release")


def requires_lock(
    method: Callable[Concatenate[LockableClass, P], T],
) -> Callable[Concatenate[LockableClass, P], T]:
    """Decorate a class method so that it requires the class lock to run.

    The class should have a reentrant lock with the name ``self._lock``.
    """

    @wraps(method)
    def wrapper(self: LockableClass, *args: P.args, **kwargs: P.kwargs) -> T:
        # Confirm the object the method is attached to has a self._lock property
        if not hasattr(self, "_lock"):
            raise AttributeError(f"{self.__class__.__name__} has no '_lock' attribute")
        # And that it is a reentrant lock.
        if not _is_lock_like(self._lock):
            raise TypeError("requires_lock requires self._lock to be a lock")
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


# Compiled regular expressions for unsafe characters
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -, :, /, \
_WINDOWS_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-:/\\]")
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -, \
_POSIX_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-/]")
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -
_NAME_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-]")


def make_path_safe(unsafe_path_string: str) -> str:
    """Replace unsafe characters in a file path with underscores, preserving separators.

    This can be used to check that user inputs have not been concatenated into an
    unsafe filepath.

    This ensures compatibility across platforms by preserving only valid characters,
    including path separators (forward slash on POSIX, and forward
    slash/backslash/colon on Windows).

    :param unsafe_path_string: The original path string to sanitise.

    :returns: A version of the input string safe to use as a file path.
    """
    unsafe_character_pattern = (
        _WINDOWS_UNSAFE_PATTERN
        if sys.platform.startswith("win")
        else _POSIX_UNSAFE_PATTERN
    )
    return unsafe_character_pattern.sub("_", unsafe_path_string)


def make_name_safe(unsafe_name_string: str) -> str:
    """Replace unsafe characters in a filename or identifier with underscores.

    This excludes all path separators, ensuring the result is safe to use as a
    standalone filename or identifier component.

    :param unsafe_name_string: The original name string to sanitise.

    :returns: A version of the input string safe to use as a file name or identifier.
    """
    return _NAME_UNSAFE_PATTERN.sub("_", unsafe_name_string)


class VersionData(BaseModel):
    """A BaseModel containing the information about the server version.

    :param version: The version string for the server. Or "Undefined" if there is an
        error.
    :param version_source: Either a Git commit hash, "TOML", "Dist" or in the case of
        an error - "Undefined".
    """

    version: str
    version_source: str


def robust_version_strings() -> VersionData:
    """Return a version string and information on its source.

    Returning a python version string is not without problems. If the package is
    installed in an editable way, often METADATA is never updated. This provides 4
    ways to provide a version string:

    1. If both a Git directory and a pyproject.toml are available. Return the version
        string from the toml file and the git hash from the ``.git`` directory as its
        source.
    2. If a pyproject.toml is available but no Git directory then this is likely an
        installation from source. Return the version string from the toml file and
        return the literal string "TOML" as its source.
    3. If a Git directory is available but no pyproject.toml then something is very
        weird - log an error. Then return "Undefined" as the version string, but still
        return the Git hash as the source.
    4. Finally if neither a Git directory nor a pyproject.toml are available then the
       server has been installed from a distribution package (i.e. a wheel). In this
       case use the standard library function ``importlib.metadata.version`` for the
       version string (prepending a "v") and return "Dist" as its source.

    :returns: a VersionData instance with the version string and source.

    """
    project_toml_path = os.path.join(REPO_DIR, "pyproject.toml")
    git_dir_path = os.path.join(REPO_DIR, ".git")
    has_project_toml = os.path.isfile(project_toml_path)
    has_git_dir = os.path.isdir(git_dir_path)

    if has_project_toml and has_git_dir:
        # .git dir and pyproject.toml available. This is a development
        # installation.
        ver = "v" + _get_version_from_toml(project_toml_path)
        source = _get_hash_from_git_dir(git_dir_path)
    elif has_project_toml:
        # Only pyproject.toml available. Likely installed from source. Check
        # file directly as `importlib.metadata.version` plays badly with editable
        # installs.
        ver = "v" + _get_version_from_toml(project_toml_path)
        source = "TOML"
    elif has_git_dir:
        # Only .git dir, that is weird.
        LOGGER.error(
            "Unexpected installation configuration. Version number cannot be verified."
        )
        ver = "Undefined"
        source = _get_hash_from_git_dir(git_dir_path)
    else:
        # Has neither .git nor pyproject.toml. It is probably installed from wheel.
        # This will be the expected method of packaging in the future. This option
        # is handled last as ``importlib.metadata.version`` can be unreliable if
        # the package is no installed as a distribution package.
        ver = "v" + version("vyuhaa")
        source = "Dist"
    return VersionData(version=ver, version_source=source)


def _get_hash_from_git_dir(git_dir_path: str) -> str:
    """Get the commit hash from a .git directory directly.

    This function doesn't rely on GIT being on the PATH or even installed. It doesn't
    require any packages outside the Python standard library. It directly inspects the
    .git directory to get the commit hash:

    * If the repository is on a detached HEAD then the hash will be in the file
        .git/HEAD. A detached HEAD is could be from checking out a tag or checking out
        a commit directly.
    * In normal operation .git/HEAD points to a reference or "ref". This ref file
        contains the hash.

    :param git_dir_path: The path to the .git directory.

    :returns: The hash, or "Undefined" if there is any error, errors are logged not
        raised.
    """
    head_path = os.path.join(git_dir_path, "HEAD")
    try:
        with open(head_path, "r", encoding="utf-8") as head_file:
            head = head_file.read()
    except IOError:
        LOGGER.error("Problem opening .git/HEAD")
        return "Undefined"
    # The file HEAD should either be a reference or commit ID.
    if match := COMMIT_REGEX.match(head):
        return match.group(0)
    if match := REF_REGEX.match(head):
        ref_path = os.path.join(git_dir_path, match.group(1))
        return _get_hash_from_git_ref(ref_path)
    LOGGER.error("Unexpected format for .git/HEAD")
    return "Undefined"


def _get_hash_from_git_ref(git_ref_path: str) -> str:
    """Get the commit hash from a ref in the .git directory.

    For more detail see `_get_hash_from_git_dir`

    :param git_ref_path: The full path to the ref file in the .git directory.

    :returns: The hash, or "Undefined" if there is any error, errors are logged not
        raised.
    """
    try:
        with open(git_ref_path, "r", encoding="utf-8") as ref_file:
            ref = ref_file.read()
    except IOError:
        LOGGER.error("Problem opening Git branch reference.")
        return "Undefined"
    if ref_match := COMMIT_REGEX.match(ref):
        return ref_match.group(0)
    LOGGER.error("No hash found in Git ref")
    return "Undefined"


def _get_version_from_toml(toml_path: str) -> str:
    """Get the version string from a pyproject.toml file.

    :param toml_path: The path to the toml file.

    :returns: The version string directly as reported in the toml file, or "Undefined"
        if there is any error, errors are logged not raised.
    """
    try:
        if not tomllib:
            return "Undefined"
        with open(toml_path, "rb") as toml_file:
            toml_dict = tomllib.load(toml_file)
        return toml_dict["project"]["version"]
    except (IOError, ValueError, KeyError):
        LOGGER.error("Problem opening pyproject.toml")
        return "Undefined"


# Use overload to clarify to MyPy that if enforce_dict is true, inputs and outputs are
# dictionaries
@overload
def merge_patch(
    target: dict[str, JSONType], patch: dict[str, JSONType], enforce_dict: Literal[True]
) -> dict[str, JSONType]: ...


# If not they are any JSONType
@overload
def merge_patch(
    target: JSONType, patch: JSONType, enforce_dict: Literal[False]
) -> JSONType: ...
@overload
def merge_patch(target: JSONType, patch: JSONType) -> JSONType: ...


def merge_patch(
    target: JSONType, patch: JSONType, enforce_dict: bool = False
) -> JSONType:
    """Merge json data using the methods from IETF RFC 7396.

    Primarily this is designed to merge dictionaries, but IETF RFC 7396 provides
    defined methods for handling non-dictionary data loaded from JSON, so this
    has been provided in full.

    :param target: The target object
    :param patch: The patch to be applied
    :param enforce_dict: Boolean, set True enfoces that the target and patch are both
        dictionaries.
    """
    if enforce_dict and not (isinstance(target, dict) and isinstance(patch, dict)):
        raise ValueError("Both target and patch should be dictionaries.")

    # If patch is not a dict (object), it replaces target entirely:
    if not isinstance(patch, dict):
        return patch

    # If target is not an object, replace it with an empty object
    if not isinstance(target, dict):
        target = {}

    result = {}
    # First, keep all keys from target that are not in patch
    for key, target_val in target.items():
        if key not in patch:
            result[key] = target_val

    # Then apply patch keys
    for key, patch_val in patch.items():
        # If patch_val is None, do not add the key (deleting from target).
        if patch_val is None:
            continue

        # Check if values are dictionaries.
        if isinstance(patch_val, dict):
            # If patch value is a dictionary then recurse.
            result[key] = merge_patch(target.get(key), patch_val)
        else:
            # Else, use the patch value
            result[key] = patch_val
    return result


def load_patched_config(config_path: str) -> dict:
    """Load a json configuration file, if it a patch, return the full config.

    Load a json file. If it does not contains the "base_config_file" key then return
    the data as read.

    If the configuration specifies a "base_config_file" but no "patch" then return the
    data from reading base_config_file as json.

    If the configuration specifies a "base_config_file" and "patch" then apply the
    patch to the data in base_config_file and return the result. The patching method
    is merge_patch()

    :param config_path: The path of the configuration file.
    :return: The contents of the configuration file after loading and patching the
        specified base configuration file if applicable.

    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except IOError as e:
        raise type(e)(f"Couldn't load configuration file {config_path}") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in configuration file {config_path}: {e}", e.doc, e.pos
        ) from e

    # If base_config_file not specified then this is a normal LabThings config file
    # Just return it.
    if "base_config_file" not in config:
        return config

    config_dir = os.path.dirname(os.path.abspath(config_path))
    base_conf_path = config["base_config_file"]

    base_conf_path = resolve_path_from_dir(base_conf_path, config_dir)

    try:
        with open(base_conf_path, "r", encoding="utf-8") as f:
            base_config = json.load(f)
    except IOError as e:
        raise type(e)(f"Couldn't load base configuration file {base_conf_path}") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in base configuration file {config_path}: {e}", e.doc, e.pos
        ) from e

    if "patch" in config:
        return merge_patch(base_config, config["patch"], enforce_dict=True)

    return base_config


def resolve_path_from_dir(path: str, directory: str) -> str:
    """Convert a relative or abs path specified in one dir to the working dir.

    This also expands any environment variables.

    :param path: The specified path (could be absolute or relative)
    :param directory: The directory from which the path was specified
    :return: The normalised path.
    """
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(directory, path)
    return os.path.normpath(path)


def coerce_thing_selector(
    thing_mapping: Mapping[str, Any],
    selected: Optional[str],
    default: Optional[str] = None,
) -> Optional[str]:
    """Return a valid selector for a mapping of things or None if empty.

    :param thing_mapping: A mapping of str -> ``Thing``
    :param selected: The key of the selected thing (or ``None``)
    :param default: The default key to fallback to if selected is ``None`` or the key is
        missing
    :return: ``None`` if the mapping is empty, else a key that is in the mapping. Order
        of preference is: selected, default, the first key.
    """
    if not thing_mapping:
        # Empty mapping
        if selected is not None:
            LOGGER.warning(
                f"Could not select {selected} from Thing mapping as the mapping is empty."
            )
        # The return is always None if the mapping is empty
        return None

    if selected in thing_mapping:
        # Selected exists, return it.
        return selected

    if selected is not None:
        LOGGER.warning(f"Could not select '{selected}' from Thing mapping")

    if default in thing_mapping:
        return default

    if default is not None:
        LOGGER.warning(f"Could not select default key '{default}' from Thing mapping")

    # Final option is to return the first key
    return list(thing_mapping)[0]
