"""Explicit source dependencies for verified earlier-defined C compilation context.

Context sources are real recovered source files, never synthetic callee stubs.
Object splitting and independent helper proof are required before publication.
"""
import os
import sys
import subprocess
import tempfile
import re
import struct
from pathlib import Path

_CONTEXT = re.compile(r'/\*\s*context:\s*(.*?)\s*\*/', re.S)


def _source_named(cfg, name):
    """The one source in the tree (any directory under src, not the include directory) with
    this exact file name, or the path it would have in the source root when there is none."""
    root = cfg.src
    inc = os.path.realpath(getattr(cfg, 'include', os.path.join(cfg.src, 'include')))
    found = []
    for dirpath, dirs, files in os.walk(root):
        if os.path.realpath(dirpath) == inc:
            dirs[:] = []
            continue
        for f in files:
            if f.casefold() == name.casefold():
                found.append(os.path.join(dirpath, f))
    if len(found) > 1:
        raise ValueError('ambiguous/case-aliased context source')
    return found[0] if found else os.path.join(root, name)


def context_source(path, cfg):
    """Return one source-root helper path, rejecting nested or ambiguous context."""
    text = Path(path).read_text(encoding='latin1')
    declarations = _CONTEXT.findall(text)
    if not declarations:
        return None
    if len(declarations) != 1:
        raise ValueError('exactly one compilation context source is supported')
    name = declarations[0]
    if not re.fullmatch(r'[A-Za-z0-9_]{1,8}\.C', name, re.I):
        raise ValueError('context must name a source-root DOS C filename')
    helper = os.path.abspath(_source_named(cfg, name))
    if not os.path.isfile(helper) or os.path.realpath(helper) == os.path.realpath(path):
        raise ValueError('context helper is missing or refers to the primary source')
    if os.path.commonpath([os.path.realpath(helper), os.path.realpath(cfg.src)]) != os.path.realpath(cfg.src):
        raise ValueError('context helper must remain in the source directory')
    if _CONTEXT.search(Path(helper).read_text(encoding='latin1')):
        raise ValueError('nested compilation context is unsupported')
    return helper


def compose_source(path, cfg):
    """Compile the actual helper definition before the primary, without rewriting C."""
    helper = context_source(path, cfg)
    if helper is None:
        return None
    return Path(helper).read_text(encoding='latin1') + '\n' + Path(path).read_text(encoding='latin1')


def context_calls(path):
    """Explicit PUSH-CS sites sharing one actual helper and direct-body alias."""
    text = Path(path).read_text(encoding='latin1')
    values = re.findall(r'/\*\s*context-call:\s*(.*?)\s*\*/', text, re.S)
    if not values:
        raise ValueError('at least one context-call declaration is required')
    calls = []
    for value in values:
        match = re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)\s+([A-Za-z_][A-Za-z_0-9@]*)\s+([A-Za-z_][A-Za-z_0-9@]*)', value)
        if not match:
            raise ValueError('invalid context-call declaration')
        offset = int(match[1], 16 if match[1].lower().startswith('0x') else 10)
        if not 0 <= offset < 0x10000:
            raise ValueError('context call exceeds 16-bit code bounds')
        calls.append((offset, match[2], match[3]))
    if len({c[0] for c in calls}) != len(calls) or len({c[1:] for c in calls}) != 1:
        raise ValueError('context calls must have unique sites and one helper/body identity')
    return tuple(calls)


def context_call(path):
    """Single-site convenience API; multi-site consumers use context_calls."""
    calls = context_calls(path)
    if len(calls) != 1:
        raise ValueError('exactly one context-call declaration is required')
    return calls[0]


def context_switch(path):
    """Optional reviewed dispatch site, trailing table offset and word count."""
    values = re.findall(r'/\*\s*context-switch:\s*(.*?)\s*\*/',
                        Path(path).read_text(encoding='latin1'), re.S)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError('only one context switch is supported')
    number = r'(0x[0-9a-fA-F]+|[0-9]+)'
    match = re.fullmatch(number + r'\s+' + number + r'\s+' + number, values[0])
    if not match:
        raise ValueError('invalid context-switch declaration')
    jump, table, count = (int(v, 16 if v.lower().startswith('0x') else 10)
                          for v in match.groups())
    if not (0 <= jump < 0x10000 and 0 < table < 0x10000 and
            count > 0 and table + 2 * count <= 0x10000):
        raise ValueError('context switch exceeds 16-bit code bounds')
    return jump, table, count


def context_helper_switches(path):
    """Up to two explicit helper-suffix dispatches, in table placement order."""
    text=Path(path).read_text(encoding='latin1')
    values=re.findall(r'/\*\s*context-helper-switch:\s*(.*?)\s*\*/',text,re.S)
    if len(values)>2:
        raise ValueError('at most two context helper switches are supported')
    number=r'(0x[0-9a-fA-F]+|[0-9]+)'
    result=[];jumps=set();tables=set()
    for value in values:
        m=re.fullmatch(number+r'\s+'+number+r'\s+'+number,value)
        if not m:raise ValueError('invalid context-helper-switch declaration')
        jump,table,count=(int(v,16 if v.lower().startswith('0x') else 10) for v in m.groups())
        if not (0<=jump<0x10000 and 0<table<0x10000 and count>0 and table+2*count<=0x10000):
            raise ValueError('context helper switch exceeds 16-bit code bounds')
        if jump in jumps or table in tables:
            raise ValueError('duplicate context helper switch jump/table')
        jumps.add(jump);tables.add(table);result.append((jump,table,count))
    return tuple(result)


def context_helper_sparse_switches(path):
    """Explicit helper-only linear key/target suffixes, separate from dense."""
    values=re.findall(r'/\*\s*context-helper-sparse-switch:\s*(.*?)\s*\*/',
                      Path(path).read_text(encoding='latin1'),re.S)
    if len(values)>2:raise ValueError('at most two context helper switches are supported')
    number=r'(0x[0-9a-fA-F]+|[0-9]+)'
    result=[];jumps=set();tables=set()
    for value in values:
        m=re.fullmatch(number+r'\s+'+number+r'\s+'+number,value)
        if not m:raise ValueError('invalid context-helper-sparse-switch declaration')
        jump,table,count=(int(v,16 if v.lower().startswith('0x') else 10) for v in m.groups())
        if not(0<=jump<0x10000 and 0<table<0x10000 and count>0 and table+4*count<=0x10000):
            raise ValueError('context helper sparse switch exceeds 16-bit code bounds')
        if jump in jumps or table in tables:raise ValueError('duplicate context helper sparse switch jump/table')
        jumps.add(jump);tables.add(table);result.append((jump,table,count))
    return tuple(result)


def context_helper_entry(path):
    """Optional explicit identity of one accepted non-exported helper row."""
    values=re.findall(r'/\*\s*context-helper-entry:\s*(.*?)\s*\*/',
                      Path(path).read_text(encoding='latin1'),re.S)
    if not values:return None
    if len(values)!=1 or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',values[0]):
        raise ValueError('invalid context-helper-entry declaration')
    return values[0]


def validate_original_context_calls(cfg,helper_target,primary_target,
                                    helper_info,primary_info,calls):
    """Bind every source-mapped call to its actual original helper entry0.

    A compiled relative placeholder and a newly inferred alias do not prove
    the original callee identity. Read original bytes before externalization.
    """
    _,_,_,ho=helper_info;pb,ps,_,po=primary_info
    hi,pi=cfg.overlay_index(helper_target),cfg.overlay_index(primary_target)
    if (hi!=pi or (hi is not None and
            (type(hi) is not int or not 0<=hi<=0xffff))):
        raise ValueError('original context call must share the helper original overlay')
    if (any(type(value) is not int for value in (ho,pb,ps,po))
            or pb<0 or ps<=0 or not 0<=ho<=0xffff or not 0<=po<=0xffff):
        raise ValueError('original context call has invalid target bounds')
    image=cfg.exe
    for offset,_,_ in calls:
        if (type(offset) is not int or offset<0 or offset+4>ps
                or po+offset+4>0x10000):
            raise ValueError('original context call exceeds image or primary payload bounds')
        at=pb+offset
        if at+4>len(image):
            raise ValueError('original context call exceeds image or primary payload bounds')
        if image[at:at+2]!=b'\x0e\xe8':
            raise ValueError('original context call must be exact PUSH CS/E8')
        displacement=struct.unpack_from('<h',image,at+2)[0]
        target=(po+offset+4+displacement)&0xffff
        if target!=ho:
            raise ValueError(f'original context call targets {target:04X}, not helper entry {ho:04X}')


def validate_private_resident_spans(cfg,helper_info,primary_info):
    """Bind both source slices to one authoritative original resident CODE span.

    Resident MZ has no FBOV function-export table. Private here means the
    explicitly selected accepted source entry has no CODE PUBLIC; low-level
    checks and original helper WHOLE/strict remain mandatory.
    """
    hb,hs,_,ho=helper_info;pb,ps,_,po=primary_info
    def need(ok,message):
        if not ok:raise ValueError('private resident helper: '+message)
    need(all(type(v) is int for v in (hb,hs,ho,pb,ps,po))
         and hs>0 and ps>0 and 0<=ho<0x10000 and 0<=po<0x10000
         and ho+hs<=0x10000 and po+ps<=0x10000,'invalid source span bounds')
    e=cfg.exe
    need(len(e)>=28 and e[:2]==b'MZ','invalid original MZ header')
    last,pages,relocs,header_paras=struct.unpack_from('<4H',e,2)
    header=header_paras*16;end=(pages-1)*512+last if last else pages*512
    reloc_table=struct.unpack_from('<H',e,0x18)[0]
    need(pages>0 and last<512 and 28<=header<=end<=len(e)
         and (not relocs or (28<=reloc_table and reloc_table+4*relocs<=header)),
         'invalid original MZ bounds')
    need(header<=hb and hb+hs<=end and header<=pb and pb+ps<=end,
         'source spans are outside original resident image')
    delta=hb-ho-header
    need(delta>=0 and delta%16==0 and delta//16<=0xffff
         and pb-po-header==delta,'source placement disagrees with resident CS')
    paragraph=delta//16
    need(end+16<=len(e) and e[end:end+4]==b'FBOV','invalid original FBOV header')
    table,count=struct.unpack_from('<II',e,end+8)
    need(count>0 and table>=header and table+count*8<=end,'invalid original segment table')
    selected=[]
    for k in range(count):
        para,maximum,flags,minimum=struct.unpack_from('<4H',e,table+8*k)
        if (para==paragraph and flags==1 and minimum<maximum
                and minimum<=ho and ho+hs<=maximum
                and minimum<=po and po+ps<=maximum):
            selected.append((k,maximum))
    need(len(selected)==1,'source slices must share one unambiguous resident CODE span')
    need(header+16*paragraph+selected[0][1]<=end,
         'selected resident CODE span exceeds original image')


def validate_private_helper_entry(cfg,entry,helper_target,primary_target,
                                  helper_info,primary_info,calls,helper_switches):
    """Bind an accepted sole row to authoritative original private CODE entry0.

    No current link state or canonical helper object supplies this identity.
    Original WHOLE and strict proof are still required after splitting.
    """
    from symbol_address import overlay_location
    from context_omf import PrivateHelperEntry
    hb,hs,rows,ho=helper_info;pb,ps,_,po=primary_info
    if helper_switches or len(rows)!=1:
        raise ValueError('private helper requires one complete function without tables')
    name,_,offset,size=rows[0]
    if (name!=entry or any(call[1]!=entry for call in calls)
            or type(offset) is not int or offset!=0
            or type(size) is not int or size<=0 or size!=hs):
        raise ValueError('private helper entry name or complete row bounds disagree')
    hi,pi=cfg.overlay_index(helper_target),cfg.overlay_index(primary_target)
    if hi is None and pi is None:
        validate_private_resident_spans(cfg,helper_info,primary_info)
        return PrivateHelperEntry(entry,0,hs)
    if hi is None or pi is None or hi!=pi:
        raise ValueError('private helper must share the primary original overlay')
    para,physical=overlay_location(cfg,hi,ho,hs)
    _,primary_physical=overlay_location(cfg,pi,po,ps)
    if physical!=hb or primary_physical!=pb or hb+hs>pb:
        raise ValueError('private helper target disagrees with original physical placement')
    # overlay_location has already validated the MZ/FBOV table and body span.
    # Validate the complete original export table as well: absence of a stub
    # must not be inferred from a malformed/truncated export range.
    image=cfg.exe;last,pages=struct.unpack_from('<HH',image,2)
    end=(pages-1)*512+last if last else pages*512
    header=struct.unpack_from('<H',image,8)[0]*16
    table=struct.unpack_from('<I',image,end+8)[0]
    _,maximum,_,_=struct.unpack_from('<4H',image,table+hi*8)
    stub=header+16*para
    count=struct.unpack_from('<H',image,stub+12)[0]
    if maximum!=32+5*count or stub+maximum>end:
        raise ValueError('invalid original private-helper export table bounds')
    code_size=struct.unpack_from('<H',image,stub+8)[0]
    for at in range(32,maximum,5):
        if image[stub+at:stub+at+2]!=b'\xcd?' or image[stub+at+4]!=0:
            raise ValueError('invalid original private-helper export record')
        target=struct.unpack_from('<H',image,stub+at+2)[0]
        if target>=code_size:
            raise ValueError('original private-helper export target exceeds CODE')
        if target==ho:
            raise ValueError('private helper entry has an original FBOV export')
    return PrivateHelperEntry(entry,0,hs)


def split_compiled_object(raw, helper_size, primary_size, calls, switch=None, code_size=None,
                          helper_switches=(), helper_code_size=None, private_helper_entry=None,
                          helper_sparse_switches=()):
    from context_omf import ContextCall, SplitSpec, split_context
    public, external = calls[0][1:]
    extra = {}
    if switch is not None:
        from context_omf import ContextSwitch
        extra = dict(primary_code_size=code_size, switches=(ContextSwitch(*switch),))
    if helper_switches:
        from context_omf import ContextSwitch
        extra.update(helper_code_size=helper_code_size,
                     helper_switches=tuple(ContextSwitch(*switch) for switch in helper_switches))
    if helper_sparse_switches:
        from context_omf import ContextSparseSwitch
        extra.update(helper_code_size=helper_code_size,
                     helper_sparse_switches=tuple(ContextSparseSwitch(*switch) for switch in helper_sparse_switches))
    if private_helper_entry is not None:
        extra['private_helper_entry']=private_helper_entry
    plan = SplitSpec(helper_size, primary_size, public, external,
                     tuple(ContextCall(c[0], 0) for c in calls), **extra)
    return split_context(raw, plan)


def finish_object(path, cfg, raw, proof_dir):
    """Return primary bytes only after independent original-byte/fixup helper proof."""
    entry=context_helper_entry(path)
    helper = context_source(path, cfg)
    if helper is None:
        if entry is not None:
            raise ValueError('context-helper-entry requires a context source')
        return raw
    if context_helper_entry(helper) is not None:
        raise ValueError('context-helper-entry belongs only to the primary source')
    helper_target = cfg.directives(helper)[0]
    primary_target = cfg.directives(str(path))[0]
    if not helper_target or not primary_target or helper_target not in cfg.matched_set():
        raise ValueError('context helper must be an accepted source with a target')
    hb, hs, helper_rows, ho = cfg.load_targets(helper_target)
    pb, ps, primary_rows, po = cfg.load_targets(primary_target)
    if hb - ho != pb - po or not (hb + hs <= pb):
        raise ValueError('context helper must precede primary in the same original code segment')
    calls = context_calls(path)
    validate_original_context_calls(cfg,helper_target,primary_target,
            (hb,hs,helper_rows,ho),(pb,ps,primary_rows,po),calls)
    switch = context_switch(path)
    helper_switches = context_helper_switches(path)
    helper_sparse_switches = context_helper_sparse_switches(path)
    helper_tables=[(j,t,n,2) for j,t,n in helper_switches]+[(j,t,n,4) for j,t,n in helper_sparse_switches]
    if len(helper_tables)>2 or len({x[0] for x in helper_tables})!=len(helper_tables) or len({x[1] for x in helper_tables})!=len(helper_tables):
        raise ValueError('at most two unique helper table dispatches are supported')
    for group in (helper_switches,helper_sparse_switches):
        if any(a[1]>=b[1] for a,b in zip(group,group[1:])):
            raise ValueError('helper tables must be in physical placement order')
    helper_tables.sort(key=lambda x:x[1])
    private_entry=None
    if entry is not None:
        private_entry=validate_private_helper_entry(cfg,entry,helper_target,primary_target,
                (hb,hs,helper_rows,ho),(pb,ps,primary_rows,po),calls,helper_tables)
    helper_code_size = None
    if helper_tables:
        if len(helper_rows)!=1 or helper_rows[0][2]!=0:
            raise ValueError('helper switches require one helper function starting at zero')
        helper_code_size=helper_rows[0][3]
        cursor=helper_code_size
        for _,table,count,width in helper_tables:
            if table!=cursor:
                raise ValueError('helper switch tables must form a contiguous function trailing suffix')
            cursor+=width*count
        if not 0<helper_code_size<hs or cursor!=hs:
            raise ValueError('helper switches must own exactly the accepted helper trailing tables')
    code_size = None
    if switch is not None:
        if len(primary_rows) != 1 or primary_rows[0][2] != 0:
            raise ValueError('context switch requires one primary function starting at zero')
        code_size = primary_rows[0][3]
        if switch[1] != code_size or code_size + 2 * switch[2] != ps:
            raise ValueError('context switch must own exactly the declared function trailing table')
    parent = Path(proof_dir); parent.mkdir(parents=True, exist_ok=True)
    # Proof subprocesses read an immutable per-invocation helper, including when
    # another build of this stem publishes its own diagnostic sidecars.
    proof = Path(tempfile.mkdtemp(prefix='proof-', dir=parent))
    (proof / 'CONTEXT.OBJ').write_bytes(raw)
    extra={} if private_entry is None else dict(private_helper_entry=private_entry)
    if helper_sparse_switches:
        extra['helper_sparse_switches']=helper_sparse_switches
    result = split_compiled_object(raw, hs, ps, calls, switch, code_size,
                                   helper_switches, helper_code_size, **extra)
    helper_obj = proof / 'HELPER.OBJ'
    helper_obj.write_bytes(result.helper_obj)
    report = []
    for tool in ('match.py', 'verify.py'):
        command = [sys.executable, str(Path(__file__).with_name(tool)), helper,
                   '--object', str(helper_obj), '--config', cfg.file]
        check = subprocess.run(command, cwd=cfg.root, text=True, capture_output=True)
        report.append(check.stdout + check.stderr)
        (proof / 'HELPER-PROOF.LOG').write_text('\n'.join(report), encoding='latin1')
        if check.returncode or (tool == 'match.py' and 'WHOLE SEGMENT MATCHES' not in check.stdout) or (tool == 'verify.py' and '-- fixups and data verified' not in check.stdout):
            raise ValueError('context helper proof failed: ' + check.stdout + check.stderr)
    return result.primary_obj

# Bounded multi-helper mode deliberately leaves the legacy APIs above intact.
from dataclasses import dataclass

@dataclass(frozen=True)
class ContextNode:
    source: str
    target: str
    original_base: int
    original_org: int
    size: int
    entry: str
    private_entry: object = None

@dataclass(frozen=True)
class ContextEdge:
    caller: int
    offset: int
    callee: int
    direct_external: str

@dataclass(frozen=True)
class ContextGraph:
    helpers: tuple
    primary: ContextNode
    edges: tuple


def uses_multi_context(path,cfg):
    values=_CONTEXT.findall(Path(path).read_text(encoding='latin1'))
    if len(values)>1:return True
    if not values:return False
    # Reuse every legacy path/name check before inspecting the dependency.
    name=values[0]
    if not re.fullmatch(r'[A-Za-z0-9_]{1,8}\.C',name,re.I):return False
    helper=Path(_source_named(cfg,name))
    return helper.is_file() and bool(_CONTEXT.search(helper.read_text(encoding='latin1')))


def _multi_calls(path):
    text=Path(path).read_text(encoding='latin1');calls=[]
    for value in re.findall(r'/\*\s*context-call:\s*(.*?)\s*\*/',text,re.S):
        m=re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)\s+([A-Za-z_][A-Za-z_0-9@]*)\s+([A-Za-z_][A-Za-z_0-9@]*)',value)
        if not m:raise ValueError('invalid multi context-call declaration')
        offset=int(m[1],16 if m[1].lower().startswith('0x') else 10)
        if not 0<=offset<0x10000:raise ValueError('invalid multi call offset')
        calls.append((offset,m[2],m[3]))
    if len({c[0] for c in calls})!=len(calls):raise ValueError('duplicate multi call offset')
    return tuple(calls)


def resolve_context_graph(path,cfg):
    """Resolve accepted sources and bind each original per-caller edge once."""
    primary=str(Path(path).absolute());root=Path(cfg.src).resolve()
    if not _CONTEXT.search(Path(primary).read_text(encoding='latin1')):return None
    nodes={};dependencies={};visiting=set();seen_real={};private={};calls={}
    def helper_path(name):
        if not re.fullmatch(r'[A-Za-z0-9_]{1,8}\.[Cc]',name):raise ValueError('invalid multi context filename')
        found=Path(_source_named(cfg,name))
        found=root/os.path.relpath(found,cfg.src) if found.is_file() else found
        matches=[found] if found.is_file() else []
        if len(matches)!=1 or matches[0].name!=name:raise ValueError('ambiguous/case-aliased context source')
        p=matches[0];real=p.resolve()
        # Any directory under the source root, never outside it, never through a link.
        if root not in real.parents or not p.is_file() or real!=p.absolute():raise ValueError('context helper escapes source root or aliases another source')
        if real==Path(primary).resolve():raise ValueError('context self dependency')
        if real in seen_real and seen_real[real]!=str(p):raise ValueError('context realpath alias')
        seen_real[real]=str(p);return str(p)
    def visit(source,is_primary=False):
        if source in visiting:raise ValueError('context dependency cycle')
        if source in nodes:return
        visiting.add(source)
        text=Path(source).read_text(encoding='latin1')
        if re.search(r'/\*\s*context-(?:helper-)?(?:sparse-)?switch:',text):raise ValueError('multi context tables unsupported')
        target=cfg.directives(source)[0]
        if not target or (not is_primary and target not in cfg.matched_set()):raise ValueError('unaccepted context helper')
        info=cfg.load_targets(target);base,size,rows,org=info
        if (any(type(v) is not int for v in (base,size,org)) or base<0 or size<=0 or not 0<=org<org+size<=0x10000
                or len(rows)!=1 or type(rows[0][2]) is not int or type(rows[0][3]) is not int or rows[0][2]!=0 or rows[0][3]!=size):raise ValueError('multi region must be one complete table-free row')
        decls=_CONTEXT.findall(text)
        if len(set(x.casefold() for x in decls))!=len(decls):raise ValueError('duplicate context declaration')
        deps=tuple(helper_path(name) for name in decls)
        dependencies[source]=deps;calls[source]=_multi_calls(source)
        annotated=set()
        for value in re.findall(r'/\*\s*context-helper-entry:\s*(.*?)\s*\*/',text,re.S):
            if len(deps)==1 and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',value):
                value=Path(deps[0]).name+' '+value
            m=re.fullmatch(r'([A-Za-z0-9_]{1,8}\.C)\s+([A-Za-z_][A-Za-z_0-9]*)',value,re.I)
            if not m:raise ValueError('multi private entry must be source-qualified')
            dep=helper_path(m[1])
            if dep not in deps:raise ValueError('private annotation must name declared helper')
            if dep in annotated:raise ValueError('duplicate qualified private entry')
            annotated.add(dep)
            if dep in private and private[dep]!=m[2]:raise ValueError('conflicting private helper identity')
            private[dep]=m[2]
        nodes[source]=(target,info)
        for dep in deps:
            if dep in visiting:raise ValueError('context dependency cycle')
            visit(dep)
        visiting.remove(source)
    visit(primary,True)
    if not 1<=len(nodes)-1<=4:raise ValueError('multi context permits at most four helpers')
    ordered=sorted(nodes,key=lambda s:nodes[s][1][0])
    if ordered[-1]!=primary:raise ValueError('all context helpers must precede primary')
    for left,right in zip(ordered,ordered[1:]):
        a,b=nodes[left][1],nodes[right][1]
        if a[0]+a[1]>b[0]:raise ValueError('overlapping context regions')
    from symbol_address import overlay_location
    for source in ordered[:-1]:
        target,info=nodes[source];pt,pi=nodes[primary]
        idx,pidx=cfg.overlay_index(target),cfg.overlay_index(pt)
        if idx!=pidx or info[0]-info[3]!=pi[0]-pi[3]:raise ValueError('context region segment mismatch')
        if idx is None:validate_private_resident_spans(cfg,info,pi)
        else:
            if overlay_location(cfg,idx,info[3],info[1])[1]!=info[0] or overlay_location(cfg,pidx,pi[3],pi[1])[1]!=pi[0]:raise ValueError('context physical overlay mismatch')
    def closure(s):
        result=set()
        for d in dependencies[s]:result.add(d);result.update(closure(d))
        return result
    incoming={};aliases={};edges=[];positions={s:i for i,s in enumerate(ordered)}
    if any(positions[dep]>=positions[source] for source,deps in dependencies.items() for dep in deps):
        raise ValueError('context dependency must precede caller physically')
    # Resolve identity through accepted sole-row names for private nodes; public
    # identities must also be unique, and will be checked against actual PUBs.
    names={}
    for s in ordered[:-1]:
        name=private.get(s,nodes[s][1][2][0][0])
        candidates=(name,) if s in private else (name,'_'+name)
        for candidate in candidates:
            if candidate in names:raise ValueError('ambiguous helper entry identity')
            names[candidate]=s
    for source in ordered:
        for off,name,alias in calls[source]:
            dep=names.get(name)
            if dep not in closure(source) or positions[dep]>=positions[source]:raise ValueError('call must target declared earlier helper entry')
            if alias==name or alias in names:raise ValueError('body alias collides with entry identity')
            if dep in aliases and aliases[dep]!=alias:raise ValueError('conflicting helper body alias')
            if alias in aliases.values() and aliases.get(dep)!=alias:raise ValueError('duplicate helper body alias')
            if dep in incoming and incoming[dep][0][1][1]!=name:raise ValueError('helper entry uses conflicting public spellings')
            aliases[dep]=alias;incoming.setdefault(dep,[]).append((source,(off,name,alias)))
            validate_original_context_calls(cfg,nodes[dep][0],nodes[source][0],nodes[dep][1],nodes[source][1],((off,name,alias),))
            edges.append(ContextEdge(positions[source],off,positions[dep],alias))
    if set(incoming)!=set(ordered[:-1]):raise ValueError('unused helper context definition')
    result=[]
    for source in ordered:
        target,info=nodes[source];base,size,rows,org=info
        entry=incoming[source][0][1][1] if source!=primary else rows[0][0];descriptor=None
        if source in private:
            caller,call=incoming[source][0]
            descriptor=validate_private_helper_entry(cfg,entry,target,nodes[caller][0],info,nodes[caller][1],(call,),())
        result.append(ContextNode(source,target,base,org,size,entry,descriptor))
    return ContextGraph(tuple(result[:-1]),result[-1],tuple(edges))


_legacy_compose_source=compose_source

def compose_source(path,cfg):
    if not uses_multi_context(path,cfg):return _legacy_compose_source(path,cfg)
    graph=resolve_context_graph(path,cfg)
    return '\n'.join(Path(n.source).read_text(encoding='latin1') for n in (*graph.helpers,graph.primary))


_legacy_finish_object=finish_object

def finish_object(path,cfg,raw,proof_dir):
    if not uses_multi_context(path,cfg):return _legacy_finish_object(path,cfg,raw,proof_dir)
    graph=resolve_context_graph(path,cfg)
    from context_omf import RegionSpec,RegionCall,MultiSplitSpec,split_multi_context
    nodes=(*graph.helpers,graph.primary)
    plan=MultiSplitSpec(tuple(RegionSpec(n.size,n.entry,n.private_entry,i==len(nodes)-1) for i,n in enumerate(nodes)),
                        tuple(RegionCall(e.caller,e.offset,e.callee,e.direct_external) for e in graph.edges))
    parent=Path(proof_dir);parent.mkdir(parents=True,exist_ok=True)
    proof=Path(tempfile.mkdtemp(prefix='proof-',dir=parent))
    (proof/'CONTEXT.OBJ').write_bytes(raw)
    parts=split_multi_context(raw,plan)
    # Every immutable helper output is checked against its own original source,
    # never a cached canonical OBJ. No primary bytes escape on any failed proof.
    for i,(node,part) in enumerate(zip(graph.helpers,parts)):
        obj=proof/('HELPER%d.OBJ'%i);obj.write_bytes(part);report=[]
        for tool in ('match.py','verify.py'):
            command=[sys.executable,str(Path(__file__).with_name(tool)),node.source,
                     '--object',str(obj),'--config',cfg.file]
            check=subprocess.run(command,cwd=cfg.root,text=True,capture_output=True)
            report.append(check.stdout+check.stderr)
            (proof/('HELPER%d-PROOF.LOG'%i)).write_text('\n'.join(report),encoding='latin1')
            marker='WHOLE SEGMENT MATCHES' if tool=='match.py' else '-- fixups and data verified'
            if check.returncode or marker not in check.stdout:
                raise ValueError('context helper proof failed: '+check.stdout+check.stderr)
    return parts[-1]
