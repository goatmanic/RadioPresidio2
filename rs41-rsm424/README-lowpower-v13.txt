RSM424 X0551026 / sensor boom X0852387
AK5Z 433.900 MHz Horus Binary V3, 100 baud
RF period: 300 seconds, offset 0

v13 fixed-24-MHz / Voltage-Range-2 low power
=============================================

v12 hardware timing was accepted: consecutive RF packets arrived 300 real seconds
apart and SONDE time also advanced 300 seconds. v13 preserves that exact SysTick/WFI
wall-clock design and changes only the boot-time CPU/clock power policy.

Clock / regulator policy
========================
* 24 MHz physical HSE crystal feeds SYSCLK directly.
* PLL is OFF permanently.
* HCLK/PCLK1/PCLK2 are all 24 MHz.
* STM32L412 regulator uses Voltage Scaling Range 2 (about 1.0 V VCORE).
* Flash latency is 3 wait states, the RM0394 requirement for 24 MHz in Range 2.
* SystemCoreClock is explicitly set to 24,000,000 and SysTick to 1 kHz because the
  generic STM32duino build otherwise assumes HSE_VALUE=8 MHz.
* There is no runtime SYSCLK switching.

RPM411 / idle behavior
======================
HSI16 remains ON while the sonde is active because PA8/MCO uses HSI to clock the
RPM411. During the long inter-packet idle, the inherited v12 sleep logic turns HSI
off, pausing the RPM411 MCO, while the 24 MHz HSE and normal 1 ms SysTick remain
running. The Cortex-M4 repeatedly executes WFI between SysTick interrupts.

No custom timer reconstructs elapsed time. SysTick is never suspended and uwTick is
never manually advanced. The final 30 seconds before a Horus slot remain awake for
pressure/PTU refresh and transmission preparation. Initial GPS acquisition and the
24-hour M10 relock also remain fully awake.

Expected diagnostics
====================
[pwr]: v13 24MHz Range2 sleep armed: HSE direct, PLL off, SysTick/WFI, HSI/RPM411 paused
[pwr]: v13 24MHz-R2 systick sleep start req=...ms tick=...
[pwr]: v13 24MHz-R2 systick sleep end elapsed=...ms tick=...

Hardware acceptance criterion
=============================
1. RF packet spacing remains about 300 real seconds.
2. SONDE time advances about 300 seconds per packet.
3. Pressure remains sane after RPM411 clock restoration.
4. Horus decoding remains reliable at 100 baud.

Existing configuration retained
===============================
* initial valid M10 fix, then host-controlled PSMOO for 24 hours
* daily UART wake/relock, then PSMOO again
* last position retained between relocks
* 16 KiB NFWL v3 RAM log
* TX-only main-board green LED
* RPM411 pressure enabled
* factory X0852387 PTU calibration and humidity diagnostic retained

v13 supersedes v12 only for the lower-clock/lower-VCORE experiment. v12 remains the
known-good fallback if any 24 MHz / Range-2 peripheral issue appears on hardware.
