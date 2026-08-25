#!/usr/bin/env python3
"""Make v5 humidity-check logs use the factory-model capacitance, not the legacy direct-frequency diagnostic capacitance."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")
p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
repls = {
    "const float preheatCap = humidityCapacitance;": "const float preheatCap = factoryRhRawCap;",
    "xdataSerial.print(humidityCapacitance, 5);": "xdataSerial.print(factoryRhRawCap, 5);",
    "capSum += humidityCapacitance;": "capSum += factoryRhRawCap;",
}
for old, new in repls.items():
    if old not in s:
        raise SystemExit(f"Expected v5 cap-log expression missing: {old}")
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("v5 humidity-check logging now uses factoryRhRawCap")
