"""Private callable finite witness; no disk proof inputs and no auto escalation."""
from pathlib import Path
import hashlib,struct,importlib,platform
from iced_x86 import Decoder,FlowControl,Mnemonic
from contract import Contract

def _load_engine():
 try:
  engine=importlib.import_module('unicorn')
 except (ImportError,OSError)as error:raise ValueError('Unicorn unavailable')from error
 # Any Unicorn 2.1 or later on any platform: the witness only needs its x86 16-bit emulation,
 # and the inputs it runs are authenticated separately (the original EXE's SHA-256 below).
 try:
  major,minor=(int(x) for x in engine.__version__.split('.')[:2])
 except ValueError as error:raise ValueError('unrecognised Unicorn version')from error
 if (major,minor)<(2,1):raise ValueError('Unicorn 2.1 or later is needed')
 return engine

def _require(ok):
 if not ok:raise ValueError('authenticated loader/candidate invariant failed')

def _execute(E,candidate,compiled,unicorn):
 x86=importlib.import_module('unicorn.x86_const')
 SHA=hashlib.sha256(E).hexdigest()
 if SHA!='dd5744b99c6fa61dc1d95c580a1f1d6f1b3e97ae8b920dd6e9cfedce29f82fb6':raise ValueError('execution EXE authentication failed')
 divisor=1;table=0x4000;descriptor_count=8
 last,pages,nrel,hp=struct.unpack_from('<4H',E,2);header=hp*16;end=(pages-1)*512+last if last else pages*512;rt=struct.unpack_from('<H',E,24)[0]
 _require(E[:2]==b'MZ' and 28<=header<=end<=len(E) and rt>=28 and rt+4*nrel<=header)
 LOAD=0x1000;CS=LOAD+0x94;PROGRAM=LOAD+0xdef;HEAP=0x4000;SS=0x9000;SP=0xf000;OUT=0x6000
 image=bytearray(E[header:end]);relocations=[]
 for k in range(nrel):
  off,para=struct.unpack_from('<HH',E,rt+4*k);p=para*16+off;_require(0<=p and p+2<=len(image));_require(p not in relocations);relocations.append(p);value=struct.unpack_from('<H',image,p)[0]+LOAD;_require(value<=65535);struct.pack_into('<H',image,p,value)
 _require(struct.unpack_from('<H',image,0x94*16+0x4696)[0]==PROGRAM)
 ROWS={0x1b1c:0x1b9a,0x138:0x233,0x110a:0x1222,0x314f:0x3166};ENDPOINT=0x1b61
 # All control targets on the witnessed route must be complete original boundaries.
 BOUNDARIES={}
 for lo,hi in ROWS.items():
  b=image[0x94*16+lo:0x94*16+hi];ins=list(Decoder(16,b,ip=lo));_require(ins and ins[-1].next_ip==hi and all(not x.is_invalid for x in ins));BOUNDARIES[lo]={x.ip for x in ins}
 NEXT_MN={getattr(Mnemonic,x) for x in ['MOV','LEA','PUSH','POP','ADD','ADC','SUB','CMP','TEST','XOR','OR','AND','INC','DEC','DIV','MUL','NOP']}
 def inside(addr,size,lo,length):return size>0 and lo<=addr and addr+size<=lo+length
 execution_image=bytearray(image)
 if compiled:
  candidate=bytearray(candidate);_require(len(candidate)==126 and candidate[72:74]==bytes(2))
  # Only the independently resolved prefix MZ word receives the loader base.
  _require(int.from_bytes(candidate[7:9],"little")==PROGRAM-LOAD)
  struct.pack_into("<H",candidate,7,PROGRAM)
  execution_image[0x94*16+0x1b1c:0x94*16+0x1b9a]=candidate
 u=unicorn.Uc(unicorn.UC_ARCH_X86,unicorn.UC_MODE_16);u.mem_map(0,0x100000);u.mem_write(LOAD*16,bytes(execution_image));u.mem_write(HEAP*16,b'\0'*0x10000);u.mem_write(SS*16,b'\0'*0x10000);u.mem_write(OUT*16+0x100,b'\0'*2)
 # These are mutable runtime data inputs, never code or anchor changes.
 def word(a,v):u.mem_write(a,struct.pack('<H',v))
 word(PROGRAM*16+0x6ca,table);word(HEAP*16+0x3e,descriptor_count);word(HEAP*16+0x40,0);word(HEAP*16+6,0);word(HEAP*16+8,divisor);word(HEAP*16+0x1a,0)
 for reg in [x86.UC_X86_REG_AX,x86.UC_X86_REG_BX,x86.UC_X86_REG_CX,x86.UC_X86_REG_DX,x86.UC_X86_REG_SI,x86.UC_X86_REG_DI,x86.UC_X86_REG_BP]:u.reg_write(reg,0)
 for reg,v in [(x86.UC_X86_REG_CS,CS),(x86.UC_X86_REG_DS,PROGRAM),(x86.UC_X86_REG_ES,OUT),(x86.UC_X86_REG_SS,SS),(x86.UC_X86_REG_SP,SP),(x86.UC_X86_REG_IP,0x1b1c),(x86.UC_X86_REG_EFLAGS,0x202)]:u.reg_write(reg,v)
 for delta,v in [(0,0),(2,0),(4,1),(6,0x100),(8,OUT)]:word(SS*16+SP+delta,v)
 trace=[];accesses=[];frames=[{'lo':0x1b1c,'hi':0x1b9a,'return_ip':None,'call_sp':None}];seen=set();state={'known_flags':255,'endpoint':False,'success':False,'error':None,'pending':None}
 initial = {}
 # Inputs are declared from authenticated image and explicit parameters, not
 # inferred from the trace produced below. Only these seventeen bytes are readable
 # before CPU writes. Initial root return/output-pointer cells stay unreadable.
 for address, payload in [
  (CS*16+0x4696, bytes(execution_image[0x94*16+0x4696:0x94*16+0x4698])),
  (PROGRAM*16+0x6ca, struct.pack('<H',table)),
  (HEAP*16+5,b'\0'), (HEAP*16+6,b'\0\0'),
  (HEAP*16+8,struct.pack('<H',divisor)), (HEAP*16+0x1a,b'\0\0'),
  (HEAP*16+0x3e,struct.pack('<H',descriptor_count)), (HEAP*16+0x40,b'\0\0'),
  (SS*16+SP+4,b'\1\0')]:
  initial.update({address+i:b for i,b in enumerate(payload)})
 guard=Contract(initial, ((SS*16+0xefc0,SS*16+SP),(PROGRAM*16+0x6c2,PROGRAM*16+0x6c4)),ss=SS,sp_low=0xefc0,sp_high=SP+10)
 expected_cleanup={0x138:0,0x110a:2,0x314f:2}
 def saved_registers():
  return {name:u.reg_read(reg) for name,reg in [('bp',x86.UC_X86_REG_BP),('si',x86.UC_X86_REG_SI),('di',x86.UC_X86_REG_DI),('ds',x86.UC_X86_REG_DS),('es',x86.UC_X86_REG_ES),('sp',x86.UC_X86_REG_SP)]}
 def need(ok,msg):
  if not ok:raise ValueError(msg)
 def fail(msg):state['error']=msg;u.emu_stop()
 def mem(u,kind,a,size,value,data):
  try:
   if kind==unicorn.UC_MEM_WRITE:
    guard.write(a,size,value)
    accesses.append({'kind':'write','address':a,'size':size,'value':value})
   else:
    guard.read(a,size)
    accesses.append({'kind':'read','address':a,'size':size,'value':bytes(u.mem_read(a,size)).hex()})
  except Exception as ex:fail(str(ex))
 def code(u,a,size,data):
  try:
   guard.stack(u.reg_read(x86.UC_X86_REG_SS),u.reg_read(x86.UC_X86_REG_SP))
   ip=u.reg_read(x86.UC_X86_REG_IP);need(u.reg_read(x86.UC_X86_REG_CS)==CS and a==CS*16+ip,'unexpected CS/linear address');need(len(trace)<512,'step bound')
   if state['pending'] is not None:
    p=state['pending'];need(ip==p['ip'],'incorrect actual return IP');guard.returned(p['saved'],saved_registers(),call_sp=p['call_sp'],cleanup=p['cleanup'],expected_cleanup=expected_cleanup[p['lo']]);guard.release_return(SS*16+p['call_sp']-2);state['pending']=None
   f=frames[-1];need(f['lo']<=ip and ip+size<=f['hi'] and ip in BOUNDARIES[f['lo']],'cross-row/mid-instruction execution')
   key=(ip,tuple(x['return_ip'] for x in frames));need(key not in seen,'repeated execution vertex');seen.add(key)
   raw=bytes(u.mem_read(a,size));expected=bytes(execution_image[a-LOAD*16:a-LOAD*16+size]);need(raw==expected,'code differs from authenticated relocated image');ins=next(iter(Decoder(16,raw,ip=ip)));need(not ins.is_invalid and ins.len==size,'decoder/CPU boundary disagreement')
   if ip==ENDPOINT:
    need(len(frames)==1 and state['pending']is None,'nested/pending endpoint')
    need(raw==(bytes.fromhex('2e8e1e0000')if compiled else bytes.fromhex('2e8e1e9646')),'wrong unresolved/original endpoint')
    need(u.reg_read(x86.UC_X86_REG_DS)==HEAP,'unexpected pre-load DS');need(u.reg_read(x86.UC_X86_REG_SP)==SP-6 and u.reg_read(x86.UC_X86_REG_BP)==SP-2 and not guard.returns,'endpoint stack contract')
    state['endpoint']=True;state['success']=True;u.emu_stop();return
   need(not(ins.rflags_read & ~state['known_flags']),'undefined flag consumption');state['known_flags']=(state['known_flags']&~ins.rflags_undefined)|ins.rflags_written|ins.rflags_set|ins.rflags_cleared
   trace.append({'ip':ip,'instruction':str(ins),'sp':u.reg_read(x86.UC_X86_REG_SP),'ds':u.reg_read(x86.UC_X86_REG_DS),'ax':u.reg_read(x86.UC_X86_REG_AX),'flags':u.reg_read(x86.UC_X86_REG_EFLAGS),'depth':len(frames)-1,'registers':{name:u.reg_read(reg)for name,reg in [('ax',x86.UC_X86_REG_AX),('bx',x86.UC_X86_REG_BX),('cx',x86.UC_X86_REG_CX),('dx',x86.UC_X86_REG_DX),('si',x86.UC_X86_REG_SI),('di',x86.UC_X86_REG_DI),('bp',x86.UC_X86_REG_BP),('sp',x86.UC_X86_REG_SP),('cs',x86.UC_X86_REG_CS),('ds',x86.UC_X86_REG_DS),('es',x86.UC_X86_REG_ES),('ss',x86.UC_X86_REG_SS)]}})
   flow=ins.flow_control
   if flow==FlowControl.NEXT:need(ins.mnemonic in NEXT_MN,'unsupported instruction')
   elif flow==FlowControl.CALL:
    need(raw[0]==0xe8 and ins.near_branch_target in ROWS and ins.near_branch_target!=0x1b1c,'unresolved/indirect/FAR call');need(len(frames)<8,'call depth bound');dest=ins.near_branch_target;guard.reserve_return(SS*16+u.reg_read(x86.UC_X86_REG_SP)-2,ins.next_ip);frames.append({'lo':dest,'hi':ROWS[dest],'return_ip':ins.next_ip,'call_sp':u.reg_read(x86.UC_X86_REG_SP),'saved':saved_registers()})
   elif flow==FlowControl.RETURN:
    need(len(frames)>1,'root invocation returned before endpoint');need(raw[0] in (0xc3,0xc2),'unsupported far return');imm=int.from_bytes(raw[1:3],'little') if raw[0]==0xc2 else 0;need(imm in (0,2),'unsupported return cleanup');f=frames.pop();need(imm==expected_cleanup[f['lo']],'incorrect helper RET cleanup');need(u.reg_read(x86.UC_X86_REG_SP)==f['call_sp']-2,'incorrect pre-return SP');ptr=SS*16+u.reg_read(x86.UC_X86_REG_SP);need(int.from_bytes(u.mem_read(ptr,2),'little')==f['return_ip'],'corrupted near return address');state['pending']={'ip':f['return_ip'],'call_sp':f['call_sp'],'saved':f['saved'],'cleanup':imm,'lo':f['lo']}
   elif flow in (FlowControl.CONDITIONAL_BRANCH,FlowControl.UNCONDITIONAL_BRANCH):need(ins.near_branch_target in BOUNDARIES[f['lo']],'branch crosses row/middle')
   else:need(False,'unsupported control flow')
  except Exception as ex:fail(str(ex))
 def interrupt(u,n,data):fail(f'CPU interrupt/exception {n}')
 u.hook_add(unicorn.UC_HOOK_CODE,code);u.hook_add(unicorn.UC_HOOK_MEM_READ|unicorn.UC_HOOK_MEM_WRITE,mem);u.hook_add(unicorn.UC_HOOK_INTR,interrupt)
 try:u.emu_start(CS*16+0x1b1c,0x100000,count=512)
 except Exception as ex:state['error']=state['error'] or str(ex)
 result={'original_only':not compiled,'compiled_prefix_only':compiled,'success':state['success'],'error':state['error'],'endpoint_reached':state['endpoint'],'endpoint_executed':False,'boundary_registers':{name:u.reg_read(reg)for name,reg in [('ax',x86.UC_X86_REG_AX),('bx',x86.UC_X86_REG_BX),('cx',x86.UC_X86_REG_CX),('dx',x86.UC_X86_REG_DX),('si',x86.UC_X86_REG_SI),('di',x86.UC_X86_REG_DI),('bp',x86.UC_X86_REG_BP),('sp',x86.UC_X86_REG_SP),('ip',x86.UC_X86_REG_IP),('cs',x86.UC_X86_REG_CS),('ds',x86.UC_X86_REG_DS),('es',x86.UC_X86_REG_ES),('ss',x86.UC_X86_REG_SS),('flags',x86.UC_X86_REG_EFLAGS)]},'known_flags':state['known_flags'],'endpoint_operand':bytes(u.mem_read(CS*16+ENDPOINT+3,2)).hex(),'trace':trace,'memory_accesses':accesses,'input_divisor':divisor,'narrow_initial_bytes':len(initial),'protected_return_slots_at_endpoint':len(guard.returns),'final_mutable':(bytes(u.mem_read(SS*16+0xefc0,64)).hex(),bytes(u.mem_read(PROGRAM*16+0x6c2,2)).hex()),'original_exe_sha256':SHA,'relocated_image_sha256':hashlib.sha256(image).hexdigest(),'unicorn_version':unicorn.__version__,'execution_requires_unsandboxed_native_JIT':True,'compiled_code_proved':False,'startup_reachability_claim':False,'admission_credit':False}
 return result

def prove(transaction,candidate):
 if type(candidate)is not bytes or len(candidate)!=126 or candidate[:69]!=transaction.original_code[:69] or candidate[69:]!=transaction.code[69:] or candidate[72:74]!=bytes(2):raise ValueError('execution candidate not bound to computed prefix/raw tail')
 engine=_load_engine()
 original=_execute(transaction.original_exe,candidate,False,engine)
 compiled=_execute(transaction.original_exe,candidate,True,engine)
 for result in(original,compiled):
  if not result['success'] or result['error']is not None or not result['endpoint_reached'] or result['endpoint_executed'] or len(result['trace'])!=67 or result['narrow_initial_bytes']!=17 or result['protected_return_slots_at_endpoint']!=0:raise ValueError('bounded execution failed: '+str(result['error']))
 for key in('trace','memory_accesses','boundary_registers','known_flags','final_mutable'):
  if original[key]!=compiled[key]:raise ValueError('original/compiled execution disagreement: '+key)
 if original['endpoint_operand']!='9646'or compiled['endpoint_operand']!='0000':raise ValueError('endpoint identity/zero-tail disagreement')
 return original,compiled
