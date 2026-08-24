RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus V3 five-minute low-power build

CANONICAL PACKAGE/BINARY BASENAME
  RSM424-X0551026-X0852387-AK5Z-433900-HorusV3-LOWPOWER-v9

v9 CHANGES FROM v8
- Horus V3 remains 433.900 MHz / AK5Z / 100 baud / power setting 3.
- RF interval remains exactly 300 seconds (5 minutes), offset 0.
- Existing daily-M10 policy remains: initial valid fix -> PSMOO -> UART-silent 24 h -> wake/relock.
- Existing 16 KiB NFWL v3 RAM log and TX-only main-board LED remain.
- New MCU idle policy activates only after the first valid GPS fix has been stored and the M10 is asleep.

STM32L412 LOW-POWER IDLE
- The final 30 seconds before each Horus slot stay fully awake so the original scheduler can prepare and key the packet normally.
- Outside that guard window, the firmware switches SYSCLK from 80 MHz HSE/PLL to 2 MHz MSI.
- HSE, PLL and HSI are disabled during the long idle interval.
- HSI is normally used as the PA8/MCO clock source for the RPM411, so the pressure-board external clock is also quiet during this idle interval.
- TIM2 is temporarily reused as a 1 kHz one-shot while the PTU measurement path is idle.  It wakes the CPU accurately from low-power sleep without relying on the inaccurate LSI/RTC oscillator.
- STM32 low-power run mode and low-power SLEEP are entered with SysTick suspended.
- On TIM2 wake, the exact RSM424 24 MHz HSE -> PLL -> 80 MHz clock tree is restored, HSI/MCO returns, and HAL/Arduino millis is advanced by the requested sleep duration.
- If an unrelated interrupt wakes the CPU early, millis is NOT advanced by the full requested sleep duration.
- When the wake lands inside the 30-second pre-TX guard, RPM411 pressure and PTU are refreshed once so the packet uses fresh environmental data.
- Initial GPS acquisition and the once-per-day GPS relock never enter MCU low-power sleep.

WHY LOW-POWER SLEEP INSTEAD OF STOP2
- STOP2 needs an always-running low-speed wake source.
- This RSM424 build has no verified precision LSE timebase available to the firmware; using the internal LSI would make five-minute RF cadence drift significantly.
- 2 MHz MSI + TIM2 low-power sleep retains deterministic packet timing while shutting down the 80 MHz CPU clock tree for most of each five-minute interval.

UNCHANGED SENSOR / DIAGNOSTIC POLICY
- Factory X0852387 PTU calibration with full matrix and pressure correction.
- Aggressive startup humidity reconditioning / physical-zero diagnostic remains enabled.
- Zero-humidity flash recalibration remains disabled.
- RPM411 pressure remains enabled.
- Continuous reference/humidity heating remains disabled after startup.

EXPECTED LOG
After first valid GPS fix:
  [gps]: valid lock stored; M10 inactive for 24h
  [pwr]: v9 low-power idle armed: 2MHz LP-sleep between 5-minute TX slots

Periodic sparse sleep accounting:
  [pwr]: lp-sleep count=... total=...s

Daily GNSS wake:
  [gps]: 24-hour relock wake - M10 acquiring

FLASH
  ./flash-openocd.sh

READ RAM LOG
  ./dump-ram-log.sh
