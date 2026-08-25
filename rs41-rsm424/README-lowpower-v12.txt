RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus Binary V3, 100 baud
RF period: 300 seconds, offset 0

v12 conservative low-power timing
=================================

Hardware tests of v9-v11 showed that custom low-power wall-clock reconstruction
could make logical time advance much faster than real time. v12 removes that
mechanism entirely.

v12 keeps the normal RSM424 clock tree unchanged:
  24 MHz HSE crystal -> PLL -> 80 MHz SYSCLK

During long idle between five-minute packets:
  * normal SysTick stays enabled at 1 ms;
  * millis() advances only from real SysTick interrupts;
  * no TIM2 wall clock is used;
  * HAL/Arduino uwTick is never manually modified;
  * Cortex-M4 repeatedly enters ordinary SLEEP with WFI;
  * each SysTick wake increments real time and the CPU immediately sleeps again;
  * unrelated interrupts cannot shorten the logical interval because the loop
    checks actual millis() elapsed time before leaving the sleep window;
  * HSI is disabled during the idle window, pausing the RPM411 MCO clock;
  * HSI/RPM411 clock is restored before pressure/PTU refresh;
  * the final 30 seconds before a Horus slot remain fully awake;
  * initial GPS acquisition and the 24-hour M10 relock stay fully awake.

Expected first-cycle diagnostics:
  [pwr]: v12 conservative sleep armed: SysTick wall clock, WFI CPU sleep, HSI/RPM411 paused
  [pwr]: v12 systick sleep start req=...ms tick=...
  [pwr]: v12 systick sleep end elapsed=...ms tick=...

The reported elapsed value should equal or exceed the requested value by only a
small number of milliseconds. There is no code path in v12 that adds a requested
sleep interval directly to uwTick.

Hardware acceptance criterion
=============================
Successive Horus packets must arrive about 300 real seconds apart, and SONDE time
must also advance by about 300 seconds per packet over multiple cycles.

Existing configuration retained
===============================
* initial valid M10 fix, then host-controlled PSMOO for 24 hours
* daily UART wake/relock, then PSMOO again
* last position retained between relocks
* 16 KiB NFWL v3 RAM log
* TX-only main-board green LED
* RPM411 pressure enabled
* factory X0852387 PTU calibration and humidity diagnostic retained

v12 supersedes v9, v10 and v11 for low-power timing tests.
