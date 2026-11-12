"""Private static own-CODE-frame predicate for authenticated RH1B9A181 only."""
from pathlib import Path
import struct
from iced_x86 import Decoder,Code
from code_frame_raw import capture,_Transaction,Relocation

def need(ok,message):
 if not ok:raise ValueError('resident own-CODE CS reference: '+message)
def token(cfg):
 try:return(cfg.symbols,True,Path(cfg.symbols).read_bytes())
 except FileNotFoundError:return(cfg.symbols,False,b'')
def stable(cfg,before):need(token(cfg)==before,'symbols changed during invocation')
def begin(cfg,seg,raw):
 need(seg=='CODE_2_RH1B9A','unsupported target')
 return capture(raw,cfg.exe,(Path(cfg.root)/'groupobj/CB002.OBJ').read_bytes(),(Path(cfg.targets)/(seg+'.tsv')).read_bytes())

def reference(cfg,t,at,f):
 need(type(t)is _Transaction,'transaction must be genuinely captured internally')
 need(at in(102,132),'not one of the two actual CS fields')
 actual=Relocation(f['seg'],f['off'],f['loc'],f['rel'],*f['frame'],*f['target'],f['disp'])
 need(actual in t.fixups and actual==Relocation(1,at,1,True,0,1,2,3,0),'not exact own-CODE external zero OFFSET16')
 need(t.code_segment==actual.frame_index and t.segments[0]==('HPALLOCH_TEXT','CODE',181),'frame index is not unique actual CODE')
 need(t.code[at:at+2]==bytes(2),'nonzero encoded field')
 for code in(t.code,t.original_code):
  ins=next((i for i in Decoder(16,code,ip=0)if i.ip==at-3),None)
  need(ins is not None and ins.code==Code.MOV_SREG_RM16 and ins.len==5 and code[at-3:at]==bytes.fromhex('2e8e1e'),'not exact original/compiled CS WORD DS load')
 e=t.original_exe;last,pages,nrel,hp=struct.unpack_from('<4H',e,2);header=hp*16;end=(pages-1)*512+last if last else pages*512;rt=struct.unpack_from('<H',e,24)[0]
 need(e[:2]==b'MZ'and pages>0 and last<512 and 28<=header<=t.base and t.base+t.size<=end<=len(e)and rt>=28 and rt+4*nrel<=header,'original MZ bounds')
 delta=t.base-t.org-header;need(delta>=0 and delta%16==0 and delta//16==t.paragraph,'actual row/native frame placement disagrees');para=delta//16
 need(e[end:end+4]==b'FBOV','original FBOV');table,count=struct.unpack_from('<II',e,end+8);need(count>0 and header<=table and table+8*count<=end,'resident table bounds')
 spans=[(lo,hi)for p,hi,flags,lo in(struct.unpack_from('<4H',e,table+8*k)for k in range(count))if p==para and flags==1]
 need(len(spans)==1,'frame lacks unique original initialized CS span');lo,hi=spans[0]
 need(lo<=t.org and t.org+t.size<=hi and header+para*16+hi<=end,'whole row does not fit original CS')
 offset=int.from_bytes(t.original_code[at:at+2],'little');need(lo<=offset and offset+2<=hi and offset+2<=65536,'loader WORD exceeds initialized CS span')
 physical=header+para*16+offset;need(header<=physical and physical+2<=end,'loader WORD physical bounds')
 program=int.from_bytes(t.original_code[8:10],'little')
 need(type(cfg.dgroup_para)is int and program==cfg.dgroup_para==int.from_bytes(t.original_code[148:150],'little')==int.from_bytes(e[physical:physical+2],'little'),'loader WORD does not hold actual program DGROUP')
 locations=[];pairs=[]
 for k in range(nrel):
  off,p=struct.unpack_from('<HH',e,rt+4*k);q=header+p*16+off;need(off+2<=65536 and header<=q and q+2<=end,'original MZ target bounds');locations.append(q);pairs.append((p,off))
 ordered=sorted(locations);need(all(a+2<=b for a,b in zip(ordered,ordered[1:])),'duplicate/overlapping MZ')
 need(locations.count(physical)==1 and pairs.count((para,offset))==1,'loader WORD lacks one canonical MZ')
 need(pairs.count((para,t.org+8))==1 and pairs.count((para,t.org+148))==1,'program operands lack canonical MZ')
 # The frame supplies the canonical segment; the operand is an offset within
 # that frame. No name/known value, callback, CPU or reachability supplies it.
 return('FAR',para,offset)
