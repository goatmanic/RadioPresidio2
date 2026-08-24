RSM424 X0551026 / PTU X0852387 - LOWPOWER v11
================================================

RF
--
Horus Binary V3, AK5Z, 433.900 MHz, 100 baud, power setting 3.
One scheduled packet every 300 seconds (5 minutes), offset 0.

GPS
---
Initial fresh valid M10 fix, then host-controlled PSMOO. The GPS receives no
UART traffic for 24 hours, wakes for a fresh fix, and returns to PSMOO.

v11 low-power timing fix
------------------------
v10 retained the stable 80 MHz HSE/PLL system clock but attempted to divide
TIM2 from 80 MHz to 1 kHz by writing PSC=79999. TIM2's prescaler field is only
16 bits, so the hardware retained 79999 & 0xffff = 14463. The timer therefore
ran at 80,000,000 / 14,464 = 5,530.973 Hz instead of 1,000 Hz. A requested
200-second sleep lasted about 36.16 seconds, exactly matching the observed RF
spacing while the firmware still advanced its logical clock by 200 seconds.

v11 uses an exact 2 kHz TIM2 base:
  TIM2 input clock = 80,000,000 Hz
  PSC              = 39,999 (valid 16-bit value)
  timer rate       = 2,000 Hz
  ticks/ms         = 2
  ARR              = sleep_ms * 2 - 1

Compile-time static_asserts reject any future prescaler value above 0xffff and
require the expected PSC=39999. The firmware also reads back PSC and ARR before
sleep and aborts the low-power interval rather than manufacturing elapsed time
if the peripheral did not retain the expected values.

The 24 MHz HSE -> PLL -> 80 MHz SYSCLK remains untouched during sleep. SysTick is
suspended only for the physical TIM2 interval. HSI, which feeds the RPM411 MCO,
is paused during sleep and restored after SysTick resumes.

Expected first-cycle diagnostic:
  [pwr]: v11 fixed-width timer sleep armed: 80MHz SYSCLK, TIM2 2kHz, HSI/RPM411 paused
  [pwr]: v11 TIM2 psc=39999 ticks=<2*requested_ms> req=<requested_ms>ms

Hardware acceptance criterion
-----------------------------
After GPS lock, consecutive scheduled Horus packets must arrive approximately
300 real seconds apart, and SONDE UTC must also advance by approximately 300
seconds. Validate multiple cycles before unattended deployment.

v11 supersedes v9 and v10 for timed low-power operation.
