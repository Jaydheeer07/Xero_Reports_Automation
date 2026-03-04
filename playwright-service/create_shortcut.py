"""
create_shortcut.py — One-time setup script.

Creates a "Xero Reports" shortcut on the Windows desktop that launches
tray.py silently (no console window) via pythonw.exe.

Run once after installation:
    python create_shortcut.py
"""

import os
import sys


def main():
    try:
        import win32com.client
    except ImportError:
        print("ERROR: pywin32 is not installed.")
        print("Run: pip install pywin32")
        sys.exit(1)

    # Paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    tray_script = os.path.join(base_dir, "tray.py")
    icon_path = os.path.join(base_dir, "assets", "icon.ico")

    # Find pythonw.exe (no-console Python)
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        # Fallback: use regular python.exe
        pythonw = sys.executable
        print("WARNING: pythonw.exe not found, using python.exe (terminal may flash briefly)")

    # Desktop path
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")

    # Create shortcut
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut_path = os.path.join(desktop, "Xero Reports.lnk")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.TargetPath = pythonw
    shortcut.Arguments = f'"{tray_script}"'
    shortcut.WorkingDirectory = base_dir
    shortcut.Description = "Xero Reports Automation"
    if os.path.exists(icon_path):
        shortcut.IconLocation = icon_path
    shortcut.Save()

    print(f"✓ Shortcut created: {shortcut_path}")
    print(f"  → Runs: {pythonw} \"{tray_script}\"")
    print(f"  → Working dir: {base_dir}")
    print()
    print("Double-click 'Xero Reports' on your desktop to start the app.")


if __name__ == "__main__":
    main()
