"""Project-owned OMF32 placements and source builds, independent of any game."""
from pathlib import Path
import hashlib,json,shlex,tomllib
import runner
from le import LEImage
from omf32 import Module
from placements import verify_module
from verified_snapshot import capture,check,copy_verified,publish_json

def need(value,message):
    if not value:raise ValueError(message)

def project_path(root,name):
    need(type(name)is str and name and not Path(name).is_absolute()and'..'not in Path(name).parts,'project paths must be relative and contained')
    path=(root/name).resolve();need(path.is_relative_to(root),'project path escapes root');return path

def read_project(cfg):
    capture(cfg.file,cfg.config_sha256).check();capture(Path(cfg.profile_dir)/'profile.toml',cfg.profile_sha256).check()
    root=Path(cfg.root).resolve();settings=cfg.raw.get('watcom',{});name=settings.get('modules')
    need(name is not None,'Watcom requires [watcom] modules = "modules.json"')
    snap=capture(project_path(root,name));spec=snap.json();need(type(spec)is dict and type(spec.get('schema'))is int and spec['schema']==1,'Watcom module manifest schema must be1')
    entries=spec.get('modules');need(type(entries)is dict and bool(entries),'Watcom requires declared source modules')
    for source,item in entries.items():
        project_path(root,source);need(type(item)is dict,'module entry must be a table')
        need(set(item)<=set('placements symbols group_frames options home assembler include module_name source_name'.split()),'unknown Watcom module key')
        need(type(item.get('placements'))is dict and bool(item['placements']),'complete segment placements required')
        need(type(item.get('symbols',{}))is dict,'independent symbol bindings must be a table')
        for field in ('home','assembler','module_name','source_name'):
            if field in item:need(type(item[field])is str and bool(item[field]),field+' must be a nonempty string')
        if 'include'in item:need(type(item['include'])is list and all(type(x)is str for x in item['include']),'include must be a string list')
        if 'options'in item:need(type(item['options'])is list and all(type(x)is str for x in item['options']),'compiler options must be a string list')
    return root,snap,spec

def module_entry(cfg,source):
    root,manifest,spec=read_project(cfg);path=Path(source)
    absolute=path.resolve()if path.is_absolute()else project_path(root,str(path))
    need(absolute.is_relative_to(root),'source is outside project')
    name=absolute.relative_to(root).as_posix();need(name in spec['modules'],'source has no complete Watcom module contract: '+name)
    return root,manifest,name,spec['modules'][name]

def object_path(cfg,source):
    name=Path(source).as_posix();key=Path(name).stem.upper()+'-'+hashlib.sha256(name.encode()).hexdigest()[:8]
    return Path(cfg.build)/'watcom'/key/'SOURCE.OBJ'

def build_source(cfg,source):
    root,manifest,name,item=module_entry(cfg,source);src=capture(root/name);obj=object_path(cfg,name);obj.parent.mkdir(parents=True,exist_ok=True)
    obj.unlink(missing_ok=True);receipt=obj.with_suffix('.json');receipt.unlink(missing_ok=True)
    obj.with_suffix('.proof.json').unlink(missing_ok=True)
    home=item.get('home')or cfg.home;need(home is not None,'Watcom compiler/assembler home is required')
    home=Path(home);home=home if home.is_absolute()else root/home
    includes=[Path(p)for p in cfg.includes if Path(p).is_dir()]
    includes += [project_path(root,p)for p in item.get('include',[])]
    pins=[manifest,src,capture(cfg.file,cfg.config_sha256),capture(Path(cfg.profile_dir)/'profile.toml',cfg.profile_sha256)]
    def header_paths(directory):
        return tuple(p for p in sorted(directory.rglob('*'))if p.is_file()and p.suffix.lower()in('.h','.inc'))
    include_members={directory:header_paths(directory)for directory in includes}
    for directory in includes:
        pins.extend(capture(p)for p in sorted(directory.rglob('*'))if p.is_file()and p.suffix.lower()in('.h','.inc'))
    assembly=Path(name).suffix.lower()in('.asm','.s');component=item.get('assembler',cfg.raw.get('toolchain',{}).get('assembler_profile','wasm'))
    options=item.get('options')
    if assembly:
        producer={'wasm':'wasm','tasm31':'tasm','tasm':'tasm','masm51':'masm','masm':'masm'}.get(component)
        need(producer is not None,'unsupported Watcom assembler component')
        if options is None:
            if producer=='tasm':
                with (Path(cfg.profile_dir).parent/'tasm31/profile.toml').open('rb')as f:options=shlex.split(tomllib.load(f)['asm']['default_opts'])
            else:options=shlex.split(cfg.asm_opts)if producer=='wasm'else[]
        runner.assemble_source(home,root/name,obj,options,source_name=item.get('source_name'),producer=producer,include_dirs=includes)
    else:runner.compile_source(home,root/name,obj,item.get('options',shlex.split(cfg.c_opts)),include_dirs=includes,module_name=item.get('module_name'))
    output=capture(obj);check(pins)
    for directory,expected in include_members.items():
        need(header_paths(directory)==expected,'include header namespace changed during build')
    value={'source_only':True,'fresh_source_build':True,'source':name,'object':str(obj),'object_sha256':output.sha256,'inputs_sha256':{str(p.path):p.sha256 for p in pins}}
    publish_json(receipt,value,[*pins,output]);return obj,pins,output

def target_snapshot(cfg):
    path=cfg.image or cfg.exe_path;need(path is not None,'Watcom requires [binary] image or exe')
    expected=cfg.raw.get('watcom',{}).get('reference_sha256');return capture(path,expected)

def verify_source(cfg,source,*,fresh=False,object_path=None):
    root,manifest,name,item=module_entry(cfg,source);target=target_snapshot(cfg)
    if fresh:obj,pins,obj_snapshot=build_source(cfg,name)
    else:
        obj=Path(object_path)if object_path is not None else globals()['object_path'](cfg,name);pins=[manifest,capture(root/name)];obj_snapshot=capture(obj)
    module=Module.parse(obj_snapshot.raw);image=LEImage.parse(target.raw)
    proof=verify_module(module,image,item['placements'],item.get('symbols',{}),group_frames=item.get('group_frames'))
    check([*pins,obj_snapshot,target,manifest]);proof.update({'source':name,'object':str(obj),'object_sha256':obj_snapshot.sha256,'fresh_source_build':fresh,'whole_file_identity':False,'inputs_sha256':{str(p.path):p.sha256 for p in [*pins,target,manifest]},'source_scope':'declared module contribution only; complete image coverage and supplied dependencies are project obligations'})
    if fresh:publish_json(obj.with_suffix('.proof.json'),proof,[*pins,obj_snapshot,target,manifest])
    return proof

def compare_file(cfg,path):
    target=target_snapshot(cfg);built=capture(path)
    need(built.raw==target.raw,'whole executable differs (including header/data/serialized relocations)')
    check([target,built]);return{'whole_file_identity':True,'bytes':len(built.raw),'sha256':built.sha256,'normalization_or_masks':False}
