"""Validated little-endian LE images, including DOS-bound MZ containers.

Format reference: Open Watcom bld/watcom/h/exeflat.h. LE page numbers
are big-endian 24-bit values; data-page offsets are relative to the MZ
container to which the LE was bound, not necessarily the outer file.
Iterated/range pages and LX are deliberately unsupported.
"""
from dataclasses import dataclass, field
import re
import struct


class LEError(ValueError):
    pass


def _span(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise LEError(f'{label} outside file: {offset:#x}+{size:#x}')
    return data[offset:offset+size]


@dataclass(frozen=True)
class Object:
    size: int
    base: int
    flags: int
    first_page: int
    page_count: int


@dataclass(frozen=True)
class Page:
    number: int
    flags: int
    file_offset: int | None
    stored_size: int


@dataclass(frozen=True)
class Fixup:
    page: int  # zero-based logical page index
    source_object: int
    source_offset: int
    source_type: int
    width: int
    target_object: int
    target_offset: int
    target_address: int
    alias: bool


@dataclass
class LEImage:
    data: bytes = field(repr=False)
    header_offset: int
    origin: int
    page_size: int
    objects: tuple[Object, ...]
    pages: tuple[Page, ...]
    entry_object: int
    entry_offset: int
    stack_object: int
    stack_offset: int
    fixup_page_offsets: tuple[int, ...]
    fixup_record_start: int

    @classmethod
    def parse(cls, data: bytes) -> 'LEImage':
        candidates = set()
        if data[:4] == b'LE\0\0':
            candidates.add((0, 0))
        # Embedded DOS stubs use a relative new-header pointer. Standard
        # unbound executables (MZ at zero) are the same rule's special case.
        for match in re.finditer(b'MZ', data):
            origin = match.start()
            if origin + 0x40 <= len(data):
                pointer = struct.unpack_from('<I', data, origin+0x3c)[0]
                header = origin + pointer
                if pointer >= 0x40 and data[header:header+4] == b'LE\0\0':
                    candidates.add((header, origin))
        valid, errors = [], []
        for header, origin in sorted(candidates):
            try:
                valid.append(cls._read(data, header, origin))
            except LEError as error:
                errors.append(f'{header:#x}: {error}')
        if len(valid) > 1:
            raise LEError('ambiguous LE images: ' + ', '.join(hex(x.header_offset) for x in valid))
        if not valid:
            raise LEError('no valid bound LE image; ' + '; '.join(errors))
        return valid[0]

    @classmethod
    def _read(cls, data: bytes, h: int, origin: int) -> 'LEImage':
        if h!=origin:
            _span(data,origin,0x40,'MZ header')
            word=lambda off:struct.unpack_from('<H',data,origin+off)[0]
            remainder,blocks=word(2),word(4)
            stub_size=(blocks-1)*512+(remainder or 512)
            header_size=word(8)*16
            if not blocks or remainder>=512 or not 0x40<=header_size<=stub_size<=h-origin:
                raise LEError('invalid MZ header/load image dimensions')
            _span(data,origin,stub_size,'MZ load image')
            relocs,table=word(6),word(0x18)
            if relocs and (table<0x1c or table+relocs*4>header_size):
                raise LEError('MZ relocations outside header')
            if header_size+word(0x16)*16+word(0x14)>=stub_size:
                raise LEError('MZ entry point outside load image')
        _span(data, h, 0xc4, 'LE header')
        u32 = lambda off: struct.unpack_from('<I', data, h+off)[0]
        if data[h:h+4] != b'LE\0\0' or u32(4) != 0:
            raise LEError('unsupported LE encoding/level')
        count, size, last = u32(20), u32(40), u32(44)
        if not count or not size or size & (size-1) or size > 65536 or last > size:
            raise LEError('invalid page dimensions')
        obj_offset, obj_count, map_offset = u32(64), u32(68), u32(72)
        if not obj_count or obj_offset < 0xc4:
            raise LEError('invalid object table')
        _span(data, h+obj_offset, obj_count*24, 'object table')
        _span(data, h+map_offset, count*4, 'page map')
        if map_offset < obj_offset+obj_count*24:
            raise LEError('page map overlaps object table')
        objects, owners = [], set()
        for i in range(obj_count):
            virtual, base, flags, first, number, _ = struct.unpack_from('<6I', data, h+obj_offset+24*i)
            if not virtual or base+virtual > 0x100000000:
                raise LEError('invalid object virtual range')
            if number and (not first or first-1+number > count or number > (virtual+size-1)//size):
                raise LEError('invalid object page range')
            indices = set(range(first-1, first-1+number)) if number else set()
            if indices & owners:
                raise LEError('overlapping object page ownership')
            owners.update(indices)
            objects.append(Object(virtual, base, flags, first, number))
        if len(owners) != count:
            raise LEError('unowned logical pages')
        pages = []
        page_start = origin + u32(128)
        for i in range(count):
            raw = data[h+map_offset+4*i:h+map_offset+4*i+4]
            physical, flags = int.from_bytes(raw[:3], 'big'), raw[3]
            if flags == 0:
                if not 1 <= physical <= count:
                    raise LEError('invalid physical page number')
                stored = last if physical == count and last else size
                offset = page_start+(physical-1)*size
                _span(data, offset, stored, 'data page')
            elif flags == 3:
                offset, stored = None, 0
            else:
                raise LEError(f'unsupported page encoding {flags}')
            pages.append(Page(physical, flags, offset, stored))
        fp, fr = u32(104), u32(108)
        _span(data, h+fp, (count+1)*4, 'fixup page table')
        if fp < map_offset+count*4 or fr < fp+(count+1)*4:
            raise LEError('overlapping fixup tables')
        fix_offsets = struct.unpack_from(f'<{count+1}I', data, h+fp)
        if fix_offsets[0] != 0 or any(a>b for a,b in zip(fix_offsets,fix_offsets[1:])):
            raise LEError('invalid fixup page offsets')
        _span(data, h+fr, fix_offsets[-1], 'fixup records')
        if fr+fix_offsets[-1] > fp+u32(48):
            raise LEError('fixup records exceed declared fixup section')
        if page_start < h+fr+fix_offsets[-1]:
            raise LEError('data pages overlap loader/fixup tables')
        entry, eip, stack, esp = u32(24), u32(28), u32(32), u32(36)
        if not 1 <= entry <= obj_count or eip >= objects[entry-1].size:
            raise LEError('invalid entry point')
        if stack and (stack > obj_count or esp > objects[stack-1].size):
            raise LEError('invalid stack')
        return cls(data, h, origin, size, tuple(objects), tuple(pages), entry,
                   eip, stack, esp, fix_offsets, h+fr)

    def _object(self, number: int) -> Object:
        if not 1 <= number <= len(self.objects):
            raise LEError(f'object number out of range: {number}')
        return self.objects[number-1]

    def file_offset(self, number: int, offset: int) -> int | None:
        obj = self._object(number)
        if not 0 <= offset < obj.size:
            raise LEError('object offset out of range')
        page_index, within = divmod(offset, self.page_size)
        if page_index >= obj.page_count:
            return None
        page = self.pages[obj.first_page-1+page_index]
        if page.file_offset is None or within >= page.stored_size:
            return None
        return page.file_offset+within

    def object_bytes(self, number: int) -> bytes:
        obj = self._object(number)
        if obj.size > 64*1024*1024:
            raise LEError('object exceeds materialization limit of 64 MiB')
        result = bytearray(obj.size)
        for i in range(obj.page_count):
            page = self.pages[obj.first_page-1+i]
            if page.file_offset is not None:
                n = min(page.stored_size, obj.size-i*self.page_size)
                result[i*self.page_size:i*self.page_size+n] = self.data[page.file_offset:page.file_offset+n]
        return bytes(result)

    def read_fixups(self) -> tuple[Fixup, ...]:
        """Read internal, non-additive LE fixups; reject other encodings.

        Preserve duplicate cross-page records, signed source locations,
        selector-vs-offset source types and alias flags for later verification.
        This method describes fixups; it does not apply selector relocations.
        """
        result = []
        widths = {0:1, 2:2, 3:4, 5:2, 6:6, 7:4, 8:4}
        for page in range(len(self.pages)):
            start, end = self.fixup_page_offsets[page:page+2]
            raw = self.data[self.fixup_record_start+start:self.fixup_record_start+end]
            pos = 0

            def take(n: int, signed: bool = False) -> int:
                nonlocal pos
                if pos+n > len(raw):
                    raise LEError(f'truncated fixup on page {page+1}')
                value = int.from_bytes(raw[pos:pos+n], 'little', signed=signed)
                pos += n
                return value

            owner_num, owner = next((n,o) for n,o in enumerate(self.objects,1)
                                    if o.first_page <= page+1 < o.first_page+o.page_count)
            while pos < len(raw):
                source_flags, target_flags = take(1), take(1)
                source_type = source_flags & 0xf
                if source_type not in widths or source_flags & 0xc0 or target_flags & ~0x50:
                    raise LEError(f'unsupported fixup flags {source_flags:#x}/{target_flags:#x}')
                if source_flags & 0x20:
                    count, sources = take(1), None
                    if not count:
                        raise LEError('empty fixup source list')
                else:
                    count, sources = 1, [take(2, signed=True)]
                target_obj = take(2 if target_flags & 0x40 else 1)
                target_off = 0 if source_type == 2 else take(4 if target_flags & 0x10 else 2)
                target = self._object(target_obj)
                if target_off > target.size:
                    raise LEError('fixup target outside object')
                if sources is None:
                    sources = [take(2, signed=True) for _ in range(count)]
                for source in sources:
                    offset = (page+1-owner.first_page)*self.page_size+source
                    if offset < 0 or offset+widths[source_type] > owner.size:
                        raise LEError('fixup source outside object')
                    result.append(Fixup(page, owner_num, offset, source_type,
                                        widths[source_type], target_obj, target_off,
                                        target.base+target_off, bool(source_flags & 0x10)))
        return tuple(result)
