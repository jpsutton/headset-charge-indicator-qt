#!/usr/bin/env python3
#
# Simple Qt6 System Tray application which uses the HeadsetControl application from 
# https://github.com/Sapd/HeadsetControl/ for retrieving charge information
# for wireless headsets and displays it as system tray icon with native tooltips
#
# Enhanced for KDE Plasma Desktop with Qt6 QSystemTrayIcon
# Simple start this application as background process, i.e. during
# startup of the graphical desktop

import argparse
import json
import os
import sys
from shutil import which
from subprocess import check_output, CalledProcessError

# Force X11 platform to avoid potential Wayland issues
#os.environ['QT_QPA_PLATFORM'] = 'xcb'

try:
    # Try to use KDE's native libraries for rich tooltips
    from KStatusNotifierItem import KStatusNotifierItem
    KDE_LIBRARIES_PRESENT = True
except ImportError:
    # Fallback to basic Qt if KDE libraries aren't available
    KDE_LIBRARIES_PRESENT = False

# KDE will be available only if libraries are present AND not forced to use Qt
KDE_AVAILABLE = KDE_LIBRARIES_PRESENT  # Will be updated after argument parsing

# Always import Qt components
from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtGui import QIcon, QAction, QPainter, QPixmap, QColor
from PySide6.QtCore import QTimer, QSettings
from PySide6.QtWidgets import QSystemTrayIcon

APPINDICATOR_ID = 'headset-charge-indicator'

global HEADSETCONTROL_BINARY
HEADSETCONTROL_BINARY = None



OPTION_CAPABILITIES = '-?'
OPTION_BATTERY = '-b'
OPTION_SILENT = '-c'
OPTION_OUTPUT = '-o'
OPTION_OUTPUT_FORMAT = 'JSON'  # Use JSON format for easy parsing
OPTION_CHATMIX = '-m'
OPTION_SIDETONE = '-s'
OPTION_LED = '-l'
OPTION_INACTIVE_TIME = '-i'

# Battery level thresholds for icon coloring
BATTERY_LOW_THRESHOLD = 20    # Red below this percentage
BATTERY_MEDIUM_THRESHOLD = 50 # Orange below this percentage

# Polling interval in seconds
POLLING_INTERVAL_SECONDS = 60  # Default: poll every 60 seconds

# Notification timeout in milliseconds
NOTIFICATION_TIMEOUT = 10000  # Default: 10 second timeout


# Global Qt objects
global app
app = None
global tray
tray = None
global charge_action
charge_action = None
global chatmix_action
chatmix_action = None

global base_icon
base_icon = None
global last_battery_level
last_battery_level = None
global last_battery_state
last_battery_state = None  # 'high', 'medium', 'low', 'charging', 'unavailable'

# Global settings object
global settings
settings = None


def save_setting(key, value):
    """Save a setting value to persistent storage"""
    global settings
    if settings is not None:
        settings.setValue(key, value)


def get_setting(key, default_value=None):
    """Get a setting value from persistent storage"""
    global settings
    if settings is not None:
        value = settings.value(key, default_value)
        return value
    return default_value


def restore_headset_settings():
    """Restore previously saved headset settings on startup"""
    
    # Restore sidetone level
    sidetone_level = get_setting("sidetone_level")
    if sidetone_level is not None:
        try:
            sidetone_level = int(sidetone_level)
            set_sidetone(sidetone_level)
        except (ValueError, CalledProcessError) as e:
            print(f"Warning: Failed to restore sidetone level: {e}")
    
    # Restore LED state
    led_state = get_setting("led_state")
    if led_state is not None:
        try:
            led_state = int(led_state)
            set_led(led_state)
        except (ValueError, CalledProcessError) as e:
            print(f"Warning: Failed to restore LED state: {e}")
    
    # Restore inactive time
    inactive_time = get_setting("inactive_time")
    if inactive_time is not None:
        try:
            inactive_time = int(inactive_time)
            set_inactive_time(inactive_time)
        except (ValueError, CalledProcessError) as e:
            print(f"Warning: Failed to restore inactive time: {e}")


def create_battery_overlay_icon(battery_percentage):
    """Create an icon with a colored circle overlay based on battery percentage"""
    global base_icon
    
    if base_icon is None:
        return QIcon()
    
    # Get the original icon as a pixmap
    original_pixmap = base_icon.pixmap(64, 64)  # Use 64x64 for good quality
    
    # Create a new pixmap to draw on
    overlay_pixmap = QPixmap(original_pixmap.size())
    overlay_pixmap.fill(QColor(0, 0, 0, 0))  # Transparent background
    
    # Create painter
    painter = QPainter(overlay_pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Draw the original icon
    painter.drawPixmap(0, 0, original_pixmap)
    
    # Calculate gradient color based on battery percentage
    # Green (100%) -> Orange (50%) -> Red (0%)
    if battery_percentage >= 50:
        # Gradient from orange to green (50% - 100%)
        ratio = (battery_percentage - 50) / 50.0  # 0.0 to 1.0
        red = int(255 - (255 - 0) * ratio)        # 255 -> 0
        green = int(165 + (255 - 165) * ratio)    # 165 -> 255  
        blue = 0
    else:
        # Gradient from red to orange (0% - 50%)
        ratio = battery_percentage / 50.0  # 0.0 to 1.0
        red = 255                                 # Stay at 255
        green = int(0 + 165 * ratio)              # 0 -> 165
        blue = 0
    
    circle_color = QColor(red, green, blue, 220)  # Semi-transparent
    
    # Draw circular overlay in the top-right corner (like notification badge)
    circle_size = 24  # Size of the notification circle
    circle_x = original_pixmap.width() - circle_size - 3
    circle_y = 3
    
    # Draw circle with subtle border for better visibility
    painter.setBrush(circle_color)
    painter.setPen(QColor(255, 255, 255, 150))  # Light white border
    painter.drawEllipse(circle_x, circle_y, circle_size, circle_size)
    
    painter.end()
    
    return QIcon(overlay_pixmap)


def send_battery_notification(title, message, urgency="normal"):
    """Send a desktop notification using the system tray"""
    global tray
    
    if tray is None or args.no_notifications:
        return
    
    # Show the notification using appropriate method
    if KDE_AVAILABLE:
        # Use KDE's native notification system with icon and timeout
        tray.showMessage(title, message, "audio-headset", NOTIFICATION_TIMEOUT)
    else:
        # Map urgency to QSystemTrayIcon message icons
        if urgency == "critical":
            icon = QSystemTrayIcon.MessageIcon.Critical
        elif urgency == "warning":
            icon = QSystemTrayIcon.MessageIcon.Warning
        else:
            icon = QSystemTrayIcon.MessageIcon.Information
        
        # Show the notification
        tray.showMessage(title, message, icon, NOTIFICATION_TIMEOUT)  # 5 second timeout


def get_battery_state(battery_level):
    """Determine the battery state based on level"""
    if battery_level < BATTERY_LOW_THRESHOLD:
        return 'low'
    elif battery_level < BATTERY_MEDIUM_THRESHOLD:
        return 'medium'
    else:
        return 'high'


def get_battery_icon(battery_level):
    """Get appropriate battery icon based on charge level"""
    if battery_level == -1:  # Charging
        # Try charging icons with fallbacks
        charging_icons = ["battery-charging", "battery-charging-symbolic", "battery-full-charging"]
        for icon_name in charging_icons:
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                return icon
    elif battery_level == -2:  # Unavailable
        # Try missing/unavailable icons with fallbacks
        missing_icons = ["battery-missing", "battery-missing-symbolic", "battery-empty"]
        for icon_name in missing_icons:
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                return icon
    else:
        # Regular battery level icons
        if battery_level >= 90:
            icon_names = ["battery-full", "battery-100", "battery-full-symbolic"]
        elif battery_level >= 75:
            icon_names = ["battery-good", "battery-080", "battery-good-symbolic"]
        elif battery_level >= 50:
            icon_names = ["battery-medium", "battery-060", "battery-medium-symbolic"]
        elif battery_level >= 25:
            icon_names = ["battery-low", "battery-040", "battery-low-symbolic"]
        elif battery_level >= 10:
            icon_names = ["battery-caution", "battery-020", "battery-caution-symbolic"]
        else:
            icon_names = ["battery-empty", "battery-000", "battery-empty-symbolic"]
        
        # Try each icon name until we find one that exists
        for icon_name in icon_names:
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                return icon
    
    # Final fallback to a generic battery icon
    fallback_icons = ["battery", "battery-symbolic", "power-profile-balanced"]
    for icon_name in fallback_icons:
        icon = QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon
    
    # If no icon found, return empty icon
    return QIcon()


def check_battery_notifications(battery_level, battery_state):
    """Check if we should send notifications based on battery level changes"""
    global last_battery_level, last_battery_state
    
    # Skip on first run when we don't have previous values
    if last_battery_level is None or last_battery_state is None:
        last_battery_level = battery_level
        last_battery_state = battery_state
        return
    
    # Check for threshold transitions
    if last_battery_state != battery_state:
        if battery_state == 'low' and last_battery_state in ['medium', 'high']:
            send_battery_notification(
                "Headset Battery Low", 
                f"Battery level dropped to {battery_level}% (below {BATTERY_LOW_THRESHOLD}%)",
                "warning"
            )
        elif battery_state == 'medium' and last_battery_state == 'high':
            send_battery_notification(
                "Headset Battery Medium", 
                f"Battery level dropped to {battery_level}% (below {BATTERY_MEDIUM_THRESHOLD}%)",
                "normal"
            )
        elif battery_state == 'high' and last_battery_state in ['low', 'medium']:
            send_battery_notification(
                "Headset Battery Recovered", 
                f"Battery level increased to {battery_level}%",
                "normal"
            )
    
    # Check for multiples of 5 when battery is low
    if (battery_state == 'low' and 
        battery_level != last_battery_level and 
        battery_level % 5 == 0):
        send_battery_notification(
            "Headset Battery Critical", 
            f"Battery level: {battery_level}%",
            "critical"
        )
    
    # Check for every percent drop when battery is very low (< 11%)
    if (battery_level < 11 and 
        battery_level < last_battery_level and 
        last_battery_level >= 11):
        # First time dropping below 11%
        send_battery_notification(
            "Headset Battery Very Low", 
            f"Battery critically low: {battery_level}%",
            "critical"
        )
    elif (battery_level < 11 and 
          battery_level < last_battery_level):
        # Every subsequent drop below 11%
        send_battery_notification(
            "Headset Battery Critical", 
            f"Battery: {battery_level}% (was {last_battery_level}%)",
            "critical"
        )
    
    # Update tracking variables
    last_battery_level = battery_level
    last_battery_state = battery_state


def change_icon():
    # Placeholder function for Qt6 implementation
    # Icon changes are handled directly in change_label() for battery level coloring
    pass


def fetch_capabilities():
    try:
        # ask HeadsetControl for the available capabilities for the current headset
        output = check_output([HEADSETCONTROL_BINARY, OPTION_CAPABILITIES, OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])

        # Parse JSON output to extract capabilities
        try:
            data = json.loads(output.decode('utf-8'))
            if 'devices' in data and len(data['devices']) > 0:
                device = data['devices'][0]  # Use first device
                capabilities = []
                device_caps = device.get('capabilities', [])
                
                # Check for capabilities based on the capability strings
                if 'CAP_BATTERY_STATUS' in device_caps:
                    capabilities.append(b'b')
                if 'CAP_CHATMIX' in device_caps:
                    capabilities.append(b'm')
                if 'CAP_SIDETONE' in device_caps:
                    capabilities.append(b's')
                if 'CAP_LED' in device_caps:
                    capabilities.append(b'l')
                if 'CAP_INACTIVE_TIME' in device_caps:
                    capabilities.append(b'i')
                    
                return b''.join(capabilities)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to parse capabilities JSON: {e}")
            return "all"
        
        return output
    except CalledProcessError as e:
        print(e)
        return "all"


def change_label():
    global charge_action, tray, last_battery_level, last_battery_state
    try:
        output = check_output([HEADSETCONTROL_BINARY, OPTION_BATTERY, OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])

        # Parse JSON output to extract battery level
        try:
            data = json.loads(output.decode('utf-8'))
            if 'devices' in data and len(data['devices']) > 0:
                device = data['devices'][0]  # Use first device
                battery_info = device.get('battery', {})
                
                if battery_info.get('status') == 'BATTERY_CHARGING':
                    battery_level = -1  # Charging
                elif battery_info.get('status') == 'BATTERY_UNAVAILABLE':
                    battery_level = -2  # Unavailable
                else:
                    # Level is already an integer in JSON format
                    battery_level = battery_info.get('level', 0)
            else:
                battery_level = -2  # No device found
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Failed to parse battery JSON: {e}")
            battery_level = -2  # Error parsing
        
        # -1 indicates "Battery is charging"
        if battery_level == -1:
            text = 'Chg'
            tooltip_text = '<b>Headset</b><br/>Charging'
            # Use original icon when charging
            if KDE_AVAILABLE:
                tray.setIconByPixmap(base_icon.pixmap(64, 64))
            else:
                tray.setIcon(base_icon)
            # Handle charging state change notification
            if last_battery_state is not None and last_battery_state != 'charging':
                send_battery_notification(
                    "Headset Charging", 
                    "Headset is now charging",
                    "normal"
                )
            last_battery_state = 'charging'
        # -2 indicates "Battery is unavailable"
        elif battery_level == -2:
            text = 'Off'
            tooltip_text = '<b>Headset</b><br/>Battery Unavailable'
            # Use original icon when unavailable
            if KDE_AVAILABLE:
                tray.setIconByPixmap(base_icon.pixmap(64, 64))
            else:
                tray.setIcon(base_icon)
            # Handle unavailable state change notification
            if last_battery_state is not None and last_battery_state != 'unavailable':
                send_battery_notification(
                    "Headset Disconnected", 
                    "Headset battery unavailable",
                    "normal"
                )
            last_battery_state = 'unavailable'
        else:
            text = str(battery_level) + '%'
            # Format tooltip with rich HTML formatting
            tooltip_text = f'<b>Headset</b><br/>Battery: <b>{text}</b>'
            # Update icon with battery level overlay
            colored_icon = create_battery_overlay_icon(battery_level)
            if KDE_AVAILABLE:
                tray.setIconByPixmap(colored_icon.pixmap(64, 64))
            else:
                tray.setIcon(colored_icon)
            
            # Determine battery state and check for notifications
            battery_state = get_battery_state(battery_level)
            check_battery_notifications(battery_level, battery_state)
                    
    except CalledProcessError as e:
        print(e)
        text = 'N/A'
        tooltip_text = '<b>Headset</b><br/>Connection Error'
        # Use original icon on error
        if KDE_AVAILABLE:
            tray.setIconByPixmap(base_icon.pixmap(64, 64))
        else:
            tray.setIcon(base_icon)

    # Update tooltip with rich formatting (main feature for KDE!)
    if KDE_AVAILABLE:
        tray.setToolTipTitle("Headset")
        tray.setToolTipSubTitle(tooltip_text.replace('<b>Headset</b><br/>', ''))
    else:
        # Fallback to plain text for non-KDE systems
        plain_text = tooltip_text.replace('<b>', '').replace('</b>', '').replace('<br/>', '\n')
        tray.setToolTip(plain_text)
    # Update menu item with icon
    charge_action.setText('Charge: ' + text)
    
    # Update battery icon based on charge level
    if battery_level == -1:
        battery_icon = get_battery_icon(-1)  # Charging
    elif battery_level == -2:
        battery_icon = get_battery_icon(-2)  # Unavailable
    else:
        battery_icon = get_battery_icon(battery_level)  # Regular level
    
    if not battery_icon.isNull():
        charge_action.setIcon(battery_icon)


def change_chatmix():
    global chatmix_action

    try:
        output = check_output([HEADSETCONTROL_BINARY, OPTION_CHATMIX, OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])
        
        # Parse JSON output to extract ChatMix level
        try:
            data = json.loads(output.decode('utf-8'))
            if 'devices' in data and len(data['devices']) > 0:
                device = data['devices'][0]  # Use first device
                
                # Check if there's an error for chatmix
                errors = device.get('errors', {})
                if 'chatmix' in errors:
                    chatmix_action.setText(f'ChatMix: {errors["chatmix"]}')
                elif 'chatmix' in device:
                    # If chatmix data exists, extract level
                    chatmix_info = device.get('chatmix', {})
                    chatmix_level = chatmix_info.get('level', 'N/A')
                    chatmix_action.setText(f'ChatMix: {chatmix_level}')
                else:
                    chatmix_action.setText('ChatMix: Not available')
            else:
                chatmix_action.setText('ChatMix: No device')
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to parse ChatMix JSON: {e}")
            chatmix_action.setText('ChatMix: N/A')
            
    except CalledProcessError as e:
        print(e)
        chatmix_action.setText('ChatMix: N/A')


def update_menu_checkmarks(menu, current_value, value_map):
    """Update checkmarks in a menu based on current value"""
    for action in menu.actions():
        if action.isCheckable():
            # Find the corresponding value for this action
            action_value = None
            for name, value in value_map:
                if action.text() == name:
                    action_value = value
                    break
            
            # Update checkmark
            action.setChecked(action_value == current_value)


def set_sidetone_with_update(level, menu):
    """Set sidetone level and update menu checkmarks"""
    set_sidetone(level)
    
    # Update checkmarks in the menu
    sidetone_options = [
        ("off", 0),
        ("low", 32),
        ("medium", 64),
        ("high", 96),
        ("max", 128)
    ]
    update_menu_checkmarks(menu, level, sidetone_options)


def set_sidetone(level):
    try:
        output = check_output([HEADSETCONTROL_BINARY, OPTION_SIDETONE, str(level), OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])
        # Save the setting for next startup
        save_setting("sidetone_level", level)
    except CalledProcessError as e:
        print(f"Error setting sidetone: {e}")

    return True


def set_inactive_time_with_update(level, menu):
    """Set inactive time and update menu checkmarks"""
    set_inactive_time(level)
    
    # Update checkmarks in the menu
    inactive_options = [
        ("off", 0),
        ("5 min", 5),
        ("15 min", 15),
        ("30 min", 30),
        ("60 min", 60),
        ("90 min", 90)
    ]
    update_menu_checkmarks(menu, level, inactive_options)


def set_inactive_time(level):
    try:
        output = check_output([HEADSETCONTROL_BINARY, OPTION_INACTIVE_TIME, str(level), OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])
        # Save the setting for next startup
        save_setting("inactive_time", level)
    except CalledProcessError as e:
        print(f"Error setting inactive time: {e}")

    return True


def set_led_with_update(level, menu):
    """Set LED state and update menu checkmarks"""
    set_led(level)
    
    # Update checkmarks in the menu
    led_options = [
        ("off", 0),
        ("on", 1)
    ]
    update_menu_checkmarks(menu, level, led_options)


def set_led(level):
    try:
        output = check_output([HEADSETCONTROL_BINARY, OPTION_LED, str(level), OPTION_SILENT, OPTION_OUTPUT, OPTION_OUTPUT_FORMAT])
        # Save the setting for next startup
        save_setting("led_state", level)
    except CalledProcessError as e:
        print(f"Error setting LED: {e}")

    return True


def sidetone_menu(parent_menu):
    # we map 5 levels to the range of [0-128]
    # The Steelseries Arctis internally supports 0-0x12, i.e. 0-18
    #    OFF -> 0
    #    LOW -> 32
    #    MEDIUM -> 64
    #    HIGH -> 96
    #    MAX -> 128

    sidemenu = parent_menu.addMenu("Sidetone")
    
    # Get current sidetone level from stored settings
    current_level = get_setting("sidetone_level")
    
    # Define sidetone options with their values
    sidetone_options = [
        ("off", 0),
        ("low", 32),
        ("medium", 64),
        ("high", 96),
        ("max", 128)
    ]
    
    for name, level in sidetone_options:
        action = QAction(name, sidemenu)
        action.setCheckable(True)
        
        # Check if this is the current level (convert to int for comparison)
        if current_level is not None and int(current_level) == level:
            action.setChecked(True)
        
        # Create a lambda that captures both the level and the action for updating checkmarks
        action.triggered.connect(lambda checked, l=level: set_sidetone_with_update(l, sidemenu))
        sidemenu.addAction(action)

    return sidemenu


def inactive_time_menu(parent_menu):
    # the option allows to set an inactive time between 0 and 90 minutes
    # therefore we map a few different time-spans to the range of [0-90]

    inactive_menu = parent_menu.addMenu("Inactive time")
    
    # Get current inactive time from stored settings
    current_time = get_setting("inactive_time")
    
    # Define inactive time options with their values
    inactive_options = [
        ("off", 0),
        ("5 min", 5),
        ("15 min", 15),
        ("30 min", 30),
        ("60 min", 60),
        ("90 min", 90)
    ]
    
    for name, time_val in inactive_options:
        action = QAction(name, inactive_menu)
        action.setCheckable(True)
        
        # Check if this is the current time (convert to int for comparison)
        if current_time is not None and int(current_time) == time_val:
            action.setChecked(True)
        
        # Create a lambda that captures both the time and the menu for updating checkmarks
        action.triggered.connect(lambda checked, t=time_val: set_inactive_time_with_update(t, inactive_menu))
        inactive_menu.addAction(action)

    return inactive_menu


def led_menu(parent_menu):
    ledmenu = parent_menu.addMenu("LED")
    
    # Get current LED state from stored settings
    current_state = get_setting("led_state")
    
    # Define LED options with their values
    led_options = [
        ("off", 0),
        ("on", 1)
    ]
    
    for name, state in led_options:
        action = QAction(name, ledmenu)
        action.setCheckable(True)
        
        # Check if this is the current state (convert to int for comparison)
        if current_state is not None and int(current_state) == state:
            action.setChecked(True)
        
        # Create a lambda that captures both the state and the menu for updating checkmarks
        action.triggered.connect(lambda checked, s=state: set_led_with_update(s, ledmenu))
        ledmenu.addAction(action)

    return ledmenu


def refresh():
    cap = fetch_capabilities()

    change_icon()
    if "all" == cap or b'b' in cap:
        change_label()
    if "all" == cap or b'm' in cap:
        change_chatmix()
    
    # return True to keep the timer running
    return True


def quit_app():
    global app
    app.quit()


def locate_headsetcontrol_binary(binary_location):
    location = which(binary_location)

    if location is None:
        print(f"Error: Unable to locate headsetcontrol binary at: {binary_location}")

    return location


def create_system_tray():
    global app, tray, charge_action, chatmix_action, base_icon
    
    # Create application
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    app.setQuitOnLastWindowClosed(False)
    
    if KDE_AVAILABLE:
        print("Using KDE KStatusNotifierItem with rich tooltip support")
        # Create KDE status notifier item for rich tooltips
        tray = KStatusNotifierItem(APPINDICATOR_ID)
        tray.setCategory(KStatusNotifierItem.ItemCategory.Hardware)
        tray.setStatus(KStatusNotifierItem.ItemStatus.Active)
        tray.setTitle("Headset Charge Indicator")
    else:
        # Check if system tray is available
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("System tray is not available on this system.")
            sys.exit(1)
        
        print("Using Qt6 QSystemTrayIcon (KDE libraries not available)")
        # Create basic Qt system tray icon
        tray = QSystemTrayIcon()
    
    # Icon selection with user preference
    if args.icon_name:
        # User specified a specific icon
        base_icon = QIcon.fromTheme(args.icon_name)
        if base_icon.isNull():
            print(f"Warning: Specified icon '{args.icon_name}' not found, falling back to defaults")
    
    if args.icon_name is None or base_icon.isNull():
        # Default preference with fallback - try monochrome/symbolic versions first
        icon_candidates = [
            "audio-headset-symbolic",      # Symbolic (monochrome) version
            "audio-headphones-symbolic",   # Symbolic headphones
            "audio-headset",               # Regular colored version
            "audio-headphones",            # Regular headphones  
            "audio-card",                  # Audio card fallback
            "multimedia-player"            # Final fallback
        ]
        
        for icon_name in icon_candidates:
            base_icon = QIcon.fromTheme(icon_name)
            if not base_icon.isNull():
                break
    
    if base_icon.isNull():
        # Final fallback to a standard system icon
        style = app.style()
        base_icon = style.standardIcon(style.StandardPixmap.SP_MediaVolume)
    
    if KDE_AVAILABLE:
        # For KDE, use the same base_icon we resolved above
        tray.setIconByPixmap(base_icon.pixmap(64, 64))
        tray.setToolTipTitle("Headset")
        tray.setToolTipSubTitle("Initializing...")
    else:
        tray.setIcon(base_icon)
        tray.setToolTip("Headset\nInitializing...")
    
    cap = fetch_capabilities()

    # Create menu with proper parent
    menu = QMenu()
    
    # Refresh item
    refresh_action = QAction("Refresh", menu)
    refresh_icon = QIcon.fromTheme("view-refresh")
    if not refresh_icon.isNull():
        refresh_action.setIcon(refresh_icon)
    refresh_action.triggered.connect(refresh)
    menu.addAction(refresh_action)

    # Add capability-based menu items
    if "all" == cap or b'b' in cap:
        charge_action = QAction("Charge: -1", menu)
        # Set initial battery icon (will be updated in change_label)
        battery_icon = QIcon.fromTheme("battery-missing")
        if not battery_icon.isNull():
            charge_action.setIcon(battery_icon)
        menu.addAction(charge_action)

    if "all" == cap or b'm' in cap:
        chatmix_action = QAction("Chat: -1", menu)
        # Add microphone/chat icon
        chatmix_icons = ["audio-input-microphone", "microphone", "audio-input-microphone-symbolic", "call-start"]
        for icon_name in chatmix_icons:
            chatmix_icon = QIcon.fromTheme(icon_name)
            if not chatmix_icon.isNull():
                chatmix_action.setIcon(chatmix_icon)
                break
        menu.addAction(chatmix_action)

    if "all" == cap or b's' in cap:
        sidetone_submenu = sidetone_menu(menu)
        # Add headphones icon to sidetone submenu
        sidetone_icons = ["audio-headphones", "audio-headset", "audio-headphones-symbolic", "audio-headset-symbolic"]
        for icon_name in sidetone_icons:
            sidetone_icon = QIcon.fromTheme(icon_name)
            if not sidetone_icon.isNull():
                sidetone_submenu.setIcon(sidetone_icon)
                break

    if "all" == cap or b'l' in cap:
        led_submenu = led_menu(menu)
        # Add LED/light icon to LED submenu
        led_icons = ["preferences-desktop-theme", "led", "lightbulb", "weather-clear", "brightness-high"]
        for icon_name in led_icons:
            led_icon = QIcon.fromTheme(icon_name)
            if not led_icon.isNull():
                led_submenu.setIcon(led_icon)
                break

    if "all" == cap or b'i' in cap:
        inactive_submenu = inactive_time_menu(menu)
        # Add timer/clock icon to inactive time submenu
        inactive_icons = ["preferences-system-time", "clock", "appointment-soon", "timer", "chronometer"]
        for icon_name in inactive_icons:
            inactive_icon = QIcon.fromTheme(icon_name)
            if not inactive_icon.isNull():
                inactive_submenu.setIcon(inactive_icon)
                break



    # Exit item if using Qt system tray
    if KDE_AVAILABLE:
        tray.quitRequested.connect(quit_app)
    else:
        menu.addSeparator()
        exit_action = QAction("Quit", menu)
        # Add quit/exit icon
        quit_icons = ["application-exit", "system-log-out", "exit", "window-close", "process-stop"]
        for icon_name in quit_icons:
            quit_icon = QIcon.fromTheme(icon_name)
            if not quit_icon.isNull():
                exit_action.setIcon(quit_icon)
                break
        exit_action.triggered.connect(quit_app)
        menu.addAction(exit_action)

    if KDE_AVAILABLE:
        tray.setContextMenu(menu)
        # KStatusNotifierItem shows automatically when status is Active
    else:
        tray.setContextMenu(menu)
        # Show tray icon for Qt system tray
        tray.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""
    Qt6 System Tray application which uses the HeadsetControl application from 
    https://github.com/Sapd/HeadsetControl/ for retrieving charge information
    for wireless headsets and displays it as system tray icon with native tooltips.
    
    Enhanced for KDE Plasma Desktop with:
    - Native tooltip support showing battery percentage
    - Color-coded icons (red/orange/normal based on battery level)
    - Desktop notifications for battery level changes and critical levels
    
    The application has optional commandline arguments for customizing behavior,
    battery thresholds, and notification settings.
    """, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--headsetcontrol-binary', metavar='<path to headsetcontrol binary>', type=str,
                        help='Optional path to headsetcontrol binary', required=False, default='headsetcontrol',
                        dest='headsetcontrolbinary')

    parser.add_argument("--verbose", help="Increase output verbosity", action="store_true")
    parser.add_argument("--low-battery", metavar='<percentage>', type=int, default=20,
                        help='Battery percentage threshold for red icon (default: 20)')
    parser.add_argument("--medium-battery", metavar='<percentage>', type=int, default=50,
                        help='Battery percentage threshold for orange icon (default: 50)')
    parser.add_argument("--no-notifications", help="Disable desktop notifications", action="store_true")
    parser.add_argument("--poll-interval", metavar='<seconds>', type=int, default=60,
                        help='Polling interval in seconds (default: 60)')
    parser.add_argument("--icon-name", metavar='<icon-name>', type=str, default=None,
                        help='Specific icon name to use (e.g. "audio-headset-symbolic" for monochrome)')
    parser.add_argument("--force-qt", help="Force use of Qt system tray instead of KDE libraries", action="store_true")
    args = parser.parse_args()

    # Determine KDE availability based on libraries and user preference
    KDE_AVAILABLE = KDE_LIBRARIES_PRESENT and not args.force_qt

    # Update configuration from command line arguments
    # Update battery thresholds
    BATTERY_LOW_THRESHOLD = args.low_battery
    BATTERY_MEDIUM_THRESHOLD = args.medium_battery
    
    # Update polling interval
    POLLING_INTERVAL_SECONDS = args.poll_interval
    
    # Validate polling interval
    if POLLING_INTERVAL_SECONDS < 1:
        print("Error: Polling interval must be at least 1 second")
        sys.exit(1)
    elif POLLING_INTERVAL_SECONDS > 3600:
        print("Warning: Polling interval is longer than 1 hour")

    HEADSETCONTROL_BINARY = locate_headsetcontrol_binary(args.headsetcontrolbinary)

    if not HEADSETCONTROL_BINARY:
        parser.print_usage()
        sys.exit(2)


    # Initialize settings for persistent storage BEFORE creating system tray
    settings = QSettings("HeadsetChargeIndicator", "HeadsetSettings")

    # Create the system tray (now with settings available for checkmarks)
    create_system_tray()
    
    # Restore previous headset settings
    restore_headset_settings()
    
    # Set up timer for updates
    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(POLLING_INTERVAL_SECONDS * 1000)  # Convert seconds to milliseconds

    # refresh values right away
    refresh()

    # Start the application
    sys.exit(app.exec())
