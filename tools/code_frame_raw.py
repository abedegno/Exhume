"""Private RH1B9A181 raw-byte boundary, not a CS identity/acceptance API.

Only capture() constructs results. Downstream integration must call it from
captured raw bytes, never deserialize an alleged result/certificate.
"""
from dataclasses import dataclass, fields
from pathlib import Path
from typing import NamedTuple
import hashlib,importlib.util,re,struct,sys
from iced_x86 import Decoder,Code
from fixups import fixups as _outer_parse
import omf

EXE_SHA='dd5744b99c6fa61dc1d95c580a1f1d6f1b3e97ae8b920dd6e9cfedce29f82fb6'
NATIVE_SHA='aa08407bd1571b015e3b1e5f9ccc70f8e96e79fbcd6194eee622eff5e0ddaa47'
_PARSER_PATH=Path(__file__).resolve().parent/'context_omf.py'
_PARSER_SHA='ed55e24425a676118a7d34300a8e09d51bb5aceb75560604d3ae06d8c4654eba'
_PARSER_SOURCE=_PARSER_PATH.read_bytes()
if hashlib.sha256(_PARSER_SOURCE).hexdigest()!=_PARSER_SHA:
 raise ValueError('frozen private parser authentication failed')
# Execute exactly the authenticated owned buffer, never SourceFileLoader/pycache.
from types import ModuleType
_parser=ModuleType('_rh1b9a_code_frame_parser')
_parser.__file__=str(_PARSER_PATH)
sys.modules[_parser.__name__]=_parser
exec(compile(_PARSER_SOURCE,str(_PARSER_PATH),'exec'),_parser.__dict__)

class TransactionError(ValueError):pass
class Relocation(NamedTuple):
 segment:int
 offset:int
 location:int
 segment_relative:bool
 frame_method:int
 frame_index:int|None
 target_method:int
 target:int
 displacement:int

@dataclass(frozen=True,slots=True,init=False)
class _Transaction:
 raw_object:bytes
 original_exe:bytes
 native_object:bytes
 target_table:bytes
 raw_sha256:str
 exe_sha256:str
 native_sha256:str
 target_sha256:str
 base:int
 size:int
 org:int
 paragraph:int
 code_segment:int
 rows:tuple
 segments:tuple
 groups:tuple
 externs:tuple
 fixups:tuple
 code:bytes
 original_code:bytes
 masked_offsets:tuple
 unmasked_count:int
 group_entries:tuple
 code_frame_rows:tuple
 def __init__(self,*args,**kwargs):raise TypeError('Use capture() with actual raw bytes; no rehydration API')

def _need(ok,message):
 if not ok:raise TransactionError(message)
def _sha(data):return hashlib.sha256(data).hexdigest()
def _rows(table):
 text=table.decode('ascii');lines=text.splitlines()
 _need(len(lines)==2,'one complete target row required')
 h=re.fullmatch(r'# segment CODE_2_RH1B9A base (0x[0-9A-Fa-f]+) size (0x[0-9A-Fa-f]+) org (0x[0-9A-Fa-f]+)',lines[0])
 _need(h is not None,'unsupported target header')
 base,size,org=(int(v,16)for v in h.groups());_need((base,size,org)==(0x34da,181,0x1b9a),'unsupported complete row placement')
 row=lines[1].split('\t');_need(len(row)==4,'invalid target columns')
 name,module,start,length=row;start=int(start,0);length=int(length,0)
 _need((name,module,start,length)==('HEAP_ALLOC_HANDLE','RH1B9A',0,181),'wrong/partial target function row')
 return base,size,org,((name,module,start,length),)
def _complete(code):
 _need(len(code)==181,'whole CODE length must be181')
 ins=tuple(Decoder(16,code,ip=0));_need(ins and all(not i.is_invalid for i in ins)and ins[-1].next_ip==181,'invalid complete function decode')
 _need(ins[-1].code==Code.RETFW_IMM16 and ins[-1].immediate16==2 and ins[-1].ip==178,'whole function must end with actual RETF2')
 return ins

def _strict_segments(parsed):
 # The strict parser already validated these records. Retain its own segment
 # view rather than assuming the permissive parser's SEGDEF interpretation.
 names=[''];segments=[]
 for r in parsed.records:
  p=r.payload
  if r.kind==0x96:
   k=0
   while k<len(p):n=p[k];k+=1;names.append(p[k:k+n].decode('latin1'));k+=n
  elif r.kind==0x98:
   sn,k=_parser.read_index(p,3);cn,k=_parser.read_index(p,k);segments.append((names[sn],names[cn],_parser.u16(p,1)))
 return tuple(segments)

def capture(raw_object,original_exe,native_object,target_table):
 """Validate this supported raw object/row; return immutable owned evidence.

 No namespace, CPU execution, CS identity, config or parsed-facts input exists.
 Errors are fail-closed TransactionError(ValueError). Native/exe hashes restrict
 the supported original program; compiled object itself is genuinely parsed.
 """
 try:return _capture(raw_object,original_exe,native_object,target_table)
 except TransactionError:raise
 except (ValueError,IndexError,KeyError,TypeError,struct.error,UnicodeError)as error:
  raise TransactionError('invalid raw transaction: '+str(error))from error

def _capture(raw_object,original_exe,native_object,target_table):
 for value in(raw_object,original_exe,native_object,target_table):_need(type(value)is bytes,'captured inputs must be immutable bytes')
 _need(_sha(original_exe)==EXE_SHA,'unsupported/authentication-failed original EXE')
 _need(_sha(native_object)==NATIVE_SHA,'unsupported/authentication-failed immutable native object')
 _need(_sha(_PARSER_PATH.read_bytes())==_PARSER_SHA,'private parser changed since load')
 base,size,org,rows=_rows(target_table)
 e=original_exe;last,pages,nrel,hp=struct.unpack_from('<4H',e,2);header=hp*16;end=(pages-1)*512+last if last else pages*512;rt=struct.unpack_from('<H',e,24)[0]
 _need(e[:2]==b'MZ'and pages>0 and last<512 and 28<=header<=base and base+size<=end<=len(e)and rt>=28 and rt+4*nrel<=header,'original MZ bounds')
 delta=base-org-header;_need(delta>=0 and delta%16==0,'original physical placement');paragraph=delta//16;_need(paragraph==0x94,'original CS placement')
 _need(end+16<=len(e)and e[end:end+4]==b'FBOV','original FBOV')
 table,count=struct.unpack_from('<II',e,end+8);_need(count>0 and header<=table and table+8*count<=end,'original resident table bounds')
 spans=[(lo,hi)for p,hi,flags,lo in(struct.unpack_from('<4H',e,table+8*k)for k in range(count))if p==paragraph and flags==1]
 _need(len(spans)==1 and spans[0][0]<=org and org+size<=spans[0][1]and header+paragraph*16+spans[0][1]<=end,'original unique initialized resident span')
 mz=[]
 for k in range(nrel):
  off,p=struct.unpack_from('<HH',e,rt+4*k);q=header+p*16+off
  _need(off+2<=65536 and header<=q and q+2<=end,'original MZ relocation bounds');mz.append(q)
 _need(all(a+2<=b for a,b in zip(sorted(mz),sorted(mz)[1:])),'original MZ relocation overlap')
 _need(sorted(q-base for q in mz if base<=q<base+size)==[8,148],'actual whole-row MZ profile')
 original_code=e[base:base+size];_complete(original_code)
 _,native_segs,native_data,native_masks,consumed=omf.module_masked(native_object)
 cis=[i for i,s in enumerate(native_segs,1)if s[1]=='CODE'];_need(len(cis)==1 and consumed==len(native_object),'native whole object/CODE identity');ci=cis[0]
 pubs=omf.publics(native_object)
 _need(all(pubs.get(name)==(ci,org)for name in('HEAP_ALLOC_HANDLE',)),'original native aliases disagree')
 native_code=bytes(native_data[ci][org:org+size]);mask=bytes(native_masks[ci][org:org+size]);_need([k for k,v in enumerate(mask)if v]==[8,9,148,149],'native whole181 MZ mask profile')
 _need(len(native_code)==size and all(native_code[k]==original_code[k]for k in range(size)if not mask[k]),'native original CODE differs')
 # Preliminary complete decode establishes the genuine full row's entry; it
 # grants no parsed-object trust. Strict parsing below authenticates all bytes.
 outer=_outer_parse(raw_object);_need(outer['segs']==[None,('HPALLOCH_TEXT','CODE',181),('_DATA','DATA',0),('_BSS','BSS',0)],'unsupported outer segment geometry')
 preliminary=bytes(outer['data'][1]);_complete(preliminary);entries=tuple(r[2]for r in rows)
 parsed=_parser.inspect_object(raw_object,group_loadds_entries=entries,
                               own_code_frame_rows=((0,181),))
 _need(tuple(r.payload[0]for r in parsed.records if r.kind==0x98)==(0x28,0x48,0x48),'actual CODE/DATA/BSS combine/alignment profile required')
 segments=_strict_segments(parsed);groups=tuple((name,tuple(members))for name,members in outer['groups'][1:]);externs=tuple(outer['ext'][1:])
 _need(segments==tuple(outer['segs'][1:])and parsed.code_segment==1 and parsed.code==preliminary,'strict/outer segment or CODE disagreement')
 _need(parsed.groups==groups and parsed.externs==externs and parsed.publics==outer['pubs'],'strict/outer group/extern/PUBLIC disagreement')
 _need(not parsed.publics and not any(bytes(v)for k,v in outer['data'].items()if k!=1),'owned PUBLIC/DATA forbidden')
 _need(groups==( ('DGROUP',(3,2)), ),'actual DGROUP membership required')
 expected_externs=('HEAP_ALLOC_ITEM','HEAP_ALLOC_NODE','HEAP_PROGRAM_DS','SWAP_GROW_FILE_ALLOWED','HEAP_FREE_ITEM','HEAP_STATUS','HEAP_DESCRIPTOR_SEGMENTS','RH_UPDATE_NODE_ADDRESS','HEAP_HOOK_CALLSITE','RH_PREPARE_DESCRIPTORS')
 _need(externs==expected_externs,'actual external identities/order required')
 strict_fx=tuple(sorted((Relocation(parsed.code_segment,f.offset,f.location,f.segment_relative,f.frame_method,f.frame_index,f.target_method,f.target,f.displacement)for f in parsed.fixups),key=lambda f:f.offset))
 outer_fx=tuple(sorted((Relocation(f['seg'],f['off'],f['loc'],f['rel'],*f['frame'],*f['target'],f['disp'])for f in outer['fixups']),key=lambda f:f.offset))
 _need(strict_fx==outer_fx,'strict/outer relocation disagreement')
 expected=(Relocation(1, 8, 2, True, 5, None, 1, 1, 0),Relocation(1, 13, 1, False, 0, 1, 2, 10, 0),Relocation(1, 24, 1, True, 1, 1, 2, 6, 0),Relocation(1, 30, 1, True, 1, 1, 2, 7, 0),Relocation(1, 33, 1, False, 0, 1, 2, 1, 0),Relocation(1, 50, 1, False, 0, 1, 2, 2, 0),Relocation(1, 81, 1, False, 0, 1, 2, 8, 0),Relocation(1, 102, 1, True, 0, 1, 2, 3, 0),Relocation(1, 114, 1, True, 1, 1, 2, 9, 0),Relocation(1, 118, 1, True, 1, 1, 2, 6, 0),Relocation(1, 125, 1, True, 1, 1, 2, 7, 0),Relocation(1, 132, 1, True, 0, 1, 2, 3, 0),Relocation(1, 136, 1, True, 1, 1, 2, 7, 0),Relocation(1, 145, 1, False, 0, 1, 2, 5, 0),Relocation(1, 148, 2, True, 5, None, 2, 4, 0),Relocation(1, 154, 1, True, 1, 1, 2, 6, 0),Relocation(1, 168, 1, True, 1, 1, 2, 9, 0))
 _need(strict_fx==expected,'exact complete17-field relocation footprint required')
 code=parsed.code;_need(all(code[f.offset:f.offset+2]==bytes(2)for f in strict_fx),'nonzero encoded relocation/addend word')
 masked=tuple(sorted(k for f in strict_fx for k in(f.offset,f.offset+1)));_need(len(masked)==34 and len(set(masked))==34,'whole field mask')
 _need(all(code[k]==original_code[k]for k in range(size)if k not in masked),'complete181 fixed bytes differ')
 _need(all(code[p-3:p]==bytes.fromhex('2e8e1e') and code[p:p+2]==bytes(2) for p in (102,132)), 'two exact unresolved CODE-frame CS operands required')
 values=dict(raw_object=raw_object,original_exe=original_exe,native_object=native_object,target_table=target_table,raw_sha256=_sha(raw_object),exe_sha256=_sha(original_exe),native_sha256=_sha(native_object),target_sha256=_sha(target_table),base=base,size=size,org=org,paragraph=paragraph,code_segment=1,rows=rows,segments=segments,groups=groups,externs=externs,fixups=strict_fx,code=code,original_code=original_code,masked_offsets=masked,unmasked_count=size-len(masked),group_entries=entries,code_frame_rows=((0,181),))
 result=object.__new__(_Transaction)
 _need(set(values)=={f.name for f in fields(_Transaction)},'internal immutable schema mismatch')
 for name,value in values.items():object.__setattr__(result,name,value)
 return result
