"""Jetson Nano Stage Control."""

from __future__ import annotations

import time
import threading
from types import TracebackType
from typing import Any, Optional

try:
    import Jetson.GPIO as GPIO
except ImportError:
    # Allow import on non-Jetson systems for testing/linting, but fail at runtime if used
    GPIO = None

# import labthings_fastapi as lt
from .base import BaseStage

# --- CONFIGURATION FROM USER CODE ---
AXIS_PINS = {
    'X': [11, 13, 15, 16],
    'Y': [18, 22, 29, 31],
    'Z': [32, 33, 36, 37]
}

# 1 Revolution = 4096 steps (using half-step sequence)
STEPS_PER_REV = 4096 
# Standard microscope lead screw moves 1mm per 1 full rotation
MM_PER_REV = 1.0 

class JetsonStage(BaseStage):
    """A stage implementation for Jetson Nano using GPIO directly."""

    def __init__(
        self,
        thing_server_interface: Any = None,
        **kwargs: Any,
    ) -> None:
        """Initialise JetsonStage."""
        super().__init__(thing_server_interface)
        self._lock = threading.RLock()
        self._simulated = False
        if GPIO is None:
            self._simulated = True
            print("WARNING: Jetson.GPIO not found! JetsonStage will run in SIMULATION mode.")
        else:
            print("SUCCESS: Jetson.GPIO loaded. Initializing stage hardware...")

    def __enter__(self) -> "JetsonStage":
        """Setup GPIO when the Thing context manager is opened."""
        with self._lock:
            if self._simulated:
                print("WARNING: JetsonStage running in SIMULATION mode (No GPIO).")
                return self

            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)
            for axis in AXIS_PINS:
                for pin in AXIS_PINS[axis]:
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.LOW)
            
            # Initialize position if not already set (BaseStage handles _hardware_position)
            # We assume 0,0,0 on startup or rely on BaseStage's default
            
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Cleanup GPIO when the Thing context manager is closed."""
        with self._lock:
            if self._simulated:
                return

            # Release pins to prevent heat/jamming
            for axis in AXIS_PINS:
                for pin in AXIS_PINS[axis]:
                    GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup()


    axis_inverted: dict[str, bool] = {"x": False, "y": False, "z": False}

    def _hardware_move_relative(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a relative move using the stepper motor sequence."""
        # Calculate displacement for each axis
        # kwargs keys are 'x', 'y', 'z' (lowercase from BaseStage)
        # We need to map to 'X', 'Y', 'Z' for AXIS_PINS
        
        with self._lock:
            self.moving = True
            try:
                for axis_lower, microns in kwargs.items():
                    if microns == 0:
                        continue
                    
                    axis_upper = axis_lower.upper()
                    if axis_upper not in AXIS_PINS:
                        continue
                    
                    # 1mm = 4096 steps => 1um = 4.096 steps
                    steps_per_um = STEPS_PER_REV / (MM_PER_REV * 1000.0)
                    steps = int(round(microns * steps_per_um))
                    
                    if steps == 0 and abs(microns) > 0:
                        # Ensure we move at least one step if a tiny movement was requested
                        steps = 1 if microns > 0 else -1
                        
                    direction = "F" if steps > 0 else "B"
                    abs_steps = abs(steps)
                    
                    # Log the conversion for debugging
                    print(f"DEBUG: JetsonStage move {axis_lower} {microns}um -> {abs_steps} steps {direction}")
                    
                    self._move_axis_hardware(axis_upper, abs_steps, direction)
                    
                    # Update hardware position in MICRONS (BaseStage property returns this)
                    self._hardware_position[axis_lower] += microns

            finally:
                self.moving = False


    def _move_axis_hardware(self, axis_name: str, steps: int, direction: str) -> None:
        """Low-level motor move function adapted from user code."""
        if self._simulated:
            print(f"SIMULATION: Moving {axis_name} {direction} {steps} steps")
            time.sleep(0.001 * steps) # Simulate time taken
            return

        # 8-step sequence for 28BYJ-48 motors (Half-stepping)
        # Provides higher resolution and better torque
        seq = [
            [1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
            [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]
        ]
        
        if direction.upper() == "B":
            seq = seq[::-1]
            
        pins = AXIS_PINS[axis_name]
        
        # Adjust cycles for 8-step sequence
        cycles = steps // 8
        remainder = steps % 8
        
        # print(f"DEBUG: Physically moving {axis_name} {steps} steps ({cycles} cycles, {remainder} extra)...")
        
        pins = AXIS_PINS[axis_name]
        
        for _ in range(cycles):
            for step_pattern in seq:
                for pin_idx in range(4):
                    GPIO.output(pins[pin_idx], step_pattern[pin_idx])
                time.sleep(0.01) # Increased for stability (10ms)

        # Handle remainder steps to be precise
        for i in range(remainder):
            step_pattern = seq[i]
            for pin_idx in range(4):
                GPIO.output(pins[pin_idx], step_pattern[pin_idx])
            time.sleep(0.01)

        # Release pins to prevent overheating
        for pin in pins:
            GPIO.output(pin, GPIO.LOW)
        # print(f"DEBUG: {axis_name} move completed.")


    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make an absolute move."""
        # BaseStage logic usually handles this by calculating relative move
        # But we must implement it if BaseStage raises NotImplementedError.
        # We can reuse the same logic as BaseStage suggests in its docstring implies
        # we can just implement _hardware_move_relative and let BaseStage handle move_absolute wrapper?
        # Wait, BaseStage says: "It is recommended to override _hardware_move_relative and _hardware_move_absolute"
        
        # Calculate displacement required in microns
        displacement = {}
        for axis, target_um in kwargs.items():
            current_um = self._hardware_position.get(axis, 0)
            diff_um = int(target_um) - int(current_um)
            displacement[axis] = diff_um
            
        self._hardware_move_relative(block_cancellation=block_cancellation, **displacement)

    def set_zero_position(self) -> None:
        """Make the current position zero."""
        self._hardware_position = {k: 0 for k in self._axis_names}

