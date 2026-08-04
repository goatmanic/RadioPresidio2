#!/usr/bin/env python3
"""Create a reproducible RS41-NFW configuration for one RSM424 sonde."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


NFW_BACKEND = Path("/src/rs41-nfw_souding-software/backend")
if str(NFW_BACKEND) not in sys.path:
    sys.path.insert(0, str(NFW_BACKEND))

from rs41_subframe import (  # type: ignore  # imported from the checked-out upstream repo
    RS41Subframe,
    download_subframe_data,
    extract_firmware_cal,
    validate_cal,
)


def replace_exact(text: str, pattern: str, replacement: str, label: str, *, count: int = 1) -> str:
    updated, substitutions = re.subn(pattern, replacement, text, count=count, flags=re.MULTILINE)
    if substitutions != count:
        raise RuntimeError(f"Could not patch {label}: expected {count} match(es), got {substitutions}")
    return updated


def set_scalar(text: str, name: str, value: object, *, count: int = 1) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, float):
        rendered = repr(value)
    else:
        rendered = str(value)

    type_pattern = (
        r"(?:(?:constexpr|const|static|volatile)\s+)*"
        r"(?:unsigned\s+long|unsigned\s+int|unsigned\s+char|long\s+long|"
        r"uint8_t|uint16_t|uint32_t|uint64_t|int8_t|int16_t|int32_t|int64_t|"
        r"int|long|float|double|bool|char|String|byte|word|size_t)"
    )
    pattern = rf"^({type_pattern}\s+{re.escape(name)}\s*=\s*)([^;]+)(\s*;)"
    return replace_exact(text, pattern, rf"\g<1>{rendered}\g<3>", name, count=count)


def set_string_macro(text: str, name: str, value: str) -> str:
    pattern = rf'^(#define\s+{re.escape(name)}\s+)"[^"]*"'
    return replace_exact(text, pattern, rf'\g<1>"{value}"', name)


def set_float_array(text: str, name: str, values: list[float]) -> str:
    rendered = ", ".join(repr(float(v)) for v in values)
    pattern = rf"^((?:(?:constexpr|const|static)\s+)*float\s+{re.escape(name)}\s*\[\s*\]\s*=\s*)\{{[^}}]*\}}(\s*;)"
    return replace_exact(text, pattern, rf"\g<1>{{{rendered}}}\g<2>", name)


def set_fixed_float_array(text: str, name: str, values: list[float]) -> str:
    rendered = ", ".join(repr(float(v)) for v in values)
    pattern = rf"^((?:(?:constexpr|const|static)\s+)*float\s+{re.escape(name)}\s*\[\s*\d+\s*\]\s*=\s*)\{{[^}}]*\}}(\s*;)"
    return replace_exact(text, pattern, rf"\g<1>{{{rendered}}}\g<2>", name)


def load_factory_cal(serial: str) -> tuple[dict[str, object], dict[str, object]]:
    subframe_bytes, telemetry = download_subframe_data(serial)
    if not subframe_bytes:
        raise RuntimeError(f"No complete RS41 calibration subframe found on SondeHub for {serial}")

    subframe = RS41Subframe(raw_bytes=subframe_bytes)
    valid, message = validate_cal(subframe.data)
    if not valid:
        raise RuntimeError(f"Factory calibration for {serial} failed validation: {message}")

    return extract_firmware_cal(subframe.data), {
        "requested_serial": serial,
        "subframe_serial": subframe.data.get("serial"),
        "mainboard": subframe.data.get("mainboard_version"),
        "mainboard_serial": subframe.data.get("mainboard_serial"),
        "variant": subframe.data.get("variant"),
        "telemetry_datetime": (telemetry or {}).get("datetime"),
    }


def build_config(config_path: Path, serial: str) -> None:
    config = config_path.read_text(encoding="utf-8")

    # Exact hardware target: RSM424 belongs to the RSM4x4/L412 family.
    config = replace_exact(
        config,
        r"^(//\s*)?#define\s+RSM4x4\b[^\n]*",
        "#define RSM4x4   // RSM424 / STM32L412RBT6",
        "RSM4x4 board selection",
    )
    config = replace_exact(
        config,
        r"^(//\s*)?#define\s+RSM4x2\b[^\n]*",
        "// #define RSM4x2  // disabled: this is not an STM32F100 board",
        "RSM4x2 board exclusion",
    )

    # One GPS-clock-aligned Horus Binary V3 packet per minute.
    config = set_scalar(config, "horusV3TimeSyncSeconds", 60)
    config = set_scalar(config, "horusV3TimeSyncOffsetSeconds", 0)
    config = set_scalar(config, "radioEnablePA", True)
    config = set_scalar(config, "pipEnable", False)
    config = set_scalar(config, "horusV3Enable", True)
    config = set_float_array(config, "horusV3FreqTable", [433.9])
    config = set_string_macro(config, "HORUS_V3_CALLSIGN", "AK5Z")
    config = set_scalar(config, "horusV3Bdr", 100)
    config = set_scalar(config, "horusV3RadioPower", 3)  # nominal 8 dBm / ~6 mW
    config = set_scalar(config, "horusV3ExtraSensorsEnable", True)

    # No secondary transmission modes or hidden/private frequencies.
    config = set_scalar(config, "horusEnable", False)
    config = set_scalar(config, "aprsEnable", False)
    config = set_scalar(config, "rttyEnable", False)
    config = set_scalar(config, "morseEnable", False)
    config = set_scalar(config, "foxHuntMode", False)
    config = set_scalar(config, "lowAltitudeFastTxThreshold", 0)
    config = set_scalar(config, "privateLandingModeEnable", False)
    config = set_scalar(config, "dataRecorderEnable", False, count=1)

    # Keep the serial console available for first-boot verification.
    config = set_scalar(config, "xdataPortMode", 1)
    config = set_scalar(config, "ledStatusEnable", True)

    # GPS remains in intelligent/cyclic tracking so every packet has current UTC and position.
    # This first production-safe build does not attempt the unproven once-daily full power-off scheme.
    config = set_scalar(config, "gpsOperationMode", 2)
    config = set_scalar(config, "m10ConstellationOptimization", True)
    config = set_scalar(config, "m10AggressiveOpt", False)
    config = set_scalar(config, "m10CyclicTracking", True)
    config = set_scalar(config, "m10CyclicPeriodSec", 60)
    config = set_scalar(config, "m10SuperS", True)
    config = set_scalar(config, "gpsUpdateRateHz", 1)
    config = set_scalar(config, "ubloxGpsAirborneMode", False)
    config = set_scalar(config, "gpsDynamicModel", 2)  # stationary
    config = set_scalar(config, "gpsSecondaryGnss", 2)  # GLONASS; compatible with cyclic tracking
    config = set_scalar(config, "gpsSbasEnable", False)  # required for M10 cyclic tracking
    config = set_scalar(config, "gpsQzssEnable", False)

    # Measure the original PTU boom once per transmitted frame and use the RPM411 pressure board.
    config = set_scalar(config, "sensorBoomEnable", True)
    config = set_scalar(config, "sensorBoomPowerSaving", True)
    config = set_scalar(config, "sensorBoomPowerSavingInterval", 60000)
    config = set_scalar(config, "sensorCalibrationMode", 2)
    config = set_scalar(config, "factoryTemperatureCheck", True)
    config = set_scalar(config, "factoryHumidityCheck", False)
    config = set_scalar(config, "humidityModuleEnable", True)
    config = set_scalar(config, "reconditioningEnabled", False)
    config = set_scalar(config, "zeroHumidityCalibration", False)
    config = set_scalar(config, "pressureMode", 1)

    # No continuous heaters for stationary long-runtime use.
    config = set_scalar(config, "referenceHeating", False)
    config = set_scalar(config, "humidityModuleHeating", False)
    config = set_scalar(config, "heatersPowerOptimisation", True)

    # Load this physical sonde's Vaisala factory PTU coefficients.
    calibration, metadata = load_factory_cal(serial)
    matrix = calibration.pop("factoryMatrixU")
    for key, value in calibration.items():
        config = set_scalar(config, key, value)
    config = set_fixed_float_array(config, "factoryMatrixU", matrix)  # type: ignore[arg-type]

    config_path.write_text(config, encoding="utf-8")

    summary = {
        "firmware_base": "Nevvman18/rs41-nfw@6b3c82936e0f3ebde21e92ee9aaa904087b5c035",
        "board": "RSM424 / RSM4x4 / STM32L412RBT6",
        "serial": serial,
        "frequency_mhz": 433.9,
        "mode": "Horus Binary V3 4FSK",
        "period_seconds": 60,
        "tx_power_setting": 3,
        "tx_power_nominal_dbm": 8,
        "payload_callsign": "AK5Z",
        "factory_calibration": metadata,
    }
    Path("/out/build-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    Path("/out/CONFIG.h").write_text(config, encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(f"Usage: {sys.argv[0]} CONFIG.h SONDE_SERIAL")
    build_config(Path(sys.argv[1]), sys.argv[2].strip().upper())
