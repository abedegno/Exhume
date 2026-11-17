"""Read measured standard OMF32 and Phar Lap Easy OMF-386 subsets.

Definitions: Open Watcom bld/watcom/h/pcobj.h. Unknown record/fixup
encodings fail explicitly; they must never disappear behind a byte mask.
Easy OMF and threads: IBM OMF/LX rev10 sections2.4.15 and2.4.23
(https://openwatcom.org/ftp/devel/docs/lxomf.pdf).
"""
from dataclasses import dataclass, field
import struct


class OMFError(ValueError):
    pass


class Cursor:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def bytes(self, n: int) -> bytes:
        if n < 0 or self.pos+n > len(self.data):
            raise OMFError('truncated record payload')
        result = self.data[self.pos:self.pos+n]
        self.pos += n
        return result

    def uint(self, width: int) -> int:
        return int.from_bytes(self.bytes(width),'little')

    def index(self) -> int:
        first = self.uint(1)
        return ((first & 0x7f)<<8)|self.uint(1) if first & 0x80 else first

    def name(self) -> str:
        return self.bytes(self.uint(1)).decode('latin1')

    def left(self) -> int:
        return len(self.data)-self.pos


@dataclass
class Segment:
    name: str
    class_name: str
    size: int
    attributes: int
    data: bytearray = field(repr=False)
    access: int | None = None


@dataclass(frozen=True)
class Fixup:
    segment: int
    offset: int
    width: int
    location: int
    relative: bool
    frame_method: int
    frame: int | None
    target_method: int
    target: int
    displacement: int


@dataclass
class Module:
    name: str = ''
    segments: list[Segment] = field(default_factory=list)
    publics: dict[str, tuple[int,int]] = field(default_factory=dict)
    externals: list[str] = field(default_factory=list)
    # Local PUBDEF/EXTDEF names share spelling syntax, not linker visibility.
    local_publics: set[str] = field(default_factory=set)
    local_externals: set[int] = field(default_factory=set)
    groups: list[tuple[str,tuple[int,...]]] = field(default_factory=list)
    fixups: list[Fixup] = field(default_factory=list)
    default_libraries: list[str] = field(default_factory=list)
    far_call_optimization_segments: list[int] = field(default_factory=list)
    comments: list[tuple[int,bytes]] = field(default_factory=list,repr=False)
    dosseg_directives: list[int] = field(default_factory=list)
    code_data_ranges: list[tuple[int,int,int]] = field(default_factory=list)
    line_numbers: list[tuple[int,int,int,int]] = field(default_factory=list)

    # Encoding provenance is separate from the stable canonical Fixup schema.
    format: str = 'standard-omf'
    format_source: dict | None = None
    fixup_encodings: list[dict] = field(default_factory=list,repr=False)
    thread_definitions: list[dict] = field(default_factory=list,repr=False)
    # Complete Easy records retain metadata absent from the canonical AST.
    source_records: list[tuple[int,int,bytes]] = field(default_factory=list,repr=False)

    @classmethod
    def parse(cls, data: bytes) -> 'Module':
        return cls._parse(data)

    @classmethod
    def _parse(cls, data: bytes, startup=None) -> 'Module':
        module, names, pos, last = cls(), [''], 0, None
        pass_two = False
        ordinal, headers, first_header = 0, 0, False
        frame_threads, target_threads = [None]*4, [None]*4

        def indexed_datum(method, cur):
            value = cur.index()
            if method == 0:
                module._segment(value)
            elif method == 1 and not 1 <= value <= len(module.groups):
                raise OMFError('invalid fixup group index')
            elif method == 2 and not 1 <= value <= len(module.externals):
                raise OMFError('invalid fixup external index')
            return value
        while pos < len(data):
            if pos+3 > len(data):
                raise OMFError('truncated record header')
            record_start, record_ordinal = pos, ordinal
            ordinal += 1
            kind, length = data[pos], struct.unpack_from('<H',data,pos+1)[0]
            end = pos+3+length
            if not length or end > len(data):
                raise OMFError('truncated record')
            # TIS OMF 1.1, Record Format: zero also denotes an omitted
            # checksum. Large Watcom-generated metadata uses that form.
            if data[end-1] and sum(data[pos:end]) & 255:
                raise OMFError(f'bad checksum at {pos:#x}')
            cur = Cursor(data[pos+3:end-1])
            if startup is not None:
                startup['records'].append({'ordinal':record_ordinal,
                    'offset':record_start,'kind':kind,
                    'raw_record':data[record_start:end]})
            pos = end
            easy = module.format == 'easy-omf386'
            wide = 4 if easy or kind & 1 else 2
            if easy and kind not in (0x80,0x88,0x96,0x98,0x9a,0x8c,0xa0,0x9c,0x90,0x8a):
                raise OMFError(f'unsupported Easy OMF record {kind:#x}')
            # Watcom libraries may append a timestamp in linker pass 2.
            # Only that exact fixed-width comment is allowed here; definitions
            # and other comments must still precede the pass separator.
            timestamp=(kind==0x88 and len(cur.data)==7 and cur.data[1:3]==b'\xfeT')
            startup_range=(startup is not None and kind==0x88 and
                           cur.data[:3]==b'\x80\xfd\x73')
            if pass_two and not timestamp and not startup_range and kind not in (0xa0,0xa1,0xa2,0xa3,0x9c,0x9d,0x94,0x95,0x8a,0x8b):
                raise OMFError('unsupported record after linker pass boundary')
            try:
                if kind == 0x80:
                    if module.name or (easy and headers):
                        raise OMFError('multiple module headers')
                    headers += 1
                    first_header = record_ordinal == 0
                    module.name = cur.name()
                elif kind == 0x88:
                    flags,klass=cur.uint(1),cur.uint(1)
                    payload=cur.bytes(cur.left())
                    padding=not easy and flags==4 and klass==0 and not any(payload)
                    if flags & 0x3f and not padding:
                        raise OMFError('unsupported comment flags')
                    c=Cursor(payload)
                    if klass==0xaa and payload==b'80386':
                        if startup is not None:
                            raise OMFError('unsupported Easy OMF startup profile')
                        if easy or record_ordinal!=1 or headers!=1 or not first_header:
                            raise OMFError('invalid Easy OMF signature order')
                        if flags!=0x80:
                            raise OMFError('unsupported Easy OMF signature flags')
                        module.format='easy-omf386'
                        module.source_records.append((0,0,data[:record_start]))
                        module.format_source={'record_offset':record_start,
                            'record_ordinal':record_ordinal,'kind':kind,'flags':flags,
                            'class':klass,'payload':payload,
                            'raw_record':data[record_start:end]}
                    elif easy and klass!=0:
                        raise OMFError(f'unsupported Easy OMF comment class {klass:#x}')
                    elif padding:
                        pass  # observed zero-filled class-0 filler in large GA objects
                    elif klass==0:
                        pass  # translator identification, retained verbatim below
                    elif klass==0x9e:
                        if payload:
                            raise OMFError('invalid DOSSEG commentary payload')
                        if not module.name:
                            raise OMFError('DOSSEG requires module header')
                        module.dosseg_directives.append(flags)
                        # FIXUPP may not bind backward across this directive.
                        last=None
                    elif klass==0xa1 and not payload:
                        pass  # measured TASM 3.1 Microsoft OMF format marker
                    elif klass==0xa2 and payload==b'\x01':
                        pass_two=True  # definitions must precede this separator
                    elif klass==0xaa and payload==b'OS220':
                        pass  # measured Watcom format marker
                    elif klass==0x9b and len(payload)==4:
                        pass  # processor/model metadata retained below
                    elif klass==0xe9:
                        if payload:
                            c.bytes(4)  # DOS date/time
                            c.name()
                            if c.left():
                                raise OMFError('invalid dependency comment')
                    elif klass==0x9f:
                        if not payload or any(x<32 or x>126 for x in payload):
                            raise OMFError('invalid default library comment')
                        module.default_libraries.append(payload.decode('ascii'))
                    elif klass==0xfd:
                        directive=c.uint(1)
                        if directive not in (ord('S'),ord('s')):
                            raise OMFError('unsupported disassembler directive')
                        index_start=c.pos
                        segment=c.index()
                        index_bytes=c.data[index_start:c.pos]
                        seg=module._segment(segment)
                        if startup is not None and pass_two and (
                                flags!=0x80 or directive!=ord('s') or
                                seg.class_name!='CODE' or not seg.attributes & 1):
                            raise OMFError('unsupported startup code/data annotation')
                        width=4 if directive==ord('S') else 2
                        start,stop=c.uint(width),c.uint(width)
                        if c.left() or not 0<=start<=stop<=seg.size:
                            raise OMFError('invalid code/data range directive')
                        module.code_data_ranges.append((segment,start,stop))
                        if startup is not None:
                            startup['annotations'].append({
                                'record_ordinal':record_ordinal,'record_offset':record_start,
                                'raw_record':data[record_start:end],'flags':flags,
                                'directive':directive,'segment':segment,
                                'index_bytes':index_bytes,'offset_width':width,
                                'start':start,'stop':stop,'pass_two':pass_two})
                            # Range metadata closes data association, not threads.
                            last=None
                    elif klass==0xfe:
                        directive=c.uint(1)
                        if directive==ord('D') and c.left()==3:
                            c.bytes(3)  # debug version and source language
                        elif directive==ord('T') and c.left()==4:
                            c.uint(4)  # library object's timestamp, retained verbatim
                        elif directive==ord('O'):
                            segment=c.index()
                            module._segment(segment)
                            if c.left():
                                raise OMFError('invalid far-call optimization directive')
                            module.far_call_optimization_segments.append(segment)
                        else:
                            raise OMFError('unsupported linker directive comment')
                    else:
                        raise OMFError(f'unsupported OMF comment class {klass:#x}')
                    module.comments.append((klass,payload))
                elif kind == 0x96:
                    while cur.left():
                        names.append(cur.name())
                elif kind in (0x98,0x99):
                    attr = cur.uint(1)
                    if attr>>5 == 0:
                        raise OMFError('unsupported absolute segment')
                    if easy and attr!=0xa8:
                        raise OMFError('unsupported Easy SEGDEF attributes')
                    size = cur.uint(wide)
                    if attr & 2:
                        size = 1<<(16 if wide==2 else 32)
                    name, klass, overlay = cur.index(),cur.index(),cur.index()
                    if any(n >= len(names) for n in (name,klass,overlay)) or size > 64*1024*1024:
                        raise OMFError('invalid segment name/size')
                    access=None
                    if easy and cur.left():
                        raise OMFError('unsupported Easy SEGDEF access extension')
                    if cur.left():
                        access=cur.uint(1)
                        debug=(names[name],names[klass]) in {
                            ('$$SYMBOLS','DEBSYM'),('$$TYPES','DEBTYP')}
                        if (cur.left() or kind!=0x99 or attr!=0x20 or access!=2 or
                                not debug or names[overlay] or
                                (0xaa,b'OS220') not in module.comments or
                                (0xfe,b'D\x01\x03C') not in module.comments):
                            raise OMFError('unsupported SEGDEF access extension')
                    module.segments.append(Segment(names[name],names[klass],size,attr,bytearray(size),access))
                elif kind == 0x9a:
                    group_name, members = names[cur.index()], []
                    while cur.left():
                        if cur.uint(1) != 0xff:
                            raise OMFError('unsupported group member')
                        member=cur.index()
                        module._segment(member)
                        members.append(member)
                    module.groups.append((group_name,tuple(members)))
                elif kind in (0x8c,0xb4,0xb5):
                    while cur.left():
                        module.externals.append(cur.name())
                        if kind in (0xb4,0xb5):
                            module.local_externals.add(len(module.externals))
                        cur.index()  # type index
                elif kind in (0x94,0x95):
                    group,segment=cur.index(),cur.index()
                    if group>len(module.groups):
                        raise OMFError('invalid LINNUM group')
                    seg=module._segment(segment)
                    if not cur.left() or cur.left()%(2+wide):
                        raise OMFError('invalid LINNUM entries')
                    while cur.left():
                        line,offset=cur.uint(2),cur.uint(wide)
                        if offset>seg.size:
                            raise OMFError('LINNUM offset outside segment')
                        module.line_numbers.append((group,segment,line,offset))
                elif kind in (0x90,0x91,0xb6,0xb7):
                    group = cur.index()
                    if easy and group>len(module.groups):
                        raise OMFError('invalid Easy public group index')
                    segment = cur.index()
                    if not segment:
                        raise OMFError('unsupported absolute public')
                    seg = module._segment(segment)
                    while cur.left():
                        name, offset = cur.name(),cur.uint(wide)
                        cur.index()
                        if offset > seg.size or name in module.publics:
                            raise OMFError('invalid/duplicate public')
                        module.publics[name] = (segment,offset)
                        if kind in (0xb6,0xb7):
                            module.local_publics.add(name)
                elif kind in (0xa0,0xa1,0xa2,0xa3):
                    segment, offset = cur.index(),cur.uint(wide)
                    seg=module._segment(segment)
                    if kind in (0xa2,0xa3):
                        def block(limit,depth=0):
                            if depth>32:
                                raise OMFError('iterated data nesting exceeds limit')
                            repeat,children=cur.uint(wide),cur.uint(2)
                            if children:
                                item=bytearray()
                                for _ in range(children):
                                    item.extend(block(limit-len(item),depth+1))
                            else:
                                item=cur.bytes(cur.uint(1))
                            if repeat*len(item)>limit:
                                raise OMFError('iterated data exceeds segment')
                            return bytes(item)*repeat
                        expanded=bytearray()
                        while cur.left():
                            expanded.extend(block(seg.size-offset-len(expanded)))
                        chunk=bytes(expanded)
                    else:
                        chunk=cur.bytes(cur.left())
                    if offset+len(chunk) > seg.size:
                        raise OMFError('data outside segment')
                    seg.data[offset:offset+len(chunk)] = chunk
                    last = (segment,offset,len(chunk)) if kind in (0xa0,0xa1) else None
                elif kind in (0x9c,0x9d):
                    while cur.left():
                        subrecord = cur.pos
                        first = cur.uint(1)
                        if not first & 0x80:
                            if first & 0x20:
                                raise OMFError('invalid reserved thread bit')
                            is_frame, encoded_method, slot = bool(first & 0x40), (first>>2)&7, first&3
                            method = encoded_method if is_frame else encoded_method&3
                            if is_frame and method in (6,7):
                                raise OMFError('invalid thread frame method')
                            if method==3:
                                raise OMFError('unsupported explicit-frame thread method')
                            datum = indexed_datum(method,cur) if method<=2 else None
                            table = frame_threads if is_frame else target_threads
                            event = len(module.thread_definitions)
                            module.thread_definitions.append({
                                'record_offset':record_start,'record_ordinal':record_ordinal,
                                'subrecord_offset':subrecord,'raw_header':first,
                                'raw_bytes':cur.data[subrecord:cur.pos],
                                'namespace':'frame' if is_frame else 'target','slot':slot,
                                'encoded_method':encoded_method,'method':method,'datum':datum,
                                'replaces':None if table[slot] is None else table[slot][2]})
                            table[slot]=(method,datum,event)
                            continue
                        if last is None:
                            raise OMFError('fixups without preceding data')
                        raw_loc, offset = (first>>2)&15, ((first&3)<<8)|cur.uint(1)
                        loc = raw_loc
                        if easy:
                            if raw_loc!=5:
                                raise OMFError(f'unsupported Easy fixup location {raw_loc}')
                            loc=9
                        widths = {0:1,1:2,2:2,3:4,4:1,5:2,9:4,11:6,13:4}
                        if loc not in widths:
                            raise OMFError(f'unsupported fixup location {loc}')
                        fd = cur.uint(1)
                        fm, tm = (fd>>4)&7,fd&3
                        frame_definition = target_definition = None
                        if fd & 0x80:
                            selected=frame_threads[fm&3]
                            if selected is None:
                                raise OMFError('undefined OMF frame thread')
                            fm,frame,frame_definition=selected
                        elif fm <= 2:
                            frame = indexed_datum(fm,cur) if easy else cur.index()
                        elif fm == 3:
                            if easy:
                                raise OMFError('unsupported Easy explicit-frame method')
                            frame = cur.uint(2)
                        elif fm in (4,5):
                            frame = None
                        else:
                            raise OMFError(f'unsupported fixup frame {fm}')
                        if fd & 8:
                            selected=target_threads[tm]
                            if selected is None:
                                raise OMFError('undefined OMF target thread')
                            tm,target,target_definition=selected
                        elif tm==3:
                            if easy:
                                raise OMFError('unsupported Easy explicit-target method')
                            target=cur.uint(2)
                        else:
                            target=indexed_datum(tm,cur)
                        disp = 0 if fd & 4 else cur.uint(wide)
                        segment,base,length = last
                        if offset+widths[loc] > length:
                            raise OMFError('fixup outside preceding data')
                        module.fixups.append(Fixup(segment,base+offset,widths[loc],loc,
                                                  not bool(first&0x40),fm,frame,tm,target,disp))
                        encoding={
                            'fixup':len(module.fixups)-1,'record_kind':kind,
                            'raw_bytes':cur.data[subrecord:cur.pos],
                            'raw_location':raw_loc,'raw_locat':cur.data[subrecord:subrecord+2],
                            'raw_fixdat':fd,'raw_frame_field':(fd>>4)&7,
                            'raw_target_field':fd&3,'frame_thread':bool(fd&0x80),
                            'target_thread':bool(fd&8),'frame_definition':frame_definition,
                            'target_definition':target_definition,
                            'displacement_present':not bool(fd&4),
                            'displacement_width':0 if fd&4 else wide}
                        # Legacy inline metadata is independent of intervening
                        # comments (existing DOSSEG metadata invariants). New
                        # Easy/thread encodings also expose exact coordinates.
                        if easy or fd & 0x88:
                            encoding.update(record_offset=record_start,
                                record_ordinal=record_ordinal,subrecord_offset=subrecord)
                        module.fixup_encodings.append(encoding)
                elif kind in (0x8a,0x8b):
                    if (not headers if easy else not module.name):
                        raise OMFError('missing module header')
                    if pos != len(data):
                        raise OMFError('trailing bytes after MODEND')
                    payload=cur.bytes(cur.left())
                    if payload != b'\0':
                        if startup is None or kind!=0x8a:
                            raise OMFError('unsupported MODEND start address')
                        entry=Cursor(payload)
                        # Measured GA/10.5 standard OMF logical entry only.
                        if entry.uint(1)!=0xc1 or entry.uint(1)!=0x50:
                            raise OMFError('unsupported startup MODEND entry')
                        index_start=entry.pos
                        segment=entry.index()
                        index_bytes=entry.data[index_start:entry.pos]
                        seg=module._segment(segment)
                        displacement=entry.uint(2)
                        if (entry.left() or seg.class_name!='CODE' or
                                not seg.attributes & 1 or displacement>=seg.size):
                            raise OMFError('invalid startup entry extent/type')
                        startup['entry']={'record_ordinal':record_ordinal,
                            'record_offset':record_start,'raw_record':data[record_start:end],
                            'raw_payload':payload,'module_type':0xc1,'end_data':0x50,
                            'frame_method':5,'target_method':0,'segment':segment,
                            'index_bytes':index_bytes,'displacement':displacement,
                            'displacement_width':2}
                    if module.format=='easy-omf386':
                        module.source_records.append((record_ordinal,record_start,data[record_start:end]))
                    return module
                else:
                    raise OMFError(f'unsupported OMF record {kind:#x}')
            except (IndexError,struct.error) as error:
                raise OMFError(f'invalid index/payload in record {kind:#x}') from error
            if cur.left():
                raise OMFError(f'trailing payload in record {kind:#x}')
            if module.format=='easy-omf386':
                module.source_records.append((record_ordinal,record_start,data[record_start:end]))
        raise OMFError('missing MODEND')

    def _segment(self, number: int) -> Segment:
        if not 1 <= number <= len(self.segments):
            raise OMFError(f'invalid segment number {number}')
        return self.segments[number-1]


@dataclass(frozen=True)
class StartupModule:
    """Opt-in envelope; annotations never exclude bytes from normal proof."""
    module: Module
    profile: str
    records: tuple[dict,...]
    annotations: tuple[dict,...]
    entry: dict | None


def parse_startup(data: bytes) -> StartupModule:
    """Decode measured standard-OMF USE32 Watcom startup additions.

    Default Module.parse and its AST remain unchanged. Entry metadata is
    separate from ordinary fixups; no placement, STACK or whole proof is
    inferred here. Raw record bytes are retained without rewriting input.
    """
    metadata={'records':[],'annotations':[],'entry':None}
    module=Module._parse(data,metadata)
    return StartupModule(module,'watcom-startup-32-v1',
                         tuple(metadata['records']),
                         tuple(metadata['annotations']),metadata['entry'])
