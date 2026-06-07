#!/usr/bin/env python3
"""Test script in pipeline script location."""
import sys, os
from pathlib import Path

print(f"executable: {sys.executable}")
print(f"real executable: {os.path.realpath(sys.executable)}")
print(f"argv: {sys.argv}")
print(f"__file__: {__file__}")
print(f"resolved __file__: {Path(__file__).resolve()}")
print(f"cwd: {os.getcwd()}")
print(f"PYTHONPATH: {os.environ.get('PYTHONPATH', '(not set)')}")
