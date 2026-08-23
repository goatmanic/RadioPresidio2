#!/usr/bin/env python3
"""Fix the factory humidity hot/dry self-check for heated-sensor operation.

Upstream getFactoryRH() returns ambient-equivalent RH: the factory matrix produces
sensor-local RH at the humidity-element temperature, then the result is multiplied
by Psat(Tsensor)/Psat(Tair) and clamped to 0..100 %RH. During the ~135-140 C
heater self-check that ambient-equivalent value can legitimately clamp at 100 %RH,
so using humidityValue as a "hot/dry <2%" criterion is wrong.

This patch exposes the factory-matrix sensor-local RH before vapor-pressure
conversion, and makes humidityCheck() average that value for its five hot/dry
samples. Normal reported/telemetry humidityValue remains unchanged.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

# 1) Add a diagnostic state variable beside the normal RH value.
needle = "uint16_t humidityValue;\nfloat pressureValue;\n"
replacement = (
    "uint16_t humidityValue;\n"
    "// Factory-matrix RH at the heated humidity element before conversion back to\n"
    "// ambient-equivalent RH. Used only by the aggressive factory humidity check.\n"
    "float factoryRhSensorLocal = -1.0f;\n"
    "float pressureValue;\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one humidityValue/pressureValue declaration block, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

# 2) Capture the factory-matrix output before the ambient vapor-pressure conversion.
needle = (
    "  if (Tair < -40.0f) rh += (Tair - (-40.0f)) / 12.0f;      // low-temperature correction\n"
    "  rh *= factoryVaporSatP(Tsensor) / factoryVaporSatP(Tair);\n"
)
replacement = (
    "  if (Tair < -40.0f) rh += (Tair - (-40.0f)) / 12.0f;      // low-temperature correction\n"
    "  // Preserve the RH physically present at the heated sensing element. The\n"
    "  // following vapor-pressure ratio converts it to ambient-equivalent RH.\n"
    "  factoryRhSensorLocal = rh;\n"
    "  rh *= factoryVaporSatP(Tsensor) / factoryVaporSatP(Tair);\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one factory RH vapor-pressure conversion, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

# 3) During the final five hot samples, use sensor-local RH and log every sample.
needle = (
    "    if (extHeaterTemperatureValue > 115.0f && !sensorBoomHumidityModuleError) {\n"
    "      rhSum += humidityValue; rhCount++;\n"
    "    }\n"
)
replacement = (
    "    if (extHeaterTemperatureValue > 115.0f && !sensorBoomHumidityModuleError) {\n"
    "      const float localRh = factoryRhSensorLocal;\n"
    "      // NaN and out-of-range values fail these comparisons and therefore do\n"
    "      // not count as successful dry samples. A timeout then fails safely.\n"
    "      if (localRh >= 0.0f && localRh <= 100.0f) {\n"
    "        rhSum += localRh; rhCount++;\n"
    "        if (xdataPortMode == 1) {\n"
    "          xdataSerial.print(\"[info]: humidity CHECK dry sample \" );\n"
    "          xdataSerial.print(rhCount); xdataSerial.print(\"/5 local=\");\n"
    "          xdataSerial.print(localRh, 3); xdataSerial.print(\" %RH reported=\");\n"
    "          xdataSerial.print(humidityValue); xdataSerial.print(\" %RH T=\");\n"
    "          xdataSerial.print(extHeaterTemperatureValue, 2); xdataSerial.println(\" C\");\n"
    "        }\n"
    "      }\n"
    "    }\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one hot/dry sample accumulation block, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

# 4) Make the final result unambiguous in the RAM/XDATA log.
s = s.replace(
    'xdataSerial.print("[err]: humidity CHECK FAILED - dry reading ");',
    'xdataSerial.print("[err]: humidity CHECK FAILED - sensor-local dry reading ");',
    1,
)
needle = '      xdataSerial.println("[info]: humidity CHECK passed");\n'
replacement = (
    '      xdataSerial.print("[info]: humidity CHECK passed - sensor-local dry reading ");\n'
    '      xdataSerial.print(dryRH, 3); xdataSerial.println(" %RH");\n'
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one humidity-check pass line, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

p.write_text(s, encoding="utf-8")
print("Patched factory humidity check to use sensor-local factory-matrix RH")
