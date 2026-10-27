#!/usr/bin/env python3
"""Run from a source checkout with the project-local Python environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phases.phase_01_foundation.terminal import main  # noqa: E402

if __name__ == '__main__':
    if sys.argv[1:2] == ['risk']:
        from phases.phase_03_risk.terminal import main as risk_main
        raise SystemExit(risk_main(sys.argv[2:]))
    if sys.argv[1:2] == ['data']:
        from phases.phase_02_data.terminal import main as data_main
        raise SystemExit(data_main(sys.argv[2:]))
    raise SystemExit(main())
