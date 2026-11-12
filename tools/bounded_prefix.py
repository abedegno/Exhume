"""Private identity-bound opt-in. Never consume callback/certificate facts."""
from pathlib import Path
from typing import NamedTuple
import struct,hashlib
from iced_x86 import Decoder,Code
from resident_cs_raw import capture
from namespace import validate_namespace,NamespaceResult
from symbol_address import parse,format_address

class PendingCS(NamedTuple):
 name:str
 offset:int

def need(ok,message):
 if not ok:raise ValueError('bounded resident prefix: '+message)
def address(value):
 need(type(value)is tuple and value and type(value[0])is str,'malformed typed address')
 need((value[0]=='DS'and len(value)==2)or(value[0]in('FAR','OVL')and len(value)==3),'unsupported typed address domain')
 need(all(type(v)is int and 0<=v<=65535 for v in value[1:]),'malformed address component')
 return value

def snapshot(current):
 need(type(current)is dict,'namespace snapshot must be a dictionary')
 result=[]
 for name,pair in current.items():
  need(type(name)is str and bool(name)and type(pair)is tuple and len(pair)==2 and all(type(v)is str for v in pair),'malformed current namespace row')
  value,label=pair;address(parse(value));need(format_address(parse(value))==value,'noncanonical namespace address');result.append((name,value,label))
 return tuple(result)

def namespace_preflight(current,syms,pubs):
 need(type(current)is tuple,'malformed immutable namespace snapshot')
 seen=set()
 for row in current:
  need(type(row)is tuple and len(row)==3 and all(type(v)is str for v in row)and row[0]not in seen,'malformed/duplicate namespace snapshot');seen.add(row[0]);address(parse(row[1]));need(format_address(parse(row[1]))==row[1],'noncanonical current namespace')
 refs=[]
 for name,pair in syms.items():
  need(type(name)is str and name and type(pair)is tuple and len(pair)==2,'malformed reference row');value,where=pair;address(value);need(type(where)is int and 0<=where<126,'malformed reference site');refs.append((name,value,where))
 public=[]
 for name,value in pubs.items():need(type(name)is str and name,'malformed PUBLIC name');public.append((name,address(value)))
 result=validate_namespace(current,tuple(refs),tuple(public))
 need(type(result)is NamespaceResult and type(result.problems)is tuple and all(type(v)is str for v in result.problems),'malformed namespace result')
 for rows in(result.known,result.labels):
  need(type(rows)is tuple and all(type(row)is tuple and len(row)==2 and all(type(v)is str for v in row)for row in rows),'malformed namespace merged rows')
 need(result.missing_address is None,'namespace PUBLIC/reference conflict')
 return result.problems

def symbol_token(cfg):
 path=cfg.symbols
 try:return(path,True,Path(path).read_bytes())
 except FileNotFoundError:return(path,False,b'')

def require_symbol_token(cfg,token):
 need(symbol_token(cfg)==token,'symbol file changed since captured namespace')

def begin(cfg,seg,raw):
 table=(Path(cfg.targets)/(seg+'.tsv')).read_bytes()
 t=capture(raw,cfg.exe,(Path(cfg.root)/'groupobj/CB002.OBJ').read_bytes(),table)
 need(seg=='CODE_2_RH1B1C','only exact supported target is eligible')
 need(type(cfg.dgroup_para)is int and cfg.dgroup_para==int.from_bytes(t.original_code[7:9],'little'),'actual initial DGROUP/config disagreement')
 aliases=cfg.raw.get('names',{}).get('extern_aliases',{})
 need(type(aliases)is dict and all(type(k)is str and type(v)is str for k,v in aliases.items()),'malformed alias map')
 need(all(aliases.get(n,n)==n for n in t.externs),'this bounded source forbids changed canonical extern spellings')
 current=snapshot(cfg.load_symbols())
 # All ordinary legacy readers see precisely this one captured namespace.
 cfg.load_symbols=lambda:{n:(value,label)for n,value,label in current}
 return t,current

def pending(t,field):
 need(field=={'seg':1,'off':72,'loc':1,'rel':True,'frame':(5,None),'target':(2,2),'disp':0},'only exact pending CS field72 is supported')
 return PendingCS(t.externs[field['target'][1]-1],72)

def project(t,syms):
 need('HEAP_PROGRAM_DS'not in syms,'CS identity cannot participate in prefix evidence')
 code=bytearray(t.code);resolved=[]
 for f in t.fixups:
  if f.offset>=69:continue
  need(f.offset+2<=69 and f.displacement==0 and code[f.offset:f.offset+2]==bytes(2),'crossing/nonzero prefix field')
  if f.target_method==0:
   need(f.offset==7 and f.location==2 and f.frame_method==1 and f.target==2,'internal initial DGROUP form');value=int.from_bytes(t.original_code[7:9],'little')
   # The exact original MZ site was validated from captured EXE by capture().
  else:
   name=t.externs[f.target-1];need(name in syms,'ordinary identity absent');identity=address(syms[name][0]);need(f.location==1,'ordinary prefix must be OFFSET16')
   if f.segment_relative:
    need(f.frame_method==1 and f.frame_index==1 and identity[0]=='DS','ordinary GROUP/domain mismatch');value=identity[1]
   else:
    need(f.frame_method==5 and f.frame_index is None and identity[0]=='FAR'and identity[1]==t.paragraph,'near call full same-CS identity mismatch')
    ins=next((i for i in Decoder(16,t.code,ip=0)if i.ip==f.offset-1),None);need(ins is not None and ins.code==Code.CALL_REL16 and ins.len==3,'near E8 complete boundary')
    value=(identity[2]-(t.org+f.offset+2))&65535
  struct.pack_into('<H',code,f.offset,value);resolved.append(f.offset)
 need(resolved==[7,12,20,26,32,63],'complete six-field prefix required')
 need(code[:69]==t.original_code[:69],'entire computed69 prefix differs')
 need(code[69:]==t.code[69:]and code[72:74]==bytes(2),'endpoint/tail modified')
 return bytes(code)

def derive_identity(t,dgroup):
 # Called only after bounded execution. Derive from original bytes/MZ, not a
 # known name, callback, helper return, injected anchor or compiled operand.
 e=t.original_exe;last,pages,nrel,hp=struct.unpack_from('<4H',e,2);header=hp*16;end=(pages-1)*512+last if last else pages*512;rt=struct.unpack_from('<H',e,24)[0]
 table,count=struct.unpack_from('<II',e,end+8)
 spans=[(lo,hi)for p,hi,flags,lo in(struct.unpack_from('<4H',e,table+8*k)for k in range(count))if p==t.paragraph and flags==1]
 need(len(spans)==1,'root must have unique original resident span');lo,hi=spans[0]
 for code in(t.code,t.original_code):
  ins=next((i for i in Decoder(16,code,ip=0)if i.ip==69),None)
  need(ins is not None and ins.len==5 and ins.code==Code.MOV_SREG_RM16 and code[69:72]==bytes.fromhex('2e8e1e'),'exact complete root CS instruction')
 need(t.code[72:74]==bytes(2),'root CS operand must remain unresolved')
 offset=int.from_bytes(t.original_code[72:74],'little');need(lo<=offset and offset+2<=hi and offset+2<=65536,'root CS WORD span')
 physical=header+t.paragraph*16+offset;need(header<=physical and physical+2<=end,'root anchor initialized bounds')
 need(type(dgroup)is int and int.from_bytes(e[physical:physical+2],'little')==dgroup==int.from_bytes(t.original_code[7:9],'little'),'root anchor actual program domain')
 locations=[];pairs=[]
 for k in range(nrel):
  off,para=struct.unpack_from('<HH',e,rt+4*k);q=header+para*16+off;need(off+2<=65536 and header<=q and q+2<=end,'MZ bounds');locations.append(q);pairs.append((para,off))
 ordered=sorted(locations);need(all(a+2<=b for a,b in zip(ordered,ordered[1:]))and locations.count(physical)==1 and pairs.count((t.paragraph,offset))==1,'root anchor unique canonical MZ')
 return ('FAR',t.paragraph,offset)

def validate_helper_provenance(t):
 e=t.original_exe
 need(hashlib.sha256(e).hexdigest()==t.exe_sha256=='dd5744b99c6fa61dc1d95c580a1f1d6f1b3e97ae8b920dd6e9cfedce29f82fb6','helper original authentication')
 last,pages,nrel,hp=struct.unpack_from('<4H',e,2);header=hp*16;end=(pages-1)*512+last if last else pages*512;rt=struct.unpack_from('<H',e,24)[0]
 body=e[header+t.paragraph*16+0x138:header+t.paragraph*16+0x233]
 instruction=next((i for i in Decoder(16,body,ip=0x138)if i.ip==0x146),None)
 physical_instruction=header+t.paragraph*16+0x146
 need(instruction is not None and instruction.len==5 and instruction.code==Code.MOV_SREG_RM16 and e[physical_instruction:physical_instruction+3]==bytes.fromhex('2e8e1e'),'actual helper0146 CS load')
 offset=int.from_bytes(e[physical_instruction+3:physical_instruction+5],'little');physical=header+t.paragraph*16+offset
 table,count=struct.unpack_from('<II',e,end+8)
 spans=[(lo,hi)for p,hi,flags,lo in(struct.unpack_from('<4H',e,table+8*k)for k in range(count))if p==t.paragraph and flags==1]
 need(len(spans)==1 and spans[0][0]<=offset and offset+2<=spans[0][1]and physical+2<=end,'helper anchor initialized CS span')
 locations=[];pairs=[]
 for k in range(nrel):
  off,para=struct.unpack_from('<HH',e,rt+4*k);locations.append(header+para*16+off);pairs.append((para,off))
 need(locations.count(physical)==1 and pairs.count((t.paragraph,offset))==1,'helper anchor unique actual canonical MZ')
 need(int.from_bytes(e[physical:physical+2],'little')==int.from_bytes(t.original_code[7:9],'little'),'helper anchor actual program domain')

def prove(t,current,syms,pubs,existing_problems,dgroup):
 need(not existing_problems,'ordinary verifier problems precede proof')
 errors=namespace_preflight(current,syms,pubs);need(not errors,'ordinary final namespace: '+'; '.join(errors))
 candidate=project(t,syms)
 validate_helper_provenance(t)
 import bounded_cpu
 witness=bounded_cpu.prove(t,candidate)
 return derive_identity(t,dgroup),witness
