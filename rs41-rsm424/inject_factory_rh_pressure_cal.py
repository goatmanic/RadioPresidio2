#!/usr/bin/env python3
"""Inject the RS41 factory humidity pressure-compensation tables into CONFIG.h.

The pinned NFW factory-RH path already carries calibU/matrixU but omits the
additional corHp[3] and corHt[12] fields used by the reference rs1729/DF9DQ
RS41 humidity calculation. They live in the same factory calibration subframe
at offsets 0x2A6 and 0x2BA.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

NFW_BACKEND = Path("/src/rs41-nfw_souding-software/backend")
if str(NFW_BACKEND) not in sys.path:
    sys.path.insert(0, str(NFW_BACKEND))

from rs41_subframe import download_subframe_data  # type: ignore

if len(sys.argv) != 3:
    raise SystemExit(f"Usage: {sys.argv[0]} CONFIG.h SENSOR_BOOM_SERIAL")

config_path = Path(sys.argv[1])
serial = sys.argv[2].strip().upper()
raw, telemetry = download_subframe_data(serial)
if not raw:
    raise SystemExit(f"No complete calibration subframe found for {serial}")
raw = bytes(raw)
if len(raw) < 746:
    raise SystemExit(f"Calibration subframe too short: {len(raw)} bytes")

cor_hp = list(struct.unpack_from("<3f", raw, 0x2A6))
cor_ht = list(struct.unpack_from("<12f", raw, 0x2BA))

cfg = config_path.read_text(encoding="utf-8")
needle = "const float factoryMatrixU[42] = {"
pos = cfg.find(needle)
if pos < 0:
    raise SystemExit("Could not locate factoryMatrixU[42] in generated CONFIG.h")
end = cfg.find(";", pos)
if end < 0:
    raise SystemExit("Could not locate end of factoryMatrixU[42]")
end += 1

if "factoryCorHp[3]" in cfg or "factoryCorHt[12]" in cfg:
    raise SystemExit("factory RH pressure compensation arrays already present")

fmt = lambda vals: ", ".join(repr(float(v)) for v in vals)
insert = (
    "\n// Full Vaisala/rs1729 factory RH pressure/temperature compensation.\n"
    "// Source: calibration subframe offsets 0x2A6 (corHp) and 0x2BA (corHt).\n"
    f"const float factoryCorHp[3] = {{{fmt(cor_hp)}}};\n"
    f"const float factoryCorHt[12] = {{{fmt(cor_ht)}}};\n"
)
cfg = cfg[:end] + insert + cfg[end:]
config_path.write_text(cfg, encoding="utf-8")

# build_config.py writes /out/CONFIG.h before this injector runs. Refresh that
# audit/package copy after injection so CI verifies and ships the exact CONFIG.h
# that the compiler actually consumed.
Path("/out/CONFIG.h").write_text(cfg, encoding="utf-8")

out = {
    "sensor_boom_serial": serial,
    "corHp": cor_hp,
    "corHt": cor_ht,
    "source_offsets": {"corHp": "0x2A6", "corHt": "0x2BA"},
    "telemetry_datetime": (telemetry or {}).get("datetime"),
}
Path("/out/factory-rh-pressure-corrections.json").write_text(
    json.dumps(out, indent=2) + "\n", encoding="utf-8"
)

# Keep the human/machine-readable build summary synchronized with the exact v5
# calculation and test that the compiler receives.
summary_path = Path("/out/build-summary.json")
summary = json.loads(summary_path.read_text(encoding="utf-8"))
summary.update({
    "diagnostic_package_revision": 5,
    "factory_humidity_model": "calibU + matrixU + corHp/corHt pressure-temperature correction",
    "factory_humidity_check_basis": "10-sample physical-zero correction: measured hot sensor-local factory RH minus expected hot RH from preheat ambient vapor pressure",
    "factory_humidity_reconditioning_target_c": 150,
    "factory_humidity_reconditioning_dwell_seconds": 180,
    "factory_humidity_zero_samples": 10,
    "factory_humidity_zero_correction_limit_rh": 2.0,
    "factory_humidity_cooling_after_check": True,
    "factory_humidity_pressure_coefficients": out,
})
summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

print(json.dumps(out, indent=2))
