"""Private finite-witness guard; no verifier acceptance or general ABI claim."""


class Contract:
    def __init__(self, initialized, mutable, *, ss, sp_low, sp_high):
        self.defined = set(initialized)
        self.mutable = tuple(mutable)
        self.ss, self.sp_low, self.sp_high = ss, sp_low, sp_high
        self.returns = {}

    @staticmethod
    def access(address, size):
        if (type(address) is not int or type(size) is not int or size <= 0
                or address < 0 or address + size > 0x100000):
            raise ValueError('invalid memory access')

    def read(self, address, size):
        self.access(address, size)
        if any(a not in self.defined for a in range(address, address + size)):
            raise ValueError(f'undefined read: {address:x}/{size}')

    def write(self, address, size, value):
        self.access(address, size)
        if not any(lo <= address and address + size <= hi for lo, hi in self.mutable):
            raise ValueError(f'forbidden write: {address:x}/{size}')
        for slot, state in self.returns.items():
            if address < slot + 2 and slot < address + size:
                if state['written']:
                    raise ValueError('protected return address')
                if (address, size, value) != (slot, 2, state['value']):
                    raise ValueError('incorrect return initialization')
                state['written'] = True
        self.defined.update(range(address, address + size))

    def reserve_return(self, address, value):
        self.access(address, 2)
        if any(address < slot + 2 and slot < address + 2 for slot in self.returns):
            raise ValueError('overlapping return address slots')
        if not any(lo <= address and address + 2 <= hi for lo, hi in self.mutable):
            raise ValueError('return slot outside mutable stack')
        self.returns[address] = dict(value=value, written=False)

    def release_return(self, address):
        state = self.returns.get(address)
        if state is None or not state['written']:
            raise ValueError('uninitialized return address slot')
        del self.returns[address]

    def stack(self, ss, sp):
        if ss != self.ss or not self.sp_low <= sp <= self.sp_high:
            raise ValueError('stack contract: changed SS or out-of-range SP')

    def returned(self, saved, current, *, call_sp, cleanup, expected_cleanup):
        if cleanup != expected_cleanup:
            raise ValueError('incorrect return cleanup')
        if current['sp'] != call_sp + expected_cleanup:
            raise ValueError('incorrect actual return SP')
        if any(current[name] != saved[name] for name in ('bp', 'si', 'di', 'ds', 'es')):
            raise ValueError('helper changed a preserved register')
