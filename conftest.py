# conftest.py — root-level pytest configuration
# Ensures that 'solar_agent' is importable without installation (editable-install style).

import sys
import os

# Add the project root to sys.path so 'import solar_agent' works in tests.
sys.path.insert(0, os.path.dirname(__file__))
