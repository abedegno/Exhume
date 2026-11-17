"""Run DOS-host Watcom C/WASM, TASM or MASM in private DOSBox-X stages."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


class CompilerError(RuntimeError):
    pass


def compiler_bin(home: Path, executable: str = 'wcc386.exe') -> str:
    for folder in ('BINB','BINW'):
        if (home/folder/executable).is_file():
            return folder
    raise CompilerError(f'DOS tool {executable} missing under {home}')


def run_batch(home: Path, work: Path, batch: str, timeout: float) -> None:
    home, work = home.resolve(), work.resolve()
    if any(c in str(home)+str(work) for c in '\r\n"'):
        raise ValueError('DOS mount paths cannot contain quotes or newlines')
    emulator = os.environ.get('EXHUME_DOSBOX_X') or shutil.which('dosbox-x')
    if not emulator:
        raise CompilerError('dosbox-x is not installed')
    (work/'RUN.BAT').write_bytes(batch.encode('ascii'))
    conf = work/'DOSBOX.CONF'
    conf.write_text('[sdl]\noutput=surface\n[cpu]\ncore=dynamic\ncycles=max\n'
                    '[mixer]\nnosound=true\n[dos]\nlfn=false\nkeyboardlayout=us\n')
    commands = [f'mount c "{work}"', f'mount d "{home}"', 'c:', 'call RUN.BAT', 'exit']
    args = [emulator, '-defaultconf', '-conf', str(conf), '-silent', '-fastlaunch',
            '-nogui', '-nomenu']
    for command in commands:
        args.extend(['-c', command])
    env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    try:
        result = subprocess.run(args, cwd=work, env=env, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(f'DOSBox-X exceeded {timeout:g} seconds') from error
    if result.returncode:
        raise CompilerError(f'DOSBox-X exit {result.returncode}: '
                            + result.stderr.decode(errors='replace')[-2000:])


def stage_includes(work: Path, include_dirs: list[Path] | None) -> list[str]:
    """Copy only explicitly supplied header trees, without following symlinks."""
    staged = []
    for index, directory in enumerate(include_dirs or []):
        directory = Path(directory)
        if directory.is_symlink():
            raise ValueError('include directory cannot be a symlink')
        dest = work/f'INC{index}'
        def ignore(folder, names):
            paths = [Path(folder)/name for name in names]
            if any(path.is_symlink() for path in paths):
                raise ValueError('include tree cannot contain a symlink')
            return [path.name for path in paths if not path.is_dir()
                    and path.suffix.lower() not in ('.h', '.inc')]
        shutil.copytree(directory, dest, ignore=ignore)
        staged.append(dest.name)
    return staged


def compile_source(home: Path, source: Path, output: Path,
                   options: list[str], timeout: float = 30,
                   *, include_dirs: list[Path] | None = None,
                   module_name: str | None = None) -> Path:
    """Compile one source; only publish its output after confirmed success.

    An optional sibling .log captures the compiler banner and diagnostics.
    Source includes may reference compiler headers and explicitly staged
    project header directories. The source media are never written to.
    An optional native module ID appends the documented /nm option while
    retaining SOURCE.C staging and its __FILE__ expansion. Existing callers
    keep their original command and module identity.
    """
    if module_name is not None:
        if not isinstance(module_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,7}', module_name):
            raise ValueError('native module name must be a DOS-safe identifier of at most eight characters')
        if any(re.match(r'[-/]nm(?:=|$)', option, re.I) for option in options):
            raise ValueError('conflicting native module-name option')
        options = [*options, '/nm='+module_name]
    if any(not re.fullmatch(r'[-/][A-Za-z0-9_=?+.,:\\/-]+', s) for s in options):
        raise ValueError('unsupported compiler option characters')
    if any(re.match(r'[-/]fo', option, re.I) for option in options):
        raise ValueError('output option is controlled by the runner')
    binaries=compiler_bin(home)
    output = output.resolve()
    if output == output.with_suffix('.log'):
        raise ValueError('object output cannot use the diagnostic .log path')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wc-', dir=output.parent) as directory:
        work = Path(directory)
        shutil.copyfile(source, work/'SOURCE.C')
        staged_includes = ['/i='+name for name in stage_includes(work, include_dirs)]
        command = 'wcc386 SOURCE.C /fo=OUT.OBJ ' + ' '.join(options+staged_includes)
        batch = ('@echo off\r\nset WATCOM=D:\\\r\nset INCLUDE=D:\\H\r\n'
                 f'set PATH=D:\\{binaries};D:\\BIN\r\n'
                 + command + ' > COMPILE.LOG\r\n'
                 'if errorlevel 1 goto failed\r\n'
                 'echo OK>STATUS.TXT\r\ngoto end\r\n:failed\r\n'
                 'echo FAILED>STATUS.TXT\r\n:end\r\n')
        run_batch(home, work, batch, timeout)
        log_path = work/'COMPILE.LOG'
        log = log_path.read_bytes() if log_path.exists() else b''
        output.with_suffix('.log').write_bytes(log)
        status = (work/'STATUS.TXT').read_text().strip() if (work/'STATUS.TXT').exists() else ''
        object_path = work/'OUT.OBJ'
        if status != 'OK' or not object_path.exists() or not object_path.stat().st_size:
            raise CompilerError(f'Compilation failed (status {status!r}):\n'
                                + log.decode('cp437', errors='replace'))
        shutil.copyfile(object_path, output)
    return output


def assembly_source_name(name: str | None) -> str:
    """Select a safe actual DOS assembler filename, preserving the old default."""
    if name is None:
        return 'SOURCE.ASM'
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,7}\.asm', name, re.I):
        raise ValueError('assembler source filename must be a DOS-safe .ASM basename')
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\.asm', name, re.I):
        raise ValueError('assembler source filename cannot name a DOS device')
    return name.upper()


def assemble_source(home: Path, source: Path, output: Path,
                    options: list[str], timeout: float = 30, *,
                    producer: str = 'wasm', source_name: str | None = None,
                    include_dirs: list[Path] | None = None) -> Path:
    """Assemble in an isolated stage; publish only successful output."""
    filename = assembly_source_name(source_name)
    if any(not re.fullmatch(r'[-/][A-Za-z0-9_=?+.,:\\/-]+', s) for s in options):
        raise ValueError('unsupported assembler option characters')
    if producer not in ('wasm','tasm','masm'):
        raise ValueError('unsupported assembler producer')
    if producer == 'wasm':
        if any(re.match(r'[-/]fo', option, re.I) for option in options):
            raise ValueError('output option is controlled by the runner')
        binaries = compiler_bin(home, 'wasm.exe')
    else:
        executable={'tasm':'TASM.EXE','masm':'MASM.EXE'}[producer]
        if not (home/executable).is_file():
            raise CompilerError(f'DOS assembler missing under {home}')
    output=output.resolve()
    if output == output.with_suffix('.log'):
        raise ValueError('object output cannot use the diagnostic .log path')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='asm-',dir=output.parent) as directory:
        work=Path(directory)
        shutil.copyfile(source,work/filename)
        include_switch = {'wasm': '/i=', 'tasm': '/i', 'masm': '/I'}[producer]
        staged_includes = [include_switch+name for name in stage_includes(work, include_dirs)]
        stage_options = options+staged_includes
        outputs='OUT.OBJ' if producer=='tasm' else 'OUT.OBJ,NUL,NUL;'
        if producer == 'wasm':
            command = 'wasm '+filename+' /fo=OUT.OBJ '+' '.join(stage_options)
            setup = ('set WATCOM=D:\\\r\nset INCLUDE=D:\\H\r\n'
                     f'set PATH=D:\\{binaries};D:\\BIN\r\n')
        else:
            command='D:\\'+executable+' '+' '.join(stage_options)+' '+filename+','+outputs
            setup = ''
        batch=('@echo off\r\n'+setup+command+' > ASSEMBLE.LOG\r\n'
               'if errorlevel 1 goto failed\r\n'
               'echo OK>STATUS.TXT\r\ngoto end\r\n:failed\r\n'
               'echo FAILED>STATUS.TXT\r\n:end\r\n')
        run_batch(home,work,batch,timeout)
        log_path=work/'ASSEMBLE.LOG'
        log=log_path.read_bytes() if log_path.exists() else b''
        output.with_suffix('.log').write_bytes(log)
        status_path=work/'STATUS.TXT'
        status=status_path.read_text().strip() if status_path.exists() else ''
        obj=work/'OUT.OBJ'
        if status!='OK' or not obj.is_file() or not obj.stat().st_size:
            raise CompilerError(f'Assembly failed (status {status!r}):\n'
                                +log.decode('cp437',errors='replace'))
        shutil.copyfile(obj,output)
    return output
