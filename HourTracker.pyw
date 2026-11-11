"""Double-click this file (Windows) or run `python HourTracker.pyw` to start Hour Tracker."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hourtracker.gui import main  # noqa: E402

main()
