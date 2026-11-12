"""Resolve explicitly bound offsets to accepted entries in an original overlay body.

Bindings identify the CODE entry, separately from the resident FBOV stub identity.
An alias may select a canonical far-public row only through explicit target/entry
configuration; acceptance, original placement and zero-addend checks still apply.
"""
from symbol_address import overlay_location


def resolve(cfg, name, caller_target, offset, *, addends=(0,)):
    binding=cfg.raw.get('names', {}).get('near_entries', {}).get(name)
    if binding is None:return None
    if isinstance(binding,str):target,entry=binding,name
    elif (isinstance(binding,dict) and set(binding)=={'target','entry'} and
          all(isinstance(binding[k],str) and binding[k] for k in binding)):
        target,entry=binding['target'],binding['entry']
    else:raise ValueError(f'{name}: invalid near entry target/entry binding')
    if any(addends):raise ValueError(f'{name}: near callback entry cannot have an address addend')
    if target not in cfg.matched_set():raise ValueError(f'{name}: near entry target is not accepted: {target}')
    caller,callee=cfg.overlay_index(caller_target),cfg.overlay_index(target)
    if caller is None or callee is None or caller!=callee:
        raise ValueError(f'{name}: near entry must share the caller original overlay')
    old_hints=getattr(cfg,'far_hints',None)
    try:
        base,total,rows,org=cfg.load_targets(target)
        caller_base,caller_size,_,caller_org=cfg.load_targets(caller_target)
    finally:
        if old_hints is not None:cfg.far_hints=old_hints
    candidates=[row for row in rows if cfg.mangle(row[0])==cfg.mangle(entry)]
    if len(candidates)!=1:raise ValueError(f'{name}: near entry target identity missing or ambiguous')
    _,_,start,size=candidates[0]
    if not(0<=start<total and size>0 and start+size<=total and
           0<=org+start<0x10000 and org+start==offset):
        raise ValueError(f'{name}: near entry bounds or original offset disagree')
    _,physical_base=overlay_location(cfg,callee,org,total)
    _,physical_entry=overlay_location(cfg,callee,offset,size)
    _,physical_caller=overlay_location(cfg,caller,caller_org,caller_size)
    if base!=physical_base or physical_entry!=base+start:
        raise ValueError(f'{name}: target disagrees with original overlay file bounds')
    if caller_base!=physical_caller:
        raise ValueError(f'{name}: caller target disagrees with original overlay file bounds')
    return ('OVL',callee,offset)
