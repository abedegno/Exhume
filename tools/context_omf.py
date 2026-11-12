"""Deliberately narrow BC20 16-bit compiler-context splitter.

No binary/metadata discovery: caller supplies reviewed sizes and exact calls.
Unsupported records, threads, owned DATA, and inferred references fail closed.
"""
from dataclasses import dataclass
import struct
import re
from iced_x86 import Decoder, FlowControl, OpKind, Mnemonic, Code, Register


class SplitError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise SplitError(message)


def u16(data, pos):
    require(pos+2<=len(data), 'truncated word')
    return struct.unpack_from('<H',data,pos)[0]


def read_index(data, pos):
    require(pos<len(data), 'truncated OMF index')
    first=data[pos];pos+=1
    if first&0x80:
        require(pos<len(data), 'truncated two-byte OMF index')
        return ((first&127)<<8)|data[pos],pos+1
    return first,pos


def index(value):
    require(0<=value<=0x7fff, 'OMF index out of range')
    return bytes([value]) if value<128 else bytes([0x80|(value>>8),value&255])


def make_record(kind, payload):
    require(len(payload)<65535, 'record too large')
    head=bytes([kind])+struct.pack('<H',len(payload)+1)+payload
    return head+bytes([-sum(head)&255])


@dataclass(frozen=True)
class Record:
    kind:int
    payload:bytes
    raw:bytes


def read_records(raw):
    result=[];pos=0;ended=False
    while pos<len(raw):
        require(pos+3<=len(raw), 'truncated record header')
        size=u16(raw,pos+1)
        require(size>=1 and pos+3+size<=len(raw), 'truncated record')
        whole=raw[pos:pos+3+size]
        require(sum(whole)&255==0, 'record checksum mismatch')
        require(not ended, 'bytes after MODEND')
        result.append(Record(raw[pos],whole[3:-1],whole))
        ended=raw[pos]==0x8a
        pos+=3+size
    require(ended, 'missing 16-bit MODEND')
    return result


@dataclass(frozen=True)
class ContextCall:
    primary_offset:int  # PUSH CS opcode, followed by E8 rel16
    helper_offset:int=0


@dataclass(frozen=True)
class ContextSwitch:
    jump_offset:int       # primary-relative CS:[BX+disp16] near JMP
    table_offset:int      # primary-relative first trailing table word
    entries:int


@dataclass(frozen=True)
class ContextSparseSwitch:
    """BC20 linear WORD-key search followed by its WORD target array."""
    jump_offset:int
    table_offset:int
    entries:int


@dataclass(frozen=True)
class PrivateHelperEntry:
    """Wrapper-proved accepted static entry; never inferred from missing PUBLIC."""
    name:str
    offset:int
    size:int


@dataclass(frozen=True)
class SplitSpec:
    helper_size:int
    primary_size:int
    helper_public:str
    direct_external:str
    calls:tuple
    primary_code_size:int|None=None
    switches:tuple=()
    helper_code_size:int|None=None
    helper_switches:tuple=()
    private_helper_entry:PrivateHelperEntry|None=None
    helper_sparse_switches:tuple=()


@dataclass(frozen=True)
class Fixup:
    offset:int             # segment-relative, not 10-bit record-relative
    location:int
    segment_relative:bool
    frame_method:int
    frame_index:int|None
    target:int            # EXTDEF index, or reviewed self CODE SEGDEF index
    displacement:int
    target_method:int=2

    @property
    def width(self):
        return {1:2,2:2,3:4}[self.location]


@dataclass
class DataRecord:
    record_number:int
    offset:int
    data:bytes
    fixups:list


@dataclass
class Object:
    records:list
    code_segment:int
    code:bytes
    code_segdef_record:int
    externs:tuple
    groups:tuple
    publics:dict
    fixups:list
    data_records:list


def decode_fixups(payload, data, extern_count, group_count, segment_count, *,
                  group_loadds=False, own_code_frames=False):
    require(type(group_loadds) is bool and type(own_code_frames) is bool,
            "parser capabilities must be exact bools")
    result=[];pos=0
    while pos<len(payload):
        first=payload[pos];pos+=1
        require(first&0x80, 'threaded FIXUPP unsupported')
        require(pos+2<=len(payload), 'truncated FIXUPP')
        local=((first&3)<<8)|payload[pos];pos+=1
        location=(first>>2)&15
        require(location in (1,2,3), 'unsupported relocation location')
        width={1:2,2:2,3:4}[location]
        require(local+width<=len(data.data), 'fixup outside associated record payload')
        fd=payload[pos];pos+=1
        require(not(fd&0x88), 'threaded frame/target unsupported')
        fm=(fd>>4)&7;fi=None
        require(fm in ((0,1,5) if own_code_frames else (1,5)), 'unsupported fixup frame (including internal code frame)')
        if fm==0:
            fi,pos=read_index(payload,pos)
            require(1<=fi<=segment_count,'invalid explicit CODE frame index')
        if fm==1:
            fi,pos=read_index(payload,pos)
            require(1<=fi<=group_count, 'invalid group frame index')
        tm=fd&3
        ti,pos=read_index(payload,pos)
        require(tm in ((0,1,2) if group_loadds else (0,2)), 'nonexternal/nonsegment target fixup unsupported')
        require(1<=ti<=(extern_count if tm==2 else group_count if tm==1 else segment_count), 'invalid fixup target index')
        disp=0
        if not fd&4:
            disp=u16(payload,pos);pos+=2
        if tm==1:
            require(group_loadds and location==2 and bool(first&64)
                    and fm==5 and disp==0,'unsupported group-target _loadds fixup')
        if fm==0:
            require(own_code_frames and tm==2 and location==1 and disp==0,
                    'unsupported explicit CODE-frame fixup')
        if tm==0:
            require(bool(first&64) and disp==0 and
                    ((location==1 and fm==5) or (location==2 and fm==1)),
                    'unsupported internal code fixup form')
        result.append(Fixup(data.offset+local,location,bool(first&64),fm,fi,ti,disp,tm))
    return result


def inspect_object(raw, *, framed_loadds_entries=(), group_loadds_entries=(),
                   own_code_frame_rows=()):
    require(type(group_loadds_entries) is tuple
            and all(type(v) is int for v in group_loadds_entries)
            and group_loadds_entries in ((),(0,)), "invalid group _loadds entries")
    require(type(own_code_frame_rows) is tuple
            and all(type(row) is tuple and len(row)==2
                    and all(type(v) is int for v in row) for row in own_code_frame_rows)
            and own_code_frame_rows in ((),((0,181),)), "invalid own-CODE complete rows")
    # Explicit capability only. A future caller must derive these CODE-relative
    # entries from independently validated complete function rows. Default
    # context splitting retains its original no-local grammar and entry checks.
    require(type(framed_loadds_entries) is tuple
            and all(type(entry) is int for entry in framed_loadds_entries),
            'framed _loadds entries must be an integer tuple')
    require(len(set(framed_loadds_entries)) == len(framed_loadds_entries),
            'duplicate framed _loadds entry')
    used_framed_entries=set()
    records=read_records(raw);names=[''];segments=[];groups=[];externs=[]
    publics={};data_records=[];fixups=[];last=None;segdef_records=[];seen_data=False
    for number,r in enumerate(records):
        kind,p=r.kind,r.payload
        if kind==0x80:
            require(number==0 and p and p[0]+1==len(p), 'unsupported THEADR')
        elif kind==0x88:
            require(len(p)>=2 and p[1] in (0x00,0xa1,0xe8,0xe9,0xea), 'unsupported semantic COMENT')
        elif kind==0x96:
            require(not seen_data, 'late LNAMES unsupported')
            pos=0
            while pos<len(p):
                n=p[pos];pos+=1;require(pos+n<=len(p),'truncated LNAMES')
                names.append(p[pos:pos+n].decode('latin1'));pos+=n
        elif kind==0x98:
            require(not seen_data and len(p)>=6, 'late/truncated SEGDEF')
            require(p[0]>>5 and not(p[0]&3), 'absolute/big/32-bit SEGDEF unsupported')
            sn,pos=read_index(p,3);cn,pos=read_index(p,pos);_,pos=read_index(p,pos)
            require(pos==len(p) and 0<sn<len(names) and 0<cn<len(names), 'bad SEGDEF indices')
            segments.append((names[sn],names[cn],u16(p,1)));segdef_records.append(number)
        elif kind==0x9a:
            require(not seen_data, 'late GRPDEF unsupported')
            name,pos=read_index(p,0);require(0<name<len(names), 'invalid group name')
            members=[]
            while pos<len(p):
                require(p[pos]==0xff,'unsupported group member');pos+=1
                member,pos=read_index(p,pos)
                require(1<=member<=len(segments),'invalid group segment');members.append(member)
            groups.append((names[name],tuple(members)))
        elif kind==0x8c:
            require(not seen_data, 'late EXTDEF unsupported')
            pos=0
            while pos<len(p):
                n=p[pos];pos+=1;require(n and pos+n<=len(p),'truncated EXTDEF')
                name=p[pos:pos+n].decode('latin1');pos+=n;_,pos=read_index(p,pos)
                require(name not in externs,'duplicate EXTDEF identity');externs.append(name)
        elif kind==0x90:
            g,pos=read_index(p,0);seg,pos=read_index(p,pos)
            require(0<=g<=len(groups) and 1<=seg<=len(segments), 'absolute/nonsegment PUBDEF unsupported')
            while pos<len(p):
                n=p[pos];pos+=1;require(n and pos+n<=len(p),'truncated PUBDEF')
                name=p[pos:pos+n].decode('latin1');pos+=n;off=u16(p,pos);pos+=2;_,pos=read_index(p,pos)
                require(name not in publics,'duplicate PUBLIC identity');publics[name]=(seg,off)
        elif kind==0xa0:
            seen_data=True
            seg,pos=read_index(p,0);off=u16(p,pos);pos+=2
            require(1<=seg<=len(segments), 'invalid LEDATA segment')
            require(segments[seg-1][1]=='CODE', 'owned noncode DATA unsupported')
            require(off+len(p)-pos<=segments[seg-1][2], 'LEDATA exceeds SEGDEF length')
            last=DataRecord(number,off,p[pos:],[]);data_records.append(last)
        elif kind==0x9c:
            require(last is not None, 'FIXUPP without associated LEDATA')
            # Only immediately consecutive FIXUPP records may extend a data record.
            require(all(x.kind==0x9c for x in records[last.record_number+1:number]), 'detached FIXUPP')
            fs=decode_fixups(p,last,len(externs),len(groups),len(segments),
                             group_loadds=bool(group_loadds_entries),
                             own_code_frames=bool(own_code_frame_rows));last.fixups.extend(fs);fixups.extend(fs)
        elif kind==0x8a:
            require(p==b'\0','MODEND start address unsupported')
        else:
            raise SplitError(f'unsupported OMF record {kind:02X}')
    require(records[0].kind==0x80, 'missing THEADR')
    code_segments=[n+1 for n,(_,cls,_) in enumerate(segments) if cls=='CODE']
    require(len(code_segments)==1, 'exactly one CODE SEGDEF required')
    code_segment=code_segments[0]
    require(all(cls in ('CODE','DATA','BSS') and (cls=='CODE' or size==0) for _,cls,size in segments), 'owned DATA/BSS or unknown segment class unsupported')
    require(all(code_segment not in members for _,members in groups),'CODE in GRPDEF unsupported')
    size=segments[code_segment-1][2];code=bytearray(size);covered=bytearray(size)
    for data in data_records:
        for off in range(data.offset,data.offset+len(data.data)):
            require(not covered[off], 'overlapping LEDATA records');covered[off]=1
        code[data.offset:data.offset+len(data.data)]=data.data
    require(size and all(covered),'CODE LEDATA gaps/empty code unsupported')
    for name,(seg,off) in publics.items():
        require(seg==code_segment and off<size,'PUBLIC outside code payload')
    require(all(0<=entry<size for entry in framed_loadds_entries),
            'framed _loadds entry outside CODE')
    if group_loadds_entries:
        require(size==181 and tuple(segments)==(('HPALLOCH_TEXT','CODE',181),
                ('_DATA','DATA',0),('_BSS','BSS',0)) and groups==[('DGROUP',(3,2))],
                'group _loadds requires exact unique empty DGROUP')
        gs=[f for f in fixups if f.target_method==1]
        require(len(gs)==1, 'one group _loadds field required')
        f=gs[0]
        require((f.offset,f.location,f.segment_relative,f.frame_method,
                 f.frame_index,f.target,f.displacement)==(8,2,True,5,None,1,0)
                and code[:12]==bytes.fromhex('558bec83ec061eb800008ed8'),
                'group _loadds requires exact zero prologue operand')
        require(not any(other is not f and other.offset<12 and
                        other.offset+other.width>0 for other in fixups),
                'another fixup overlaps group _loadds prologue')
    if own_code_frame_rows:
        from iced_x86 import Decoder,Code
        require(size==181, 'own CODE frame needs full181 row')
        instructions=tuple(Decoder(16,bytes(code),ip=0))
        require(instructions and all(not i.is_invalid for i in instructions)
                and instructions[-1].ip==178 and instructions[-1].next_ip==181
                and instructions[-1].code==Code.RETFW_IMM16
                and instructions[-1].immediate16==2,'invalid complete own-CODE row')
        by_ip={i.ip:i for i in instructions}
        fs=[f for f in fixups if f.frame_method==0]
        require(sorted(f.offset for f in fs)==[13,33,50,81,102,132,145],
                'actual seven own-CODE external fields required')
        for f in fs:
            require(f.frame_index==code_segment and f.target_method==2
                    and f.location==1 and f.displacement==0
                    and u16(code,f.offset)==0,'invalid own-CODE external operand')
            start=f.offset-(3 if f.segment_relative else 1)
            ins=by_ip.get(start)
            require(ins is not None and
                    ((not f.segment_relative and f.offset in (13,33,50,81,145)
                      and ins.code==Code.CALL_REL16 and ins.len==3)
                     or (f.segment_relative and f.offset in (102,132)
                         and ins.code==Code.MOV_SREG_RM16 and ins.len==5
                         and code[start:f.offset]==bytes.fromhex('2e8e1e'))),
                    'own-CODE operand lacks supported whole instruction')
            require(not any(other is not f and other.offset<ins.next_ip
                            and start<other.offset+other.width for other in fixups),
                    'another fixup overlaps own-CODE instruction')
    for f in fixups:
        if f.target_method!=0:continue
        if f.location==1:
            require(f.target==code_segment,'internal target is not self CODE')
        else:
            require(segments[f.target-1]==('_DATA','DATA',0),
                    'DS segment reference must target empty _DATA')
            require(groups[f.frame_index-1][0]=='DGROUP'
                    and f.target in groups[f.frame_index-1][1]
                    and sum(name=='DGROUP' for name,_ in groups)==1,
                    'DS segment reference needs unique owning DGROUP')
            if framed_loadds_entries:
                matches=[]
                for entry in framed_loadds_entries:
                    if code[entry:entry+3]!=bytes.fromhex('55 8b ec'):
                        continue
                    if code[entry+3:entry+5]==bytes.fromhex('4c 4c'):
                        field=entry+7
                    elif (code[entry+3:entry+5]==bytes.fromhex('83 ec')
                          and entry+6<=size and 0<code[entry+5]<0x80
                          and code[entry+5]%2==0):
                        field=entry+8
                    else:
                        continue
                    if (f.offset==field and u16(code,f.offset)==0
                            and code[field-2:field+4]
                                ==bytes.fromhex('1e b8 00 00 8e d8')):
                        matches.append(entry)
                require(len(matches)==1 and matches[0] not in used_framed_entries,
                        'DS segment reference lacks one authorized framed _loadds entry')
                require(not any(other is not f and other.offset<f.offset+4
                                and matches[0]<other.offset+other.width
                                for other in fixups),
                        'another fixup overlaps framed _loadds prologue')
                used_framed_entries.add(matches[0])
            else:
                require(u16(code,f.offset)==0 and f.offset>=5
                        and code[f.offset-5:f.offset+4]==bytes.fromhex('55 8b ec 1e b8 00 00 8e d8'),
                        'DS segment reference requires exact zero-word _loadds prologue')
    require(used_framed_entries==set(framed_loadds_entries),
            'unused framed _loadds entry')
    occupied=set()
    for f in fixups:
        for off in range(f.offset,f.offset+f.width):
            require(off not in occupied,'overlapping fixups');occupied.add(off)
    return Object(records,code_segment,bytes(code),segdef_records[code_segment-1],tuple(externs),tuple(groups),publics,fixups,data_records)


def encode_fixup(f, data_base):
    local=f.offset-data_base
    require(0<=local<=1023,'fixup location exceeds record 10-bit limit')
    head=bytes([0x80|(64 if f.segment_relative else 0)|(f.location<<2)|(local>>8),local&255])
    fd=(f.frame_method<<4)|f.target_method|4
    frame=index(f.frame_index) if f.frame_method in (0,1) else b''
    # Normalize to explicit displacement form only when nonzero; preserving
    # meaning, not original record byte spellings, is the contract.
    if f.displacement:
        fd&=~4
    return head+bytes([fd])+frame+index(f.target)+(struct.pack('<H',f.displacement) if f.displacement else b'')


def dgroup_far_callback(obj, ins):
    """An FF /3 reads a far callback value at a relocated DS operand address.

    Its CODE relocation is OFFSET16 for that address, not POINTER32 for the
    callback value held in DATA. All other indirect call forms stay rejected.
    """
    if (ins.code != Code.CALL_M1616 or ins.op0_kind != OpKind.MEMORY
            or ins.memory_segment != Register.DS
            or ins.memory_base != Register.NONE or ins.memory_index != Register.NONE
            or ins.memory_displ_size != 2):
        return False
    decoder = Decoder(16, obj.code[ins.ip:ins.next_ip], ip=ins.ip)
    decoded = decoder.decode()
    constants = decoder.get_constant_offsets(decoded)
    if constants.displacement_size != 2:
        return False
    word = ins.ip + constants.displacement_offset
    refs = [f for f in obj.fixups if f.target_method == 2 and f.offset == word and f.location == 1
            and f.segment_relative and f.frame_method == 1
            and obj.groups[f.frame_index - 1][0] == 'DGROUP']
    return len(refs) == 1


def helper_near_callback(obj, ins, helper_code_end):
    """Recognize only an unrelocated DS:[SI/DI/BX+disp] helper near CALL.

    This low-level grammar does not prove callback targets or helper identity.
    The production wrapper must still require an accepted helper and its
    per-invocation original-byte match and strict reference proof before it
    returns/publishes any primary. Primary near callbacks remain unsupported.
    """
    return (0 <= ins.ip < ins.next_ip <= helper_code_end
            and ins.code == Code.CALL_RM16 and ins.op0_kind == OpKind.MEMORY
            and ins.memory_segment == Register.DS
            and ins.memory_base in (Register.SI, Register.DI, Register.BX)
            and ins.memory_index == Register.NONE
            and not any(f.offset < ins.next_ip and f.offset + f.width > ins.ip
                        for f in obj.fixups))


def code_regions(plan):
    """Executable ranges and explicit table suffixes inside each payload slice."""
    h=plan.helper_size
    require(all(type(t) is ContextSwitch for t in plan.switches+plan.helper_switches),
            'dense switch descriptor required')
    require(all(type(t) is ContextSparseSwitch for t in plan.helper_sparse_switches),
            'sparse helper descriptor required')
    for group in (plan.helper_switches,plan.helper_sparse_switches):
        require(all(a.table_offset<b.table_offset for a,b in zip(group,group[1:])),
                'helper tables must be in physical placement order')
    helpers=tuple(sorted(plan.helper_switches+plan.helper_sparse_switches,key=lambda t:t.table_offset))
    regions=[]
    for start,size,declared,tables,limit in (
            (0,h,plan.helper_code_size,helpers,2),
            (h,plan.primary_size,plan.primary_code_size,plan.switches,1)):
        code=size if declared is None else declared
        require(type(code) is int and 0<code<=size,'bad code boundary')
        if tables:
            require(declared is not None and len(tables)<=limit,'explicit bounded switch metadata required')
            cursor=code;jumps=set()
            for table in tables:
                require(all(type(v) is int for v in (table.jump_offset,table.table_offset,table.entries)),
                        'switch metadata must be integer offsets/count')
                require(0<=table.jump_offset<code and table.jump_offset not in jumps,
                        'duplicate/outside switch jump')
                require(table.entries>0 and table.table_offset==cursor,'switch tables must form contiguous trailing data')
                cursor+=(4 if type(table) is ContextSparseSwitch else 2)*table.entries
                require(cursor<=size,'switch table exceeds payload')
                jumps.add(table.jump_offset)
            require(cursor==size,'switch tables must cover complete trailing data')
        else:
            require(code==size,'unclaimed trailing table data')
        regions.append((start,start+code,start+size,tables))
    return tuple(regions)


def validate_sparse_guard(obj, instructions, positions, ins, table, start,
                          region_start, code_end, boundaries, internal):
    """Recognize only BC20's contiguous CX-bounded linear WORD search.

    Keys are literal ascending unsigned WORDs; only base/target words relocate.
    Exact original helper proof remains the production wrapper's obligation.
    """
    pos=positions[ins.ip]
    require(pos>=9,'missing sparse search guard')
    guard=instructions[pos-9:pos+1]
    cx,base,load,cmp,equal,inc1,inc2,loop,fallback,jump=guard
    require(cx.ip>=region_start and all(a.next_ip==b.ip for a,b in zip(guard,guard[1:])),
            'noncontiguous sparse search guard')
    require(cx.code==Code.MOV_R16_IMM16 and cx.op0_register==Register.CX
            and cx.immediate16==table.entries,'sparse search count disagrees with keys')
    require(base.code==Code.MOV_R16_IMM16 and base.op0_register==Register.BX
            and base.immediate16==start,'sparse search base disagrees with keys')
    decoder=Decoder(16,obj.code[base.ip:base.next_ip],ip=base.ip)
    decoded=decoder.decode();constants=decoder.get_constant_offsets(decoded)
    word=base.ip+constants.immediate_offset
    require(constants.immediate_size==2 and word in internal,
            'sparse search base lacks exact self CODE fixup')
    require(load.code==Code.MOV_R16_RM16 and load.op0_register==Register.AX
            and load.op1_kind==OpKind.MEMORY and load.memory_segment==Register.CS
            and load.memory_base==Register.BX and load.memory_index==Register.NONE
            and load.memory_displ_size==0,'sparse keys require exact CS:[BX] WORD load')
    require(cmp.code==Code.CMP_R16_RM16 and cmp.op0_register==Register.AX
            and cmp.op1_kind==OpKind.MEMORY and cmp.memory_segment==Register.SS
            and cmp.memory_base==Register.BP and cmp.memory_index==Register.NONE
            and cmp.memory_displ_size==1 and 0xff80<=cmp.memory_displacement<=0xffff,
            'sparse selector requires WORD local BP slot')
    require(equal.mnemonic==Mnemonic.JE and equal.op0_kind==OpKind.NEAR_BRANCH16
            and equal.near_branch_target==jump.ip,'sparse match must enter its dispatch')
    require(all(i.code==Code.INC_R16 and i.op0_register==Register.BX for i in (inc1,inc2)),
            'sparse key cursor must advance exactly two bytes')
    require(loop.code==Code.LOOP_REL8_16_CX and loop.near_branch_target==load.ip,
            'sparse search must use exact bounded CX LOOP')
    require(fallback.mnemonic==Mnemonic.JMP and fallback.op0_kind==OpKind.NEAR_BRANCH16
            and region_start<=fallback.near_branch_target<code_end
            and fallback.near_branch_target in boundaries,'sparse fallback must target retained code')
    require(jump.memory_displacement==2*table.entries,'sparse target displacement must skip literal keys')
    guard_fixups=[f for f in obj.fixups if f.offset<jump.next_ip and f.offset+f.width>cx.ip]
    require(len(guard_fixups)==1 and guard_fixups[0].offset==word
            and guard_fixups[0].target_method==0,'unreviewed sparse guard relocation')
    # This extension admits the one observed BC20 encoding, including widths;
    # ignored prefixes and equivalent alternative instructions are not inferred.
    require([obj.code[i.ip:i.next_ip] for i in (load,inc1,inc2)]
            ==[b'\x2e\x8b\x07',b'\x43',b'\x43']
            and cx.len==3 and base.len==3
            and obj.code[cmp.ip:cmp.ip+2]==b'\x3b\x46' and cmp.len==3
            and equal.code==Code.JE_REL8_16 and equal.len==2
            and loop.len==2 and fallback.code==Code.JMP_REL16 and fallback.len==3
            and obj.code[jump.ip:jump.ip+3]==b'\x2e\xff\xa7' and jump.len==5,
            'unsupported sparse search instruction encoding')
    protected={i.ip for i in guard[1:]}
    require(fallback.near_branch_target not in protected,'sparse fallback bypasses guard')
    allowed={address:None for address in protected}
    allowed[load.ip]=loop.ip;allowed[jump.ip]=equal.ip
    keys=[u16(obj.code,start+2*k) for k in range(table.entries)]
    require(all(a<b for a,b in zip(keys,keys[1:])),'sparse keys must be strictly ascending unique WORDs')
    return {word},protected,allowed


def validate_loadds_entries(obj, starts):
    # Empty DATA SEGMENT16 is a compiler entry protocol, never a self-CODE
    # address. Keep it intact and pin its complete instruction sequence to
    # the actual function region entry (for helpers and the primary).
    for f in obj.fixups:
        if f.target_method==0 and f.location==2:
            require(f.offset-5 in starts,'_loadds prologue must start at region entry')


def validate_switches(obj, instructions, boundaries, regions):
    """Prove each bounded dispatch and the exact global internal-fixup union."""
    by_ip={ins.ip:ins for ins in instructions}
    positions={ins.ip:pos for pos,ins in enumerate(instructions)}
    internal={f.offset:f for f in obj.fixups if f.target_method==0 and f.location==1}
    expected=set();jumps=set();guard_entries={};table_targets=[]
    for region_start,code_end,payload_end,tables in regions:
      for table in tables:
        at=region_start+table.jump_offset;start=region_start+table.table_offset
        require(at in by_ip,'switch jump is not an instruction boundary')
        ins=by_ip[at]
        require(ins.code==Code.JMP_RM16 and ins.op0_kind==OpKind.MEMORY
                and ins.memory_segment==Register.CS and ins.memory_base==Register.BX
                and ins.memory_index==Register.NONE and ins.memory_displ_size==2,
                'unsupported switch dispatch operand')
        if type(table) is ContextSparseSwitch:
            fields,protected,allowed=validate_sparse_guard(obj,instructions,positions,ins,
                                                          table,start,region_start,code_end,boundaries,internal)
            require(not(set(protected)&set(guard_entries)), 'overlapping switch guards')
            expected.update(fields);guard_entries.update(allowed)
            target_start=start+2*table.entries
        else:
            decoder=Decoder(16,obj.code[ins.ip:ins.next_ip],ip=ins.ip)
            decoded=decoder.decode();constants=decoder.get_constant_offsets(decoded)
            require(constants.displacement_size==2,'switch needs a full 16-bit displacement')
            word=ins.ip+constants.displacement_offset
            require(word in internal and u16(obj.code,word)==start,'switch base lacks its exact self CODE table fixup')
            expected.add(word)
            pos=positions[at]
            require(pos>=4,'missing bounded switch guard')
            compare,branch,fallback,shift=instructions[pos-4:pos]
            require(compare.ip>=region_start and compare.next_ip==branch.ip
                    and branch.next_ip==fallback.ip and fallback.next_ip==shift.ip
                    and shift.next_ip==ins.ip,'noncontiguous switch guard')
            require(compare.mnemonic==Mnemonic.CMP and compare.op0_kind==OpKind.REGISTER
                    and compare.op0_register==Register.BX
                    and compare.op1_kind in (OpKind.IMMEDIATE8TO16,OpKind.IMMEDIATE16)
                    and compare.immediate(1)==table.entries-1,'switch bound does not match table extent')
            require(branch.mnemonic==Mnemonic.JBE and branch.op0_kind==OpKind.NEAR_BRANCH16
                    and branch.near_branch_target==shift.ip,'switch bound branch must enter shift')
            require(fallback.mnemonic==Mnemonic.JMP and fallback.flow_control==FlowControl.UNCONDITIONAL_BRANCH
                    and fallback.op0_kind==OpKind.NEAR_BRANCH16
                    and region_start<=fallback.near_branch_target<code_end
                    and fallback.near_branch_target in boundaries,'switch default target is not retained code')
            require(shift.code==Code.SHL_RM16_1 and shift.op0_kind==OpKind.REGISTER
                    and shift.op0_register==Register.BX,'switch index must be scaled by two')
            guard_fixups=[f for f in obj.fixups if f.offset<ins.next_ip
                          and f.offset+f.width>compare.ip]
            require(len(guard_fixups)==1 and guard_fixups[0].offset==word
                    and guard_fixups[0].target_method==0,
                    'switch guard contains an unreviewed relocation')
            protected={branch.ip,fallback.ip,shift.ip,ins.ip}
            require(fallback.near_branch_target not in protected,'switch default bypasses guard')
            require(not(protected & set(guard_entries)), 'overlapping switch guards')
            for address in protected:guard_entries[address]=branch.ip if address==shift.ip else None
            target_start=start
        for field in range(target_start,target_start+2*table.entries,2):
            require(field in internal,'switch table entry lacks self CODE fixup')
            target=u16(obj.code,field)
            require(region_start<=target<code_end and target in boundaries,
                    'table target is not retained owning-region instruction boundary')
            table_targets.append(target);expected.add(field)
        require({f.offset for f in obj.fixups if f.offset<target_start+2*table.entries and f.offset+f.width>start}
                ==set(range(target_start,target_start+2*table.entries,2)),'table fixup coverage is not exact')
        jumps.add(ins.ip)
    require(all(target not in guard_entries for target in table_targets),'table target bypasses switch guard')
    require(all(seg!=obj.code_segment or off not in guard_entries for seg,off in obj.publics.values()),
            'PUBLIC entry bypasses switch guard')
    require(set(internal)==expected,'unproven internal code reference')
    return jumps,guard_entries


def helper_software_interrupt(obj, ins, helper_code_end):
    """Only an unrelocated, complete helper CD21, conservatively returning.

    This is instruction grammar, not evidence of the helper's original identity
    or of DOS service semantics. Mandatory independent helper proof remains.
    """
    return (type(helper_code_end) is int and 0 < helper_code_end <= len(obj.code)
            and ins.code == Code.INT_IMM8 and ins.flow_control == FlowControl.INTERRUPT
            and ins.len == 2 and 0 <= ins.ip < ins.next_ip <= helper_code_end
            and obj.code[ins.ip:ins.next_ip] == b'\xcd\x21'
            and not any(f.offset < ins.next_ip and ins.ip < f.offset + f.width
                        for f in obj.fixups))


def validate_reachable_boundaries(obj, plan, instructions, regions, external_near):
    """Prove neither entry can execute a fallthrough across its code boundary.

    Calls are conservatively assumed to return. Local call targets are also
    traversed; known earlier-helper calls use the independently seeded helper
    entry. Unreachable decoded padding is permitted.
    """
    by_ip={ins.ip:ins for ins in instructions}
    table_edges={start+table.jump_offset:tuple(u16(obj.code,start+table.table_offset+(2*table.entries if type(table) is ContextSparseSwitch else 0)+2*k)
                    for k in range(table.entries)) for start,_,_,tables in regions for table in tables}
    near_kinds=(OpKind.NEAR_BRANCH16,OpKind.NEAR_BRANCH32,OpKind.NEAR_BRANCH64)
    for start,end,_,_ in regions:
        pending=[start];visited=set()
        while pending:
            ip=pending.pop()
            if ip in visited:continue
            require(start<=ip<end and ip in by_ip,'reachable edge crosses code boundary')
            visited.add(ip);ins=by_ip[ip];flow=ins.flow_control;edges=[]
            if flow==FlowControl.RETURN:
                continue
            if flow==FlowControl.UNCONDITIONAL_BRANCH:
                if ip not in external_near:edges.append(ins.near_branch_target)
            elif flow==FlowControl.CONDITIONAL_BRANCH:
                edges.append(ins.next_ip)
                if ip not in external_near:edges.append(ins.near_branch_target)
            elif flow in (FlowControl.CALL,FlowControl.INDIRECT_CALL):
                edges.append(ins.next_ip)
                if flow==FlowControl.CALL and ins.op0_kind in near_kinds and ip not in external_near:
                    target=ins.near_branch_target
                    if start<=target<end:edges.append(target)
                    else:
                        require(start==plan.helper_size and target==0,'reachable call crosses code boundary')
            elif flow==FlowControl.INDIRECT_BRANCH:
                require(ip in table_edges,'reachable indirect branch has no proven edges')
                edges.extend(table_edges[ip])
            elif flow==FlowControl.INTERRUPT and helper_software_interrupt(obj,ins,regions[0][1]):
                edges.append(ins.next_ip)
            else:
                require(flow==FlowControl.NEXT,'unsupported reachable control flow')
                edges.append(ins.next_ip)
            for target in edges:
                require(start<=target<end and target in by_ip,'reachable edge crosses code boundary')
                pending.append(target)


def validate_calls(obj, plan):
    h=plan.helper_size;size=len(obj.code)
    require(0<h<size and plan.primary_size>0 and h+plan.primary_size==size, 'bad slice bounds')
    if plan.private_helper_entry is None:
        require(obj.publics.get(plan.helper_public)==(obj.code_segment,0),'wrong helper public/entry')
    else:
        entry=plan.private_helper_entry
        require(isinstance(entry,PrivateHelperEntry) and entry.name==plan.helper_public
                and type(entry.offset) is int and entry.offset==0
                and type(entry.size) is int and entry.size==h,
                'invalid explicit private helper entry')
        require(not plan.helper_switches and not plan.helper_sparse_switches and plan.helper_code_size is None,
                'private helper tables are unsupported')
        require(plan.helper_public not in obj.publics
                and not any(seg==obj.code_segment and off<h
                            for seg,off in obj.publics.values()),
                'private helper has conflicting CODE PUBLICs')
    require(plan.direct_external and plan.direct_external!=plan.helper_public,'direct body alias must differ from stub public')
    require(plan.direct_external not in obj.publics,'direct body alias collides with PUBLIC')
    require(plan.calls and len(set(c.primary_offset for c in plan.calls))==len(plan.calls),'missing/duplicate call mapping')
    regions=code_regions(plan)
    validate_loadds_entries(obj,{r[0] for r in regions})
    code_end=regions[1][1]
    for f in obj.fixups:
        require(not(f.offset<h<f.offset+f.width),'fixup crosses slice boundary')
    authorized={}
    for c in plan.calls:
        require(c.helper_offset==0,'only reviewed helper entry supported')
        at=h+c.primary_offset
        require(h<=at and at+4<=code_end,'call mapping outside primary code')
        require(obj.code[at:at+2]==b'\x0e\xe8','known call must be PUSH CS/E8')
        dest=(at+4+struct.unpack_from('<h',obj.code,at+2)[0])&65535
        require(dest==c.helper_offset,'wrong callee for known call')
        # Opcode bytes may cross records; the rewritten relative operand
        # and its new FIXUPP must remain within one associated LEDATA.
        require(any(d.offset<=at+2 and at+4<=d.offset+len(d.data) for d in obj.data_records),'known call operand crosses LEDATA record boundary')
        require(not any(f.offset<at+4 and f.offset+f.width>at for f in obj.fixups),'known call overlaps existing fixup')
        authorized[at+1]=at
    instructions=[];boundaries=set()
    for start,end,_,_ in regions:
        for ins in Decoder(16,obj.code[start:end],ip=start):
            require(not ins.is_invalid and ins.next_ip<=end,'invalid/cross-boundary instruction')
            boundaries.add(ins.ip);instructions.append(ins)
    for call,at in authorized.items():
        require(call in boundaries and at in boundaries,'known call not at instruction boundary')
    switch_jumps,guard_entries=validate_switches(obj,instructions,boundaries,regions)
    used=set();external_near=set()
    for ins in instructions:
        if ins.flow_control == FlowControl.INDIRECT_CALL:
            require(dgroup_far_callback(obj, ins)
                    or helper_near_callback(obj, ins, regions[0][1]),
                    'unsupported indirect callback call form or relocation')
            continue
        if ins.flow_control==FlowControl.INDIRECT_BRANCH and ins.ip in switch_jumps:
            continue
        if ins.flow_control==FlowControl.INTERRUPT and helper_software_interrupt(obj,ins,regions[0][1]):
            continue
        require(ins.flow_control not in (FlowControl.INDIRECT_BRANCH,FlowControl.INTERRUPT,FlowControl.EXCEPTION),'indirect/exception control flow unsupported')
        if ins.op0_kind in (OpKind.FAR_BRANCH16,OpKind.FAR_BRANCH32):
            require(ins.mnemonic==Mnemonic.CALL and any(f.offset==ins.ip+1 and f.location==3 and f.segment_relative for f in obj.fixups),'far call/branch requires reviewed external pointer relocation')
            continue
        if ins.op0_kind not in (OpKind.NEAR_BRANCH16,OpKind.NEAR_BRANCH32,OpKind.NEAR_BRANCH64):continue
        decoder=Decoder(16,obj.code[ins.ip:ins.next_ip],ip=ins.ip)
        decoded=decoder.decode()
        constant=decoder.get_constant_offsets(decoded)
        # Existing external near references have no meaningful in-object target.
        word=ins.ip+constant.immediate_offset
        refs=[f for f in obj.fixups if f.target_method==2 and f.offset==word and f.location==1 and not f.segment_relative]
        if refs:
            external_near.add(ins.ip)
            continue
        target=ins.near_branch_target
        if ins.ip in authorized:
            require(ins.mnemonic==Mnemonic.CALL and target==0,'wrong known call destination');used.add(ins.ip);continue
        start,end,_,_=next(region for region in regions if region[0]<=ins.ip<region[1])
        require(start<=target<end,'unknown cross-slice reference or reference into table data')
        require(target in boundaries,'relative target is not an instruction boundary')
        require(target not in guard_entries or guard_entries[target]==ins.ip,'reference bypasses switch guard')
    require(used==set(authorized),'known call was not decoded as CALL')
    validate_reachable_boundaries(obj,plan,instructions,regions,external_near)
    return authorized


def slice_object(obj, start, end, additions, external):
    out=[];inserted=False
    # A slice must not inherit link dependencies that occur only in its sibling.
    used={f.target for f in obj.fixups
          if start<=f.offset<end and f.target_method==2}
    if additions and external in obj.externs:used.add(obj.externs.index(external)+1)
    remap={old:new for new,old in enumerate(sorted(used),1)}
    ext_index=(remap[obj.externs.index(external)+1]
               if additions and external in obj.externs else len(remap)+1)
    external_number=0
    for number,r in enumerate(obj.records):
        kind,p=r.kind,r.payload
        if kind==0x88 or kind==0x9c:continue  # recognized nonsemantic/debug COMENT only
        if number==obj.code_segdef_record:
            out.append(make_record(kind,p[:1]+struct.pack('<H',end-start)+p[3:]))
        elif kind==0x8c:
            pos=0;items=[]
            while pos<len(p):
                before=pos;n=p[pos];pos+=1+n;_,pos=read_index(p,pos)
                external_number+=1
                if external_number in remap:items.append(p[before:pos])
            if items:out.append(make_record(kind,b''.join(items)))
        elif kind==0x90:
            group,pos=read_index(p,0);seg,pos=read_index(p,pos);items=[]
            while pos<len(p):
                before=pos;n=p[pos];pos+=1;name=p[pos:pos+n];pos+=n;off=u16(p,pos);pos+=2;_,nextpos=read_index(p,pos)
                typebytes=p[pos:nextpos];pos=nextpos
                if start<=off<end:items.append(bytes([n])+name+struct.pack('<H',off-start)+typebytes)
            if items:out.append(make_record(kind,index(group)+index(seg)+b''.join(items)))
        elif kind==0xa0:
            if not inserted:
                if additions and external not in obj.externs:
                    name=external.encode('latin1');require(len(name)<=255,'external name too long')
                    out.append(make_record(0x8c,bytes([len(name)])+name+b'\0'))
                inserted=True
            data=next(d for d in obj.data_records if d.record_number==number)
            lo=max(start,data.offset);hi=min(end,data.offset+len(data.data))
            if lo>=hi:continue
            payload=bytearray(data.data[lo-data.offset:hi-data.offset]);newfix=[]
            for f in data.fixups:
                if lo<=f.offset<hi:
                    require(f.offset+f.width<=hi,'fixup crosses retained payload boundary')
                    if f.target_method==0 and f.location==1:
                        target=u16(data.data,f.offset-data.offset)
                        require(start<=target<end,'internal target falls in discarded slice')
                        payload[f.offset-lo:f.offset-lo+2]=struct.pack('<H',target-start)
                    target=remap[f.target] if f.target_method==2 else f.target
                    newfix.append(Fixup(f.offset-start,f.location,f.segment_relative,f.frame_method,f.frame_index,target,f.displacement,f.target_method))
            for call in additions:
                word=call+1 # additions hold E8 addresses
                if lo<=word<hi:
                    require(word+2<=hi,'new call fixup crosses retained payload')
                    payload[word-lo:word-lo+2]=b'\0\0'
                    newfix.append(Fixup(word-start,1,False,5,None,ext_index,0))
            base=lo-start
            out.append(make_record(0xa0,index(obj.code_segment)+struct.pack('<H',base)+payload))
            if newfix:
                out.append(make_record(0x9c,b''.join(encode_fixup(f,base) for f in sorted(newfix,key=lambda f:f.offset))))
        else:out.append(r.raw)
    return b''.join(out)


@dataclass(frozen=True)
class SplitResult:
    helper_obj:bytes
    primary_obj:bytes


def split_context(raw, plan):
    obj=inspect_object(raw);calls=validate_calls(obj,plan)
    helper=slice_object(obj,0,plan.helper_size,{},plan.direct_external)
    primary=slice_object(obj,plan.helper_size,len(obj.code),calls,plan.direct_external)
    # Parse the serialized outputs as a final independent bounds/checksum check.
    inspect_object(helper);inspect_object(primary)
    return SplitResult(helper,primary)


def signature(raw):
    obj=inspect_object(raw)
    fs=[]
    for f in obj.fixups:
        frame=('TARGET',) if f.frame_method==5 else ('GROUP',obj.groups[f.frame_index-1][0])
        target=(('CODE' if f.location==1 else 'DATA'),f.target) if f.target_method==0 else ('EXTERNAL',obj.externs[f.target-1])
        fs.append((f.offset,f.location,f.segment_relative,frame,target,f.displacement))
    return obj.code,tuple(sorted(fs)),obj.publics


def prove_helper(candidate, reference, *, helper_public):
    left=signature(candidate);right=signature(reference)
    require(helper_public in left[2] and helper_public in right[2], 'helper public identity missing')
    require(left[0]==right[0],'helper bytes differ')
    require(left[1]==right[1],'helper fixup identities differ')
    require(left[2]==right[2],'helper public layout differs')

@dataclass(frozen=True)
class RegionSpec:
    size: int
    entry: str
    private_entry: PrivateHelperEntry | None
    primary: bool

@dataclass(frozen=True)
class RegionCall:
    caller_region: int
    caller_offset: int
    callee_region: int
    direct_external: str

@dataclass(frozen=True)
class MultiSplitSpec:
    regions: tuple
    calls: tuple


def _validate_multi(obj,plan):
    """Table-free region grammar. All entries and call edges are explicit."""
    require(type(plan) is MultiSplitSpec and type(plan.regions) is tuple and type(plan.calls) is tuple,'invalid multi split descriptor')
    require(2<=len(plan.regions)<=5,'multi context needs one to four helpers')
    starts=[];cursor=0;expected={};entries=set()
    for i,r in enumerate(plan.regions):
        require(type(r) is RegionSpec and type(r.size) is int and 0<r.size<0x10000 and type(r.primary) is bool,'invalid region size/descriptor')
        require(r.primary==(i==len(plan.regions)-1),'exactly the final region is primary')
        require(isinstance(r.entry,str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9@]{0,254}',r.entry) is not None and r.entry not in entries,'missing/invalid/duplicate region identity')
        entries.add(r.entry);starts.append(cursor)
        pubs={n:(seg,off) for n,(seg,off) in obj.publics.items() if cursor<=off<cursor+r.size}
        if r.private_entry is not None:
            d=r.private_entry
            require(not r.primary and type(d) is PrivateHelperEntry and d.name==r.entry and type(d.offset) is int and d.offset==0 and type(d.size) is int and d.size==r.size,'invalid private multi entry')
            require(not pubs,'private region has conflicting PUBLIC')
        elif not r.primary or pubs:
            valid=(len(pubs)==1 and next(iter(pubs)) in (r.entry,'_'+r.entry)
                   and next(iter(pubs.values()))==(obj.code_segment,cursor)) if r.primary else pubs=={r.entry:(obj.code_segment,cursor)}
            require(valid,'wrong or ambiguous region PUBLIC')
            expected.update(pubs)
        cursor+=r.size
    require(cursor==len(obj.code) and cursor<=0x10000,'multi region sizes do not cover exact CODE')
    require(obj.publics==expected,'unexpected multi PUBLIC')
    for cut in starts[1:]:require(not any(f.offset<cut<f.offset+f.width for f in obj.fixups),'fixup crosses multi region boundary')
    validate_loadds_entries(obj,set(starts))
    authorized={};aliases={};incoming=set()
    for c in plan.calls:
        require(type(c) is RegionCall and all(type(v) is int for v in (c.caller_region,c.caller_offset,c.callee_region)),'invalid multi call descriptor')
        require(0<=c.callee_region<c.caller_region<len(starts),'multi call must point backward to helper')
        r=plan.regions[c.caller_region];at=starts[c.caller_region]+c.caller_offset
        require(0<=c.caller_offset and c.caller_offset+4<=r.size,'multi call outside caller region')
        require(isinstance(c.direct_external,str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9@]{0,254}',c.direct_external) is not None and c.direct_external not in entries and c.direct_external not in obj.publics,'invalid/colliding direct body alias')
        old=aliases.get(c.callee_region)
        require(old is None or old==c.direct_external,'conflicting direct body identity')
        require(not any(k!=c.callee_region and v==c.direct_external for k,v in aliases.items()),'body alias shared by different helper entries')
        aliases[c.callee_region]=c.direct_external;incoming.add(c.callee_region)
        require(at+1 not in authorized,'duplicate multi call site')
        require(obj.code[at:at+2]==b'\x0e\xe8','multi call must be PUSH CS/E8')
        dest=(at+4+struct.unpack_from('<h',obj.code,at+2)[0])&0xffff
        require(dest==starts[c.callee_region],'wrong compiled multi callee')
        require(any(d.offset<=at+2 and at+4<=d.offset+len(d.data) for d in obj.data_records),'multi call operand crosses LEDATA')
        require(not any(f.offset<at+4 and f.offset+f.width>at for f in obj.fixups),'multi call overlaps fixup')
        authorized[at+1]=(at,c.direct_external,starts[c.callee_region])
    require(incoming==set(range(len(starts)-1)),'unused multi helper definition')
    near_kinds=(OpKind.NEAR_BRANCH16,OpKind.NEAR_BRANCH32,OpKind.NEAR_BRANCH64)
    used=set()
    for i,(start,r) in enumerate(zip(starts,plan.regions)):
        end=start+r.size;instructions={}
        for ins in Decoder(16,obj.code[start:end],ip=start):
            require(not ins.is_invalid and ins.next_ip<=end,'invalid/cross-region instruction')
            instructions[ins.ip]=ins
        # Internal OFFSET16 CODE addresses must occupy an actual complete
        # instruction operand, not a table word, and stay on an instruction
        # boundary in their own region. The parser already pins self CODE,
        # segment-relative OFFSET16/TARGET frame/zero displacement.
        operand_fields=set()
        for ins in instructions.values():
            decoder=Decoder(16,obj.code[ins.ip:ins.next_ip],ip=ins.ip)
            decoded=decoder.decode();constants=decoder.get_constant_offsets(decoded)
            for offset,width in ((constants.immediate_offset,constants.immediate_size),
                                 (constants.displacement_offset,constants.displacement_size)):
                if width==2 and ins.op0_kind not in near_kinds:operand_fields.add(ins.ip+offset)
        for f in obj.fixups:
            if f.target_method!=0 or f.location!=1 or not start<=f.offset<end:continue
            target=u16(obj.code,f.offset)
            require(f.offset in operand_fields and start<=target<end and target in instructions,
                    'internal CODE operand must target its own region instruction boundary')
        external=set()
        for ins in instructions.values():
            flow=ins.flow_control
            require(ins.code!=Code.HLT,'HLT unsupported in multi context')
            if flow==FlowControl.RETURN:
                require(ins.code in (Code.RETNW,Code.RETNW_IMM16,Code.RETFW,Code.RETFW_IMM16),'unsupported multi return')
                continue
            if flow==FlowControl.INDIRECT_CALL:
                require(dgroup_far_callback(obj,ins) or (not r.primary and helper_near_callback(obj,ins,end)),'unsupported multi indirect callback')
                continue
            if flow==FlowControl.INTERRUPT:
                require(not r.primary and helper_software_interrupt(obj,ins,end),'unsupported multi interrupt')
                continue
            require(flow in (FlowControl.NEXT,FlowControl.CALL,FlowControl.CONDITIONAL_BRANCH,FlowControl.UNCONDITIONAL_BRANCH),'unsupported multi control flow')
            if ins.op0_kind in (OpKind.FAR_BRANCH16,OpKind.FAR_BRANCH32):
                require(ins.code==Code.CALL_PTR1616 and any(f.offset==ins.ip+1 and f.location==3 and f.segment_relative for f in obj.fixups),'far multi call requires external pointer fixup')
                continue
            if ins.op0_kind not in near_kinds:continue
            decoder=Decoder(16,obj.code[ins.ip:ins.next_ip],ip=ins.ip);decoded=decoder.decode();constants=decoder.get_constant_offsets(decoded)
            word=ins.ip+constants.immediate_offset
            refs=[f for f in obj.fixups if f.offset==word and f.location==1 and not f.segment_relative]
            if refs:
                require(ins.code==Code.CALL_REL16 and len(refs)==1,'only external near CALL supported')
                external.add(ins.ip);continue
            if ins.ip in authorized:
                at,_,target=authorized[ins.ip]
                require(ins.code==Code.CALL_REL16 and at in instructions and instructions[at].code==Code.PUSHW_CS and ins.near_branch_target==target,'multi call not on actual instruction boundaries')
                used.add(ins.ip);continue
            require(start<=ins.near_branch_target<end and ins.near_branch_target in instructions,'unknown cross-region/interior reference')
        # Reachability starts independently at each entry. Calls may return;
        # declared earlier callees are separately seeded, not followed here.
        pending=[start];seen=set()
        while pending:
            ip=pending.pop()
            if ip in seen:continue
            require(ip in instructions,'reachable multi edge crosses region boundary')
            seen.add(ip);ins=instructions[ip];flow=ins.flow_control;edges=[]
            if flow==FlowControl.RETURN:continue
            if flow==FlowControl.UNCONDITIONAL_BRANCH:edges.append(ins.near_branch_target)
            elif flow==FlowControl.CONDITIONAL_BRANCH:edges.extend((ins.next_ip,ins.near_branch_target))
            elif flow==FlowControl.CALL:
                edges.append(ins.next_ip)
                if ins.op0_kind in near_kinds and ip not in external and ip not in authorized:edges.append(ins.near_branch_target)
            else:edges.append(ins.next_ip)
            for target in edges:
                require(start<=target<end and target in instructions,'reachable multi fallthrough crosses boundary')
                pending.append(target)
    require(used==set(authorized),'multi call not decoded in owned region')
    return starts,authorized


def _slice_multi(obj,start,end,additions):
    # Reuse the existing strict crop/retained-fixup serializer, then append
    # independently named external relative relocations in this slice only.
    raw=slice_object(obj,start,end,{},'')
    cropped=inspect_object(raw)
    names=list(cropped.externs)
    for _,name,_ in additions.values():
        if name not in names:names.append(name)
    extra=names[len(cropped.externs):];out=[];inserted=False
    # Retain exact original EXTDEF type-index bytes when a mapped alias was
    # present but unused in the cropped slice. Indices, including two-byte
    # ones, are never reconstructed from a guessed type.
    definitions={}
    for r in obj.records:
        if r.kind!=0x8c:continue
        p=r.payload;pos=0
        while pos<len(p):
            begin=pos;n=p[pos];pos+=1;name=p[pos:pos+n].decode('latin1');pos+=n
            _,pos=read_index(p,pos);definitions[name]=p[begin:pos]

    for number,r in enumerate(cropped.records):
        if r.kind==0x9c:continue
        if r.kind!=0xa0:out.append(r.raw);continue
        if not inserted:
            if extra:out.append(make_record(0x8c,b''.join(definitions.get(n,bytes([len(n)])+n.encode('latin1')+b'\0') for n in extra)))
            inserted=True
        data=next(d for d in cropped.data_records if d.record_number==number)
        payload=bytearray(data.data);fixups=list(data.fixups)
        for call,(_,alias,_) in additions.items():
            word=call+1-start
            if data.offset<=word<data.offset+len(payload):
                require(word+2<=data.offset+len(payload),'multi new operand crosses LEDATA')
                payload[word-data.offset:word-data.offset+2]=b'\0\0'
                fixups.append(Fixup(word,1,False,5,None,names.index(alias)+1,0))
        out.append(make_record(0xa0,index(cropped.code_segment)+struct.pack('<H',data.offset)+payload))
        if fixups:out.append(make_record(0x9c,b''.join(encode_fixup(f,data.offset) for f in sorted(fixups,key=lambda f:f.offset))))
    return b''.join(out)


def split_multi_context(raw,plan):
    obj=inspect_object(raw);starts,authorized=_validate_multi(obj,plan);result=[]
    for start,r in zip(starts,plan.regions):
        additions={at:v for at,v in authorized.items() if start<=at<start+r.size}
        part=_slice_multi(obj,start,start+r.size,additions)
        inspect_object(part);result.append(part)
    return tuple(result)
