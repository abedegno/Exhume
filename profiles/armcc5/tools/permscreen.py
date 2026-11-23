"""Screen a permuter candidate with clang before it is kept: is it still the same program?

compiler.md ("Searching for the source"): a random permuter writes C that is not the same
program (two n++ in one expression, a temporary read before it is set), and in past runs the
lowest score was such a text. Each candidate that reaches a new best is run through clang
`-fsyntax-only` for the target, with armcc's keywords defined away for clang only, and its
-Wunsequenced, -Wuninitialized and -Wsometimes-uninitialized warnings compared with the base
source's: a candidate with any warning of those kinds the base does not have is rejected (the
base's own warnings are allowed, since a tree may compile with some). clang's errors are not
compared: armcc accepts C that clang refuses, and armcc itself has already compiled the
candidate."""
import os, re, shutil, subprocess, tempfile, collections

ARMCC_KEYWORDS = ['-D__packed=__attribute__((packed))', '-D__forceinline=__inline__', '-D__inline=__inline__', '-D__irq=', '-D__weak=',
                  '-D__value_in_regs=', '-D__align(x)=', '-D__restrict=', '-D__int64=long long', '-D__asm(x)=',
                  '-D__declspec(x)=', '-D__swi(x)=', '-D__pure=', '-D__softfp=', '-D__svc(x)=', '-D__smc(x)=',
                  '-D__ARMCC_VERSION=5060300', '-D__CC_ARM=1', '-D__TARGET_ARCH_7_M=1', '-D__thumb=1',
                  '-D__breakpoint(x)=', '-D__nop()=', '-D__wfi()=', '-D__enable_irq()=', '-D__disable_irq()=0']
WARNINGS = ['-Wno-everything', '-Wunsequenced', '-Wuninitialized', '-Wsometimes-uninitialized']
KINDS = ('-Wunsequenced', '-Wuninitialized', '-Wsometimes-uninitialized', '-Wconditional-uninitialized')
_W = re.compile(r':(\d+):\d+: warning: (.*?) \[(-W[\w-]+)\]')


def clang():
    return shutil.which('clang')


class Screen:
    def __init__(self, src_path, opts, base_text, armcc_include=None):
        self.cc = clang()
        self.args = ['--target=arm-none-eabi', '-mcpu=cortex-m3', '-mthumb', '-std=gnu99', '-fsyntax-only',
                     '-ferror-limit=0', '-fno-builtin'] + ARMCC_KEYWORDS + WARNINGS
        inc = ['-I' + os.path.dirname(os.path.abspath(src_path))]
        for o in opts:
            if o.startswith('-I') or o.startswith('-D'): inc.append(o)
        if armcc_include: inc += ['-isystem', armcc_include]
        self.args += inc
        self.stem = os.path.basename(src_path)
        self.base = self.warnings(base_text) if self.cc else None

    def warnings(self, text):
        """Counter of (kind, message without names of line numbers) for the screened kinds."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, self.stem)
            with open(p, 'w', encoding='latin1') as f: f.write(text)
            r = subprocess.run([self.cc] + self.args + [p], capture_output=True, text=True)
        out = collections.Counter()
        for m in _W.finditer(r.stderr):
            if m.group(3) in KINDS:
                out[(m.group(3), re.sub(r"'[^']*'", "'_'", m.group(2)))] += 1
                out[('msg', m.group(3), m.group(2))] += 1
        return out

    def check(self, text):
        """(ok, new warnings): ok when the candidate has no screened warning the base lacks."""
        if self.base is None: return True, ['clang not found: not screened']
        w = self.warnings(text)
        new = [f'{k[1]}: {k[2]}' for k, v in w.items() if k[0] == 'msg' and v > self.base.get(k, 0)]
        return not new, new
