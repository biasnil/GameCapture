"""Double-click launcher: opens the GameCapture window without a console."""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from main import GameCaptureCLI  # noqa: E402

sys.exit(GameCaptureCLI.main(["gui"]))
