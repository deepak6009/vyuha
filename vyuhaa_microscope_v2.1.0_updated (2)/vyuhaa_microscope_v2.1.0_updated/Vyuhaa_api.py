"""
Vyuhaa_api.py
─────────────
Hardware abstraction layer for the Vyuhaa Microscope app.

Detects environment (Jetson vs laptop) and _load_dotenv()  # must run before Vyuhaa_api is imported
"""

import sys
import os
import time
import logging
import threading
import platform
from typing import Optional, List, Dict, Any, Tuple, Callable
from dataclasses import dataclass
import numpy as np
import cv2

# --- Scans / Captures Directories ---
ROOT_DIR   = os.path.dirname(os.path.abspath(__file__))
SCANS_DIR  = os.path.join(ROOT_DIR, "scans")
CAPTURES_DIR = os.path.join(ROOT_DIR, "captures")
for _d in (SCANS_DIR, CAPTURES_DIR):
    os.makedirs(_d, exist_ok=True)

# --- Environment Check & Safe Imports ---
def _check_is_jetson() -> bool:
    """Robust check for Jetson environment."""
    try:
        if os.path.exists("/proc/device-tree/model"):
            with open("/proc/device-tree/model", "r") as f:
                model = f.read().lower()
                if "nvidia" in model or "jetson" in model:
                    return True
        # Alternative check
        if os.path.exists("/etc/nv_tegra_release"):
            return True
        if os.path.isdir("/usr/local/cuda"):
            return True
    except:
        pass
    return False

IS_JETSON = _check_is_jetson()
if IS_JETSON:
    print("Environment: Jetson detected via device-tree.")
else:
    print("Environment: Non-Jetson (Laptop/PC) detected.")

# Allow explicit override via HARDWARE_TYPE environment variable
# If not set, we will probe for it.
HARDWARE_OVERRIDE = os.environ.get("HARDWARE_TYPE", "").lower()

# Add project root to sys.path to ensure local stitching module is found
_curr_dir = os.path.dirname(os.path.abspath(__file__))
if _curr_dir not in sys.path:
    sys.path.insert(0, _curr_dir)

# Direct hardware imports — from the local vyuhaa/ package
JetsonCamera, JetsonStage, NemaStage, AutofocusThing = None, None, None, None
SimulatedCamera, DummyStage = None, None

def _load_drivers():
    """Load all available drivers safely."""
    global JetsonCamera, JetsonStage, NemaStage, AutofocusThing, SimulatedCamera, DummyStage
    
    # Always load simulation drivers as ultimate fallback
    try:
        from vyuhaa.things.camera.simulation import SimulatedCamera
        from vyuhaa.things.stage.dummy import DummyStage
        from vyuhaa.things.autofocus import AutofocusThing
    except ImportError as e:
        print(f"Warning: Simulation drivers not available: {e}")

    # Try loading hardware drivers
    try:
        from vyuhaa.things.camera.jetson import JetsonCamera
    except ImportError:
        pass

    try:
        from vyuhaa.things.stage.nema import NemaStage
    except ImportError:
        pass

    try:
        from vyuhaa.things.stage.jetson import JetsonStage
    except ImportError:
        pass

_load_drivers()

log = logging.getLogger(__name__)


# --- Mock Components ---
class MockStream:
    """Mock MJPEGStream to keep camera threads happy."""
    def __init__(self): self._streaming = False
    def add_frame(self, frame): pass
    def grab_frame(self): return b""
    async def frame_async_generator(self): yield b""
    def stop(self): pass


class MockInterface:
    def __init__(self):
        self.states = {}
        self._loop = None
        self._thread = None
        self._start_loop()

    def _start_loop(self):
        import asyncio
        import threading
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        time.sleep(0.1)

    def get_thing_states(self): return self.states
    def start_async_task_soon(self, func, *args, **kwargs):
        import asyncio
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(func(*args, **kwargs), self._loop)
    def call_async_task(self, func, *args, **kwargs):
        return func(*args, **kwargs) if callable(func) else None


@dataclass
class DeviceInfo:
    name: str
    id: str
    connected: bool = False
    is_simulation: bool = False


@dataclass
class StagePosition:
    x: int = 0
    y: int = 0
    z: int = 0


@dataclass
class ScanConfig:
    width_tiles:  int = 10
    height_tiles: int = 10
    overlap_pct:  int = 10
    save_mode:    str = "save_only"   # "save_only" | "both" | "stitched_only"
    scan_name:    str = ""
    use_autofocus: bool = False


# --- GPIO helpers (Jetson only) ---
def hard_reset_gpio():
    if not IS_JETSON:
        return
    print("Performing GPIO Hard Reset...")
    commands = [
        "sudo busybox devmem 0x2430098 w 0x5",
        "sudo busybox devmem 0x243D030 w 0x1005",
        "sudo busybox devmem 0x2440020 w 0x5",
        "sudo busybox devmem 0x243D020 w 0x5",
        "sudo busybox devmem 0x243D010 w 0x5",
        "sudo busybox devmem 0x243D000 w 0x5",
        "sudo busybox devmem 0x2430068 w 0x8",
        "sudo busybox devmem 0x2430070 w 0x8",
        "sudo busybox devmem 0x2434080 w 0x5",
        "sudo busybox devmem 0x2434040 w 0x4",
        "sudo busybox devmem 0x2430090 w 0x5",
        "sudo busybox devmem 0x243D048 w 0x5",
    ]
    for cmd in commands:
        os.system(cmd)
    try:
        import Jetson.GPIO as GPIO
        GPIO.setmode(GPIO.BOARD)
        GPIO.cleanup()
        print("GPIO cleanup successful.")
    except Exception as e:
        print(f"GPIO cleanup warning: {e}")


def force_stop_competing_processes():
    if not IS_JETSON:
        return
    print("Checking for competing processes...")
    import subprocess
    try:
        cmd = "sudo lsof /dev/video0 /dev/gpiochip* 2>/dev/null | awk '{print $2}' | grep -v PID | sort -u"
        pids = subprocess.check_output(cmd, shell=True).decode().split()
        my_pid = str(os.getpid())
        for pid in pids:
            if pid != my_pid:
                print(f"Stopping competing process: {pid}")
                subprocess.run(["sudo", "kill", "-9", pid], stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --- Global State ---
_camera: Optional[Any] = None
_stage:  Optional[Any] = None
_autofocus: Optional[Any] = None
_interface = MockInterface()
_abort_scan = False


# --- Discovery & Connection ---
def discover_devices() -> List[DeviceInfo]:
    name = "Local Jetson Hardware" if IS_JETSON else "Microscope Driver (Simulation)"
    return [DeviceInfo(name=name, id="local_device", is_simulation=not IS_JETSON)]


def get_connection_status() -> bool:
    return _camera is not None and _stage is not None


def connect(device: Optional[DeviceInfo] = None) -> bool:
    global _camera, _stage, _autofocus
    try:
        # 1. Jetson-Specific Pre-Connect
        if IS_JETSON:
            force_stop_competing_processes()
            hard_reset_gpio()
            try:
                import Jetson.GPIO as GPIO
                GPIO.setmode(GPIO.BOARD)
                GPIO.cleanup()
            except Exception as e:
                print(f"Pre-connect GPIO notice: {e}")

        # 2. Stage Probing (NEMA -> GPIO -> Simulation)
        print("Probing for stage hardware...")
        
        # Path A: NEMA (Serial)
        if NemaStage:
            try:
                print("Attempting NemaStage (Serial) connection...")
                test_stage = NemaStage(baudrate=250000)
                test_stage.__enter__()
                # Strict check: did we actually find a serial port?
                if getattr(test_stage, "_simulated", False):
                    print("❌ NemaStage probe failed: No firmware detected on serial ports.")
                    test_stage.__exit__(None, None, None)
                    _stage = None
                else:
                    _stage = test_stage
                    print("✅ NemaStage detected and connected via serial.")
            except Exception as e:
                print(f"❌ NemaStage probe failed: {e}")
                _stage = None

        # Path B: GPIO (28BYJ-48)
        if _stage is None and (IS_JETSON or HARDWARE_OVERRIDE == "jetson") and JetsonStage:
            try:
                print("Attempting JetsonStage (GPIO) connection...")
                test_stage = JetsonStage(_interface)
                test_stage.__enter__()
                # Strict check: did the GPIO module actually load?
                if getattr(test_stage, "_simulated", False):
                    print("❌ JetsonStage probe failed: Jetson.GPIO not available or not on a Jetson.")
                    test_stage.__exit__(None, None, None)
                    _stage = None
                else:
                    _stage = test_stage
                    print("✅ JetsonStage detected and connected via GPIO.")
            except Exception as e:
                print(f"❌ JetsonStage probe failed: {e}")
                _stage = None

        # Path C: Simulation Fallback
        if _stage is None:
            if DummyStage:
                print("⚠️ No hardware stage detected. Falling back to Simulation/DummyStage.")
                _stage = DummyStage(_interface)
                _stage.__enter__()
            else:
                print("🛑 Fatal: No stage drivers available (even DummyStage).")
                return False

        print(f"ACTIVE STAGE: {type(_stage).__name__} (Simulated: {getattr(_stage, '_simulated', False)})")

        # 3. Camera Probing (Hardware -> Simulation)
        print("Probing for camera hardware...")
        if IS_JETSON and JetsonCamera:
            try:
                cal_path = os.path.join(CAPTURES_DIR, "camera_calibration.npy")
                _camera = JetsonCamera(_interface, cal_file_path=cal_path)
                _camera.__enter__()
                time.sleep(2.0)
                print("✅ JetsonCamera connected.")
            except Exception as e:
                print(f"❌ Hardware camera failed ({e}). Falling back to simulation.")
                _camera = None

        if _camera is None and SimulatedCamera:
            print("⚠️ Falling back to SimulatedCamera.")
            _camera = SimulatedCamera(_interface)
            _camera._stage = _stage
            _camera.__enter__()

        # 4. Autofocus Init
        if AutofocusThing:
            _autofocus = AutofocusThing(_interface)
            _autofocus._cam   = _camera
            _autofocus._stage = _stage

        print(f"Connection complete. Mode: {'Hardware' if not getattr(_camera, 'is_simulation', False) else 'Simulation'}")
        return True
    except Exception as e:
        log.error(f"Critical failure during connect: {e}", exc_info=True)
        disconnect()
        return False


def disconnect():
    global _camera, _stage, _autofocus
    if _camera:
        try: _camera.__exit__(None, None, None)
        except: pass
    if _stage:
        try: _stage.__exit__(None, None, None)
        except: pass
    _camera = _stage = _autofocus = None


# --- Stage Control ---
def get_position() -> StagePosition:
    if _stage:
        p = _stage.position
        return StagePosition(x=int(p.get("x", 0)), y=int(p.get("y", 0)), z=int(p.get("z", 0)))
    return StagePosition()


def move_relative(dx: int = 0, dy: int = 0, dz: int = 0) -> StagePosition:
    if _stage:
        available = _stage.axis_names
        kwargs = {}
        if 'x' in available: kwargs['x'] = int(dx)
        if 'y' in available: kwargs['y'] = int(dy)
        if 'z' in available: kwargs['z'] = int(dz)
        _stage.move_relative(**kwargs)
        return get_position()
    return StagePosition()


def move_absolute(x: Optional[float] = None, y: Optional[float] = None,
                  z: Optional[float] = None) -> StagePosition:
    print(f"API: move_absolute(x={x}, y={y}, z={z})")
    if _stage:
        available = _stage.axis_names
        kwargs = {}
        if 'x' in available and x is not None: kwargs["x"] = int(round(x))
        if 'y' in available and y is not None: kwargs["y"] = int(round(y))
        if 'z' in available and z is not None: kwargs["z"] = int(round(z))
        print(f"DEBUG: Stage move_absolute kwargs: {kwargs}")
        try:
            _stage.move_absolute(**kwargs)
        except Exception as e:
            print(f"ERROR: _stage.move_absolute failed: {e}")
            import traceback; traceback.print_exc()
        return get_position()
    else:
        print("WARNING: move_absolute called but _stage is None!")
    return StagePosition()


def zero_stage():
    if _stage:
        _stage.set_zero_position()


# --- Camera Control ---
def grab_frame_array() -> Optional[np.ndarray]:
    if _camera:
        try:
            return _camera.capture_array()
        except Exception as e:
            log.debug(f"Grab error: {e}")
    return None


def capture_frame() -> bytes:
    arr = grab_frame_array()
    if arr is not None:
        _, encoded = cv2.imencode(".jpg", cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        return encoded.tobytes()
    return b""


def get_stream_url() -> str:
    return "internal://direct-access"


# --- Autofocus / Focus Quality ---
def run_autofocus() -> float:
    if _autofocus:
        try:
            # Note: Using the specific laplacian routine from the thing
            results = _autofocus.laplacian_autofocus(dz=2000)
            return float(max(results.get("sharpness", [0.0])))
        except Exception as e:
            print(f"Autofocus failed: {e}")
    return 0.0


def get_focus_score() -> float:
    """Calculate live Tenengrad/Laplacian focus score for UI feedback."""
    arr = grab_frame_array()
    if arr is not None:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return 0.0


# --- Calibration ---
def run_white_balance() -> Tuple[float, float, float]:
    log.info("Running White Balance...")
    return (1.0, 1.0, 1.0)


def capture_flatfield() -> Any:
    log.info("Capturing Flatfield...")
    return True


def clear_flatfield():
    log.info("Clearing Flatfield...")


def get_available_axes() -> List[str]:
    """Return a list of axis names actually found on the hardware (e.g. ['x', 'y', 'z'] or just ['z'])."""
    if _stage:
        return list(_stage.axis_names)
    return []


# --- Autofocus / Focus Quality ---
def run_autofocus() -> float:
    if _autofocus:
        try:
            results = _autofocus.laplacian_autofocus(dz=10000)
            return float(max(results.get("sharpness", [0.0])))
        except Exception as e:
            print(f"Autofocus failed: {e}")
    return 0.0


def jump_to_focus() -> bool:
    """Jump to the predicted focus position based on previous autofocus results."""
    if _autofocus:
        return _autofocus.jump_to_focus()
    return False


def get_focus_score() -> float:
    arr = grab_frame_array()
    if arr is not None:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return 0.0


# --- Calibration ---
def run_white_balance() -> Tuple[float, float, float]:
    log.info("Running White Balance...")
    return (1.0, 1.0, 1.0)


def capture_flatfield() -> Any:
    log.info("Capturing Flatfield...")
    if _camera:
        res = _camera.calibrate_pink_fix()
        _camera.save_calibration()
        return res
    return False


def clear_flatfield():
    log.info("Clearing Flatfield...")
    if _camera:
        _camera.clear_calibration()


# --- Scan Control ---
def start_scan(config: ScanConfig,
               tile_callback: Optional[Callable] = None,
               done_callback: Optional[Callable] = None):
    """Run a tile scan according to config.

    Save modes
    ----------
    save_only      : capture each tile and write it to disk as JPEG; no stitching.
    both           : write each tile to disk AND push the numpy frame into the
                     stitching queue; produce an OME-TIFF alongside the JPEGs.
    stitched_only  : push each numpy frame directly into the stitching queue
                     (never written to disk); produce only the OME-TIFF.
    """
    from datetime import datetime
    try:
        from stitching import ProcessCollection, d_que
    except ImportError as e:
        print(f"ERROR: Could not import 'stitching' module: {e}")
        from stitching import ProcessCollection, d_que

    global _abort_scan
    _abort_scan = False

    save_mode    = config.save_mode
    do_save      = save_mode in ("save_only", "both")
    do_stitch    = save_mode in ("both", "stitched_only")

    # ── output directory (needed for save_only / both) ────────────────────────
    ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_dir   = os.path.join(SCANS_DIR, f"scan_{ts}")
    images_dir = os.path.join(scan_dir, "images")
    if do_save:
        os.makedirs(images_dir, exist_ok=True)
    else:
        os.makedirs(scan_dir, exist_ok=True)

    # ── minimal frame wrapper expected by d_que / ProcessCollection ─────────
    class _Frame:
        def __init__(self, image):
            self.image      = image
            self.expected_x = None
            self.expected_y = None

    # ── get live camera frame dimensions ─────────────────────────────────────
    test_frame = grab_frame_array()
    if test_frame is not None:
        cam_h, cam_w = test_frame.shape[:2]
    else:
        cam_w, cam_h = 1280, 720   # safe fallback

    # ── set up stitching queue + engine ───────────────────────────────────────
    queue    = None
    proc     = None
    stitch_t = None

    if do_stitch:
        class _MotionCurrent:
            x_steps                  = config.width_tiles
            y_steps                  = config.height_tiles
            target_overlap_percentage = config.overlap_pct

        queue = d_que()
        proc  = ProcessCollection(
            queue,
            motion_current   = _MotionCurrent(),
            camera_width     = cam_w,
            camera_height    = cam_h,
            pixel_size_um    = None,
            enable_blending  = False,
        )
        stitch_t = threading.Thread(target=proc.process_frames_thread, daemon=True)
        stitch_t.start()
        log.info(f"Stitching thread started ({cam_w}x{cam_h}, "
                 f"{config.width_tiles}x{config.height_tiles} tiles)")

    # ── main capture loop ─────────────────────────────────────────────────────
    print(f"Starting scan: {config}")
    
    # Get starting position and calculate step sizes in microns
    start_pos = get_position()
    start_x, start_y = start_pos.x, start_pos.y
    
    # Calculate step size in microns — how far to move between each tile.
    # We use the camera field-of-view minus overlap. This is hardware-agnostic
    # and always produces a valid float, unlike proc attributes that may not exist.
    # Assumption: 1px = 1um (can be calibrated via pixel_size_um in the future).
    step_x_um = float(cam_w) * (1.0 - config.overlap_pct / 100.0)
    step_y_um = float(cam_h) * (1.0 - config.overlap_pct / 100.0)
    print(f"Scan step size: X={step_x_um:.1f}um, Y={step_y_um:.1f}um per tile")

    for r in range(config.height_tiles):
        for c in range(config.width_tiles):
            if _abort_scan:
                break

            # Move to target tile position
            target_x = start_x + (c * step_x_um)
            target_y = start_y + (r * step_y_um)
            
            print(f"DEBUG: Processing TILE ({r},{c}) | Target: X={target_x:.1f}um, Y={target_y:.1f}um")
            try:
                move_absolute(x=target_x, y=target_y)
            except Exception as e:
                print(f"ERROR in move_absolute at tile ({r},{c}): {e}")
                import traceback; traceback.print_exc()

            # Optional: Autofocus at each point
            if config.use_autofocus:
                print(f"DEBUG: Running autofocus at TILE ({r},{c})...")
                run_autofocus()

            # Small settle time for vibration-free imaging
            time.sleep(0.1)

            frame_np = grab_frame_array()
            if frame_np is None:
                frame_np = np.zeros((cam_h, cam_w, 3), dtype=np.uint8)

            # Save to disk
            if do_save:
                _, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR))
                fname = os.path.join(images_dir, f"tile_{r:03d}_{c:03d}.jpg")
                with open(fname, "wb") as f:
                    f.write(encoded.tobytes())

            # Push into stitching queue
            if do_stitch:
                frm           = _Frame(cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR))
                frm.expected_x = float(c)
                frm.expected_y = float(r)
                queue.append(frm)

            if tile_callback:
                _, enc = cv2.imencode(".jpg", cv2.cvtColor(frame_np, cv2.COLOR_RGB2BGR))
                tile_callback(c, r, enc.tobytes())

        if _abort_scan:
            break

    # ── wait for stitching and save output ────────────────────────────────────
    if do_stitch and stitch_t is not None:
        log.info("Waiting for stitching thread to finish...")
        stitch_t.join()
        if proc.wsi_dask.processed_da is not None:
            out_path = os.path.join(scan_dir, "stitched.ome.tiff")
            try:
                proc.wsi_dask.save_processed_images(
                    out_path,
                    pixel_size_um = 1.0,
                    step_y        = int(proc.step_y_px),
                )
                log.info(f"Stitched image saved: {out_path}")
            except Exception as e:
                log.error(f"Failed to save stitched image: {e}")

    print("Scan ended.")
    if done_callback:
        done_callback()


def abort_scan():
    global _abort_scan
    _abort_scan = True


# --- Files ---
def get_scans_list() -> List[Dict[str, Any]]:
    from vyuhaa.scan_directories import ScanDirectoryManager
    manager = ScanDirectoryManager(SCANS_DIR)
    scans = []
    if not os.path.exists(SCANS_DIR):
        return []
    for name in os.listdir(SCANS_DIR):
        full_path = os.path.join(SCANS_DIR, name)
        file_type = "cap"
        size_bytes = 0
        images = 0
        if os.path.isdir(full_path):
            stats = os.stat(full_path)
            created = stats.st_ctime
            for root, dirs, files in os.walk(full_path):
                for f in files:
                    size_bytes += os.path.getsize(os.path.join(root, f))
            try:
                info = manager.get_scan_info(name)
                images = info.number_of_images
                file_type = "msi" if images > 10 else "cap"
            except Exception:
                pass
            if "cal" in name.lower() or "flatfield" in name.lower():
                file_type = "cal"
        else:
            size_bytes = os.path.getsize(full_path)
            created = os.path.getctime(full_path)
            ext = os.path.splitext(name)[1].lower()
            if ext in (".zip", ".tar", ".gz"):
                file_type = "zip"
            elif ext in (".ndpi", ".vsi"):
                file_type = "msi"
            elif ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                file_type = "cap"
            elif ext in (".json", ".npy", ".csv") and ("cal" in name.lower() or "flat" in name.lower()):
                file_type = "cal"
            images = 1
        scans.append({
            "id":         name,
            "name":       name,
            "created":    created,
            "size_bytes": size_bytes,
            "type":       file_type,
            "images":     images,
        })
    scans.sort(key=lambda x: x["created"], reverse=True)
    return scans


def get_disk_usage() -> Dict[str, Any]:
    """Calculate used vs total space for the UI dashboard."""
    import shutil
    total, used_sys, free = shutil.disk_usage(SCANS_DIR)
    folder_size = 0
    if os.path.exists(SCANS_DIR):
        for root, dirs, files in os.walk(SCANS_DIR):
            for f in files:
                try:
                    folder_size += os.path.getsize(os.path.join(root, f))
                except: pass
    return {
        "folder_size":    folder_size,
        "total_capacity": total,
        "free_space":     free,
        "percent_used":   (folder_size / total * 100) if total > 0 else 0,
    }


def delete_scan(scan_name: str) -> bool:
    """Permanently delete a scan folder."""
    import shutil
    full_path = os.path.join(SCANS_DIR, scan_name)
    if os.path.exists(full_path) and os.path.isdir(full_path):
        shutil.rmtree(full_path)
        return True
    return False


# --- Relay & Remote Access ---
_relay_active = False
_relay_url    = ""
_relay_room   = ""


def relay_connect(url: str, room: str, password: str = "") -> bool:
    global _relay_active, _relay_url, _relay_room
    print(f"Relay: Connecting to {url} (Room: {room})...")
    time.sleep(0.5)
    _relay_active = True
    _relay_url    = url
    _relay_room   = room
    print("Relay: Connected.")
    return True


def relay_disconnect() -> bool:
    global _relay_active
    _relay_active = False
    print("Relay: Disconnected.")
    return True


def get_relay_status() -> Dict[str, Any]:
    return {
        "active":        _relay_active,
        "url":           _relay_url,
        "room":          _relay_room,
        "client_count":  0,
    }

# --- Storage Utility ---
def open_scan_folder(name: str) -> bool:
    """Open the scan folder or best available image in the system file explorer."""
    import subprocess
    
    # Build candidate paths to try opening
    candidates = []
    
    # 1. If it's a directory in SCANS_DIR
    dir_path = os.path.join(SCANS_DIR, name)
    if os.path.isdir(dir_path):
        # Prefer stitched tiff
        stitched = os.path.join(dir_path, "stitched.ome.tiff")
        if os.path.exists(stitched):
            candidates.append(stitched)
        # Fallback: first jpg in images/ subfolder
        images_dir = os.path.join(dir_path, "images")
        if os.path.isdir(images_dir):
            jpegs = sorted([f for f in os.listdir(images_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.tif'))])
            if jpegs:
                candidates.append(os.path.join(images_dir, jpegs[0]))
        candidates.append(dir_path)  # Finally open folder itself
    else:
        # It might be a direct file path (captures etc.)
        direct = os.path.join(SCANS_DIR, name)
        candidates.append(direct)
        # Also try CAPTURES_DIR
        cap = os.path.join(CAPTURES_DIR, name)
        if os.path.exists(cap):
            candidates.append(cap)
    
    target = next((c for c in candidates if os.path.exists(c)), None)
    if target is None:
        print(f"open_scan_folder: Nothing found for '{name}'. Tried: {candidates}")
        return False
    
    print(f"Opening: {target}")
    try:
        if platform.system() == "Windows":
            os.startfile(target)
            return True
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", target], start_new_session=True)
            return True
        else:
            # Linux / Jetson Ubuntu — try multiple launchers
            for launcher in ["eog", "eom", "display", "xdg-open", "nautilus"]:
                try:
                    subprocess.Popen(
                        [launcher, target],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    print(f"Opened with: {launcher}")
                    return True
                except FileNotFoundError:
                    continue  # This launcher isn't installed, try next
            print(f"No viewer found. File is at: {target}")
            return False
    except Exception as e:
        print(f"open_scan_folder error: {e}")
        return False


def get_disk_usage() -> Dict[str, Any]:
    import shutil
    total, used_sys, free = shutil.disk_usage(SCANS_DIR)
    folder_size = 0
    for root, dirs, files in os.walk(SCANS_DIR):
        for f in files:
            folder_size += os.path.getsize(os.path.join(root, f))
    return {
        "folder_size":    folder_size,
        "total_capacity": total,
        "free_space":     free,
        "percent_used":   (folder_size / total * 100) if total > 0 else 0,
    }


def delete_scan(scan_name: str) -> bool:
    import shutil
    full_path = os.path.join(SCANS_DIR, scan_name)
    if os.path.exists(full_path) and os.path.isdir(full_path):
        shutil.rmtree(full_path)
        return True
    return False


# --- Relay & Remote Access ---
_relay_active = False
_relay_url    = ""
_relay_room   = ""


def relay_connect(url: str, room: str, password: str = "") -> bool:
    global _relay_active, _relay_url, _relay_room
    print(f"Relay: Connecting to {url} (Room: {room})...")
    time.sleep(1.0)
    _relay_active = True
    _relay_url    = url
    _relay_room   = room
    print("Relay: Connected.")
    return True


def relay_disconnect() -> bool:
    global _relay_active
    _relay_active = False
    print("Relay: Disconnected.")
    return True


def get_relay_status() -> Dict[str, Any]:
    return {
        "active":        _relay_active,
        "url":           _relay_url,
        "room":          _relay_room,
        "client_count":  0,
    }