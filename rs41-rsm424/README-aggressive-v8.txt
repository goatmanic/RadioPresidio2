RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus V3 aggressive humidity diagnostic / low-power build

CANONICAL PACKAGE/BINARY BASENAME
  RSM424-X0551026-X0852387-AK5Z-433900-HorusV3-AGGRESSIVE-RAMLOG-v8

WHY v8 EXISTS
- v7 established the host-controlled M10 PSMOO policy: initial GPS fix, 24-hour GNSS sleep,
  deliberate UART wake/relock, then another 24-hour sleep interval.
- v8 keeps the v7 GPS, PTU, RPM411, 16 KiB RAM-log and TX-only LED behavior unchanged.
- The only intended flight-configuration change is the Horus V3 RF interval:
  60 seconds -> 300 seconds (5 minutes).

DAILY GPS LOW-POWER POLICY
- The M10 starts fully awake and stays awake until a fresh valid position/time fix is obtained.
- Normal NFW cyclic tracking is disabled.
- After a valid fix, the M10 enters host-controlled PSM ON/OFF (PSMOO) inactive mode.
- While inactive, gpsHandler returns before GPS management or GPS UART traffic.
- Exactly 86,400,000 ms after the last successful lock, the MCU sends one NAV-PVT poll to wake it.
- The M10 remains awake until a genuinely fresh valid fix is received, then returns inactive.
- Last valid position remains in telemetry while GNSS is inactive.
- Packet UTC advances from the last GPS-set UTC using MCU millis() and is corrected at daily relock.

NORMAL RADIO / FLIGHT CONFIGURATION
- Board: RSM424 / STM32L412RBT6
- Horus Binary V3: enabled, 100 baud
- RF frequency: 433.900 MHz
- Callsign: AK5Z
- RF TX interval: exactly 300 seconds (5 minutes), offset 0
- Scheduler: GPS-seeded, MCU free-run between daily GPS relocks
- Horus V3 TX power: setting 3, nominal about 8 dBm / 6 mW
- RPM411 pressure: enabled
- Sensor boom: enabled; one-minute sensor-boom power-saving interval remains unchanged
- APRS, Horus V2, RTTY, Morse, PIP, fox-hunt, private landing and RSM4x4 data recorder: disabled
- Continuous reference/humidity heating: disabled after startup validation

FACTORY CALIBRATION / HUMIDITY MODEL
- Vaisala factory calibration for X0852387 is loaded and verified against RSM424 X0551026.
- Normal RH uses calibU0/calibU1, the full 7x6 matrixU, and corHp/corHt pressure correction.
- Zero-humidity flash recalibration remains OFF.

AGGRESSIVE STARTUP GROUND CHECK
- Factory temperature consistency check enabled.
- Humidity reconditioning target approximately 150 C.
- Hot dwell begins at 145 C and lasts 180 seconds.
- Heat-up timeout 90 seconds; hard safety cutoff 165 C.
- Ten hot physical-zero samples are attempted after the dwell.
- The heater is then shut off before normal operation.

LOW-POWER LED POLICY
- Normal status/startup/calibration LEDs are disabled.
- Both main-board LEDs remain OFF while idle.
- Main-board green LED turns ON only for the actual radioEnableTx() -> radioDisableTx() keyed RF interval.
- The separate RPM411 daughterboard LED is controlled by RPM411 factory firmware and is unaffected.

ST-LINK RAM LOG
- 16,384-byte continuously circular SRAM XDATA log, NFWL ABI v3.
- Periodic $NFW telemetry remains on physical XDATA but is omitted from the RAM ring.
- dump-ram-log.sh also captures the complete 40 KiB SRAM.
- CI requires at least 15 KiB conservative free SRAM after static/global allocation.

EXPECTED GPS LOG LINES
After the first valid fix:
  [gps]: valid lock stored; M10 inactive for 24h

After 24 hours:
  [gps]: 24-hour relock wake - M10 acquiring

After the fresh relock:
  [gps]: valid lock stored; M10 inactive for 24h

FLASH
  ./flash-openocd.sh

READ THE RAM LOG
  ./dump-ram-log.sh
