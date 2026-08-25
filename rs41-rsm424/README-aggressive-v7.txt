RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus V3 aggressive humidity diagnostic / low-power build

CANONICAL PACKAGE/BINARY BASENAME
  RSM424-X0551026-X0852387-AK5Z-433900-HorusV3-AGGRESSIVE-RAMLOG-v7

WHY v7 EXISTS
- v5 established the full factory humidity model and 150 C / 180 s physical-zero diagnostic.
- v6 doubled the ST-Link RAM-log ring to 16 KiB and made the LEDs TX-only.
- v7 keeps all v6 PTU, radio, RAM-log and LED behavior, and replaces the normal
  continuously-managed M10 GPS policy with a fixed-site low-power daily relock policy.

DAILY GPS LOW-POWER POLICY
- The M10 starts normally and remains fully awake until a fresh valid position/time fix is obtained.
- Normal 60-second M10 cyclic tracking is disabled in CONFIG; initial acquisition is continuous.
- After the first fresh fix, the firmware configures u-blox M10 PSM ON/OFF (PSMOO):
    * CFG-PM-POSUPDATEPERIOD = 0: no autonomous position-search wake
    * CFG-PM-ACQPERIOD = 0: no autonomous acquisition retry
    * CFG-PM-ONTIME = 0: return inactive immediately after a valid fix
    * CFG-PM-DONOTENTEROFF = 1: after an external wake, stay acquiring until fixed
    * CFG-PM-WAITTIMEFIX = 0: require a normal position fix, not just time
    * CFG-PM-UPDATEEPH = 0: no ephemeris-maintenance wake cycles
    * CFG-PM-OPERATEMODE = 1: PSMOO
- While the M10 is inactive, gpsHandler returns before GPSManagement and before every
  GPS UART read/write/poll. Normal one-minute scheduler activity therefore cannot wake it.
- Exactly 86,400,000 ms after the last successful lock, the MCU sends one NAV-PVT poll.
  M10 UART RX activity is the deliberate PSMOO wake source.
- The receiver then stays awake and the normal bounded GPS read loop runs until a genuinely
  fresh valid position/time solution with at least four satellites is received.
- A 2-second post-wake guard prevents a retained/stale solution from being accepted as the relock.
- The successful relock replaces the stored position/time base, restarts the 24-hour timer,
  and the M10 returns to PSMOO inactive state.
- Last valid latitude/longitude/altitude remain in telemetry while the GPS is asleep.
- Horus V3 UTC does NOT freeze while the GPS is asleep: it advances from the last GPS-set
  UTC using MCU millis(), and is corrected again at the next daily GPS relock.
- The 24-hour timer uses unsigned millis subtraction and is safe across the STM32 millis wrap.
- Current GNSS selection remains GPS + Galileo + GLONASS, with SBAS and BeiDou B1C disabled,
  so M10 power-save mode is compatible with the configured signal set.

FACTORY CALIBRATION / HUMIDITY MODEL
- Vaisala factory calibration for sensor boom X0852387 is loaded from its complete
  SondeHub calibration subframe and is verified to belong to RSM424 X0551026.
- The normal factory calculation uses calibU0/calibU1 and the complete 7x6 matrixU.
- corHp[3] and corHt[12] from the same factory subframe provide the pressure/temperature
  correction to Cp before matrixU evaluation.
- Normal reported/telemetry RH continues to use the full factory model. No arbitrary
  gain or hand-tuned RH offset is applied to flight telemetry.
- Zero-humidity flash recalibration remains OFF.

AGGRESSIVE STARTUP GROUND CHECK
- Factory temperature consistency check remains enabled.
- Humidity reconditioning target: approximately 150 C.
- The hot dwell begins once the humidity-element heater reaches 145 C and lasts 180 seconds.
- Heat-up timeout: 90 seconds. Safety cutoff: 165 C.
- After the dwell, 10 physical-zero samples are taken while the element remains hot.
- Each sample records sensor temperature, sensor-local factory RH, expected hot-element RH,
  reported RH, factory-interpolated Humicap capacitance, corrected Cp, humidity/0-pF/47-pF
  frequencies, and RPM411 pressure.
- PASS requires the ten-sample average physical-zero correction to be within +/-2 %RH.
- The heater is then shut off and the boom cools before normal operation.

NORMAL RADIO / FLIGHT CONFIGURATION
- Board: RSM424 / STM32L412RBT6
- Horus Binary V3: enabled
- RF frequency: 433.900 MHz
- Callsign: AK5Z
- RF TX interval: exactly 60 seconds; scheduler is GPS-seeded and then free-runs between relocks
- Horus V3 TX power: setting 3, nominal about 8 dBm / 6 mW
- RPM411 pressure: enabled
- Sensor boom: enabled, one-minute power-saving interval after startup validation
- APRS, Horus V2, RTTY, Morse, PIP, fox-hunt, private landing and RSM4x4 data recorder: disabled
- Continuous reference/humidity heating: disabled after startup validation

LOW-POWER LED POLICY
- Normal status/startup/calibration LEDs are disabled.
- Both LEDs remain OFF while idle.
- The green LED turns ON only for the actual interval between radioEnableTx() and
  radioDisableTx(), so one visible flash corresponds to one keyed RF burst.

ST-LINK RAM LOG
- Human-readable XDATA output is mirrored into a 16,384-byte circular SRAM ring.
- The ring remains circular continuously; it is NOT frozen.
- Periodic $NFW telemetry remains on physical XDATA but is omitted from the RAM ring.
- Runtime identity is "NFWL" RAM-log ABI v3.
- RAMLOG-SYMBOLS.txt is generated from the exact final ELF and dump-ram-log.sh resolves
  the actual address/ring size from it.
- dump-ram-log.sh also captures the complete 40 KiB SRAM.
- CI requires at least 15 KiB of conservative free SRAM after static/global allocation.

EXPECTED DAILY GPS LOG LINES
After the first valid fix:
  [gps]: valid lock stored; M10 inactive for 24h

After 24 hours:
  [gps]: 24-hour relock wake - M10 acquiring

After the fresh relock:
  [gps]: valid lock stored; M10 inactive for 24h

FLASH
  ./flash-openocd.sh

READ THE LIVE RAM LOG
  ./dump-ram-log.sh

Useful outputs:
  X0551026-ramlog.txt
  X0551026-ramlog-chronological.bin
  X0551026-sram-full.bin
