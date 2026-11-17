"""Verify every segment of a compiled module at explicit target placements.

This proves module contributions and zero-fill obligations, not the full
executable or original linker selection/order. No reference bytes are copied.
"""
from matching import compare_range,MatchError,validate_group_frames


class ModuleError(ValueError):
    pass


def verify_module(module,image,placements,symbols,*,group_frames=None,logical_origins=None):
    placements = _placements(module, image, placements)
    if not isinstance(symbols, dict) or any(type(name) is not str or not name for name in symbols):
        raise ModuleError('invalid module symbol bindings')
    symbols = {name: _location(image, value) for name, value in symbols.items()}
    if not module.local_publics <= module.publics.keys() or any(
            type(n) is not int or not 1 <= n <= len(module.externals)
            for n in module.local_externals):
        raise ModuleError('invalid module local symbol scope')
    private_externals = {module.externals[n - 1] for n in module.local_externals}
    global_externals = {name for n, name in enumerate(module.externals, 1)
                        if n not in module.local_externals}
    if global_externals & (module.local_publics | private_externals):
        raise ModuleError('ambiguous module local/global external scope')
    # Optional origin evidence changes only the alignment coordinate. Keep the
    # default proof and all byte/fixup/storage checks exactly as before.
    origins,origin_receipts={},[]
    if logical_origins is not None and not (
            type(logical_origins) in (tuple,list) and not logical_origins):
        from logical_origins import bind_logical_origins
        try:
            origins,origin_receipts=bind_logical_origins(module,image,placements,logical_origins)
        except ValueError as error:
            raise ModuleError(str(error)) from error
    placements={int(k):tuple(v) for k,v in placements.items()}
    symbols={k:tuple(v) for k,v in symbols.items()}
    try:
        frames=validate_group_frames(module,image,placements,group_frames)
    except MatchError as error:
        raise ModuleError(str(error)) from error
    ranges=[]
    for number,seg in enumerate(module.segments,1):
        if not seg.size:
            continue
        if number not in placements:
            raise ModuleError(f'unplaced segment {number}: {seg.name}')
        obj,start=placements[number]
        target=image._object(obj)
        alignment={1:1,2:2,3:16,4:256,5:4}.get(seg.attributes>>5)
        if alignment is None:
            raise ModuleError('unsupported SEGDEF alignment')
        coordinate=start-origins[number] if number in origins else target.base+start
        if coordinate%alignment:
            raise ModuleError(f'segment {seg.name} violates SEGDEF alignment')
        if start<0 or start+seg.size>target.size:
            raise ModuleError('segment placement outside target')
        if any(o==obj and start<end and start+seg.size>begin for o,begin,end in ranges):
            raise ModuleError('overlapping segment placements')
        ranges.append((obj,start,start+seg.size))
    for name,(segment,offset) in module.publics.items():
        seg=module._segment(segment)
        if not seg.size:
            continue
        obj,start=placements[segment]
        value=(obj,start+offset)
        if name in symbols and symbols[name]!=value:
            raise ModuleError(f'conflicting binding for public {name}')
        symbols[name]=value
    reports=[]; initialized=zero_fill=fixups=0
    for number,seg in enumerate(module.segments,1):
        if not seg.size:
            continue
        obj,start=placements[number]
        if seg.class_name.upper()=='BSS':
            if any(seg.data) or any(f.segment==number for f in module.fixups):
                raise ModuleError('BSS contains initialized content or fixups')
            if any(image.file_offset(obj,start+i) is not None for i in range(seg.size)):
                raise ModuleError('BSS target range has file backing')
        try:
            report=compare_range(module,number,0,image,obj,start,seg.size,
                                 symbols,placements,label=seg.name,group_frames=frames)
        except MatchError as error:
            raise ModuleError(f'{seg.name}: {error}') from error
        if not report['match']:
            raise ModuleError(f'{seg.name}: differing bytes at {report["differing_offsets"][:20]}')
        if seg.class_name.upper()=='BSS':zero_fill+=seg.size
        else:initialized+=seg.size
        fixups+=report['verified_fixups']
        reports.append(report)
    local_names=module.local_publics | {module.externals[n-1] for n in module.local_externals}
    local_symbols={n:symbols[n] for n in sorted(local_names) if n in symbols}
    result={'verified':True,'initialized_bytes':initialized,'zero_fill_bytes':zero_fill,
            'verified_fixups':fixups,'segments':reports,'publics':symbols,
            'local_symbols':local_symbols,
            'full_executable_rebuilt':False}
    if frames:
        result['group_frames']=frames
    if origin_receipts:
        result['logical_segment_origins']=origin_receipts
    return result


def external_anchor_bindings(proof):
    """Verified reference/export names only; exclude module-local spellings.

    Local symbols remain in the complete contribution proof. A static name
    cannot constrain an unrelated external in another module.
    Older reports must be replayed to acquire explicit scope metadata.
    """
    if proof.get('verified') is not True:
        raise ValueError('unverified cross-module anchor report')
    symbols=proof['publics']
    local=proof.get('local_symbols')
    if not isinstance(local,dict) or any(n not in symbols or tuple(v)!=tuple(symbols[n])
                                         for n,v in local.items()):
        raise ValueError('missing/inconsistent symbol scope metadata')
    return {n:v for n,v in symbols.items() if n not in local}


"""Infer consistent placements from target fixups and direct call operands.

These are constraints to be checked by verify_module, not independent proof
of historical symbol spellings or complete original binary ownership.
"""


class BindingError(ValueError):
    pass


def infer_data_bindings(module,known_symbols):
    """Place relocation-free data from explicit, independent public anchors."""
    if not known_symbols or module.fixups:
        raise BindingError('data-only proof requires anchors and no relocations')
    if any(s.size and s.class_name.upper() not in ('DATA','BSS') for s in module.segments):
        raise BindingError('data-only proof contains a non-data segment')
    placements={};symbols={};constraints=[]
    for name,(obj,target) in known_symbols.items():
        if name not in module.publics:
            raise BindingError('data anchor is not a source public')
        segment,offset=module.publics[name]
        if target<offset or obj<1:
            raise BindingError('invalid data anchor placement')
        placement=(obj,target-offset)
        if segment in placements and placements[segment]!=placement:
            raise BindingError('conflicting data anchor placements')
        placements[segment]=placement;symbols[name]=(obj,target)
        constraints.append({'kind':'independent data public anchor','symbol':name,
                            'source_segment':segment,'source_offset':offset,
                            'target_object':obj,'target_offset':target})
    if any(s.size and n not in placements for n,s in enumerate(module.segments,1)):
        raise BindingError('unanchored nonempty data segment')
    return {'placements':placements,'symbols':symbols,'constraints':constraints}


def infer_bindings(module,image,code_object,code_base,*,known_symbols=None):
    code_segments=[n for n,s in enumerate(module.segments,1) if s.size and s.class_name.upper()=='CODE']
    if len(code_segments)!=1:
        raise BindingError('expected exactly one nonempty CODE segment')
    code=code_segments[0]
    placements={code:(code_object,code_base)}
    symbols={k:tuple(v) for k,v in (known_symbols or {}).items()}
    target=image.object_bytes(code_object)
    source=module.segments[code-1].data
    if code_base<0 or code_base+len(source)>len(target):
        raise BindingError('CODE segment placement outside target')
    le_fixups={}
    for f in image.read_fixups():
        if f.source_type==8 and f.width!=4:
            raise BindingError('unsupported relative loader width')
        key=(f.source_object,f.source_offset)
        value=(f.source_type,f.target_object,f.target_offset,f.alias)
        if key in le_fixups and le_fixups[key]!=value:
            raise BindingError('conflicting target fixups')
        le_fixups[key]=value

    def put(table,key,value):
        if key in table and table[key]!=value:
            raise BindingError(f'conflicting placement/binding for {key}: {table[key]} != {value}')
        table[key]=value

    for name,(seg,off) in module.publics.items():
        if seg==code:
            put(symbols,name,(code_object,code_base+off))
    constraints=[]
    occupied=set()
    for fix in module.fixups:
        if fix.segment!=code:
            continue
        if fix.width!=4 or fix.location!=9:
            raise BindingError('unsupported fixup width/location')
        locations=set(range(fix.offset,fix.offset+fix.width))
        if occupied & locations:
            raise BindingError('overlapping object fixups')
        occupied.update(locations)
        pos=code_base+fix.offset
        addend=int.from_bytes(source[fix.offset:fix.offset+4],'little')+fix.displacement
        loader=le_fixups.get((code_object,pos))
        if fix.relative:
            if loader is not None:
                flat_frame=(fix.frame_method==1 and fix.frame is not None and
                            1<=fix.frame<=len(module.groups) and
                            module.groups[fix.frame-1][0]=='FLAT')
                if (loader[0]!=8 or loader[3] or loader[1]==code_object or
                    fix.target_method!=2 or fix.displacement!=0 or
                    not (flat_frame or fix.frame_method==5)):
                    raise BindingError('unsupported relative loader fixup')
                # Original WLINK retains the input addend in this field;
                # the LE loader replaces it with the final cross-object rel32.
                if int.from_bytes(target[pos:pos+4],'little')!=addend:
                    raise BindingError('stored relative loader addend differs from source')
                _,obj,effective,_=loader
            else:
                obj=code_object
                effective=pos+4+int.from_bytes(target[pos:pos+4],'little',signed=True)
        else:
            if loader is None or loader[0]!=7 or loader[3]:
                raise BindingError(f'missing/unsupported loader fixup at {pos:#x}')
            _,obj,effective,_=loader
            if int.from_bytes(target[pos:pos+4],'little')!=effective:
                raise BindingError('loader target disagrees with stored absolute operand')
        value=(obj,(effective-addend)&0xffffffff)
        if value[1]>image._object(obj).size:
            raise BindingError('inferred target outside object')
        if fix.target_method==2:
            name=module.externals[fix.target-1]
            put(symbols,name,value)
            if name in module.publics:
                seg,off=module.publics[name]
                put(placements,seg,(obj,value[1]-off))
            constraints.append({'source_offset':fix.offset,'symbol':name,'value':value})
        elif fix.target_method==0:
            put(placements,fix.target,value)
            constraints.append({'source_offset':fix.offset,'segment':fix.target,'value':value})
        else:
            raise BindingError('unsupported target method')
    for name,(seg,off) in module.publics.items():
        if seg in placements:
            obj,start=placements[seg]
            put(symbols,name,(obj,start+off))
    return {'placements':placements,'symbols':symbols,'constraints':constraints,
            'unplaced_segments':[n for n,s in enumerate(module.segments,1)
                                 if s.size and n not in placements]}


"""Verify complete emitted objects and their exact combined contribution union.

Objects retain their original segments and relocations. Explicit placements
prove contributions, not historical linker selection/order or executable identity.
"""



def _integer(value):
    return type(value) is int


def _location(image, value):
    if not isinstance(value, (tuple, list)) or len(value) != 2 or not all(
            _integer(n) for n in value):
        raise ModuleError('invalid composite object/offset binding')
    obj, offset = value
    if not 1 <= obj <= len(image.objects) or not 0 <= offset <= image._object(obj).size:
        raise ModuleError('composite binding outside target')
    return obj, offset


def _placements(module, image, mapping):
    if not isinstance(mapping, dict):
        raise ModuleError('invalid composite placements')
    result = {}
    for key, value in mapping.items():
        if _integer(key):
            number = key
        elif isinstance(key, str) and key.isascii() and key.isdecimal() and str(int(key)) == key:
            number = int(key)
        else:
            raise ModuleError('invalid composite segment index')
        if number in result:
            raise ModuleError('duplicate normalized composite segment index')
        result[number] = _location(image, value)
    required = {n for n, seg in enumerate(module.segments, 1) if seg.size}
    if not required or result.keys() != required:
        raise ModuleError('composite placements must cover exactly every nonempty segment')
    return result


def _union(image, ranges):
    checked = []
    for value in ranges:
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            raise ModuleError('invalid composite range obligation')
        obj, start, size, kind = value
        _location(image, (obj, start))
        if not _integer(size) or size <= 0 or kind not in ('initialized', 'BSS'):
            raise ModuleError('invalid composite range size/kind')
        if start + size > image._object(obj).size:
            raise ModuleError('composite range outside target')
        checked.append((obj, start, start + size, kind))
    if not checked:
        raise ModuleError('empty composite range obligations')
    result = []
    for obj, start, end, kind in sorted(checked):
        if result and result[-1][0] == obj:
            previous = result[-1]
            if start < previous[2]:
                raise ModuleError('overlapping composite ranges')
            if start == previous[2] and kind == previous[3]:
                result[-1] = (obj, previous[1], end, kind)
                continue
        result.append((obj, start, end, kind))
    return result


def verify_composite(modules, image, placements, symbols, expected_ranges, *, group_frames=None):
    """Prove all emitted segments, unique exports and exact range obligations.

    Inputs are name-keyed parsed OMF modules and placements. Expected ranges
    are (LE object, offset, byte count, 'initialized' or 'BSS'). Local names
    never become another producer's external anchors. No object is modified.
    """
    if not isinstance(modules, dict) or not modules or any(
            not isinstance(name, str) or not name for name in modules):
        raise ModuleError('invalid composite producer set')
    if not isinstance(placements, dict) or placements.keys() != modules.keys():
        raise ModuleError('mismatched composite producer placements')
    frames = {} if group_frames is None else group_frames
    if not isinstance(frames, dict) or not frames.keys() <= modules.keys():
        raise ModuleError('unknown composite group-frame producer')
    if not isinstance(symbols, dict) or any(
            not isinstance(name, str) or not name for name in symbols):
        raise ModuleError('invalid composite external bindings')
    shared = {name: _location(image, value) for name, value in symbols.items()}
    normalized = {}
    emitted = []
    exports = {}
    private_names = set()
    for name, module in modules.items():
        places = normalized[name] = _placements(module, image, placements[name])
        if not module.local_publics <= module.publics.keys():
            raise ModuleError('invalid composite local public scope')
        if any(not _integer(n) or not 1 <= n <= len(module.externals)
               for n in module.local_externals):
            raise ModuleError('invalid composite local external scope')
        private_names.update(module.local_publics)
        local_external_names = {module.externals[n - 1] for n in module.local_externals}
        global_external_names = {n for i, n in enumerate(module.externals, 1)
                                 if i not in module.local_externals}
        if global_external_names & (module.local_publics | local_external_names):
            raise ModuleError('ambiguous composite local/global external scope')
        private_names.update(local_external_names)
        for number, seg in enumerate(module.segments, 1):
            if not seg.size:
                continue
            obj, start = places[number]
            emitted.append((obj, start, seg.size,
                            'BSS' if seg.class_name.upper() == 'BSS' else 'initialized'))
        for public, (segment, offset) in module.publics.items():
            if segment not in places or not _integer(offset) or not (
                    0 <= offset <= module.segments[segment - 1].size):
                raise ModuleError('public outside composite contribution')
            if public in module.local_publics:
                continue
            if public in exports:
                raise ModuleError(f'duplicate composite public export: {public}')
            obj, start = places[segment]
            value = (obj, start + offset)
            if public in shared and shared[public] != value:
                raise ModuleError(f'conflicting composite public binding: {public}')
            exports[public] = value
    if (shared.keys() & private_names) - exports.keys():
        raise ModuleError('private/local symbol cannot be a composite external anchor')
    actual = _union(image, emitted)
    expected = _union(image, expected_ranges)
    if actual != expected:
        raise ModuleError('composite emitted union differs from complete range obligations')
    shared.update(exports)
    proofs = {}
    for name, module in modules.items():
        private_externals = {module.externals[n - 1] for n in module.local_externals}
        if not private_externals <= module.local_publics:
            raise ModuleError('unresolved composite private/local external')
        private = module.local_publics | private_externals
        bindings = {n: value for n, value in shared.items() if n not in private}
        proofs[name] = verify_module(module, image, normalized[name], bindings,
                                     group_frames=frames.get(name))
    return {
        'verified': True,
        'initialized_bytes': sum(p['initialized_bytes'] for p in proofs.values()),
        'zero_fill_bytes': sum(p['zero_fill_bytes'] for p in proofs.values()),
        'verified_fixups': sum(p['verified_fixups'] for p in proofs.values()),
        'components': proofs,
        'publics': shared,
        'local_symbols': {},
        'ranges': [{'object': obj, 'offset': start, 'size': end - start, 'kind': kind}
                   for obj, start, end, kind in actual],
        'unique_public_definitions': True,
        'complete_emitted_union_verified': True,
        'full_executable_rebuilt': False,
    }
