#!/usr/bin/env python3
"""
Simple Qt6 test to debug the segmentation fault
"""

import sys
import os

print("Testing Qt6 step by step...")

print("1. Testing basic import...")
try:
    from PySide6.QtCore import QCoreApplication
    print("   ✓ QtCore import successful")
except Exception as e:
    print(f"   ✗ QtCore import failed: {e}")
    sys.exit(1)

print("2. Testing QtWidgets import...")
try:
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    print("   ✓ QtWidgets import successful")
except Exception as e:
    print(f"   ✗ QtWidgets import failed: {e}")
    sys.exit(1)

print("3. Testing QApplication creation...")
try:
    # Set some environment variables that might help
    os.environ['QT_QPA_PLATFORM'] = 'xcb'  # Force X11 instead of Wayland
    
    app = QApplication(sys.argv)
    print("   ✓ QApplication created successfully")
except Exception as e:
    print(f"   ✗ QApplication creation failed: {e}")
    sys.exit(1)

print("4. Testing system tray availability...")
try:
    available = QSystemTrayIcon.isSystemTrayAvailable()
    print(f"   System tray available: {available}")
    if not available:
        print("   ✗ System tray is not available")
        sys.exit(1)
    else:
        print("   ✓ System tray is available")
except Exception as e:
    print(f"   ✗ System tray check failed: {e}")
    sys.exit(1)

print("5. Testing QSystemTrayIcon creation...")
try:
    tray = QSystemTrayIcon()
    print("   ✓ QSystemTrayIcon created successfully")
except Exception as e:
    print(f"   ✗ QSystemTrayIcon creation failed: {e}")
    sys.exit(1)

print("6. Testing setting tooltip...")
try:
    tray.setToolTip("Test tooltip")
    print("   ✓ Tooltip set successfully")
except Exception as e:
    print(f"   ✗ Setting tooltip failed: {e}")
    sys.exit(1)

print("All Qt6 tests passed! The issue might be elsewhere.")
print("Cleaning up...")
app.quit() 