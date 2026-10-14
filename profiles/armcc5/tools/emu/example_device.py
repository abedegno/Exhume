"""A sysemu plugin, as an example: a made-up register-file device on a bit-banged I2C bus.

A firmware that bit-bangs a bus toggles GPIO pins thousands of times a transfer; modelling the
pins is slow and fragile. The firmware's own bus functions are a better seam: stub them, and
the device sees whole bytes. Here the device answers at a 7-bit address with 32 byte registers
and SMBus-style transfers (a write is the address, a register and data; a read is the address,
a register, a repeated start, the address with the read bit, then data, the register advancing
each byte), with an optional PEC byte (CRC-8, polynomial 7) after `pec` data bytes of a read.

exhume.toml:

    [sysemu]
    plugins = ["tools/my_device.py"]          # a copy of this file, made the project's own
    [sysemu.example_device]
    address = 0x2A
    start = "i2c_start"                       # void (void)
    stop = "i2c_stop"                         # void (void)
    write = "i2c_write"                       # int (unsigned char byte): 1 for ACK
    read = "i2c_read"                         # unsigned char (int ack): ack 0 on the last byte
    pec = 0                                   # data bytes before a PEC byte (0: no PEC)

A test reaches the model through m.periph['example_device'] (regs, log)."""
from sysemu import Peripheral


def crc8(data, c=0):
    for b in data:
        c ^= b
        for _ in range(8): c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


class RegisterDevice(Peripheral):
    """No memory-mapped registers (size 0): only stubs reach it."""
    name, base, size = 'example_device', 0, 0

    def __init__(self, address=0x2A, pec=0):
        self.address, self.pec = address, pec
        self.regs = bytearray(32)
        self.log = []                     # ('w', reg, data) and ('r', reg, data)

    def reset(self):
        self.frame, self.reading, self.sent, self.reg = [], False, [], 0

    def start(self, *_):
        # a repeated start after the address and a register begins a read from that register
        if len(self.frame) == 2 and self.frame[0] == self.address << 1: self.reg = self.frame[1]
        else: self.end()
        self.frame, self.reading = [], False

    def stop(self, *_):
        self.end(); self.frame, self.reading = [], False

    def end(self):
        f = self.frame
        if len(f) >= 3 and f[0] == self.address << 1:
            reg, data = f[1], f[2:]
            for i, b in enumerate(data): self.regs[(reg + i) % len(self.regs)] = b
            self.log.append(('w', reg, bytes(data)))

    def write(self, b):
        b &= 0xFF
        if not self.frame and b == (self.address << 1 | 1):
            self.reading = True
            self.sent = []
            return 1
        if not self.frame and b >> 1 != self.address: return 0          # NACK: not this device
        self.frame.append(b)
        return 1

    def read(self, ack):
        """The next register, or after `pec` bytes the PEC over the whole read."""
        if self.pec and len(self.sent) == self.pec:
            v = crc8([self.address << 1, self.reg, self.address << 1 | 1] + self.sent)
            self.log.append(('r', self.reg, bytes(self.sent)))
        else:
            v = self.regs[(self.reg + len(self.sent)) % len(self.regs)]
            self.sent.append(v)
            if not self.pec and not ack: self.log.append(('r', self.reg, bytes(self.sent)))
        return v


def attach(m, options):
    o = options.get('example_device', {})
    dev = m.add(RegisterDevice(o.get('address', 0x2A), o.get('pec', 0)))
    m.stub(o.get('start', 'i2c_start'), lambda m, r: dev.start())
    m.stub(o.get('stop', 'i2c_stop'), lambda m, r: dev.stop())
    m.stub(o.get('write', 'i2c_write'), lambda m, r: dev.write(r[0]))
    m.stub(o.get('read', 'i2c_read'), lambda m, r: dev.read(r[0]))
    return dev
