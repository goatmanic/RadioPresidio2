RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus Binary V3, 100 baud
RF period: 300 seconds, offset 0

v14 true M10 sleep + 24 MHz Range-2 low-power
================================================

v13 hardware timing was stable for hours at five-minute intervals, but received
latitude/longitude/altitude continued changing every packet. That proves fresh
NAV-PVT solutions were still being published and the GNSS was not achieving the
intended one-fix-per-day inactive behavior.

Root causes fixed in v14
========================
1. RS41-NFW has scheduler-side GPS UART drains outside gpsHandler(). The previous
   daily sleep gate only returned early from gpsHandler(), so the quick pre-TX
   drain, the precision pre-TX wait drain, and the post-TX backlog drain bypassed
   the daily sleep policy. v14 gates all of those paths with dailyGpsUartAllowed().

2. The upstream M10 UBX-CFG-VALSET helper stores configuration in RAM only
   (layer 0x01). u-blox M10 clears RAM in the PSMOO off state, so RAM-only PM
   configuration is not sufficient. v14 writes the daily PSMOO configuration to
   both RAM and BBR (layer mask 0x03), with OPERATEMODE written last.

3. On the first gpsHandler() pass after MCU reset/flash, v14 generates a UART wake
   edge and writes OPERATEMODE=FULL to RAM+BBR. This prevents a PSMOO state retained
   in BBR from a prior run from trapping the next firmware boot in sleep.

4. After a valid fix is stored and PSMOO is armed, trailing UART bytes are discarded
   without publishing them. If the M10 is still streaming after a 3-second quiet
   period, v14 logs the unexpected RX and reasserts the RAM+BBR PSMOO profile once.

Expected RF behavior after first lock
=====================================
The coordinates and altitude in Horus packets should remain bit-for-bit frozen for
24 hours while SONDE UTC continues advancing from the GPS-seeded MCU SysTick clock.
RF packets should remain approximately 300 real seconds apart.

Expected XDATA/RAM-log messages include:
  [gps]: v14 boot PM normalize FULL RAM+BBR; new NAKs=0
  [gps]: v14 PSMOO stored RAM+BBR; new NAKs=0
  [gps]: valid lock stored; M10 PSMOO RAM+BBR inactive for 24h

A message like this indicates the M10 was still producing output and v14 reasserted
its sleep profile:
  [gps]: WARN unexpected RX while asleep bytes=...; reasserting PSMOO RAM+BBR

CPU / MCU low-power policy retained from v13
=============================================
* fixed 24 MHz physical HSE crystal directly as SYSCLK
* PLL permanently OFF
* Voltage Scaling Range 2
* 3 Flash wait states
* SystemCoreClock explicitly 24,000,000 Hz
* SysTick explicitly 1 kHz
* no runtime SYSCLK switching
* no custom TIM2 wall clock
* no manual uwTick adjustment
* repeated WFI idle using real SysTick/millis() elapsed time
* HSI16 / RPM411 MCO paused during the long inter-packet idle
* final 30 seconds before each Horus slot awake for pressure/PTU refresh

Other retained configuration
============================
* M10 initial fresh fix, then one relock every 24 hours
* last position retained in telemetry between relocks
* 16 KiB NFWL v3 circular SRAM log
* TX-only main-board green LED
* RPM411 pressure enabled
* X0852387 factory PTU calibration + pressure compensation
* aggressive startup humidity reconditioning diagnostic retained
* zero-humidity flash recalibration disabled

Hardware acceptance criteria
============================
1. successive RF arrivals: about 300 real seconds apart
2. SONDE UTC: advances about 300 seconds per packet
3. latitude/longitude/altitude: identical between packets after the initial lock
4. pressure/PTU: sane after each CPU/RPM411 idle interval
5. RAM log: PSMOO RAM+BBR new NAKs=0 and preferably no unexpected-RX warning
