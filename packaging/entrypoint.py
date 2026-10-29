#!/usr/bin/python3
"""Use only the package's Python code, wheels and the OS hardware bindings."""
from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parent
sys.path[:0] = [str(root / "vendor"), str(root)]
# Native audio/TTS helpers run as child Python processes and need the same
# packaged modules without relying on a developer's virtual environment.
os.environ["PYTHONPATH"] = os.pathsep.join([str(root / "vendor"), str(root)])
from radxa_duck.launcher import main

main()
