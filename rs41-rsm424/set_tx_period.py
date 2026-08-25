#!/usr/bin/env python3
"""Override the Horus V3 TX period for a reproducible build artifact."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(f"Usage: {sys.argv[0]} CONFIG.h PERIOD_SECONDS OUT_DIR")

    config_path = Path(sys.argv[1])
    period_seconds = int(sys.argv[2])
    out_dir = Path(sys.argv[3])

    if period_seconds < 5 or period_seconds > 3600:
        raise SystemExit("PERIOD_SECONDS must be between 5 and 3600")

    config = config_path.read_text(encoding="utf-8")
    pattern = r"^(\s*(?:(?:constexpr|const|static|volatile)\s+)*(?:unsigned\s+long|unsigned\s+int|uint16_t|uint32_t|int|long)\s+horusV3TimeSyncSeconds\s*=\s*)([^;]+)(\s*;)"
    config, count = re.subn(pattern, rf"\g<1>{period_seconds}\g<3>", config, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Expected exactly one horusV3TimeSyncSeconds assignment, got {count}")

    config_path.write_text(config, encoding="utf-8")
    (out_dir / "CONFIG.h").write_text(config, encoding="utf-8")

    summary_path = out_dir / "build-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["period_seconds"] = period_seconds
    summary["rf_schedule"] = f"GPS-seeded Horus V3 slot every {period_seconds} seconds; MCU free-run between daily GPS relocks"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Set Horus V3 TX period to {period_seconds} seconds")


if __name__ == "__main__":
    main()
