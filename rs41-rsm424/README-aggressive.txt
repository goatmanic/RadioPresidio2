RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus V3 aggressive humidity diagnostic build

CANONICAL PACKAGE/BINARY BASENAME
  RSM424-X0551026-X0852387-AK5Z-433900-HorusV3-AGGRESSIVE-RAMLOG-v6

WHY v6 EXISTS
- v3 proved the heater works but exposed a bad dry-check domain: the normal
  ambient-equivalent RH value clamps at 100 % while the humidity element is hot.
- v4 exposed the factory matrix's sensor-local value, but the short ~138 C / 30 s
  cycle still produced about 23 %RH at the hot element. That cycle is not the
  Vaisala RS41 ground-check preparation profile and therefore is not accepted as
  evidence that this physical boom is bad.
- v5 performs a substantially more faithful RS41 ground check and exposes the raw
  quantities needed to distinguish a real sensor offset from bad firmware math.
- v6 keeps the v5 PTU/radio behavior, doubles the ST-Link RAM-log ring from 8 KiB
  to 16 KiB, and uses a low-power TX-only LED policy.

FACTORY CALIBRATION / HUMIDITY MODEL
- Vaisala factory calibration for sensor boom X0852387 is loaded from its complete
  SondeHub calibration subframe and is verified to belong to RSM424 X0551026.
- The normal factory calculation uses calibU0/calibU1 and the complete 7x6 matrixU.
- v5/v6 additionally load the pressure/temperature humidity-correction coefficients
  corHp[3] and corHt[12] from the same factory subframe. The pinned upstream NFW
  factory-RH implementation omitted these coefficients.
- The added correction follows the rs1729/DF9DQ RS41 calculation: pressure-dependent
  correction is applied to Cp before matrixU evaluation.
- Normal reported/telemetry RH continues to use the full factory model. No arbitrary
  gain or hand-tuned RH offset is applied to flight telemetry.
- Zero-humidity flash recalibration remains OFF. The diagnostic measures the required
  correction but does not silently change factory calibration or write a made-up zero.

AGGRESSIVE STARTUP GROUND CHECK
- Factory temperature consistency check remains enabled.
- Humidity reconditioning target: approximately 150 C.
- The 3-minute dwell begins once the humidity-element heater reaches 145 C.
- Hot dwell: 180 seconds.
- Heat-up timeout: 90 seconds.
- Safety cutoff: 165 C.
- After the hot dwell, the firmware takes 10 physical-zero samples while keeping the element hot.
- For each sample it records:
    * heater/sensor temperature
    * sensor-local factory RH
    * physically expected hot-element RH derived from preheat ambient T/RH
    * normal ambient-equivalent reported RH
    * factory-interpolated Humicap capacitance
    * corrected Cp
    * humidity frequency, 0-pF reference frequency and 47-pF reference frequency
    * RPM411 pressure
- The check compares measured sensor-local RH against the physically expected local
  RH, rather than assuming mathematically exact 0.000 %RH. PASS requires the
  ten-sample average correction to be within +/-2 %RH.
- After the hot test the heater is shut off and the firmware runs a cooling phase
  before entering normal operation.
- DO NOT TOUCH THE SENSOR BOOM during the reconditioning/check cycle.

NORMAL RADIO / FLIGHT CONFIGURATION
- Board: RSM424 / STM32L412RBT6
- Horus Binary V3: enabled
- RF frequency: 433.900 MHz
- Callsign: AK5Z
- RF TX interval: exactly 60 seconds, GPS-clock aligned, offset 0
- Horus V3 TX power: setting 3, nominal about 8 dBm / 6 mW
- GPS: operation mode 2, M10 cyclic tracking 60 s, GLONASS secondary, SBAS off
- RPM411 pressure: enabled
- Sensor boom: enabled, one-minute power-saving interval after startup validation
- APRS, Horus V2, RTTY, Morse, PIP, fox-hunt, private landing and active RSM4x4
  data recorder: disabled
- Continuous reference/humidity heating: disabled after startup validation

LOW-POWER LED POLICY
- Normal status LEDs are disabled, including startup/calibration/status blinking.
- Both LEDs remain electrically OFF while idle.
- The green LED turns ON only inside radioEnableTx(), after the transmitter is keyed,
  and turns OFF inside radioDisableTx(). This makes it an actual-RF TX indicator,
  rather than a scheduler/status indicator.
- Because the LED hook is at the Si4032 TX gate, the policy remains correct if another
  RF mode is later enabled: any real keyed transmission gets the green indication.

ST-LINK RAM LOG
- Human-readable XDATA output is mirrored into a 16384-byte circular SRAM ring.
- The ring remains circular continuously; it is NOT frozen after the startup test.
  Once more than 16 KiB of human-readable output has been written, the oldest bytes
  are overwritten while logging continues.
- Periodic $NFW telemetry lines are forwarded normally to the physical XDATA UART
  but deliberately NOT copied into the RAM ring.
- Filtering is line-oriented at write(uint8_t), recognizes both $NFW| and legacy
  $NFW, prefixes, and works regardless of the Arduino Print overload used.
- A counter records how many $NFW frames were omitted.
- Runtime identity is "NFWL" RAM-log ABI v3. ABI v3 denotes the 16 KiB ring.
- RAMLOG-SYMBOLS.txt is generated from the exact final ELF; dump-ram-log.sh resolves
  addresses and ring size from that file rather than hard-coding an older layout.
- dump-ram-log.sh also captures the complete 40 KiB SRAM.
- CI rejects the build unless conservative ELF accounting leaves at least 15 KiB
  free after static/global allocation for stack, locals and runtime headroom.

FLASH
  ./flash-openocd.sh

The flash helper derives the binary name from SHA256SUMS, verifies SHA-256, RDP0,
no WRP, and software boot from main flash (nBOOT0=1, nSWBOOT0=0), then programs,
verifies and confirms PC execution inside 0x08000000-0x0801FFFF without issuing a
second reset. This preserves the first startup ground-check run in the RAM log.

READ THE LIVE RAM LOG
  ./dump-ram-log.sh

Useful outputs:
  X0551026-ramlog.txt
  X0551026-ramlog-chronological.bin
  X0551026-sram-full.bin

The decisive lines are:
  [info]: humidity CHECK preheat ...
  [info]: humidity CHECK 3-minute hot dwell started
  [info]: humidity CHECK reconditioning ... dwell=N/180s
  [info]: humidity CHECK zero sample 1/10 ...
  ...
  [info]: humidity CHECK zero result measured=... expected=... correction=...
  [info]: humidity CHECK passed - physical-zero correction ...
or
  [err]: humidity CHECK FAILED - physical-zero correction ...
