#!/usr/bin/env python3
"""Instrument RS41-NFW XDATA output with a RAM-resident circular log.

The original XDATA UART remains fully functional. Human-readable XDATA log output
is mirrored into an 8192-byte ring buffer with stable global symbols so OpenOCD can
recover the actual recent runtime log through SWD/ST-Link.

The periodic bulk $NFW telemetry frame is deliberately *not* copied into the ring:
it can exceed 1 KB and is emitted repeatedly during calibration loops, which would
otherwise overwrite the startup diagnostics we are trying to preserve. A counter
records how many $NFW frames were omitted from the ring.

A magic/version pair lets the host-side dumper refuse to decode SRAM unless the
running firmware is this instrumented diagnostic build.
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
// Diagnostic build: mirror human-readable XDATA TX into a RAM circular log so
// the latest real runtime log can be recovered non-invasively through ST-Link/SWD.
// Periodic bulk $NFW telemetry frames are intentionally omitted from the ring;
// otherwise they would overwrite the useful startup/calibration log within seconds.
// Symbols are intentionally global and non-static for ELF/OpenOCD discovery.
constexpr uint32_t NFW_RAM_LOG_SIZE = 8192;
volatile uint32_t nfwRamLogMagic __attribute__((used)) = 0x4E46574C;    // ASCII "NFWL"
volatile uint32_t nfwRamLogVersion __attribute__((used)) = 2;
volatile uint32_t nfwRamLogWrite = 0;
volatile uint32_t nfwRamLogTotal = 0;
volatile uint32_t nfwRamLogNfwFramesOmitted = 0;
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
    const bool isNfwFrame = size >= 5 &&
      buffer[0] == '$' && buffer[1] == 'N' && buffer[2] == 'F' &&
      buffer[3] == 'W' && buffer[4] == ',';
    if (isNfwFrame) {
      nfwRamLogNfwFramesOmitted++;
    } else {
      for (size_t i = 0; i < size; ++i) nfwRamLogByte(buffer[i]);
    }
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
print("Injected 8192-byte XDATA human-log ring with magic/version ($NFW bulk frames omitted)")
