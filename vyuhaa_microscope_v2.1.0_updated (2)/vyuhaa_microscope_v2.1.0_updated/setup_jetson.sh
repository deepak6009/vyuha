#!/bin/bash
# setup_jetson.sh — Vyuhaa Microscope Jetson Setup Script
set -e

echo "🚀 Starting Vyuhaa Jetson Setup..."

# 1. Update and Install System Dependencies
echo "📦 Installing system dependencies..."
sudo apt-get update || echo "⚠️ Warning: Some repositories failed to update, but we will try to proceed..."
sudo apt-get install -y --no-install-recommends \
    libatlas-base-dev \
    libopenblas-dev \
    busybox \
    v4l-utils \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    libqt6gui6 \
    libvips-dev \
    python3-venv \
    python3-pip

# 2. Configure Permissions (Serial for NEMA)
echo "🔒 Configuring permissions for NEMA Motors (Serial)..."
sudo usermod -a -G dialout $USER
echo "✅ Added $USER to 'dialout' group."

# 3. Setup Virtual Environment with System Site Packages (Required for Jetson HW)
echo "🐍 Creating Virtual Environment with system-site-packages..."
# This allows the app to access JetPack's pre-installed hardware-accelerated drivers
# while keeping other libs (like PySide6) isolated.
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 4. Install Python Dependencies
echo "📥 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

echo "-------------------------------------------------------"
echo "✅ SETUP COMPLETE!"
echo "-------------------------------------------------------"
echo "⚠️ IMPORTANT: You MUST REBOOT your Jetson now for the"
echo "   NEMA motor permissions to take effect."
echo "   Run: sudo reboot"
echo "-------------------------------------------------------"
