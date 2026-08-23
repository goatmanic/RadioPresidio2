#!/usr/bin/env python3
"""Make the RSM424 factory humidity path match the RS41 ground-check physics.

Changes applied to the pinned upstream source:
  * add the missing rs1729/DF9DQ pressure/temperature correction to factory RH;
  * preserve raw cap, corrected Cp, and sensor-local RH for diagnostics;
  * replace the short 30 s / ~138 C humidity check with a ~150 C, 3 minute
    reconditioning dwell, matching Vaisala's documented RS41 procedure;
  * perform a 10-sample physical-zero check while hot and log raw frequencies,
    capacitance, Cp, local RH, ambient-equivalent RH and pressure;
  * cool the boom after reconditioning before normal operation;
  * rate-limit heater-power chatter so the 8 KiB ST-Link RAM log keeps the whole
    meaningful sequence.

Normal flight telemetry RH still uses the factory model and is not replaced by
an ad-hoc humidity formula.  Zero-humidity calibration is not written to flash.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# 1) Host-visible / check-visible factory RH diagnostics.
needle = "uint16_t humidityValue;\nfloat pressureValue;\n"
replacement = (
    "uint16_t humidityValue;\n"
    "// Factory-humidity diagnostics used by the aggressive RS41 ground check.\n"
    "float factoryRhSensorLocal = -1.0f;\n"
    "float factoryRhCorrectedCp = 0.0f;\n"
    "float factoryRhRawCap = 0.0f;\n"
    "float pressureValue;\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one humidityValue/pressureValue declaration block, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

# ---------------------------------------------------------------------------
# 2) Complete getFactoryRH() with the pressure-dependent Cp correction used by
#    the reference rs1729/DF9DQ implementation.  factoryCorHp/factoryCorHt are
#    injected into CONFIG.h from this boom's calibration subframe.
needle = (
    "  float cap = factoryRefCapLow + (factoryRefCapHigh - factoryRefCapLow) * cfh;\n"
    "  float Cp = ((float)cap / factoryCalibU0 - 1.0f) * factoryCalibU1;\n\n"
    "  float Trh = ((float)Tsensor - 20.0f) / 180.0f;\n"
)
replacement = (
    "  float cap = factoryRefCapLow + (factoryRefCapHigh - factoryRefCapLow) * cfh;\n"
    "  float Cp = ((float)cap / factoryCalibU0 - 1.0f) * factoryCalibU1;\n"
    "  factoryRhRawCap = cap;\n\n"
    "  float Trh = ((float)Tsensor - 20.0f) / 180.0f;\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one factory RH cap/Cp block, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

needle = (
    "  float rh = 0.0f;\n"
    "  float aj = 1.0f;                                          // aj = Cp^j\n"
)
replacement = (
    "  // Full factory pressure correction (rs1729/DF9DQ). pressureValue is hPa.\n"
    "  // The correction is skipped only until a valid pressure sample exists.\n"
    "  if (pressureValue > 0.0f && pressureValue < 1200.0f) {\n"
    "    const float pBar = pressureValue / 1000.0f;\n"
    "    float cpj = 1.0f;\n"
    "    float bp[3];\n"
    "    for (int j = 0; j < 3; ++j) {\n"
    "      const float h = factoryCorHp[j];\n"
    "      bp[j] = h * (pBar / (1.0f + h * pBar) - cpj / (1.0f + h));\n"
    "      cpj *= Cp;\n"
    "    }\n"
    "    float corrCp = 0.0f;\n"
    "    for (int j = 0; j < 3; ++j) {\n"
    "      float bt = 0.0f;\n"
    "      for (int k = 0; k < 4; ++k) bt += factoryCorHt[4 * j + k] * b[k];\n"
    "      corrCp += bp[j] * bt;\n"
    "    }\n"
    "    Cp -= corrCp;\n"
    "  }\n"
    "  factoryRhCorrectedCp = Cp;\n\n"
    "  float rh = 0.0f;\n"
    "  float aj = 1.0f;                                          // aj = Cp^j\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one factory RH matrix preamble, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

needle = (
    "  if (Tair < -40.0f) rh += (Tair - (-40.0f)) / 12.0f;      // low-temperature correction\n"
    "  rh *= factoryVaporSatP(Tsensor) / factoryVaporSatP(Tair);\n"
)
replacement = (
    "  // With valid pressure, the full factory Cp correction above replaces the\n"
    "  // empirical no-pressure low-temperature fallback used by the decoder.\n"
    "  if (!(pressureValue > 0.0f && pressureValue < 1200.0f) && Tair < -40.0f)\n"
    "    rh += (Tair - (-40.0f)) / 12.0f;\n"
    "  factoryRhSensorLocal = rh;\n"
    "  rh *= factoryVaporSatP(Tsensor) / factoryVaporSatP(Tair);\n"
)
if s.count(needle) != 1:
    raise SystemExit(f"Expected one factory RH vapor-pressure conversion, got {s.count(needle)}")
s = s.replace(needle, replacement, 1)

# ---------------------------------------------------------------------------
# 3) Rate-limit raw heater-power chatter.  We still log immediately on OFF,
#    first use, >=25-count jumps, or at least once every five seconds.
needle = (
    "  if (xdataPortMode == 1) {\n"
    "    xdataSerial.print(\"[info]: extHeater pwr \" );\n"
)
# Upstream has no space before ); -- use the exact actual block below instead.
needle_actual = (
    "  if (xdataPortMode == 1) {\n"
    "    xdataSerial.print(\"[info]: extHeater pwr \" );\n"
)
# Handle exact source spelling independently to keep failure explicit.
if needle_actual not in s:
    needle_actual = (
        "  if (xdataPortMode == 1) {\n"
        "    xdataSerial.print(\"[info]: extHeater pwr \" );\n"
    )
# The generated source spelling from pinned v77 is known; replace the full block
# using a simpler exact literal to avoid whitespace ambiguity.
old_block = '''  if (xdataPortMode == 1) {
    xdataSerial.print("[info]: extHeater pwr ");
    xdataSerial.print(heaterPower);
    xdataSerial.println("/500");
  }
'''
new_block = '''  static int _lastHeaterLogValue = -1000;
  static unsigned long _lastHeaterLogMillis = 0;
  if (xdataPortMode == 1) {
    const int delta = abs((int)heaterPower - _lastHeaterLogValue);
    if (heaterPower == 0 || _lastHeaterLogValue < 0 || delta >= 25 ||
        millis() - _lastHeaterLogMillis >= 5000UL) {
      xdataSerial.print("[info]: extHeater pwr ");
      xdataSerial.print(heaterPower);
      xdataSerial.println("/500");
      _lastHeaterLogValue = (int)heaterPower;
      _lastHeaterLogMillis = millis();
    }
  }
'''
if s.count(old_block) != 1:
    raise SystemExit(f"Expected one extHeater power logging block, got {s.count(old_block)}")
s = s.replace(old_block, new_block, 1)

# ---------------------------------------------------------------------------
# 4) Replace upstream's 30-second check with the documented RS41-style cycle.
start = s.find("void humidityCheck() {")
if start < 0:
    raise SystemExit("Could not locate humidityCheck()")
end = s.find("\n}\n#endif", start)
if end < 0:
    raise SystemExit("Could not locate end of humidityCheck()")
end += 2  # include the function's closing brace, leave #endif in place

new_func = r'''void humidityCheck() {
  if (xdataPortMode == 1) {
    xdataSerial.println("[info]: Factory humidity CHECK - RS41 3-minute reconditioning");
    xdataSerial.println("[warn]: Humidity sensor heating to about 150C for 3 minutes - DO NOT TOUCH");
  }
  setStage("22");

  // Capture the ambient state before heating.  At 150 C the same absolute water
  // vapour content corresponds to well below 1 %RH; we calculate that expected
  // physical-zero value for each final sample instead of pretending it is exactly 0.
  sensorBoomHandler();
  pressureHandler();
  const float preheatAirRh = (float)humidityValue;
  const float preheatAirT = mainTemperatureValue;
  const float preheatCap = humidityCapacitance;
  if (xdataPortMode == 1) {
    xdataSerial.print("[info]: humidity CHECK preheat T="); xdataSerial.print(preheatAirT, 2);
    xdataSerial.print(" C RH="); xdataSerial.print(preheatAirRh, 1);
    xdataSerial.print(" % cap="); xdataSerial.print(preheatCap, 5);
    xdataSerial.print(" p="); xdataSerial.print(pressureValue, 1); xdataSerial.println(" hPa");
  }

  const float reconditioningTargetC = 150.0f;
  const float dwellStartThresholdC = 145.0f;
  const unsigned long reconditioningDwellMs = 180000UL;  // Vaisala: about 3 min at ~150 C
  const unsigned long heatupTimeoutMs = 90000UL;
  const float hardOvertempC = 165.0f;

  const unsigned long beginMillis = millis();
  unsigned long dwellBeginMillis = 0;
  unsigned long lastProgressMillis = 0;
  unsigned long lastPressureMillis = 0;
  bool dwellStarted = false;
  delay(500);

  while (true) {
    orangeLed(); delay(80); bothLedOff();
    sensorBoomHandler();
    buttonHandlerSimplified();
    interfaceHandler();

    if (millis() - lastPressureMillis >= 5000UL) {
      pressureHandler();
      lastPressureMillis = millis();
    }

    if (sensorBoomHumidityModuleError) {
      extHeaterHandler(false, 0, 0);
      humidityCheckError = true; calibrationError = true;
      if (xdataPortMode == 1)
        xdataSerial.println("[err]: humidity CHECK - sensor boom error during reconditioning");
      return;
    }

    extHeaterHandler(true, reconditioningTargetC, extHeaterTemperatureValue);

    if (extHeaterTemperatureValue > hardOvertempC) {
      extHeaterHandler(false, 0, 0);
      humidityCheckError = true; calibrationError = true;
      if (xdataPortMode == 1)
        xdataSerial.println("[err]: humidity CHECK FAILED - heater exceeded 165 C safety limit");
      return;
    }

    if (!dwellStarted && extHeaterTemperatureValue >= dwellStartThresholdC) {
      dwellStarted = true;
      dwellBeginMillis = millis();
      if (xdataPortMode == 1)
        xdataSerial.println("[info]: humidity CHECK 3-minute hot dwell started");
    }

    if (millis() - lastProgressMillis >= 5000UL) {
      lastProgressMillis = millis();
      if (xdataPortMode == 1) {
        xdataSerial.print("[info]: humidity CHECK reconditioning T=");
        xdataSerial.print(extHeaterTemperatureValue, 2);
        xdataSerial.print(" C cap="); xdataSerial.print(humidityCapacitance, 5);
        xdataSerial.print(" local="); xdataSerial.print(factoryRhSensorLocal, 3);
        xdataSerial.print(" %RH p="); xdataSerial.print(pressureValue, 1);
        if (dwellStarted) {
          xdataSerial.print(" hPa dwell=");
          xdataSerial.print((millis() - dwellBeginMillis) / 1000UL);
          xdataSerial.print("/180s");
        } else {
          xdataSerial.print(" hPa heatup");
        }
        xdataSerial.println();
      }
    }

    if (dwellStarted && millis() - dwellBeginMillis >= reconditioningDwellMs) break;
    if (!dwellStarted && millis() - beginMillis >= heatupTimeoutMs) {
      extHeaterHandler(false, 0, 0);
      humidityCheckError = true; calibrationError = true;
      if (xdataPortMode == 1)
        xdataSerial.println("[err]: humidity CHECK FAILED - did not reach 145 C in 90 s");
      return;
    }
  }

  // Physical zero-humidity check.  Keep the sensor hot, take ten independent
  // factory-model samples, and compare the measured sensor-local RH to the RH
  // physically expected from the preheat room-air vapour pressure at this hot T.
  setStage("23");
  float measuredSum = 0.0f;
  float expectedSum = 0.0f;
  float capSum = 0.0f;
  int sampleCount = 0;
  unsigned long sampleBegin = millis();

  while (sampleCount < 10 && millis() - sampleBegin < 30000UL) {
    sensorBoomHandler();
    if (millis() - lastPressureMillis >= 5000UL) {
      pressureHandler();
      lastPressureMillis = millis();
    }
    extHeaterHandler(true, reconditioningTargetC, extHeaterTemperatureValue);
    buttonHandlerSimplified();
    interfaceHandler();

    if (extHeaterTemperatureValue >= 140.0f && extHeaterTemperatureValue <= hardOvertempC &&
        !sensorBoomHumidityModuleError && factoryRhSensorLocal >= -20.0f &&
        factoryRhSensorLocal <= 120.0f) {
      const float expectedLocalRh = preheatAirRh *
        factoryVaporSatP(preheatAirT) / factoryVaporSatP(extHeaterTemperatureValue);
      measuredSum += factoryRhSensorLocal;
      expectedSum += expectedLocalRh;
      capSum += humidityCapacitance;
      sampleCount++;

      if (xdataPortMode == 1) {
        xdataSerial.print("[info]: humidity CHECK zero sample ");
        xdataSerial.print(sampleCount); xdataSerial.print("/10 T=");
        xdataSerial.print(extHeaterTemperatureValue, 2);
        xdataSerial.print("C local="); xdataSerial.print(factoryRhSensorLocal, 3);
        xdataSerial.print("% expected="); xdataSerial.print(expectedLocalRh, 3);
        xdataSerial.print("% reported="); xdataSerial.print(humidityValue);
        xdataSerial.print("% cap="); xdataSerial.print(humidityCapacitance, 5);
        xdataSerial.print("pF Cp="); xdataSerial.print(factoryRhCorrectedCp, 6);
        xdataSerial.print(" fU="); xdataSerial.print(humidityFrequency, 2);
        xdataSerial.print(" f0="); xdataSerial.print(refCapLowFrequency, 2);
        xdataSerial.print(" f47="); xdataSerial.print(refCapHighFrequency, 2);
        xdataSerial.print(" P="); xdataSerial.print(pressureValue, 1);
        xdataSerial.println("hPa");
      }
      delay(250);
    } else {
      delay(100);
    }
  }

  extHeaterHandler(false, 0, 0);

  const float measuredDryRh = sampleCount ? measuredSum / sampleCount : 100.0f;
  const float expectedDryRh = sampleCount ? expectedSum / sampleCount : 0.0f;
  const float zeroCorrection = measuredDryRh - expectedDryRh;
  const float avgDryCap = sampleCount ? capSum / sampleCount : -1.0f;
  const bool zeroPass = sampleCount == 10 && fabsf(zeroCorrection) <= 2.0f;

  if (xdataPortMode == 1) {
    xdataSerial.print("[info]: humidity CHECK zero result measured=");
    xdataSerial.print(measuredDryRh, 3);
    xdataSerial.print("% expected="); xdataSerial.print(expectedDryRh, 3);
    xdataSerial.print("% correction="); xdataSerial.print(zeroCorrection, 3);
    xdataSerial.print("% avgCap="); xdataSerial.print(avgDryCap, 5);
    xdataSerial.println("pF");
  }

  // Vaisala's normal preparation includes cooling after reconditioning.  Cool
  // before returning to normal telemetry so we do not start the main loop with a
  // 150 C humidity element and a deliberately saturated ambient-equivalent RH.
  setStage("24");
  if (xdataPortMode == 1) xdataSerial.println("[info]: humidity CHECK cooling after reconditioning");
  const unsigned long coolBegin = millis();
  unsigned long lastCoolLog = 0;
  while (millis() - coolBegin < 90000UL) {
    sensorBoomHandler();
    buttonHandlerSimplified();
    interfaceHandler();
    if (extHeaterTemperatureValue <= mainTemperatureValue + 10.0f) break;
    if (millis() - lastCoolLog >= 5000UL) {
      lastCoolLog = millis();
      if (xdataPortMode == 1) {
        xdataSerial.print("[info]: humidity CHECK cooling T=");
        xdataSerial.print(extHeaterTemperatureValue, 2);
        xdataSerial.print("C air="); xdataSerial.print(mainTemperatureValue, 2);
        xdataSerial.println("C");
      }
    }
    delay(250);
  }

  if (!zeroPass) {
    humidityCheckError = true; calibrationError = true;
    if (xdataPortMode == 1) {
      xdataSerial.print("[err]: humidity CHECK FAILED - physical-zero correction ");
      xdataSerial.print(zeroCorrection, 3);
      xdataSerial.println(" %RH (limit +/-2)");
    }
  } else {
    humidityCheckError = false;
    if (xdataPortMode == 1) {
      xdataSerial.print("[info]: humidity CHECK passed - physical-zero correction ");
      xdataSerial.print(zeroCorrection, 3);
      xdataSerial.println(" %RH");
    }
  }
}
'''

s = s[:start] + new_func + s[end:]

p.write_text(s, encoding="utf-8")
print("Patched full factory RH compensation + 150C/3min RS41 physical-zero check")
