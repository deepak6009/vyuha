#!/bin/bash

# Vyuhaa Hardware Initialization
# ------------------------------
# This script reconfigures the Jetson Nano pinmux for the Microscope motors.

echo "Setting up Pinmux for GPIO pins (X, Y, Z Axis)..."

# X Axis: 11, 13, 15, 16
sudo busybox devmem 0x2430098 w 0x5    # Pin 11
sudo busybox devmem 0x243D030 w 0x1005 # Pin 13
sudo busybox devmem 0x2440020 w 0x5    # Pin 15
sudo busybox devmem 0x243D020 w 0x5    # Pin 16

# Y Axis: 18, 22, 29, 31
sudo busybox devmem 0x243D010 w 0x5    # Pin 18
sudo busybox devmem 0x243D000 w 0x5    # Pin 22
sudo busybox devmem 0x2430068 w 0x8    # Pin 29
sudo busybox devmem 0x2430070 w 0x8    # Pin 31

# Z Axis: 32, 33, 36, 37
sudo busybox devmem 0x2434080 w 0x5    # Pin 32
sudo busybox devmem 0x2434040 w 0x4    # Pin 33
sudo busybox devmem 0x2430090 w 0x5    # Pin 36
sudo busybox devmem 0x243D048 w 0x5    # Pin 37

echo "Hardware Initialization Complete."
