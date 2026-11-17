"""Bounded OMF32 to LE range comparisons with exact relocation targets."""
from le import LEImage
from omf32 import Module


class MatchError(ValueError):
    pass


def validate_group_frames(module, image, placements, group_frames):
    """Bind named OMF frames explicitly to LE object bases, never by name.

    Only measured absolute32 and same-object relative32 cases are supported.
    A local group contribution need not begin at zero; its *linked frame* must.
    """
    result = {}
    if group_frames is not None and not isinstance(group_frames, dict):
        raise MatchError('invalid group frame bindings')
    for key, value in (group_frames or {}).items():
        try:
            number = int(key)
            obj, base = value
        except (TypeError, ValueError) as error:
            raise MatchError('invalid group frame binding') from error
        if (not (type(key) is int or (type(key) is str and key.isascii()
                  and key.isdecimal() and str(number) == key)) or number in result or
                not 1 <= number <= len(module.groups) or
                type(obj) is not int or type(base) is not int or base != 0):
            raise MatchError('invalid group frame index/object/base')
        try:
            image._object(obj)
        except ValueError as error:
            raise MatchError('invalid group frame object') from error
        name, members = module.groups[number-1]
        if name == 'FLAT':
            raise MatchError('FLAT does not require a group frame binding')
        for member in members:
            if module._segment(member).size:
                if member not in placements or placements[member][0] != obj:
                    raise MatchError('group frame member is unplaced or in another object')
        result[number] = (obj, base)
    return result


def is_alignment_padding(data: bytes) -> bool:
    """Recognize measured 386/486/Pentium Watcom no-op fill sequences."""
    if len(data)>15:
        return False
    nops=tuple(bytes.fromhex(s) for s in (
        '90','8bc0','8bd2','8d4000','8d5200','8d442000',
        '8d8000000000','8d9200000000','8d842000000000','8d942200000000'))
    while data:
        instruction=next((op for op in nops if data.startswith(op)),None)
        if instruction is None:
            return False
        data=data[len(instruction):]
    return True


def match_function(module: Module, symbol: str, image: LEImage,
                   target_object: int, target_offset: int, size: int,
                   symbols: dict[str,tuple[int,int]],
                   placements: dict[int,tuple[int,int]] | None = None,
                   *, group_frames: dict[int,tuple[int,int]] | None = None) -> dict:
    placements = dict(placements or {})
    segment, start = module.publics[symbol]
    original = module.segments[segment-1].data
    if size <= 0 or start+size > len(original):
        raise MatchError('function range outside object code')
    later=[off for seg,off in module.publics.values() if seg==segment and off>start]
    end=min(later,default=len(original))
    if start+size>end:
        raise MatchError('function extent crosses the next public')
    padding=bytes(original[start+size:end])
    if (padding and not later) or not is_alignment_padding(padding) or (padding and end%4):
        raise MatchError('function extent leaves unverified instructions')
    return compare_range(module,segment,start,image,target_object,target_offset,size,
                         symbols,placements,label=symbol,group_frames=group_frames)


def compare_range(module: Module, segment: int, start: int, image: LEImage,
                  target_object: int, target_offset: int, size: int,
                  symbols: dict[str,tuple[int,int]],
                  placements: dict[int,tuple[int,int]] | None=None,
                  *, label: str='range',
                  group_frames: dict[int,tuple[int,int]] | None=None) -> dict:
    """Compare an explicit initialized range; completeness is the caller's duty."""
    placements=dict(placements or {})
    original=module._segment(segment).data
    if size<=0 or start<0 or start+size>len(original):
        raise MatchError('comparison range outside object segment')
    target = image._object(target_object)
    if target_offset < 0 or target_offset+size > target.size:
        raise MatchError('function target range outside LE object')
    # Local code references resolve relative to the explicitly bound function.
    inferred = (target_object,target_offset-start)
    if segment in placements and placements[segment] != inferred:
        raise MatchError('inconsistent code segment placement')
    placements[segment] = inferred
    frames=validate_group_frames(module,image,placements,group_frames)
    data = bytearray(original[start:start+size])
    target_fixups = {}
    for f in image.read_fixups():
        if f.source_object != target_object:
            continue
        if f.source_offset < target_offset+size and f.source_offset+f.width > target_offset:
            if f.source_type==8 and f.width!=4:
                raise MatchError('unsupported relative loader width')
            if not target_offset <= f.source_offset or f.source_offset+f.width > target_offset+size:
                raise MatchError('LE fixup crosses function boundary')
            key=f.source_offset-target_offset
            if key in target_fixups and target_fixups[key] != (
                    f.source_type,f.target_object,f.target_offset,f.alias):
                raise MatchError('conflicting LE fixups')
            target_fixups[key]=(f.source_type,f.target_object,f.target_offset,f.alias)
    accounted,verified = set(),0
    occupied=set()
    for fix in module.fixups:
        if fix.segment != segment or fix.offset >= start+size or fix.offset+fix.width <= start:
            continue
        local = fix.offset-start
        if local < 0 or local+fix.width > size:
            raise MatchError('OMF fixup crosses function boundary')
        span=set(range(local,local+fix.width))
        if occupied & span:
            raise MatchError('overlapping OMF fixups')
        occupied.update(span)
        if fix.width != 4 or fix.location != 9:
            raise MatchError('unsupported OMF fixup width/location')
        flat_frame = (fix.frame_method==1 and fix.frame is not None
                      and 1 <= fix.frame <= len(module.groups)
                      and module.groups[fix.frame-1][0]=='FLAT')
        # TIS OMF F5 takes its frame from the target's index. Explicit F0/T0
        # or F2/T2 with the identical index specify that same frame (MASM).
        target_frame = (fix.frame_method in (0,2)
                        and fix.frame_method==fix.target_method
                        and fix.frame==fix.target)
        bound_group = fix.frame_method==1 and fix.frame in frames
        # Measured WASM/EMU387 form: an external REL32 uses the source CODE
        # segment as its explicit F0 frame. A common frame cancels only after
        # checking that source/frame and resolved target share the LE object.
        # Keep unmeasured nonzero inline addends/displacements unsupported.
        source_code_frame = (module.format=='standard-omf' and fix.relative
                             and fix.frame_method==0 and fix.frame==segment
                             and fix.target_method==2
                             and 1 <= fix.target <= len(module.externals)
                             and fix.displacement==0
                             and 0 <= inferred[1]
                             and inferred[1]+module._segment(segment).size <= target.size
                             and module._segment(segment).class_name.upper()=='CODE'
                             and module._segment(segment).attributes & 1
                             and int.from_bytes(data[local:local+4],'little')==0)
        if (not flat_frame and not target_frame and fix.frame_method != 5
                and not bound_group and not source_code_frame):
            raise MatchError('unsupported OMF frame semantics')
        if bound_group and (fix.target_method==0 and
                fix.target not in module.groups[fix.frame-1][1]):
            raise MatchError('group frame requires a member target for an internal reference')
        if fix.target_method == 2:
            name=module.externals[fix.target-1]
            if name not in symbols:
                raise MatchError(f'unresolved external {name}')
            obj,offset=symbols[name]
        elif fix.target_method == 0:
            if fix.target not in placements:
                raise MatchError(f'unresolved segment {fix.target}')
            obj,offset=placements[fix.target]
        else:
            raise MatchError('unsupported OMF target method')
        if bound_group and obj != frames[fix.frame][0]:
            raise MatchError('resolved target is outside the bound group frame object')
        if source_code_frame and obj != placements[segment][0]:
            raise MatchError('source CODE frame requires a same-object external target')
        addend=int.from_bytes(data[local:local+4],'little')
        offset=(offset+fix.displacement+addend)&0xffffffff
        dest=image._object(obj)
        if not 0 <= offset <= dest.size:
            raise MatchError('resolved target outside LE object')
        if fix.relative:
            if obj != target_object:
                if (fix.target_method!=2 or fix.displacement!=0 or
                    not (flat_frame or fix.frame_method==5)):
                    raise MatchError('cross-object relative fixup unsupported')
                expected=(8,obj,offset,False)
                if target_fixups.get(local)!=expected:
                    raise MatchError('cross-object relative loader target mismatch')
                accounted.add(local)
                # Checked native WLINK output preserves the original LEDATA
                # addend here, even when the linked target offset differs.
                # Keep every physical byte in the comparison; do not mask it.
                value=addend
            else:
                if local in target_fixups:
                    raise MatchError('unexpected loader fixup on same-object relative reference')
                value=offset-(target_offset+local+4)
        else:
            expected=(7,obj,offset,False)
            if target_fixups.get(local) != expected:
                raise MatchError(f'LE fixup target mismatch at +{local:#x}: '
                                 f'{target_fixups.get(local)} != {expected}')
            accounted.add(local)
            # The file stores object-relative target offsets; DOS/4GW adds
            # the loaded object base. Do not add preferred bases here.
            value=offset
        data[local:local+4]=(value&0xffffffff).to_bytes(4,'little')
        verified+=1
    if set(target_fixups)-accounted:
        raise MatchError('unaccounted target LE fixups')
    expected=image.object_bytes(target_object)[target_offset:target_offset+size]
    differences=[i for i,(a,b) in enumerate(zip(data,expected)) if a!=b]
    return {'symbol':label,'target_object':target_object,'target_offset':target_offset,
            'compared_bytes':size,'verified_fixups':verified,
            'differing_offsets':differences,'match':not differences}
