"""Vyuhaa Microscope autofocus module.

This module defines a Thing that is responsible for using the stage and
camera together to perform an autofocus routine, and for collecting stacks
of images (a 'z-stack').

This version is a complete, high-precision port of the OpenFlexure Microscope logic,
specifically adapted for the Jetson Nano and Vyuhaa's 1mm/rev hardware.

See repository root for licensing information.
"""

from __future__ import annotations
import logging
import os
import time
import threading
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Mapping, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from .camera import BaseCamera
from .stage import BaseStage

LOGGER = logging.getLogger(__name__)

# --- OFM Standards ---
MIN_TEST_IMAGE_COUNT = 3
MAX_TEST_IMAGE_COUNT = 25  # Relaxed for high-density fast scans
EXTRA_STACK_CAPTURES = 15


class NotStreamingError(RuntimeError):
    """No images captured from stream. The camera is almost certainly not streaming."""


class NotAPeakError(RuntimeError):
    """The data to fit isn't a peak."""


class NoFocusFoundError(RuntimeError):
    """No focus found during looping Autofocus."""


class StackParams(BaseModel):
    """A class for holding for stack parameters, and returning computed ones."""

    stack_dz: int
    images_to_save: int
    min_images_to_test: int
    autofocus_dz: int
    images_dir: str
    save_resolution: tuple[int, int]

    settling_time: float = 0.3
    backlash_correction: int = 1000
    stack_height_limit: int = 15
    img_undershoot: int = 5
    max_attempts: int = 3

    @field_validator("min_images_to_test")
    @classmethod
    def check_images_to_test(cls, min_images_to_test: int) -> int:
        if min_images_to_test < MIN_TEST_IMAGE_COUNT:
            raise ValueError(f"Can't test focus with <{MIN_TEST_IMAGE_COUNT} images.")
        return min_images_to_test

    @field_validator("images_to_save")
    @classmethod
    def check_images_to_save(cls, images_to_save: int) -> int:
        if images_to_save % 2 == 0 or images_to_save <= 0:
            raise ValueError("Images to save must be positive and odd")
        return images_to_save

    @model_validator(mode="after")
    def check_image_limits(self) -> "StackParams":
        if self.images_to_save > self.min_images_to_test:
            raise ValueError("Can't save more images than tested.")
        return self

    @computed_field  
    @property
    def stack_z_range(self) -> int:
        return self.stack_dz * (self.min_images_to_test - 1)

    @computed_field  
    @property
    def steps_undershoot(self) -> int:
        return self.stack_dz * self.img_undershoot

    @computed_field  
    @property
    def max_images_to_test(self) -> int:
        return self.min_images_to_test + EXTRA_STACK_CAPTURES

    def slice_to_save(self, sharpest_index: int) -> slice:
        images_each_side = (self.images_to_save - 1) // 2
        return slice(
            max(sharpest_index - images_each_side, 0),
            sharpest_index + images_each_side + 1,
        )


@dataclass
class CaptureInfo:
    """The information from a capture in a z_stack."""

    buffer_id: int
    position: Mapping[str, int]
    sharpness: float

    @property
    def filename(self) -> str:
        return f"img_{self.position['x']}_{self.position['y']}_{self.position['z']}.jpeg"


def _get_capture_by_id(captures: list[CaptureInfo], buffer_id: int) -> CaptureInfo:
    return captures[_get_capture_index_by_id(captures, buffer_id)]


def _get_capture_index_by_id(captures: list[CaptureInfo], buffer_id: int) -> int:
    ids = [capture.buffer_id for capture in captures]
    if buffer_id not in ids:
        raise ValueError(f"No capture has a buffer id of {buffer_id}")
    return ids.index(buffer_id)


class SharpnessDataArrays(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    jpeg_times: np.ndarray
    jpeg_sizes: np.ndarray
    stage_times: np.ndarray
    stage_positions: list[dict[str, int]]


class JPEGSharpnessMonitor:
    """The 'Monitor' class that enables continuous sampling during movement.
    
    This is the core 'mathematical logic' addition required for precision autofocus.
    """

    def __init__(self, stage: BaseStage, camera: BaseCamera) -> None:
        self.camera = camera
        self.stage = stage
        self.reset_data()
        self._thread: Optional[threading.Thread] = None
        self.running = False

    def reset_data(self):
        self.stage_positions: list[Mapping[str, int]] = []
        self.stage_times: list[float] = []
        self.jpeg_times: list[float] = []
        self.jpeg_sizes: list[float] = []

    def monitor_sharpness(self) -> None:
        """Poll the camera as fast as frames arrive."""
        import cv2
        last_frame_size = -1
        while self.running:
            try:
                # Fast Check: JPEG Size (used for majority of sweep)
                current_size = self.camera.lores_mjpeg_stream.next_frame_size()
                
                # Option for high-precision Laplacian on thumbnails (if needed)
                # For now, JPEG size is the OFM fast-autofocus standard.
                if current_size != last_frame_size:
                    self.jpeg_times.append(time.time())
                    self.jpeg_sizes.append(float(current_size))
                    last_frame_size = current_size
            except:
                pass
            time.sleep(0.01)

    def __enter__(self) -> JPEGSharpnessMonitor:
        self.running = True
        self._thread = threading.Thread(target=self.monitor_sharpness, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=0.5)

    def focus_rel(self, dz: int) -> tuple[int, int]:
        """Perform a blocking move and record the start/end timestamps."""
        self.stage_times.append(time.time())
        self.stage_positions.append(self.stage.position.copy())
        
        self.stage.move_relative(z=dz)

        self.stage_times.append(time.time())
        self.stage_positions.append(self.stage.position.copy())
        return len(self.stage_positions) - 2, self.stage.position["z"]

    def move_data(self, idx: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Interpolate the collected sizes to find focus as a function of Z."""
        st = np.array(self.stage_times)[idx:idx+2]
        sz = np.array([p["z"] for p in self.stage_positions[idx:idx+2]])
        jt = np.array(self.jpeg_times)
        js = np.array(self.jpeg_sizes)

        # Mask data within this move's window
        mask = (jt >= st[0]) & (jt <= st[1])
        t_seg = jt[mask]
        s_seg = js[mask]

        if len(t_seg) < 3:
            return st, sz, np.array([0, 0])

        # Mathematical mapping: linear interp of Time -> Z
        z_seg = np.interp(t_seg, st, sz)
        return t_seg, z_seg, s_seg

    def data_dict(self) -> SharpnessDataArrays:
        return SharpnessDataArrays(
            jpeg_times=np.array(self.jpeg_times),
            jpeg_sizes=np.array(self.jpeg_sizes),
            stage_times=np.array(self.stage_times),
            stage_positions=[dict(p) for p in self.stage_positions]
        )


class AutofocusThing:
    """The high-level Thing for performing autofocus and stacks."""

    def __init__(self, thing_server_interface: Any = None) -> None:
        self._focal_surface = FocalSurface()

    _cam: BaseCamera = None
    _stage: BaseStage = None

    def laplacian_autofocus(self, dz: int = 4000) -> dict:
        """The primary UI entry point. Implements a Two-Pass robust focus."""
        LOGGER.info(f"Starting High-Precision Two-Pass Autofocus (sweep={dz})...")
        try:
            # We use looping_autofocus as it handles re-centering and convergence
            heights, sizes = self.looping_autofocus(dz=dz)
            return {"z": heights, "sharpness": sizes}
        except Exception as e:
            LOGGER.error(f"Autofocus Convergence Failed: {e}")
            raise

    def predict_focus(self) -> Optional[float]:
        """Predict focus at current (X, Y) location."""
        pos = self._stage.position
        return self._focal_surface.predict(pos["x"], pos["y"])

    def jump_to_focus(self) -> bool:
        """Jump to predicted focus position."""
        z = self.predict_focus()
        if z is not None:
            LOGGER.info(f"Jumping to predicted focus: Z={int(z)}")
            # Apply backlash correction logic
            backlash = 1000
            self._stage.move_absolute(z=int(z - backlash))
            self._stage.move_absolute(z=int(z))
            return True
        LOGGER.warning("No focal surface data available to predict focus.")
        return False

    def fast_autofocus(self, dz: int = 2000) -> SharpnessDataArrays:
        """Perform a single-pass monitor-based focus sweep."""
        with JPEGSharpnessMonitor(self._stage, self._cam) as monitor:
            # Backlash approach
            backlash = 1000
            self._stage.move_relative(z=-int(backlash + dz // 2))
            self._stage.move_relative(z=backlash)
            
            # Sweep
            idx, _ = monitor.focus_rel(dz)
            _, heights, sizes = monitor.move_data(idx)
            
            if len(sizes) < 3:
                raise NotStreamingError("Not enough frames captured. Is the camera on?")

            # Fit peak
            try:
                turning = _get_peak_turning_point(sizes)
                # Linear interpolation for peak_z
                l_idx = int(np.floor(turning))
                u_idx = int(np.ceil(turning))
                if l_idx == u_idx: peak_z = int(heights[l_idx])
                else: peak_z = int(heights[l_idx] + (turning - l_idx) * (heights[u_idx] - heights[l_idx]))
                LOGGER.info(f"Fitted Peak: Z={peak_z} (confidence check passed)")
            except Exception as e:
                LOGGER.warning(f"Peak fitting failed ({e}), using maximum index.")
                peak_z = int(heights[np.argmax(sizes)])

            # Final Move
            self._stage.move_absolute(z=int(peak_z - backlash // 2))
            self._stage.move_absolute(z=peak_z)
            return monitor.data_dict()

    def looping_autofocus(self, dz: int = 3000) -> tuple[list[float], list[float]]:
        """A convergence loop that ensures the focus is centrally located."""
        attempt = 0
        backlash = 1000
        while attempt < 10:
            attempt += 1
            LOGGER.info(f"Convergence Attempt {attempt}/10...")
            
            with JPEGSharpnessMonitor(self._stage, self._cam) as monitor:
                # Approach start
                self._stage.move_relative(z=-int(backlash + dz // 2))
                self._stage.move_relative(z=backlash)
                
                # Data Capture
                idx, _ = monitor.focus_rel(dz)
                _, heights, sizes = monitor.move_data(idx)
                
                if not sizes.any(): raise NotStreamingError("No camera response.")

                # Analysis
                try:
                    p_val = np.max(sizes)
                    p_idx = np.argmax(sizes)
                    peak_z = heights[p_idx]
                    
                    # Refine peak_z with parabola fit if possible
                    try: 
                        t = _get_peak_turning_point(sizes)
                        l = int(np.floor(t))
                        if l < len(heights) - 1:
                            peak_z = heights[l] + (t - l) * (heights[l+1] - heights[l])
                    except: pass

                    # CONVERGENCE CHECK (Full Logic Requirement)
                    # Peak must be within the central 60% of the scan
                    z_min, z_max = np.min(heights), np.max(heights)
                    tolerance = (z_max - z_min) * 0.2
                    
                    self._stage.move_absolute(z=int(peak_z - backlash))
                    self._stage.move_absolute(z=int(peak_z))

                    if (z_min + tolerance) < peak_z < (z_max - tolerance):
                        LOGGER.info(f"Autofocus Converged at Z={int(peak_z)}")
                        # Record point for interpolation
                        self._focal_surface.add_point(self._stage.position["x"], self._stage.position["y"], peak_z)
                        return heights.tolist(), sizes.tolist()
                    else:
                        LOGGER.warning(f"Peak at {int(peak_z)} outside central safe-zone. Re-looping...")
                        
                except Exception as e:
                    LOGGER.error(f"Scan analysis error: {e}")

        raise NoFocusFoundError("Autofocus failed to find clean center in 10 attempts.")

    # --- Z-Stack Logic (Restored Full Logic) ---

    def create_stack_params(self, images_dir: str, autofocus_dz: int, save_resolution: tuple[int, int]) -> StackParams:
        # User defined defaults in Vyuhaa
        return StackParams(
            stack_dz=50,
            images_to_save=1,
            min_images_to_test=9,
            autofocus_dz=autofocus_dz,
            images_dir=images_dir,
            save_resolution=save_resolution
        )

    def run_smart_stack(self, params: StackParams, save_on_failure: bool = False, check_turning_points: bool = True) -> tuple[bool, int]:
        """Capture images around focus, with auto-refocus if failed."""
        attempt = 0
        while attempt < params.max_attempts:
            attempt += 1
            success, captures, best_bid = self.z_stack(params, check_turning_points)
            if success: break
            
            # Auto-refocus logic
            start_z = captures[0].position["z"] if captures else self._stage.position["z"]
            self._stage.move_absolute(z=start_z)
            try: self.looping_autofocus(dz=params.autofocus_dz)
            except: break
            
        if success or save_on_failure:
            self.save_stack(best_bid, captures, params)
            
        best_z = _get_capture_by_id(captures, best_bid).position["z"] if captures else 0
        return success, best_z

    def z_stack(self, params: StackParams, check_turning_points: bool) -> tuple[bool, list[CaptureInfo], int]:
        """Capture a discrete series of images until focus is confirmed central."""
        # Initial position with backlash
        self._stage.move_relative(z=-int(params.steps_undershoot + params.backlash_correction + params.stack_z_range // 2))
        self._stage.move_relative(z=params.backlash_correction)
        
        captures: list[CaptureInfo] = []
        while len(captures) < params.max_images_to_test:
            time.sleep(params.settling_time)
            
            # Discrete Capture
            loc = self._stage.position.copy()
            bid = self._cam.capture_to_memory(buffer_max=params.min_images_to_test)
            score = float(self._cam.grab_jpeg_size(stream_name="lores"))
            captures.append(CaptureInfo(buffer_id=bid, position=loc, sharpness=score))
            
            if len(captures) >= params.min_images_to_test:
                # Check recent window
                win = captures[-params.min_images_to_test:]
                res, best_bid = self.check_stack_result(win, check_turning_points)
                if res == "success": return True, captures, best_bid
                if res == "restart": return False, captures, best_bid
                
            self._stage.move_relative(z=params.stack_dz)
        return False, captures, 0

    def check_stack_result(self, captures: list[CaptureInfo], check_tp: bool) -> tuple[Literal["success", "continue", "restart"], int]:
        scores = np.array([c.sharpness for c in captures])
        best_idx = np.argmax(scores)
        bid = captures[best_idx].buffer_id
        
        try:
            t = _get_peak_turning_point(scores)
            edge = 2
            if t < edge - 0.5 or best_idx < edge: return "restart", bid
            if t > len(scores) - edge - 0.5 or best_idx >= len(scores) - edge: return "continue", bid
            if check_tp and _count_turning_points(scores) > 1: return "continue", bid
            return "success", bid
        except: return "continue", bid

    def save_stack(self, best_bid: int, captures: list[CaptureInfo], params: StackParams):
        idx = _get_capture_index_by_id(captures, best_bid)
        slc = params.slice_to_save(idx)
        for cap in captures[slc]:
            self._cam.save_from_memory(
                jpeg_path=os.path.join(params.images_dir, cap.filename),
                save_resolution=params.save_resolution,
                buffer_id=cap.buffer_id
            )
        self._cam.clear_buffers()


# --- Mathematical Utilities ---

# --- Mathematical Utilities / Interpolation ---

class FocalSurface:
    """Stores focus points and provides surface interpolation."""
    def __init__(self):
        self.points = [] # list of (x, y, z)
    
    def add_point(self, x, y, z):
        self.points.append((float(x), float(y), float(z)))
        LOGGER.info(f"FocalSurface: Added point ({x}, {y}, {z}). Total points: {len(self.points)}")
        
    def predict(self, x_target, y_target) -> Optional[float]:
        if not self.points:
            return None
            
        pts = np.array(self.points)
        
        # If we have very few points, cubic griddata will fail.
        # Use nearest neighbor if < 4 points or if points are collinear.
        if len(self.points) < 4:
            # Simple nearest neighbor
            dist = np.sqrt((pts[:,0] - x_target)**2 + (pts[:,1] - y_target)**2)
            return float(pts[np.argmin(dist), 2])
            
        from scipy.interpolate import griddata
        try:
            # Try cubic for smooth surface
            val = griddata(pts[:, :2], pts[:, 2], (x_target, y_target), method='cubic')
            if np.isnan(val):
                # Fallback to nearest if outside the convex hull
                val = griddata(pts[:, :2], pts[:, 2], (x_target, y_target), method='nearest')
            return float(val)
        except Exception as e:
            LOGGER.warning(f"Interpolation error: {e}, using nearest instead.")
            dist = np.sqrt((pts[:,0] - x_target)**2 + (pts[:,1] - y_target)**2)
            return float(pts[np.argmin(dist), 2])


def _get_peak_turning_point(sharpnesses: np.ndarray) -> float:
    """Quadratic peak fitting with confidence interval checks.
    
    This is the exact 'Mathematical Logic' used in the OpenFlexure core.
    """
    if len(sharpnesses) < 3:
        raise NotAPeakError("Need at least 3 points.")
    
    x = np.arange(len(sharpnesses))
    # Light smoothing of the score curve to reject jitter
    s = np.convolve(sharpnesses, np.ones(3)/3, mode='same')
    s[0], s[-1] = sharpnesses[0], sharpnesses[-1] # Preserve edges
    
    coeffs, cov = np.polyfit(x, s, deg=2, cov=True)
    a, b, c = coeffs
    
    # Peak = -b / 2a
    turning = -b / (2 * a)
    
    # 95% Confidence check (ci_high must be < 0 for a downward parabola)
    sigma_a = np.sqrt(cov[0, 0])
    if (a + 2 * sigma_a) > -1e-9:
        raise NotAPeakError("95% confidence check failed: data not clearly a peak.")
        
    return float(turning)


def _count_turning_points(sharpnesses: np.ndarray) -> int:
    """Detect number of local maxima/minima using sign changes in gradient."""
    d = np.diff(sharpnesses)
    # Filter noise
    avg_d = np.mean(np.abs(d))
    if avg_d == 0: return 0
    prominent = np.abs(d) > (0.5 * avg_d)
    d = d[prominent]
    if len(d) < 2: return 0
    return int(np.sum(d[1:] * d[:-1] < 0))