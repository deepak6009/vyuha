"""BTT SKR Mini E3 NEMA 14 Stage Control via G-Code."""

from __future__ import annotations

import logging
import threading
import time
from types import TracebackType
from typing import Any, Optional
import glob

import serial

from .base import BaseStage

# 16,640 steps per mm as configured in Marlin Configuration.h
STEPS_PER_MM = 16640.0

logger = logging.getLogger(__name__)


def find_marlin_port(baudrate: int = 250000) -> Optional[str]:
    """Auto-detect the BTT SKR board serial port."""
    # Look for common Linux USB serial patterns
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    
    for port in ports:
        try:
            # Try to connect and send M115 (Firmware Info)
            with serial.Serial(port, baudrate, timeout=1) as ser:
                time.sleep(1) # Wait for potential DTR reset
                ser.reset_input_buffer()
                ser.write(b"M115\n")
                
                # Check response
                start_time = time.time()
                while time.time() - start_time < 2:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if "Marlin" in line or "FIRMWARE_NAME" in line:
                        logger.info(f"Auto-detected Marlin board on {port}")
                        return port
        except (OSError, serial.SerialException):
            pass
            
    return None


class NemaStage(BaseStage):
    """A stage implementation for BTT SKR Mini E3 controlling NEMA 14 motors via G-code."""

    def __init__(
        self,
        thing_server_interface: Any = None,
        port: Optional[str] = None,
        baudrate: int = 250000,
        simulate: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialise NemaStage."""
        super().__init__(thing_server_interface)
        self._lock = threading.RLock()
        self.port = port
        self.baudrate = baudrate
        self.ser: Optional[serial.Serial] = None
        self._simulated = simulate

    def __enter__(self) -> "NemaStage":
        """Connect to the BTT board when the context manager is opened."""
        with self._lock:
            if self.port is None and not self._simulated:
                self.port = find_marlin_port(baudrate=self.baudrate)
                if self.port is None:
                    self._simulated = True
                    print("\n" + "!"*60)
                    print("CRITICAL WARNING: No Marlin hardware detected on serial ports!")
                    print("The NemaStage is falling back to SIMULATION MODE.")
                    print("Move commands will NOT affect the physical stage.")
                    print("!"*60 + "\n")
                    logger.warning("Could not auto-detect BTT board serial port. Running in simulated mode.")

            if self._simulated:
                return self

            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=2, dsrdtr=False)
                # Wait for board to boot/reset
                time.sleep(2)
                
                # Wake up and set to absolute coordinates as a baseline
                self.send_gcode("M400") # Wait for current moves to finish
                self.send_gcode("G21")  # Set units to Millimeters (CRITICAL)
                self.send_gcode("G90")  # Set absolute positioning
                
                # Set coordinates to 0,0,0 initially 
                # (since auto-homing is disabled per user request)
                self.set_zero_position()

                # Discover available axes
                try:
                    response = self.send_gcode("M114")
                    discovered = []
                    # Typical M114: X:0.00 Y:0.00 Z:0.00 E:0.00 ...
                    # We check for X:, Y:, Z: presence
                    for ax in ["X", "Y", "Z"]:
                        if f"{ax}:" in response:
                            discovered.append(ax.lower())
                    
                    if discovered:
                        self._axis_names = tuple(discovered)
                        # Re-initialize instance state for discovered axes
                        self.axis_inverted = {ax: False for ax in self._axis_names}
                        self._hardware_position = {ax: 0 for ax in self._axis_names}
                        logger.info(f"Discovered axes: {self._axis_names}")
                    else:
                        logger.warning("No axes discovered via M114, defaulting to (x, y, z)")
                except Exception as e:
                    logger.warning(f"Axis discovery failed: {e}. Defaulting to (x, y, z)")

                # Turn LED ON full brightness
                self.send_gcode("M355 S1 P255")
                
            except serial.SerialException as e:
                raise RuntimeError(f"Failed to connect to BTT board on {self.port}: {e}")
                
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Disconnect PySerial when context manager is closed."""
        with self._lock:
            if self.ser and self.ser.is_open:
                # Turn LED OFF before closing
                try:
                    self.send_gcode("M355 S0")
                except Exception as e:
                    logger.debug(f"Failed to turn off LED: {e}")

                # Issue emergency stop or just flush buffers?
                # Probably best to just close cleanly.
                self.ser.close()


    def send_gcode(self, gcode: str) -> str:
        """Send a G-code command and wait for the 'ok' response. Returns the text before 'ok'."""
        if self._simulated:
            logger.debug(f"SIMULATION: {gcode}")
            return ""

        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Serial connection is not open.")
            
        cmd = f"{gcode}\n"
        self.ser.write(cmd.encode('ascii'))
        
        # Wait for 'ok'
        response_lines = []
        while True:
            line = self.ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            if line.startswith("ok"):
                # Marlin 2.x may send the response on the same line as 'ok':
                #   ok X:1.50 Y:2.00 Z:0.00 E:0.00 Count X:24960 Y:33280 Z:0
                # Capture everything after 'ok' so M114 parsing works correctly.
                rest = line[2:].strip()
                if rest:
                    response_lines.append(rest)
                break
            if line.startswith("Error") or "!! " in line:
                logger.error(f"Marlin G-code Error: {line} (Command: {gcode})")
                raise RuntimeError(f"Marlin firmware error on command '{gcode}': {line}")
            response_lines.append(line)
            
        return "\n".join(response_lines)

    @property
    def position(self) -> dict[str, int]:
        """Current position of the stage queried directly from firmware via M114."""
        if not self._simulated and self.ser and self.ser.is_open:
            try:
                with self._lock:
                    response = self.send_gcode("M114")
                # Example M114: X:1.50 Y:2.00 Z:0.00 E:0.00 Count X:24960 Y:33280 Z:0
                # We can parse the Count X: Y: Z: directly for steps!
                if "Count" in response:
                    count_str = response.split("Count")[1]
                    for part in count_str.split():
                        if part.startswith("X:"): self._hardware_position["x"] = int(part[2:])
                        elif part.startswith("Y:"): self._hardware_position["y"] = int(part[2:])
                        elif part.startswith("Z:"): self._hardware_position["z"] = int(part[2:])
            except Exception as e:
                logger.debug(f"Failed to sync position with M114: {e}")
                
        return self._apply_axis_direction(self._hardware_position)

    def _hardware_move_relative(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a relative move sent via G-code. 
        
        Input 'kwargs' are in microns (um).
        """
        with self._lock:
            self.moving = True
            try:
                gcode_parts = ["G1"]
                for axis_lower, microns in kwargs.items():
                    if microns == 0:
                        continue
                    
                    axis_upper = axis_lower.upper()
                    if axis_upper not in ["X", "Y", "Z"]:
                        continue
                        
                    # Convert microns to physical millimeters for G-code
                    mm_val = microns / 1000.0
                    gcode_parts.append(f"{axis_upper}{mm_val:.4f}")
                    
                    # Update hardware tracking in raw steps
                    steps = int(microns * (STEPS_PER_MM / 1000.0))
                    self._hardware_position[axis_lower] += steps

                if len(gcode_parts) > 1:
                    command = " ".join(gcode_parts)
                    print(f"DEBUG: NemaStage Send -> {command} (Simulated: {self._simulated})")
                    # G91 = Relative Positioning
                    self.send_gcode("G91") 
                    self.send_gcode(command)
                    self.send_gcode("M400") # Wait for move
                    self.send_gcode("G90") # Back to Absolute
                    
            finally:
                self.moving = False

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make an absolute move by converting micron targets to relative microns."""
        displacement = {}
        for axis, target_microns in kwargs.items():
            # Convert current raw steps back to microns for comparison
            current_microns = self._hardware_position.get(axis, 0) / (STEPS_PER_MM / 1000.0)
            diff_microns = int(target_microns) - int(current_microns)
            displacement[axis] = diff_microns
            
        self._hardware_move_relative(block_cancellation=block_cancellation, **displacement)

    def set_zero_position(self) -> None:
        """Make the current hardware position zero inside Vyuhaa and Marlin."""
        with self._lock:
            self.send_gcode("G92 X0 Y0 Z0")
            self._hardware_position = {k: 0 for k in self._axis_names}
