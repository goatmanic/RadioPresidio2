RSM424 X0551026 / sensor boom X0852387
LOWPOWER v10 - stable-clock 5-minute Horus V3 build

Radio
- 433.900 MHz
- Horus Binary V3 4FSK, 100 baud
- callsign AK5Z
- TX power setting 3 (~+8 dBm nominal)
- one packet every 300 seconds, offset 0
- APRS, Horus V2 and auxiliary RF modes disabled

GPS
- M10 stays awake until the initial fresh valid position/time fix
- after a valid fix it enters host-controlled PSMOO
- no GPS UART traffic for 24 hours
- after 86,400,000 ms the MCU wakes the M10 with NAV-PVT
- M10 remains awake until a fresh valid fix, then returns to PSMOO
- last valid coordinates remain in telemetry between relocks

v10 MCU low-power policy
- replaces the experimental v9 80 MHz -> 2 MHz clock switching path
- SYSCLK remains on the proven 24 MHz HSE -> PLL -> 80 MHz configuration at all times
- corrected 80 MHz SysTick configuration is never rebuilt or re-scaled
- outside the final 30 seconds before each 5-minute slot, SysTick is suspended
- TIM2 is programmed from the known physical 80 MHz APB1 timer clock to a 1 kHz one-shot
- Cortex-M4 enters ordinary SLEEP and wakes through SEVONPEND/WFE when TIM2 expires
- HSI is disabled during the long sleep; HSI is the RPM411 MCO source, so the pressure daughterboard clock is paused while idle
- HSI is restored before pressure/PTU refresh
- elapsed Arduino/HAL time is advanced only after the TIM2 one-shot completes
- the TIM2 NVIC enable state and Cortex SCR state are restored after wake
- initial GPS acquisition and daily GPS relock stay fully awake

Why v10 exists
v9 was observed on hardware to advance the sonde time approximately 5.2 times faster than wall time after the low-power path armed. That made nominal 300-second RF slots occur roughly once per real minute. v10 removes the SYSCLK/MSI transition entirely and keeps the already-validated 80 MHz clock domain intact.

Diagnostics retained
- 16 KiB continuously circular NFWL v3 SRAM log
- main-board LEDs dark except green during actual keyed RF TX
- RPM411 pressure enabled
- matched X0852387 PTU factory calibration and pressure corrections
- aggressive startup humidity diagnostic retained
- zero-humidity flash recalibration disabled

First hardware validation after flashing
1. Obtain an initial GPS fix.
2. Confirm the v10 power message appears.
3. Compare RX arrival times to SONDE time for at least three packets.
4. Expected RF spacing: approximately 300 real seconds, not ~60 seconds.
5. Confirm pressure resumes after every wake.
6. Measure battery current during the long sleep interval if possible.
