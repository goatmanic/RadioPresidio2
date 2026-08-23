#!/usr/bin/env python3
"""Instrument RS41-NFW XDATA output with a RAM-resident circular log.

The original XDATA UART remains fully functional. Every transmitted byte is also
mirrored into a 4096-byte ring buffer with stable global symbols so OpenOCD can
locate and dump it through SWD/ST-Link.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"Usage: {sys.argv[0]} rs41-nfw_sonde-firmware.ino")

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

needle = "// XDATA (2,3)          rx    tx\nHardwareSerial xdataSerial(PB11, PB10);\n"
if needle not in s:
    raise SystemExit("Could not locate XDATA HardwareSerial declaration")

replacement = r'''// XDATA (2,3)          rx    tx
// Diagnostic build: mirror every XDATA TX byte into a RAM circular log so the
// latest real runtime log can be recovered non-invasively through ST-Link/SWD.
// Symbols are intentionally global and non-static for ELF/OpenOCD discovery.
constexpr uint32_t NFW_RAM_LOG_SIZE = 4096;
volatile uint32_t nfwRamLogMagic = 0x474F4C4E;  // little-endian ASCII "NLOG"
volatile uint32_t nfwRamLogWrite = 0;
volatile uint32_t nfwRamLogTotal = 0;
uint8_t nfwRamLog[NFW_RAM_LOG_SIZE] = {0};

static inline void nfwRamLogByte(uint8_t b) {
  uint32_t pos = nfwRamLogWrite;
  nfwRamLog[pos] = b;
  pos++;
  if (pos >= NFW_RAM_LOG_SIZE) pos = 0;
  nfwRamLogWrite = pos;
  nfwRamLogTotal++;
}

HardwareSerial xdataHardwareSerial(PB11, PB10);

class XDataRamLogSerial : public Print {
 public:
  explicit XDataRamLogSerial(HardwareSerial &serial) : hw(serial) {}

  void begin(unsigned long baud) { hw.begin(baud); }
  int available() { return hw.available(); }
  int read() { return hw.read(); }
  int peek() { return hw.peek(); }
  void flush() { hw.flush(); }

  size_t write(uint8_t b) override {
    nfwRamLogByte(b);
    return hw.write(b);
  }

  size_t write(const uint8_t *buffer, size_t size) {
    for (size_t i = 0; i < size; ++i) nfwRamLogByte(buffer[i]);
    return hw.write(buffer, size);
  }

  using Print::write;

 private:
  HardwareSerial &hw;
};

XDataRamLogSerial xdataSerial(xdataHardwareSerial);
'''

s = s.replace(needle, replacement, 1)
p.write_text(s, encoding="utf-8")
print("Injected 4096-byte XDATA RAM debug ring")
