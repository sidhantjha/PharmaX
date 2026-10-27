#!/usr/bin/env python3
"""PharmaX integrated operator demo and existing HTTP server."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from phases.phase_05_product.terminal import main  # noqa: E402

if __name__=='__main__':
    raise SystemExit(main())
