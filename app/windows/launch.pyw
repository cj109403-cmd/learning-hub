"""The shortcut targets pythonw directly, so no console window is created."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.desktop.launch import main

raise SystemExit(main())
