"""Jetson Nano Camera Control (V5 - Intensity Adaptive White Fix)."""
from __future__ import annotations
import logging
import threading
from threading import Thread
import time
import os
import subprocess
from typing import Literal, Optional, Tuple, Sequence, Any
from datetime import datetime
import numpy as np
import cv2
from PIL import Image

# import labthings_fastapi as lt
# from labthings_fastapi.types.numpy import NDArray
NDArray = np.ndarray
from . import BaseCamera

LOGGER = logging.getLogger(__name__)

class GstSubprocessReader:
    """Read JPEG frames from GStreamer subprocess pipe. Resolves VENV OpenCV bugs & byte-wrap split lines."""
    def __init__(self, pipeline: str, width: int, height: int):
        self.w, self.h = width, height
        self.proc: Optional[subprocess.Popen] = None
        self._buffer = bytearray()

    def open(self):
        try:
            # We use hardware NVJPEG encoding to create frame boundaries
            # This completely eliminates the "vertical split line" byte-wrap bug 
            # caused by unaligned stdout reading of raw continuous 2D matrices.
            cmd = [
                "gst-launch-1.0",
                "nvarguscamerasrc", "sensor-id=0", "sensor-mode=3", "wbmode=1", "!", 
                "video/x-raw(memory:NVMM), width=1640, height=1232, format=NV12, framerate=21/1", "!",
                "nvjpegenc", "idct-method=1", "!",
                "fdsink", "sync=false", "async=false"
            ]
            self.proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                shell=False
            )
            time.sleep(1.0)
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode()
                LOGGER.error(f"GstSubprocess failed early: {err}")
                return False
                
            # Make stdout non-blocking so the capture thread doesn't hang
            if os.name != 'nt':
                import fcntl
                fl = fcntl.fcntl(self.proc.stdout.fileno(), fcntl.F_GETFL)
                fcntl.fcntl(self.proc.stdout.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
                
            return True
        except Exception as e:
            LOGGER.error(f"Subprocess GStreamer startup exception: {e}")
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.proc or self.proc.poll() is not None:
            return False, None
            
        try:
            # We must read in massive chunks. A 1640x1232 JPEG can easily exceed 128KB.
            # If we don't read fast enough, the OS pipe buffer fills & the GStreamer process blocks/lags.
            while True:
                chunk = self.proc.stdout.read(1048576) # Read up to 1MB chunks
                if not chunk: break
                self._buffer.extend(chunk)
        except BlockingIOError:
            pass
        except Exception as e:
            return False, None

        # We want the LATEST frame to eliminate latency.
        # Find the last EOI (End of Image) marker in the buffer
        end_idx = self._buffer.rfind(b'\xff\xd9')
        if end_idx != -1:
            # Find the SOI (Start of Image) marker that immediately precedes this EOI
            start_idx = self._buffer.rfind(b'\xff\xd8', 0, end_idx)
            if start_idx != -1:
                jpg_data = self._buffer[start_idx : end_idx + 2]
                
                # Truncate anything before the END of this newest frame (dropping older frames to catch up to real-time)
                self._buffer = self._buffer[end_idx + 2 :]
                
                frame = cv2.imdecode(np.frombuffer(jpg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
                    
        # Failsafe: if the buffer explodes (e.g. malformed stream), flush it
        if len(self._buffer) > 2000000:
            self._buffer = bytearray()
            
        return False, None

    def release(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=1.0)
            self.proc = None

    def isOpened(self):
        return self.proc is not None and self.proc.poll() is None


class FFmpegSubprocessReader:
    """Read BGR frames directly from FFmpeg. Bulletproof V4L2 fallback that avoids Jetson OpenCV green-screen bug."""
    def __init__(self, device: str = "/dev/video0", width: int = 640, height: int = 480):
        self.w, self.h = width, height
        self.frame_size = width * height * 3
        self.proc: Optional[subprocess.Popen] = None
        self.device = device

    def open(self):
        try:
            cmd = [
                "ffmpeg",
                "-hide_banner", "-loglevel", "error",
                "-f", "v4l2",
                "-video_size", f"{self.w}x{self.h}",
                "-framerate", "30",
                "-i", self.device,
                "-f", "image2pipe",
                "-vcodec", "rawvideo",
                "-pix_fmt", "bgr24",
                "-"
            ]
            self.proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                shell=False
            )
            time.sleep(1.0) # Wait for ffmpeg to spin up
            if self.proc.poll() is not None:
                LOGGER.error(f"FFmpeg failed to start: {self.proc.stderr.read().decode()}")
                return False
            return True
        except Exception as e:
            LOGGER.error(f"FFmpeg startup exception: {e}")
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.proc or self.proc.poll() is not None:
            return False, None
        try:
            raw = self.proc.stdout.read(self.frame_size)
            if len(raw) < self.frame_size:
                return False, None
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((self.h, self.w, 3))
            return True, frame
        except Exception as e:
            return False, None

    def release(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=1.0)
            self.proc = None

    def isOpened(self):
        return self.proc is not None and self.proc.poll() is None


class JetsonCamera(BaseCamera):
    """A Camera Thing for Jetson Nano using GStreamer pipeline and OpenCV."""

    def __init__(self, thing_server_interface: Any = None, cal_file_path: Optional[str] = None) -> None:
        super().__init__(thing_server_interface)
        self._capture_thread: Optional[Thread] = None
        self._capture_enabled = False
        self._cap: Any = None # Can be cv2.VideoCapture or GstSubprocessReader
        self._master_white: Optional[np.ndarray] = None
        self._brightness_factor = 1.0
        
        # Default fallback - using a simpler path relative to the app root
        if cal_file_path:
            self._cal_file = cal_file_path
        else:
            self._cal_file = os.path.abspath("captures/camera_calibration.npy")
        
        # EMA Filter for brightness stability (prevents flickering)
        self._brightness_ema: Optional[float] = None
        self._ema_alpha = 0.2 # Smoothing factor (0.1 = slow/smooth, 0.9 = fast/twitchy)
        
        # Load existing calibration if it exists
        self.load_calibration()

        # UPDATED: Robust fallback list to bypass Jetson ISP stride bugs.
        # We avoid format=BGRx in nvvidconv, instead opting for format=NV12 or format=I420 
        # to ensure the CPU does the final memory alignment color conversion.
        self.pipelines = [
            # 1. 1920x1080 (Sensor mode 2) - Native HD, scales cleanly to 1280x720. 
            "nvarguscamerasrc sensor-id=0 sensor-mode=2 wbmode=1 ! video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! nvvidconv ! video/x-raw, width=1280, height=720, format=NV12 ! videoconvert ! video/x-raw, format=BGR ! appsink drop=true sync=false",
            # 2. 1280x720 (Sensor mode 4) - What we tried originally, but with NV12 memory fetch
            "nvarguscamerasrc sensor-id=0 sensor-mode=4 wbmode=1 ! video/x-raw(memory:NVMM), width=1280, height=720, format=NV12, framerate=30/1 ! nvvidconv ! video/x-raw, width=1280, height=720, format=NV12 ! videoconvert ! video/x-raw, format=BGR ! appsink drop=true sync=false",
            # 3. Native 3264x2464 (Sensor mode 0) - Full res ISP bypass
            "nvarguscamerasrc sensor-id=0 sensor-mode=0 wbmode=1 ! video/x-raw(memory:NVMM), width=3264, height=2464, format=NV12, framerate=21/1 ! nvvidconv ! video/x-raw, width=1280, height=720, format=I420 ! videoconvert ! video/x-raw, format=BGR ! appsink drop=true sync=false"
        ]

        self._last_frame: Optional[np.ndarray] = None
        self._last_display_frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()

    def __enter__(self):
        super().__enter__()
        
        # [VYUHAA API - Jetson CSI Fix] 
        # The camera is a pure CSI module that strictly requires the nvargus-daemon ISP.
        # Direct V4L2 kernel access hangs. The vertical split line is caused by nvargus-daemon 
        # state corruption. The pipeline must use nvarguscamerasrc.
        
        print("INFO: Initializing robust Jetson CSI Pipeline...")
        self._cap = None
        
        for i, pipeline in enumerate(self.pipelines):
            print(f"Testing Pipeline #{i+1}...")
            try:
                self._cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                if self._cap and self._cap.isOpened():
                    success = False
                    for _ in range(5):
                        ret, _ = self._cap.read()
                        if ret:
                            success = True
                            break
                        time.sleep(0.1)
                    
                    if success:
                        print(f"SUCCESS: CSI Pipeline #{i+1} active.")
                        break
                    else:
                        print(f"Pipeline #{i+1} timed out.")
                        self._cap.release()
                        self._cap = None
            except Exception as e:
                print(f"Pipeline #{i+1} exception: {e}")
                if self._cap: self._cap.release()
                self._cap = None

        if not self._cap or not self._cap.isOpened():
            print("WARNING: OpenCV GStreamer failed. Falling back to Subprocess CSI Pipeline...")
            self._cap = GstSubprocessReader("nvarguscamerasrc", 1640, 1232)
            if self._cap.open():
                # Allow the GStreamer subprocess a few seconds to spin up and produce the first JPEG
                success = False
                for _ in range(30):
                    ret, _ = self._cap.read()
                    if ret:
                        success = True
                        break
                    time.sleep(0.1)
                
                if success:
                    print("SUCCESS: Subprocess CSI Pipe active.")
                else:
                    self._cap.release()
                    self._cap = None
            else:
                self._cap.release()
                self._cap = None

        if not self._cap or not self._cap.isOpened():
            print("WARNING: All CSI pipelines failed. Attempting FFmpeg V4L2 fallback...")
            self._cap = FFmpegSubprocessReader("/dev/video0", 1280, 720)
            if self._cap.open():
                print("Testing FFmpeg stream viability...")
                success = False
                for _ in range(5):
                    ret, frame = self._cap.read()
                    if ret and frame is not None and frame.size > 0:
                        # Quick check if it's pure green (OpenCV V4L2 bug symptom, just in case)
                        mean_color = np.mean(frame, axis=(0, 1))
                        if mean_color[0] < 10 and mean_color[1] > 240 and mean_color[2] < 10:
                            print("Warning: FFmpeg produced pure green frame. Trying next...")
                            time.sleep(0.5)
                            continue
                        success = True
                        break
                    time.sleep(0.5)
                
                if success:
                    print("SUCCESS: FFmpeg fallback active and streaming valid frames.")
                else:
                    print("FFmpeg stream failed to produce valid frames. Releasing camera.")
                    self._cap.release()
                    self._cap = None

        if self._cap and self._cap.isOpened():
            self._capture_enabled = True
            self._capture_thread = Thread(target=self._capture_frames, daemon=True)
            self._capture_thread.start()
        else:
            print("CRITICAL: All camera pipelines (OpenCV, GStreamer, FFmpeg) failed to produce frames!")
            
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._capture_enabled = False
        if self._capture_thread: self._capture_thread.join(timeout=1.0)
        if self._cap: self._cap.release()
        super().__exit__(exc_type, exc_value, traceback)

    @property
    def stream_active(self) -> bool:
        return self._capture_enabled and self._capture_thread is not None and self._capture_thread.is_alive()

    @property
    def brightness(self) -> float:
        return self._brightness_factor

    @brightness.setter
    def brightness(self, value: float) -> None:
        self._brightness_factor = max(0.5, min(3.0, value))

    def _capture_frames(self) -> None:
        """Background thread to capture frames and update streams."""
        start_time = time.time()
        calibrated_at_startup = (self._master_white is not None)
        consecutive_failures = 0
        
        while self._capture_enabled:
            if not self._cap or not self._cap.isOpened():
                time.sleep(1); continue
            
            ret, frame = self._cap.read()
            if not ret or frame is None:
                consecutive_failures += 1
                # If we fail enough times in a row, the camera has died.
                if consecutive_failures > 100:
                    print("CRITICAL: Camera stream stalled. Stopping capture thread for safety.")
                    self._capture_enabled = False
                    break
                # Only sleep if we did NOT get a frame, otherwise fetch as fast as possible
                time.sleep(0.01)
                continue

            consecutive_failures = 0

            with self._lock:
                self._last_frame = frame.copy()

            # Auto-calibrate on startup if no calibration exists
            # We wait 3 seconds for the Jetson ISP Auto-White-Balance to settle 
            # and the first few frames to arrive.
            if not calibrated_at_startup and (time.time() - start_time > 3.0):
                try: 
                    self.calibrate_pink_fix()
                    self.save_calibration()
                    calibrated_at_startup = True
                except Exception as e:
                    LOGGER.error(f"Auto-calibration failed: {e}")
            
            # Apply Correction & Brightness
            display_frame = frame
            if self._master_white is not None:
                try:                   
                    # If resolution changed, do NOT stretch the old calibration image.
                    # Delete the corrupted calibration array and trigger a fresh image capture on the new FOV!
                    if self._master_white.shape[:2] != frame.shape[:2]:
                        LOGGER.warning(f"Resolution changed! Discarding old {self._master_white.shape} calibration.")
                        self.clear_calibration()
                        calibrated_at_startup = False # Force next loop to recalibrate
                        raise ValueError("Forcing fresh calibration")
                        
                    # 1. Remove Pink Tint (Division)
                    corrected = frame.astype(np.float32) / self._master_white
                    
                    # 2. OPTIMIZED AUTO-BRIGHTNESS with EMA Smoothing
                    # np.percentile is very slow; we use np.max on a sampled subset.
                    # We skip the outermost 5% of the frame to avoid lens-edge artifacts.
                    h, w = corrected.shape[:2]
                    crop_h, crop_w = int(h*0.05), int(w*0.05)
                    sample = corrected[crop_h:-crop_h:10, crop_w:-crop_w:10]
                    instant_max = np.max(sample) if sample.size > 0 else 1.0
                    
                    # Apply Exponential Moving Average (EMA) to smooth out flicker
                    if self._brightness_ema is None:
                        self._brightness_ema = instant_max
                    else:
                        self._brightness_ema = (self._ema_alpha * instant_max) + ((1 - self._ema_alpha) * self._brightness_ema)
                    
                    top_val = self._brightness_ema
                    if top_val > 0.1:
                        corrected *= (240.0 / top_val) * self._brightness_factor
                    
                    # 3. FAST CLIPPING
                    display_frame = np.clip(corrected, 0, 255).astype(np.uint8)
                    
                    # NOTE: Heavy Sharpening and Denoising are DISABLED for the live feed 
                    # to achieve 21 FPS without latency.
                except Exception as e:
                    pass
            
            with self._lock:
                self._last_display_frame = display_frame.copy()

            try:
                jpeg = cv2.imencode(".jpg", display_frame)[1].tobytes()
                self.mjpeg_stream.add_frame(jpeg)
                small = cv2.resize(display_frame, (320, 240))
                self.lores_mjpeg_stream.add_frame(cv2.imencode(".jpg", small)[1].tobytes())
            except: pass

    def capture_array(self, stream_name="main", wait=None) -> NDArray:
        with self._lock:
            if self._last_display_frame is not None:
                return cv2.cvtColor(self._last_display_frame, cv2.COLOR_BGR2RGB)
        
        # Fallback to black if no frame yet
        return np.zeros((960, 1280, 3), dtype=np.uint8)

    def capture_image(self, stream_name="main", wait=None) -> Image.Image:
        return Image.fromarray(self.capture_array())

    def calibrate_pink_fix(self) -> str:
        """Intensity-Adaptive Calibration using frame buffering to avoid hardware contention."""
        frames = []
        for _ in range(10):
            with self._lock:
                if self._last_frame is not None:
                    frames.append(self._last_frame.astype(np.float32))
            time.sleep(0.05)
        if not frames: raise RuntimeError("Failed to capture calibration frames (is camera streaming?)")
        
        temp_white = np.mean(frames, axis=0)
        # We normalize the map by its OWN maximum, so it only cares about COLOR/SHAPE
        # The overall brightness will be handled by the auto-stretching in _capture_frames
        max_val = np.max(temp_white)
        self._master_white = temp_white / max_val
        self._master_white = np.maximum(self._master_white, 0.05)
        return "Intensity-Adaptive Calibration Success!"

    def save_calibration(self) -> str:
        if self._master_white is None: raise RuntimeError("Calibrate first.")
        try:
            os.makedirs(os.path.dirname(self._cal_file), exist_ok=True)
            np.save(self._cal_file, self._master_white)
            LOGGER.info(f"Calibration SAVED to {self._cal_file}")
            return "Saved permanently!"
        except Exception as e:
            LOGGER.error(f"Failed to save calibration: {e}")
            raise

    def load_calibration(self) -> bool:
        """Load calibration from disk if it exists."""
        try:
            if os.path.exists(self._cal_file):
                self._master_white = np.load(self._cal_file)
                LOGGER.info(f"Loaded persistent calibration from {self._cal_file}")
                return True
        except Exception as e:
            LOGGER.error(f"Failed to load calibration: {e}")
        return False

    def clear_calibration(self) -> None:
         self._master_white = None
         if os.path.exists(self._cal_file): os.remove(self._cal_file)