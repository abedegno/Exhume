"""Check what match.py masks: every fixup, and the file's initialised data.

    python3 tools/verify.py SRC [--update | --object PATH] [--config PATH]     (run match.py first: this reads its build)

Written for the Borland medium model with a VROOMM overlay (MZ + FBOV); the project's
DGROUP paragraph, overlay naming and symbol files come from exhume.toml.

- An extern must resolve to one address everywhere it is used: a DS offset for near data,
  segment:offset for far calls. Two externs resolving to one address are reported too. Each
  segment word and each offset word of a far address is checked, not just one of each.
- The file's own publics are placed too (_DATA and _BSS from their bases, resident code at
  its paragraph, overlay code at its stub entry) and checked against symbols.tsv like the
  externs, so one name cannot mean two addresses in two files. A public of an overlay that
  the EXE's stub table has no entry for was static in the original.
- A reference into this file's own code must land where the object file says: calls to
  functions later in the file (rewritten by the linker to nop; push cs; call near) and jump
  tables. A function's address stored as data points at its overlay stub entry instead,
  which the linker assigns; those must be one per function.
- References into this file's _DATA must agree on one base, and the _DATA bytes must equal
  the EXE's data segment at that base. References into _BSS must agree on one base.
- The file's own far segments (Turbo C gives each `far` variable a paragraph-aligned segment
  of its own, FILE<n>_FAR, class FAR_DATA): every reference to one (segment words, offsets,
  far pointers, in code and in _DATA) must agree on one address, and the segment's bytes
  must equal the EXE's there. A far segment nothing in the file refers to is placed by its
  publics' addresses in symbols.tsv.
Names are always checked against symbols.tsv (one name per address, one address per
name); --update also merges them in, and refuses to write when there is any problem.

--object PATH verifies that existing proof object instead of the canonical build
object. It is read-only and cannot be combined with --update.

main(argv, on_names=None) is importable: on_names(syms) receives every extern resolved
({name: (address, where)}), which is how a link reference implementation learns the
addresses (examples/uw2/extract.py). It never touches sys.argv."""
import sys, os, re, struct
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import config
from fixups import fixups, target_name
from symbol_address import parse as parse_address, format_address, overlay_location

CFG = None
DS_FILE = DS_PARA = None

def stub_key():
    """The toolchain profile's name key for the order of publics (bssorder.py's key), or None."""
    p = os.path.join(config.EXHUME, 'profiles', CFG.profile_name, 'bssorder.py') if hasattr(CFG, 'profile_name') else None
    if not p or not os.path.exists(p): return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('bssorder', p); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.key


def read_symbols():
    """symbols.tsv as {name: address text}."""
    return {n: v for n, (v, _) in CFG.load_symbols().items()}

def note_reference(syms, problems, name, val, where, aliases):
    """Record one extern under its explicitly configured canonical spelling."""
    name = aliases.get(name, name)
    prev = syms.setdefault(name, (val, where))
    if prev[0] != val:
        problems.append(f'{name}: {fmt(prev[0])} at +{prev[1]:X} but {fmt(val)} at +{where:X}')


def resident_instruction_field(obj, at, rows, size):
    """Return one unprefixed E8/E9 field in an explicit function CODE row."""
    from iced_x86 import Decoder, Code
    candidates=[]
    for row in rows:
        _,_,start,length=row
        if (type(start) is int and type(length) is int and start>=0 and length>0
                and start+length<=min(size,len(obj)) and start<=at-1 and at+2<=start+length):
            candidates.append(row)
    if len(candidates)!=1:return None
    start,length=candidates[0][2:]
    for ins in Decoder(16,obj[start:start+length],ip=start):
        if ins.is_invalid or ins.next_ip>start+length:return None
        if ins.ip==at-1:
            return ins if ins.code in (Code.CALL_REL16,Code.JMP_REL16) and ins.len==3 else None
    return None


def resident_target_near(cfg, seg, base, size, rows, org, obj, at, f, code):
    """Bind only TARGET-framed resident self-relative external OFFSET16 calls.

    OMF S=0 is self-relative; the encoded WORD and target displacement are
    modular target addends, not an original absolute address. Existing CODE
    frame handling is deliberately unchanged.
    """
    def need(ok,message):
        if not ok:raise ValueError('resident near reference: '+message)
    need(f['frame']==(5,None),'unsupported non-CODE frame')
    need(f['rel'] is False,'OFFSET16 must be self-relative')
    need(cfg.overlay_index(seg) is None,'caller is not resident')
    e=cfg.exe
    need(e[:2]==b'MZ' and len(e)>=0x1c,'invalid original MZ header')
    # Same MZ invariants as symbol_address.overlay_location, before using any
    # resident paragraph or original relocation/segment-table geometry.
    last,pages,relocs,header_paras=struct.unpack_from('<4H',e,2)
    header=header_paras*16
    end=(pages-1)*512+last if last else pages*512
    reloc_table=struct.unpack_from('<H',e,0x18)[0]
    need(not(pages==0 or last>=512 or header<28 or header>end or end>len(e) or
             (relocs and (reloc_table<28 or reloc_table+relocs*4>header))),
         'invalid original MZ bounds')
    need(all(type(v) is int for v in (base,size,org)) and size>0 and 0<=org<0x10000
         and org+size<=0x10000 and header<=base and base+size<=end<=len(e),
         'caller CODE bounds exceed resident image')
    delta=base-org-header
    need(delta>=0 and delta%16==0 and delta//16<=0xffff,'caller physical placement disagrees with CS')
    paragraph=delta//16
    need(end+16<=len(e) and e[end:end+4]==b'FBOV','invalid original FBOV header')
    table,count=struct.unpack_from('<II',e,end+8)
    need(count>0 and table>=header and table+8*count<=end,'invalid original segment table bounds')
    spans=[(low,high) for para,high,flags,low in
           (struct.unpack_from('<4H',e,table+8*k) for k in range(count))
           if para==paragraph and flags==1 and low<high and low<=org and org+size<=high]
    need(len(spans)==1,'caller is not in one original resident segment span')
    low,high=spans[0]
    need(header+paragraph*16+high<=end,
         'original resident segment span exceeds loaded image')
    ins=resident_instruction_field(obj,at,rows,size)
    need(ins is not None,'operand is not a complete E8/E9 instruction in function CODE')
    row=next((r for r in rows if r[2]<=ins.ip and ins.next_ip<=r[2]+r[3]),None)
    need(row is not None,'missing caller function row')
    original_ins=resident_instruction_field(e[base:base+size],at,(row,),size)
    need(original_ins is not None and original_ins.code==ins.code,
         'original instruction boundary or CALL/JMP kind disagrees')
    need(0<=at-1 and at+2<=min(size,len(obj)),'operand crosses retained CODE boundary')
    need(type(f['disp']) is int and 0<=f['disp']<=0xffff,'unsupported target addend')
    add=(struct.unpack_from('<H',obj,at)[0]+f['disp'])&0xffff
    displacement=struct.unpack_from('<h',e,base+at)[0]
    effective=(org+at+2+displacement)&0xffff
    target=(effective-add)&0xffff
    need(low<=effective<high and low<=target<high,'effective or base target crosses original CS span')
    return ('FAR',paragraph,target)


def resident_cs_program_ds(cfg, seg, base, size, rows, org, obj, at, f, code, raw):
    """Prove a CS-resident loader-relocated WORD holding program DGROUP.

    Only the exact MOV DS,CS:[disp16] instruction is admitted. The original
    MZ relocation and stored DGROUP paragraph establish the otherwise missing
    FAR segment identity; no symbol name or address hint supplies it.
    """
    from iced_x86 import Decoder, Code, FlowControl
    from context_omf import inspect_object, SplitError
    def need(ok,message):
        if not ok:raise ValueError('resident CS program-DS reference: '+message)
    # Frame: BCC's own output uses the target's frame (method 5); code compiled
    # through TASM (-B) names the caller's own CODE segment (method 0). The
    # word lies in that same segment (checked against the CS span below), so
    # both frames give the same offset.
    need(f['seg']==code and f['off']==at and f['loc']==1 and f['rel'] is True
         and f['frame'] in ((5,None),(0,code)) and f['target'][0]==2 and f['disp']==0,
         'unsupported OFFSET16 form, frame or addend')
    need(cfg.overlay_index(seg) is None,'caller is not resident')
    need(type(at) is int and 3<=at and at+2<=len(obj) and obj[at:at+2]==b'\0\0',
         'nonzero or out-of-bounds encoded addend')
    # Strict OMF parsing is local to this new proof. Legacy verification paths
    # retain their existing grammar. Reject threads, duplicate/overlap writers,
    # malformed checksums and overlapping/unsupported fixups before inference.
    if f['frame']==(0,code):
        # Compiled through TASM (-B): the frame is the module's own CODE
        # segment (ASSUME CS). The strict parser (hash-pinned by two other
        # proofs) admits that frame only for its one authenticated module, so
        # this path does its own record checks: known 16-bit record types
        # only, valid checksums, no LIDATA, CODE LEDATA without overlap or
        # gaps, and the target an EXTDEF index; then one zero-addend external
        # OFFSET16 at this field, framed by the single CODE segment, and no
        # other fixup on the instruction.
        from fixups import fixups as read_fixups
        from omf import records as read_records, idx as read_index
        allowed={0x80,0x88,0x96,0x98,0x9A,0x8C,0x90,0xA0,0x9C,0x8A}
        covered=set(); ledata_seen=False
        for kind,body,pos in read_records(raw):
            need(kind in allowed,'proof OMF record %02X unsupported'%kind)
            length=struct.unpack_from('<H',raw,pos+1)[0]
            need(pos+3+length<=len(raw) and
                 (raw[pos+2+length]==0 or sum(raw[pos:pos+3+length])&0xFF==0),
                 'proof OMF checksum')
            if kind==0xA0:
                si,i=read_index(body,0); off=struct.unpack_from('<H',body,i)[0]; i+=2
                ledata_seen=True
                if si==code:
                    span=set(range(off,off+len(body)-i))
                    need(not span&covered,'proof OMF CODE LEDATA overlaps'); covered|=span
            if kind==0x8C: need(not ledata_seen,'proof OMF late EXTDEF')
        need(covered==set(range(len(obj))),'proof OMF CODE LEDATA gaps')
        whole=read_fixups(raw)
        need([i for i,s in enumerate(whole['segs']) if s and s[1]=='CODE']==[code]
             and bytes(whole['data'][code])==bytes(obj),'proof OMF CODE disagrees')
        need(1<=f['target'][1]<len(whole['ext']),'target is not an EXTDEF index')
        same=[x for x in whole['fixups'] if x['seg']==code and x['off']==at]
        need(len(same)==1,'proof OMF fixup disagrees')
        width={0:1,1:2,2:2,3:4,4:1,5:2,9:4,11:6,13:4}
        need(not any(x['seg']==code and x['off']<at+2 and at-3<x['off']+width.get(x['loc'],6)
                     and x['off']!=at for x in whole['fixups']),
             'another fixup overlaps instruction')
    else:
        try: parsed=inspect_object(raw)
        except (ValueError,IndexError,TypeError,struct.error) as error:
            raise ValueError('resident CS program-DS reference: invalid proof OMF: '+str(error)) from error
        need(parsed.code_segment==code and parsed.code==obj,'proof OMF CODE disagrees')
        fs=[x for x in parsed.fixups if x.offset==at]
        need(len(fs)==1 and fs[0].location==1 and fs[0].segment_relative
             and fs[0].frame_method==5 and fs[0].frame_index is None
             and fs[0].target_method==2 and fs[0].target==f['target'][1]
             and fs[0].displacement==0,'proof OMF fixup disagrees')
        need(not any(x.offset<at+2 and at-3<x.offset+x.width and x.offset!=at
                     for x in parsed.fixups),'another fixup overlaps instruction')
    e=cfg.exe
    need(len(e)>=28 and e[:2]==b'MZ','invalid original MZ header')
    last,pages,relocs,hp=struct.unpack_from('<4H',e,2);header=hp*16
    end=(pages-1)*512+last if last else pages*512
    rt=struct.unpack_from('<H',e,0x18)[0]
    need(pages>0 and last<512 and header>=28 and header<=end<=len(e)
         and (not relocs or (rt>=28 and rt+4*relocs<=header)),
         'invalid original MZ bounds')
    need(all(type(v) is int for v in (base,size,org)) and size>0
         and 0<=org<0x10000 and org+size<=0x10000 and header<=base
         and base+size<=end and len(obj)==size,'caller CODE bounds disagree')
    delta=base-org-header
    need(delta>=0 and delta%16==0 and delta//16<=0xffff,'caller physical placement disagrees with CS')
    para=delta//16
    need(end+16<=len(e) and e[end:end+4]==b'FBOV','invalid original FBOV header')
    table,count=struct.unpack_from('<II',e,end+8)
    need(count>0 and table>=header and table+8*count<=end,'invalid original segment table')
    spans=[(low,high) for p,high,flags,low in
           (struct.unpack_from('<4H',e,table+8*k) for k in range(count))
           if p==para and flags==1]
    need(len(spans)==1,'CS is not one unique original resident span')
    low,high=spans[0]
    need(low<high and low<=org and org+size<=high and header+para*16+high<=end,
         'original resident span bounds disagree')
    candidates=[r for r in rows if type(r[2]) is int and type(r[3]) is int
                and r[2]>=0 and r[3]>0 and r[2]+r[3]<=size
                and r[2]<=at-3 and at+2<=r[2]+r[3]]
    need(len(candidates)==1,'instruction lacks one explicit complete function row')
    row=candidates[0]
    need(sum(type(r[2]) is int and type(r[3]) is int and r[3]>0
             and r[2]<at+2 and at-3<r[2]+r[3] for r in rows)==1,
         'overlapping function rows at instruction')
    prefixes=[]
    for payload in (obj,e[base:base+size]):
        found=None;prefix=[];last=None
        for ins in Decoder(16,payload[row[2]:row[2]+row[3]],ip=row[2]):
            last=ins
            need(not ins.is_invalid and ins.next_ip<=row[2]+row[3], 'invalid function instruction boundary')
            # A linear decode of the row, valid in both payloads, with the
            # same instructions before the load in each, fixes the boundary.
            # Jumps and calls there are allowed (TASM-compiled C has them).
            if ins.ip<at-3:prefix.append((ins.ip,ins.code))
            if ins.ip==at-3:found=ins
        need(found is not None and found.len==5 and found.code==Code.MOV_SREG_RM16
             and payload[at-3:at]==b'\x2e\x8e\x1e',
             'not exact MOV DS,CS:[WORD] at original and compiled boundary')
        need(last is not None and last.next_ip==row[2]+row[3],
             'linear decode does not end at the function row end')
        prefixes.append(prefix)
    need(prefixes[0]==prefixes[1],'original and compiled prefix instructions differ')
    offset=struct.unpack_from('<H',e,base+at)[0]
    need(low<=offset and offset+2<=high and offset+2<=0x10000,
         'CS WORD crosses original initialized span')
    physical=header+para*16+offset
    need(header<=physical and physical+2<=end,'CS WORD exceeds resident image')
    # The word is a loader-relocated segment: program DGROUP (HEAP_PROGRAM_DS)
    # or another segment kept in CS the same way (the overlay manager's data
    # segment at 0094:4694).  The exact MZ relocation below is what proves it
    # is a segment value; its name is then checked against symbols.tsv.
    need(type(cfg.dgroup_para) is int and 0<=cfg.dgroup_para<=0xffff,
         'configured program DGROUP missing')
    relocation_locations=[];relocation_pairs=[]
    for k in range(relocs):
        off,p=struct.unpack_from('<HH',e,rt+4*k);q=header+p*16+off
        need(off+2<=0x10000 and header<=q and q+2<=end,'invalid original MZ relocation target')
        relocation_locations.append(q);relocation_pairs.append((p,off))
    ordered=sorted(relocation_locations)
    need(all(a+2<=b for a,b in zip(ordered,ordered[1:])),
         'duplicate or overlapping original MZ relocations')
    need(relocation_locations.count(physical)==1 and relocation_pairs.count((para,offset))==1,
         'original CS WORD lacks one exact MZ relocation')
    return ('FAR',para,offset)


def _resident_policy_lists(cfg):
    raw = getattr(cfg, 'raw', {})
    if type(raw) is not dict:
        raise ValueError('malformed configuration metadata')
    section = raw.get('proofs', {})
    allowed = {'resident_cs_prefix_modules': 'POOLINIT',
               'resident_cs_code_frame_modules': 'HPALLOCH'}
    if type(section) is not dict or set(section) - set(allowed):
        raise ValueError('unknown or malformed [proofs] section')
    lists = {}
    for key, supported in allowed.items():
        modules = section.get(key, [])
        if (type(modules) is not list or
                any(type(name) is not str or name != supported for name in modules) or
                len(set(modules)) != len(modules)):
            raise ValueError(key+' must be a unique list containing only '+supported)
        lists[key] = modules
    return lists


def _bounded_resident_policy(cfg, stem, explicit):
    if type(explicit) is not bool:
        raise ValueError('keyword bounded_resident_prefix must be an exact bool')
    modules = _resident_policy_lists(cfg)['resident_cs_prefix_modules']
    return explicit or stem in modules


def _code_frame_resident_policy(cfg, stem, explicit):
    if type(explicit) is not bool:
        raise ValueError('keyword code_frame_resident_ds must be an exact bool')
    modules = _resident_policy_lists(cfg)['resident_cs_code_frame_modules']
    return explicit or stem in modules


def main(argv=None, on_names=None, *, bounded_resident_prefix=False, code_frame_resident_ds=False):
    global CFG, DS_FILE, DS_PARA
    path, a = config.pop_config(sys.argv[1:] if argv is None else argv)
    CFG = config.load(path)
    if type(code_frame_resident_ds) is not bool:
        print('PROBLEM resident own-CODE policy: keyword must be exact bool')
        print('-- 1 problems')
        return 1
    object_path = None
    if '--object' in a:
        i = a.index('--object')
        if i + 1 >= len(a) or a[i + 1].startswith('--'):
            sys.exit('--object requires a path')
        object_path = a[i + 1]
        a = a[:i] + a[i + 2:]
        if '--update' in a:
            sys.exit('--object cannot be combined with --update')
    DS_PARA = CFG.dgroup_para; DS_FILE = CFG.dgroup_file
    if not a or a[0].startswith('-'): sys.exit(__doc__)
    src = a[0]
    stem = CFG.stem(src)
    try:
        bounded_resident_prefix = _bounded_resident_policy(CFG, stem, bounded_resident_prefix)
        code_frame_resident_ds = _code_frame_resident_policy(CFG, stem, code_frame_resident_ds)
    except ValueError as error:
        print('PROBLEM', 'resident CS proof policy: '+str(error))
        print('-- 1 problems')
        return 1
    if code_frame_resident_ds and (stem != 'HPALLOCH' or bounded_resident_prefix):
        print('PROBLEM resident own-CODE policy: only separate RH1B9A case is supported')
        print('-- 1 problems')
        return 1
    if bounded_resident_prefix or code_frame_resident_ds:
        # config.load caches instances. Namespace/hint overrides belong only
        # to this invocation, while respecting caller-set fields (e.g. Gate's
        # temporary symbols path) copied from the cached instance.
        import copy
        CFG = copy.copy(CFG)
    seg = CFG.directives(src)[0]
    if not seg: sys.exit(f'{src}: no /* target: SEG */ line')
    with open(object_path if object_path is not None else CFG.obj(stem), 'rb') as stream:
        raw_object = stream.read()
    bounded_transaction = bounded_namespace = None
    pending_cs = None
    code_frame_transaction = None
    if code_frame_resident_ds:
        try:
            from code_frame_proof import begin as begin_code, token, stable
            code_symbol_token = token(CFG)
            code_frame_transaction = begin_code(CFG, seg, raw_object)
            # Validate namespace syntax/domain bounds early without storing or
            # using any known value as static reference evidence.
            CFG.load_symbols()
            stable(CFG, code_symbol_token)
            o = fixups(raw_object)
        except (ValueError,IndexError,KeyError,TypeError,OSError,struct.error) as error:
            print('PROBLEM', 'resident own-CODE capture: '+str(error))
            print('-- 1 problems')
            return 1
    elif bounded_resident_prefix:
        try:
            from bounded_prefix import begin, symbol_token, require_symbol_token
            bounded_symbol_token = symbol_token(CFG)
            bounded_transaction, bounded_namespace = begin(CFG, seg, raw_object)
            require_symbol_token(CFG, bounded_symbol_token)
            o = fixups(raw_object)
        except (ValueError,IndexError,KeyError,TypeError,OSError,struct.error) as error:
            print('PROBLEM', 'bounded resident prefix: '+str(error))
            print('-- 1 problems')
            return 1
    else:
        o = fixups(raw_object)
    word = lambda b, i: struct.unpack_from('<H', b, i)[0]
    exe = CFG.exe; hdr = struct.unpack_from('<H', exe, 8)[0] * 16
    # overlay code names other segments by their byte offset in the overlay manager's
    # segment table (8-byte entries, the first word being the paragraph); the FBOV header
    # after the load image gives the table's file offset
    cblp, cp = struct.unpack_from('<HH', exe, 2); mzend = (cp - 1) * 512 + cblp if cblp else cp * 512
    segtab = struct.unpack_from('<I', exe, mzend + 8)[0]
    para_of = lambda v: word(exe, segtab + v) if base >= mzend else v
    if code_frame_resident_ds:
        base,size,rows,org = (code_frame_transaction.base,code_frame_transaction.size,
                              list(code_frame_transaction.rows),code_frame_transaction.org)
        CFG.far_hints = {}
    elif bounded_resident_prefix:
        base, size, rows, org = (bounded_transaction.base, bounded_transaction.size,
                                 list(bounded_transaction.rows), bounded_transaction.org)
        CFG.far_hints = {}
    else:
        base, size, rows, org = CFG.load_targets(seg)
    FARHINT.clear(); FARHINT.update(CFG.far_hints)
    segs = o['segs']
    code = next(i for i, s in enumerate(segs) if s and s[2] and CFG.is_code_class(s[1]))
    datas = next(i for i, s in enumerate(segs) if s and s[0] == '_DATA')
    bss = next((i for i, s in enumerate(segs) if s and s[0] == '_BSS'), None)
    fars = {i for i, s in enumerate(segs) if s and s[1] == 'FAR_DATA'}
    farref = {}; farfx = []     # far segment -> {'seg'|'off': [(value, where)]}; fixups inside them
    syms = {}; problems = []; data_bases = set(); cs_values = set(); internal = 0; entries = {}; bss_bases = set(); halves = {}; offset_addends = {}; datafx = []; segonly = {}

    aliases = CFG.raw.get('names', {}).get('extern_aliases', {})

    def note(name, val, where):
        note_reference(syms, problems, name, val, where, aliases)

    def note_body(name, index, offset, where, addends=(0,)):
        from near_entries import resolve
        try:
            _, physical = overlay_location(CFG, index, org, size)
            if physical != base:
                raise ValueError(f'{name}: caller target disagrees with original overlay body')
            overlay_location(CFG, index, offset)
            value = resolve(CFG, name, seg, offset, addends=addends)
            note(name, value if value is not None else ('OVL', index, offset), where)
        except ValueError as error:
            problems.append(str(error))

    for f in o['fixups']:
        if f['seg'] == datas:
            datafx.append(f); continue      # checked once the data base is known
        if f['seg'] in fars:
            farfx.append(f); continue       # checked once the far segment is placed
        if f['seg'] != code:
            problems.append(f'fixup in segment {segs[f["seg"]][0]} not checked'); continue
        at = f['off']; obj = o['data'][code]; loc = f['loc']; tm, ti = f['target']
        add = word(obj, at) + f['disp']
        cs_program_ds = (tm == 2 and base < mzend and at >= 3 and
                         (obj[at-3:at] == b'\x2e\x8e\x1e' or
                          exe[base+at-3:base+at] == b'\x2e\x8e\x1e'))
        if cs_program_ds:
            name = o['ext'][ti]
            if code_frame_resident_ds:
                try:
                    from code_frame_proof import reference
                    note(name, reference(CFG, code_frame_transaction, at, f), at)
                except ValueError as error:
                    problems.append(f'{name}: {error}')
            elif bounded_resident_prefix:
                try:
                    from bounded_prefix import pending
                    if pending_cs is not None: raise ValueError('second pending CS reference')
                    pending_cs = pending(bounded_transaction, f)
                except ValueError as error:
                    problems.append(f'{name}: {error}')
            else:
                try:
                    note(name, resident_cs_program_ds(CFG, seg, base, size, rows, org,
                                                      obj, at, f, code, raw_object), at)
                except ValueError as error:
                    problems.append(f'{name}: {error}')
        elif (tm == 2 and loc == 1 and base < mzend and f['frame'] != (0, code)
                and (not f['rel'] or resident_instruction_field(obj, at, rows, size) is not None)):
            name = o['ext'][ti]
            try:
                note(name, resident_target_near(CFG, seg, base, size, rows, org,
                                                obj, at, f, code), at)
            except ValueError as error:
                problems.append(f'{name}: {error}')
        elif tm == 2 and loc == 1 and f['frame'] == (0, code):
            # a label in this file's own code segment that another module of it defines (an
            # assembly segment built from several modules): a near call or jump (self-relative)
            # or an offset; either way it is an address in this segment
            name = o['ext'][ti]
            n = CFG.overlay_index(seg)
            para = (word(exe, segtab + 8 * n) if base >= mzend and n is not None
                    else (base - org - hdr) // 16)
            v = word(exe, base + at) - add + (org + at + 2 if not f['rel'] else 0)
            if base >= mzend and n is not None:
                note_body(name, n, v & 0xFFFF, at, (add,))
            else:
                note(name, ('FAR', para, v & 0xFFFF), at)
        elif tm == 2:                                   # extern
            name = o['ext'][ti]
            if (loc == 1 and base >= mzend and CFG.overlay_index(seg) is not None
                    and exe[base + at - 1] in (0xE8, 0xE9)):
                # A near call to another module in this overlay. Its OMF record names an
                # extern, but TLINK resolves the displacement within the overlay, not DGROUP.
                n = CFG.overlay_index(seg)
                para = word(exe, segtab + 8 * n)
                disp = struct.unpack_from('<h', exe, base + at)[0]
                note_body(name, n, (org + at + 2 + disp) & 0xFFFF, at, (add,))
            elif loc in (1, 5) and f['frame'] and f['frame'][0] == 5:
                # the offset half of a far address used as data (say, a callback argument)
                halves.setdefault(name, {}).setdefault('off', []).append(((word(exe, base + at) - add) & 0xFFFF, at))
                offset_addends.setdefault(name, []).append(add)
            elif loc in (1, 5):
                note(name, ('DS', (word(exe, base + at) - add) & 0xFFFF), at)
            elif loc == 2:
                halves.setdefault(name, {}).setdefault('seg', []).append((para_of(word(exe, base + at)), at))
            elif loc == 3 and exe[base + at - 1:base + at + 2] == b'\x90\x0e\xe8':
                # TLINK rewrote this extern far call to a near call in the same overlay.
                n = CFG.overlay_index(seg)
                disp = struct.unpack_from('<h', exe, base + at + 2)[0]
                if n is not None and base >= mzend:
                    note_body(name, n, (org + at + 4 + disp) & 0xFFFF, at, (add,))
                else:
                    note(name, ('FAR', (base - org - hdr) // 16,
                                (org + at + 4 + disp) & 0xFFFF), at)
            elif loc == 3:
                note(name, ('FAR', para_of(word(exe, base + at + 2)), (word(exe, base + at) - add) & 0xFFFF), at)
            else:
                problems.append(f'+{at:X}: {name} loc {loc} not handled')
        elif tm == 0 and ti == code:                    # this file's own code
            internal += 1
            if loc == 3:
                if exe[base + at - 1:base + at + 2] == b'\x90\x0e\xe8':
                    # in an overlay the linker rewrites it to nop; push cs; call near rel16
                    dest = (at + 4 + word(exe, base + at + 2)) & 0xFFFF
                    if dest != add + org: problems.append(f'+{at:X}: call lands at +{dest:X}, object says +{add + org:X}')
                else:
                    # in resident code it stays a far call to this segment's own paragraph
                    para = (base - org - hdr) // 16
                    if word(exe, base + at) != add + org or word(exe, base + at + 2) != para:
                        problems.append(f'+{at:X}: far self-call goes to {word(exe, base + at + 2):04X}:{word(exe, base + at):04X}, '
                                        f'expected {para:04X}:{add + org:04X}')
            elif loc == 5 and base < mzend:
                # a function's address taken in resident code: its real offset
                if word(exe, base + at) != add + org:
                    problems.append(f'+{at:X}: function address {word(exe, base + at):X}, object says {add + org:X}')
            elif loc == 5:
                # a far function's address taken in an overlay (-Y): the linker points it
                # at the function's entry in the overlay's stub table, which it assigns
                entries.setdefault(add + org, set()).add(word(exe, base + at))
            elif loc == 1:
                # absolute code offsets count from the segment's paragraph, org bytes before the file
                if word(exe, base + at) != add + org:
                    problems.append(f'+{at:X}: code offset {word(exe, base + at):X}, object says {add + org:X}')
            elif loc == 2:
                cs_values.add(word(exe, base + at))
        elif tm == 0 and ti == bss:                    # this file's uninitialised data
            internal += 1
            bss_bases.add((word(exe, base + at) - add) & 0xFFFF)
        elif (tm == 0 and ti == datas or tm == 1) and loc == 2:     # DGROUP's paragraph
            internal += 1
            if word(exe, base + at) != DS_PARA: problems.append(f'+{at:X}: DGROUP referenced as {word(exe, base + at):04X}')
        elif tm == 0 and ti == datas or tm == 1:       # this file's data, through DGROUP
            internal += 1
            data_bases.add((word(exe, base + at) - add) & 0xFFFF)
        elif tm == 0 and ti in fars and loc in (1, 2, 3, 5):    # this file's own far data
            internal += 1
            r = farref.setdefault(ti, {})
            if loc in (1, 5): r.setdefault('off', []).append(((word(exe, base + at) - add) & 0xFFFF, f'+{at:X}'))
            elif loc == 2: r.setdefault('seg', []).append((para_of(word(exe, base + at)), f'+{at:X}'))
            else:
                r.setdefault('off', []).append(((word(exe, base + at) - add) & 0xFFFF, f'+{at:X}'))
                r.setdefault('seg', []).append((para_of(word(exe, base + at + 2)), f'+{at:X}'))
        else:
            problems.append(f'+{at:X}: target {target_name(o, f["target"])} loc {loc} not handled')

    for name, h in halves.items():
        # every half must agree: one name used for two variables shows up here
        for half in ('seg', 'off'):
            vals = sorted({v for v, _ in h.get(half, [])})
            if len(vals) > 1:
                problems.append(f'{name}: {half} halves differ: ' + ', '.join(
                    f'{v:04X} at +' + '/+'.join(f'{w:X}' for u, w in h[half] if u == v) for v in vals))
        if 'off' in h and 'seg' in h: note(name, ('FAR', h['seg'][0][0], h['off'][0][0]), h['off'][0][1])
        elif 'seg' in h:
            # only the segment is used, as in FP_SEG(x) + 1: check it against the map
            segonly[name] = h['seg'][0][0]
        else:
            # only the offset is used (a far variable addressed through a segment register
            # set elsewhere, frame its own segment): it must agree with symbols.tsv
            from near_entries import resolve as resolve_near_entry
            try:
                entry = resolve_near_entry(CFG, name, seg, h['off'][0][0],
                                           addends=offset_addends.get(name, ()))
            except ValueError as error:
                problems.append(str(error))
                entry = None
            if entry is not None:
                note(name, entry, h['off'][0][1])
            else:
                known = read_symbols()
                v = known.get(name, '')
                accepted = False
                if v:
                    try:
                        address = parse_address(v)
                        if address[0] == 'OVL':
                            if address[1] != CFG.overlay_index(seg) or any(offset_addends.get(name, ())):
                                raise ValueError(f'{name}: overlay offset-only reference has a different caller or an addend')
                            _, physical_caller = overlay_location(CFG, address[1], org, size)
                            if physical_caller != base:
                                raise ValueError(f'{name}: caller target disagrees with original overlay body')
                            overlay_location(CFG, address[1], address[2])
                        accepted = address[0] in ('FAR', 'OVL') and address[2] == h['off'][0][0]
                    except ValueError as error:
                        problems.append(str(error))
                if accepted:
                    if address[0] == 'OVL': note(name, address, h['off'][0][1])
                    print(f'{name}: offset {h["off"][0][0]:04X} referenced on its own, agreeing with {v}')
                else:
                    problems.append(f'{name}: only the offset half of its far address is referenced' +
                                    (f', and symbols.tsv has {v}' if v else ''))
    # _BSS has no bytes to compare; every reference must agree on one base
    if len(bss_bases) > 1:
        problems.append('_BSS references disagree on the base: ' + ', '.join(f'DS:{b:X}' for b in sorted(bss_bases)))
    elif bss_bases:
        print(f'_BSS: {segs[bss][2]} bytes at DS:{min(bss_bases):X}')
    if len(cs_values) > 1: problems.append(f'own segment referenced as {sorted(cs_values)}')
    data = bytes(o['data'][datas])
    if len(data_bases) > 1:
        problems.append('_DATA references disagree on the base: ' + ', '.join(f'DS:{b:X}' for b in sorted(data_bases)))
    db = None
    if len(data_bases) == 1:
        db = min(data_bases)
    elif data and not data_bases:
        # nothing in the file's code refers to its _DATA (an assembly module whose code runs
        # with DS on its own segment): its publics in symbols.tsv place it, and must agree
        known = read_symbols()
        bases = {(int(known[n][3:], 16) - off) & 0xFFFF for n, (si, off) in o['pubs'].items()
                 if si == datas and known.get(n, '').startswith('DS:')}
        if len(bases) == 1: db = min(bases); print(f'_DATA placed by its publics in symbols.tsv')
        elif datafx or bases: problems.append('_DATA: nothing in the code locates it, and its publics in symbols.tsv ' +
                                              ('disagree' if bases else 'do not place it'))
    if db is not None:
        theirs = exe[DS_FILE + db:DS_FILE + db + len(data)]
        # fixups inside the data (pointer tables): check each, then leave its bytes out
        masked = set()
        para = (base - org - hdr) // 16
        for f in datafx:
            at = f['off']; loc = f['loc']; tm, ti = f['target']; add = word(data, at) + f['disp']
            here = DS_FILE + db + at
            masked.update(range(at, at + {0: 1, 1: 2, 2: 2, 3: 4, 4: 1, 5: 2}[loc]))
            if tm == 0 and ti == code and loc == 3:
                internal += 1
                if base < mzend and (word(exe, here) != add + org or word(exe, here + 2) != para):
                    problems.append(f'_DATA+{at:X}: pointer to {word(exe, here + 2):04X}:{word(exe, here):04X}, expected {para:04X}:{add + org:04X}')
                elif base >= mzend:
                    entries.setdefault(add + org, set()).add(word(exe, here))
            elif tm == 0 and ti == bss and loc in (1, 5):
                # a pointer into this file's own _BSS, as in `p = &static_var`
                internal += 1
                if len(bss_bases) != 1:
                    problems.append(f'_DATA+{at:X}: points into _BSS, whose base is not known')
                elif word(exe, here) != (min(bss_bases) + add) & 0xFFFF:
                    problems.append(f'_DATA+{at:X}: points to DS:{word(exe, here):X}, expected DS:{(min(bss_bases) + add) & 0xFFFF:X}')
            elif tm == 0 and ti == datas and loc in (1, 5):
                internal += 1
                if word(exe, here) != (db + add) & 0xFFFF:
                    problems.append(f'_DATA+{at:X}: points to DS:{word(exe, here):X}, expected DS:{(db + add) & 0xFFFF:X}')
            elif tm == 0 and ti in fars and loc in (1, 2, 3, 5):
                # a pointer into this file's own far data: placed with the code's references
                internal += 1
                r = farref.setdefault(ti, {})
                if loc in (1, 3, 5): r.setdefault('off', []).append(((word(exe, here) - add) & 0xFFFF, f'_DATA+{at:X}'))
                if loc in (2, 3): r.setdefault('seg', []).append((word(exe, here + (2 if loc == 3 else 0)), f'_DATA+{at:X}'))
            elif tm == 2 and loc == 3:
                # DGROUP is resident, so segment words in data are real paragraphs
                note(o['ext'][ti], ('FAR', word(exe, here + 2), (word(exe, here) - add) & 0xFFFF), at)
            elif tm == 2 and loc in (1, 5):
                note(o['ext'][ti], ('DS', (word(exe, here) - add) & 0xFFFF), at)
            else:
                problems.append(f'_DATA+{at:X}: fixup to {target_name(o, f["target"])} loc {loc} not handled')
        bad = [k for k in range(len(data)) if k not in masked and (k >= len(theirs) or data[k] != theirs[k])]
        if bad:
            problems.append(f'_DATA at DS:{db:X} differs at {len(bad)} bytes:\n    obj {data.hex(" ")}\n    exe {theirs.hex(" ")}')
        else:
            print(f'_DATA: {len(data)} bytes match at DS:{db:X}' + (f' ({len(datafx)} pointers checked)' if datafx else ''))

    farat = place_far(o, fars, farref, farfx, exe, hdr, word, note, problems)

    for fn, e in sorted(entries.items()):
        name = next((r[0] for r in rows if r[2] + org == fn), f'+{fn:X}')
        if len(e) > 1: problems.append(f'{name} has several overlay entries: {sorted(e)}')
        else: print(f'overlay entry: {name} at stub +{min(e):X}')
    flat = [min(e) for e in entries.values()]
    if len(set(flat)) < len(flat): problems.append('two functions share an overlay entry')
    byval = {}
    for n, (v, _) in syms.items(): byval.setdefault(v, []).append(n)
    for v, ns in byval.items():
        if len(ns) > 1: problems.append(f'{", ".join(ns)} all resolve to {fmt(v)}')

    if segonly:
        known = read_symbols()
        for name, para in segonly.items():
            v = known.get(name) or (fmt(syms[name][0]) if name in syms else None)
            expected_para = None
            if v:
                try:
                    address = parse_address(v)
                    expected_para = (overlay_location(CFG, address[1], address[2])[0]
                                     if address[0] == 'OVL' else
                                     CFG.dgroup_para if address[0] == 'DS' else address[1])
                except ValueError as error:
                    problems.append(str(error))
                    continue
            if expected_para is not None and expected_para != para:
                problems.append(f'{name}: segment {para:04X} referenced, but its address is {v}')
            else:
                print(f'{name}: segment {para:04X} referenced on its own' + (f', agreeing with {v}' if v else ''))
    # the file's own publics, where the EXE has them
    pubs = {}
    para = (base - org - hdr) // 16
    stub = None
    if base >= mzend and CFG.overlay_index(seg) is not None:
        # the overlay's stub: segment table entry N for overlay N, entries of INT 3Fh, offset, 0 after 0x20
        n = CFG.overlay_index(seg); sp, smax = struct.unpack_from('<HH', exe, segtab + 8 * n)
        lo = hdr + sp * 16
        stub = (sp, {word(exe, lo + e + 2): e for e in range(0x20, smax, 5) if exe[lo + e:lo + e + 2] == b'\xcd\x3f'})
    for name, (si, off) in o['pubs'].items():
        if si == datas and db is not None: pubs[name] = ('DS', (db + off) & 0xFFFF)
        elif si == bss and len(bss_bases) == 1: pubs[name] = ('DS', (min(bss_bases) + off) & 0xFFFF)
        elif si in farat: pubs[name] = ('FAR', farat[si][0], farat[si][1] + off)
        elif si == code and base < mzend: pubs[name] = ('FAR', para, org + off)
        elif si == code and stub:
            if off + org in stub[1]: pubs[name] = ('FAR', stub[0], stub[1][off + org])
            else: problems.append(f'{name} is public, but the overlay stub has no entry for +{off + org:X}: the original had it static')
    print(f'{len(o["fixups"])} fixups: {len(syms)} externs resolved, {internal} internal references, {len(pubs)} publics placed')
    # an overlay's stub entries follow the order the compiler lists its publics, which for
    # Turbo C is set by a hash of each name (the profile's bssorder.py): so the names of an
    # overlay's functions must give the EXE's stub order, which the masked compare cannot see
    if stub:
        order = sorted((stub[1][off + org], name) for name, (si, off) in o['pubs'].items() if si == code and off + org in stub[1])
        ko = stub_key()
        if ko and len(order) > 1:
            ks = [(e, n, ko(n[1:] if n.startswith('_') else n)) for e, n in order]   # the compiler's one underscore
            for (e1, n1, k1), (e2, n2, k2) in zip(ks, ks[1:]):
                if k2 < k1:
                    problems.append(f'stub order: {n2} (key {k2}, stub +{e2:X}) follows {n1} (key {k1}, stub +{e1:X}), '
                                    f'but the compiler lists publics by ascending key: one of the two names is wrong')
            # the order itself, ties of equal keys included: TLINK numbers the stub entries from
            # the last public the object lists, so the object's own order, reversed, is the EXE's
            listed = [n for n, (si, off) in o['pubs'].items() if si == code and off + org in stub[1]][::-1]
            want = [n for _, n in order]
            if not any(p.startswith('stub order') for p in problems) and listed != want:
                i = next(i for i, (a, b) in enumerate(zip(listed, want)) if a != b)
                problems.append(f'stub order: the object lists {listed[i]} where the stub has {want[i]} (stub entry {i + 1} '
                                f'of {len(want)}): their keys are equal, and the compiler breaks the tie by the order '
                                f'it first sees the names (the first seen is listed last, so it takes the earlier stub entry): '
                                f'name {want[i]} before {listed[i]}, with a prototype at the top of the file if need be')
            if not any(p.startswith('stub order') for p in problems): print(f'stub order: {len(ks)} entries agree with their names')
    # without --update, still check the names against symbols.tsv, read-only
    if code_frame_resident_ds:
        try:
            update(syms, pubs, problems, write=False)
            if not problems and on_names: on_names(dict(syms))
            if not problems and '--update' in a:
                stable(CFG, code_symbol_token)
                update(syms, pubs, problems, write=True)
        except (ValueError,IndexError,KeyError,TypeError,OSError,struct.error) as error:
            problems.append('resident own-CODE final validation: '+str(error))
    elif bounded_resident_prefix:
        if not problems:
            try:
                from bounded_prefix import prove, namespace_preflight
                if pending_cs is None: raise ValueError('missing typed pending CS reference')
                identity, witness = prove(bounded_transaction, bounded_namespace, syms,
                                          pubs, problems, CFG.dgroup_para)
                note(pending_cs.name, identity, pending_cs.offset)
                problems.extend(namespace_preflight(bounded_namespace, syms, pubs))
                if not problems and on_names: on_names(dict(syms))
            except (ValueError,IndexError,KeyError,TypeError,OSError,ImportError,struct.error) as error:
                problems.append('bounded resident prefix: '+str(error))
        if not problems and '--update' in a:
            try:
                require_symbol_token(CFG, bounded_symbol_token)
                update(syms, pubs, problems, write=True)
            except (ValueError,OSError) as error:
                problems.append('bounded resident prefix: '+str(error))
    else:
        if on_names: on_names(syms)
        update(syms, pubs, problems, write='--update' in a)
    for p in problems: print('PROBLEM', p)
    print('-- fixups and data verified' if not problems else f'-- {len(problems)} problems')
    return 1 if problems else 0

def place_far(o, fars, farref, farfx, exe, hdr, word, note, problems):
    """Place each of the file's own far segments and compare its bytes with the EXE's.
    Returns {segment index: (paragraph, offset)}. References give the address; a segment the
    file never refers to is placed by its publics in symbols.tsv. Far data is resident, so
    segment words are real paragraphs, and fixups inside it are checked and masked."""
    segs = o['segs']; at = {}
    known = read_symbols()
    for si in sorted(fars):
        name = segs[si][0]; r = farref.get(si, {}); got = {}
        for half in ('seg', 'off'):
            vals = sorted({v for v, _ in r.get(half, [])})
            if len(vals) > 1:
                problems.append(f'{name}: {half} references differ: ' + ', '.join(
                    f'{v:04X} at ' + '/'.join(w for u, w in r[half] if u == v) for v in vals))
            elif vals: got[half] = vals[0]
        # what symbols.tsv says, through the segment's publics
        tsv = set()
        for n, (psi, off) in o['pubs'].items():
            v = known.get(n)
            if psi == si and v:
                address = parse_address(v)
                if address[0] == 'OVL':
                    problems.append(f'{n}: overlay body address cannot place resident far data')
                elif address[0] == 'FAR':
                    _, p, q = address
                    tsv.add((p, (q - off) & 0xFFFF))
        piece = FARHINT.get(name)
        if piece:
            # a piece of a far segment that several modules contribute to, placed by the
            # target table ('# far NAME PPPP:OOOO'); its bytes are compared like any other
            para, off = piece; how = 'placed by the target table'
        elif 'seg' in got:
            para = got['seg']; off = got.get('off')
            if off is None:
                # only the segment is used: its offset is the segment's start in that paragraph
                offs = {q for p, q in tsv if p == para}
                off = min(offs) if len(offs) == 1 else 0
            how = 'referenced'
        elif len(tsv) == 1:
            para, off = min(tsv); how = 'placed by symbols.tsv'
        else:
            if 'off' in got: problems.append(f'{name}: only the offset half of its address is referenced')
            elif tsv: problems.append(f'{name}: its publics disagree in symbols.tsv')
            elif segs[si][2]: problems.append(f'{name}: {segs[si][2]} bytes, but nothing places it (no reference, no public in symbols.tsv)')
            continue
        at[si] = (para, off)
        data = bytes(o['data'][si]); lo = hdr + para * 16 + off; theirs = exe[lo:lo + len(data)]
        masked = set()
        for f in farfx:
            if f['seg'] != si: continue
            k = f['off']; loc = f['loc']; tm, ti = f['target']; add = word(data, k) + f['disp']; here = lo + k
            masked.update(range(k, k + {0: 1, 1: 2, 2: 2, 3: 4, 4: 1, 5: 2}[loc]))
            if tm == 2 and loc == 3:
                note(o['ext'][ti], ('FAR', word(exe, here + 2), (word(exe, here) - add) & 0xFFFF), k)
            else:
                problems.append(f'{name}+{k:X}: fixup to {target_name(o, f["target"])} loc {loc} not handled')
        # TLINK lists every segment in the overlay manager's segment table ({paragraph, end,
        # flags, start}): the EXE's segment there must be exactly this long
        cblp, cp = struct.unpack_from('<HH', exe, 2); mzend = (cp - 1) * 512 + cblp
        segtab, nseg = struct.unpack_from('<II', exe, mzend + 8)
        ent = [struct.unpack_from('<4H', exe, segtab + 8 * i) for i in range(nseg)]
        # (empty segments share a paragraph with the next one: they are left out)
        ext = [e[1] - e[3] for e in ent if e[0] == para and e[3] == off and e[1] != 0xFFFF and e[1] > e[3]]
        if piece:
            # the piece must lie inside one segment of the table
            if not any(e[0] == para and e[1] != 0xFFFF and e[3] <= off and off + len(data) <= e[1] for e in ent):
                problems.append(f'{name}: {len(data)} bytes at {para:04X}:{off:04X} are not inside a segment of the EXE\'s segment table')
        elif data and (len(ext) != 1 or ext[0] != len(data)):
            problems.append(f'{name}: {len(data)} bytes, but the EXE\'s segment at {para:04X}:{off:04X} is ' +
                            (f'{ext[0]:X}h bytes' if len(ext) == 1 else 'not in its segment table'))
        bad = [k for k in range(len(data)) if k not in masked and (k >= len(theirs) or data[k] != theirs[k])]
        if bad:
            problems.append(f'{name} at {para:04X}:{off:04X} differs at {len(bad)} bytes, first at +{bad[0]:X}')
        else:
            print(f'{name}: {len(data)} bytes match at {para:04X}:{off:04X} ({how})')
    return at

def fmt(v):
    return format_address(v)

FARHINT = {}    # far segment name -> (paragraph, offset), from '# far' lines of the target table

def update(syms, pubs, problems, write=True):
    path = CFG.symbols
    cur = CFG.load_symbols()
    known = {n: v for n, (v, _) in cur.items()}; label = {n: l for n, (_, l) in cur.items()}
    for n, (v, _) in syms.items():
        if n in known and known[n] != fmt(v):
            problems.append(f'{n}: symbols.tsv has {known[n]}, this file gives {fmt(v)}')
        known[n] = fmt(v)
    # this file's publics: the same rules, and merged too
    for n, v in pubs.items():
        if n in known and known[n] != fmt(v):
            problems.append(f'{n}: symbols.tsv has {known[n]}, but this file defines it at {fmt(v)}')
        if n not in syms: syms = dict(syms); syms[n] = (v, None)
        known[n] = fmt(v)
    # one address, one name, across every file merged so far
    byaddr = {}
    for n, v in known.items(): byaddr.setdefault(v, []).append(n)
    for n, (v, _) in syms.items():
        others = [m for m in byaddr[fmt(v)] if m != n]
        if others: problems.append(f'{n} is at {fmt(v)}, which symbols.tsv already calls {", ".join(others)}')
    # names found in the sibling build's symbol table are the originals; the rest are provisional
    orig = CFG.original_names()
    lim = CFG.profile.get('c', {}).get('name_limit', 32)
    if not write: return
    if problems:
        print('symbols.tsv not updated: fix the problems first'); return
    with open(path, 'w') as f:
        f.write('# name\taddress\tname source\n# address: DS:offset for near data, segment:offset for far code (load-relative paragraphs)\n')
        for n in sorted(known, key=str.lower):
            # Turbo C keeps 32 characters of an identifier
            # (a C library name marked 'library' by hand keeps that mark)
            src = CFG.original_label if n in orig or len(n) >= lim and any(o.startswith(n) for o in orig) else ('library' if CFG.is_library_name(n) or label.get(n) == 'library' else 'provisional')
            f.write(f'{n}\t{known[n]}\t{src}\n')

if __name__ == '__main__':
    sys.exit(main())
