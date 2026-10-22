"""Use the sibling checkout to verify the actual VideoPrinterTurbo adapter."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root.parent / 'VideoPrinterTurbo' / 'server'))
sys.path.insert(0, str(root))
