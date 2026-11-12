// language: Arduino C++, file: hid_mouse.ino, board: Leonardo / Pro Micro (ATmega32u4)
// GameLab HID mouse bridge. Receives packets over USB serial from the vision
// host and re-emits them as HID mouse reports to the gaming PC. The gaming
// PC sees a standard USB mouse — anticheat has no hook into it.
//
// Serial protocol (all little-endian int16 for coordinates):
//   0xAA | i16 dx | i16 dy | 0x55          -> move
//   0xBB | 0x55                             -> left click
//   0xBC | 0x55                             -> right click
//   0xBD | 0x55                             -> middle click
//   0xCC | 0x55                             -> ping; replies with 0xCD 0x55

#include <Mouse.h>

static int16_t read_i16() {
  while (Serial.available() < 2) { /* spin briefly */ }
  uint8_t lo = Serial.read();
  uint8_t hi = Serial.read();
  return (int16_t)((hi << 8) | lo);
}

static uint8_t read_u8() {
  while (!Serial.available()) { /* spin */ }
  return (uint8_t)Serial.read();
}

void setup() {
  Serial.begin(115200);
  Mouse.begin();
}

static void split_move(int16_t dx, int16_t dy) {
  // Mouse.move() takes signed 8-bit per report, so split large deltas.
  while (dx != 0 || dy != 0) {
    int sx = constrain((int)dx, -120, 120);
    int sy = constrain((int)dy, -120, 120);
    Mouse.move(sx, sy, 0);
    dx -= sx;
    dy -= sy;
    delayMicroseconds(400);
  }
}

void loop() {
  if (Serial.available() <= 0) return;
  uint8_t head = Serial.read();

  switch (head) {
    case 0xAA: {
      int16_t dx = read_i16();
      int16_t dy = read_i16();
      if (read_u8() != 0x55) return;
      split_move(dx, dy);
      break;
    }
    case 0xBB: {
      if (read_u8() != 0x55) return;
      Mouse.click(MOUSE_LEFT);
      break;
    }
    case 0xBC: {
      if (read_u8() != 0x55) return;
      Mouse.click(MOUSE_RIGHT);
      break;
    }
    case 0xBD: {
      if (read_u8() != 0x55) return;
      Mouse.click(MOUSE_MIDDLE);
      break;
    }
    case 0xCC: {
      if (read_u8() != 0x55) return;
      Serial.write(0xCD);
      Serial.write(0x55);
      break;
    }
    default:
      // unknown header — drain one byte to resync
      break;
  }
}
