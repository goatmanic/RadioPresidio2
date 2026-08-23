RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus V3 aggressive humidity diagnostic build

CANONICAL PACKAGE/BINARY BASENAME
  RSM424-X0551026-X0852387-AK5Z-433900-HorusV3-AGGRESSIVE-RAMLOG-v2

PTU startup validation
- Vaisala factory calibration for X0852387 is loaded.
- Factory temperature consistency check is enabled.
- Factory humidity check is enabled.
- The factory humidity check itself performs about one minute of humidity-sensor
  reconditioning/heating near 138 C, then checks a hot/dry reading. DO NOT TOUCH
  THE SENSOR BOOM during this phase.
- The separate NFW-mode reconditioningEnabled setting is OFF because factory
  calibration mode does not execute that branch; the humidity check already does
  the required reconditioning.
- Zero-humidity recalibration is OFF. It would mutate calibration and should not
  be run without a genuine dry reference.

Normal runtime configuration
- Board: RSM424 / STM32L412RBT6
- Horus Binary V3: enabled, 433.900 MHz, callsign AK5Z
- TX interval: 60 seconds, GPS-clock aligned
- Horus V3 TX power: setting 3, nominal about 8 dBm / 6 mW
- GPS: intelligent mode 2, M10 cyclic tracking 60 s, GLONASS secondary,
  SBAS off so cyclic tracking is legal
- RPM411 pressure: enabled
- Sensor boom: enabled, one-minute power-saving interval
- APRS, Horus V2, RTTY, Morse, PIP, fox-hunt, private landing and the active
  RSM4x4 data-recorder setting: disabled
- Continuous reference/humidity heating: disabled after startup validation

ST-LINK RAM LOG
- Human-readable XDATA output is mirrored into an 8192-byte circular SRAM ring.
- The periodic bulk $NFW telemetry frames are deliberately NOT copied into the
  ring because they would overwrite calibration diagnostics within seconds.
- A counter records how many $NFW frames were omitted.
- The running firmware exposes magic "NFWL" and RAM-log version 2. The decoder
  refuses to interpret SRAM if those markers do not match, preventing an old or
  mismatched firmware image from being mistaken for this diagnostic build.
- RAMLOG-SYMBOLS.txt is generated from the exact ELF on every CI build.
- dump-ram-log.sh resolves addresses from that symbol file; it does not hard-code
  addresses from an older build.

FLASH
  ./flash-openocd.sh

The flash helper verifies SHA-256, RDP0, no WRP, and software boot from main flash
(nBOOT0=1, nSWBOOT0=0), then programs/verifies and confirms the PC is executing
inside 0x08000000-0x0801FFFF without issuing a second reset. This preserves the
first aggressive humidity-check run and its RAM log.

READ THE LIVE RAM LOG
  ./dump-ram-log.sh

Outputs include:
  X0551026-ramlog.txt
  X0551026-ramlog-chronological.bin
  X0551026-sram-full.bin
