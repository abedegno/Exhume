"""Small Watcom command adapters. Project manifests retain game-specific policy."""
from pathlib import Path
import argparse,json,subprocess,sys
import wproject
from verified_snapshot import capture,check,publish_json

def main(cfg,argv,mode):
    parser=argparse.ArgumentParser(description=f'Watcom {mode}: typed OMF32/LE proof; project-owned source and link contracts.')
    if mode=='gate':
        parser.add_argument('operation',nargs='?',default='check',choices=['check','exact'])
    else:parser.add_argument('sources',nargs='*')
    parser.add_argument('--all',action='store_true');parser.add_argument('--no-build',action='store_true');parser.add_argument('--object',type=Path);parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    try:
        if mode in ('merge','gate'):
            (Path(cfg.build)/'watcom'/('accepted.json'if mode=='merge'else'gate.json')).unlink(missing_ok=True)
        root,manifest,spec=wproject.read_project(cfg);target=wproject.target_snapshot(cfg)
        if mode=='gate':names=list(spec['modules'])
        else:names=list(spec['modules'])if args.all else args.sources
        if not names:parser.error('name a source or use --all')
        if args.object and(len(names)!=1 or mode not in('match','verify')):parser.error('--object is read-only and requires exactly one match/verify source')
        if mode=='build':
            if args.no_build:parser.error('--no-build is only valid for read-only match/verify')
            reports=[];proof_inputs=[manifest,target]
            for name in names:
                obj,pins,out=wproject.build_source(cfg,name);proof_inputs.extend([*pins,out]);reports.append({'source':name,'object':str(obj),'sha256':out.sha256,'fresh_source_build':True})
            check(proof_inputs);result={'built':reports}
        else:
            fresh=not args.no_build and args.object is None
            if mode in('merge','gate')and not fresh:raise ValueError('acceptance/gate requires fresh source builds')
            reports=[wproject.verify_source(cfg,name,fresh=fresh,object_path=args.object)for name in names]
            proof_inputs=[manifest,target]
            for proof in reports:
                proof_inputs.extend(capture(path,expected)for path,expected in proof['inputs_sha256'].items())
                proof_inputs.append(capture(proof['object'],proof['object_sha256']))
            check(proof_inputs)
            result={'declared_modules_verified':True,'modules':reports,'whole_file_identity':False,'source_coverage':'Only declared modules proved; full source coverage and supplied-component attribution remain project obligations.'}
            if mode=='merge':
                receipt=Path(cfg.build)/'watcom'/'accepted.json';publish_json(receipt,result,proof_inputs)
            if mode=='gate':
                link=spec.get('link',{});command=link.get('command');output=link.get('output')
                if type(command)is not list or not command or not all(type(s)is str and s for s in command):raise ValueError('Watcom gate requires link.command as an argument list')
                out=wproject.project_path(root,output);build=Path(cfg.build).resolve()
                if not out.is_relative_to(build)or out==target.path.resolve():raise ValueError('link output must be a separate file inside the build directory')
                out.unlink(missing_ok=True);check(proof_inputs);run=subprocess.run(command,cwd=root,capture_output=True,text=True)
                log=Path(cfg.build)/'watcom'/'link.log';log.parent.mkdir(parents=True,exist_ok=True);log.write_text(run.stdout+run.stderr)
                if run.returncode:raise ValueError('project link command failed: '+str(log))
                exact=wproject.compare_file(cfg,out);result.update(exact);result['project_link_command']=command
                proof_inputs.append(capture(out,exact['sha256']))
                publish_json(Path(cfg.build)/'watcom'/'gate.json',result,proof_inputs)
        check([manifest,target])
        if args.report:publish_json(args.report,result,proof_inputs)
        print(json.dumps(result,indent=2));return 0
    except (ValueError,OSError,RuntimeError)as error:
        print(f'Watcom {mode}: {error}',file=sys.stderr);return 1
