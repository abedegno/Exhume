"""armpermute2.py's parts: the object reader (orcobj), the C reader (permc), the rewrite rules
(permrules) and the scorer's instruction tokens (permscore).

No compiler is needed. Each rule is run on a small generic C file that has one site for it:
it must apply, the result must still tokenize with balanced brackets and (when clang is on the
host) pass `clang -fsyntax-only`, and for a two-way rule some application of the rule (or its
inverse) to the result must give the original text back (compared without whitespace). With
EXHUME_ARMCC_LINUX and EXHUME_ARMCC_MAPPINGS set (a Linux armcc), the object reader is also
checked against fromelf's listing of a compiled object."""
import hashlib, os, random, shutil, struct, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'tools'))
import orcobj, permc, permrules as R, permscore      # noqa: E402

CLANG = shutil.which('clang')

PRELUDE = '''typedef unsigned char uchar;
typedef unsigned short ushort;
typedef unsigned int uint;
typedef struct { ushort level; uchar flags; uchar mode; ushort limits[4]; } State;
extern State state;
extern ushort table[8];
extern ushort samples[];
extern uchar status, phase;
extern uint counter;
extern void send(uchar code, ushort value);
extern int read_byte(void);
'''


def ctx_for(body, func='f'):
    text = PRELUDE + body
    toks, tail = permc.tokenize(text)
    ti = permc.TypeInfo(); ti.scan(text); ti.scan_globals(toks)
    return R.Ctx(toks, tail, func, ti), text


def norm(text):
    return ' '.join(t[1] for t in permc.tokenize(text)[0])


def run_rule(rule, body, func='f', seeds=200, everywhere=None, want=None):
    """[(seed, new text, Edit)] of the distinct results of a rule on body."""
    ctx, text = ctx_for(body, func)
    out, seen = [], set()
    for seed in range(seeds):
        rng = random.Random(seed)
        ev = (seed % 2 == 0) if everywhere is None else everywhere
        ed = R.BY_ID[rule].fn(ctx, rng, everywhere=ev)
        if ed is None: continue
        new = permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)
        if norm(new) in seen: continue
        seen.add(norm(new)); out.append((seed, new, ed))
    return out, text


def clang_ok(text):
    if not CLANG: return True, ''
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'x.c')
        with open(p, 'w') as f: f.write(text)
        r = subprocess.run([CLANG, '--target=arm-none-eabi', '-std=gnu99', '-fsyntax-only', '-Wno-everything',
                            '-D__forceinline=__inline__', p], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


# rule: (function body file text, substring the forward result must contain, inverse rule or None)
CASES = {
    'probe_cmp': ('void f(void) { uchar n; n = status; if (n == 3) { send(1, n); } phase = n; }', 'z = (unsigned int)(', 'probe_remove'),
    'probe_shift': ('void f(void) { uchar n; n = status; if (n == 3) { send(5, n); } phase = n; }', '<<', 'probe_remove'),
    'probe_addr': ('void f(void) { status = 1; phase = 2; counter = 3; }', '(const void *)&', 'probe_remove'),
    'probe_remove': ('void f(void) { uchar n; n = status; { unsigned int z; z = (unsigned int)(n == 3); } phase = n; }', 'n = status; phase = n', None),
    'probe_move': ('void f(void) { uchar n; n = status; { unsigned int z; z = (unsigned int)(n == 3); } phase = n; send(1, n); }', 'z = (unsigned int)(n == 3)', 'probe_move'),
    'local_inline': ('void f(void) { short v; float s; v = table[1] * 256 + table[2]; s = v / 100.0f; send(1, s); }', '(short)(table[1] * 256 + table[2])', None),
    'local_cache': ('void f(void) { if (state.level > table[2]) status = 1; }', '=', 'local_inline'),
    'getter': ('void f(void) { send(1, state.level); send(2, state.level); }', 'static __forceinline', None),
    'pointer_direct': ('void f(void) { State *p; p = &state; p->mode = 1; send(p->flags, 0); }', 'state.mode', None),
    'stmt_swap': ('void f(void) { status = 1; phase = 2; }', 'phase = 2; status = 1;', 'stmt_swap'),
    'if_swap': ('void f(void) { if (status == 1) phase = 2; else phase = 3; }', 'status != 1', 'if_swap'),
    'nest': ('void f(void) { if (status == 1 && phase == 2) counter = 0; }', 'if (status == 1) { if (phase == 2)', 'nest'),
    'test_order': ('void f(void) { if (status == 1 && phase == 2) counter = 0; }', 'phase == 2 && status == 1', 'test_order'),
    'retype': ('void f(void) { ushort v; v = status; send(1, v); }', 'v', 'retype'),
    'init_decl': ('void f(void) { uchar n; n = 0; send(n, 1); }', 'uchar n = 0;', 'init_decl'),
    'temp_expr': ('void f(ushort v) { status = v * 10; }', 'status = pmt_t0;', 'local_inline'),
    'const_local': ('void f(void) { send(7, 1); }', 'pmt_k', None),
    'dead_local': ('void f(void) { send(status, 1); }', '= status;', None),
    'ptr_store': ('void f(void) { status = 1; }', '*pmt_p0 = 1', None),
    'dup_store': ('void f(void) { status = 3; phase = 1; }', 'status = 3', None),
    'if_switch': ('void f(void) { if (status == 1) phase = 2; else if (status == 5) phase = 3; else phase = 0; }', 'switch (status)', None),
    'goto_or': ('void f(void) { if (status != 0 || phase != 0) counter = 1; else counter = 2; }', 'goto', 'goto_or'),
    'chain_store': ('void f(void) { status = 0; phase = 0; }', '= 0;', 'chain_store'),
    'postinc_index': ('void f(uchar n) { table[n] = 5; n++; send(n, 0); }', 'table[n++] = 5', 'postinc_index'),
    'preinc_cond': ('void f(void) { counter++; if (counter > 250) status = 1; }', '++counter > 250', 'preinc_cond'),
    'range_test': ('void f(uchar c) { if (c >= 0x60 && c <= 0x64) status = 1; }', '0x60U <= 4', 'range_test'),
    'bit_test': ('void f(void) { if ((status & 4) == 4) phase = 1; }', '(status & 4) != 0', 'bit_test'),
    'ternary': ('void f(ushort v) { ushort c; if (v > 100) c = 100; else c = v; send(1, c); }', '? 100 : v', 'ternary'),
    'shift_mul': ('void f(ushort v) { send(1, v << 2); }', 'v * 4', 'shift_mul'),
    'cast_assign': ('void f(ushort v) { status = v + 1; }', '(uchar)(v + 1)', 'cast_assign'),
    'compound': ('void f(void) { counter = counter + 2; }', 'counter += 2', 'compound'),
    'case_edit': ('void f(void) { switch (status) { case 1: phase = 1; break; case 4: phase = 2; break; case 8: break; } }', 'case', None),
    'scope': ('void f(void) { uchar n; if (status) { n = phase; send(n, 0); } }', '{ uchar n;', 'scope'),
    'decl_order': ('void f(void) { uchar a; ushort b; a = 1; b = 2; send(a, b); }', 'ushort b; uchar a;', 'decl_order'),
    'split_decl': ('void f(void) { uchar a, b; a = 1; b = 2; send(a, b); }', 'uchar a; uchar b;', None),
    'unprototyped': ('void f(void) { status = 1; }', 'void f()', 'unprototyped'),
    'operand_order': ('void f(ushort v) { if (v < 100) status = 1; }', '100 > v', 'operand_order'),
    'volatile_read': ('void f(void) { phase = status; }', 'volatile', None),
    'cond_explicit': ('void f(void) { if (status) phase = 1; }', 'status != 0', 'cond_explicit'),
    'inequality': ('void f(ushort v) { if (v < 100) status = 1; }', 'v <= 99', 'inequality'),
    'ins_block': ('void f(void) { status = 1; }', '{ status = 1; }', None),
}


class RuleTest(unittest.TestCase):
    def test_every_rule_has_a_case(self):
        self.assertEqual(set(CASES) | {'sized_extern'}, {r.id for r in R.RULES})

    def test_rules(self):
        for rule, (body, want, inverse) in CASES.items():
            with self.subTest(rule=rule):
                outs, text = run_rule(rule, body)
                self.assertTrue(outs, f'{rule} found no site')
                hits = [(s, new, ed) for s, new, ed in outs if norm(want) in norm(new)]
                self.assertTrue(hits, f'{rule}: no result contains {want!r}: ' + ' / '.join(norm(n)[len(norm(PRELUDE)):] for _, n, _ in outs[:4]))
                for s, new, ed in outs:
                    toks, _ = permc.tokenize(new)
                    permc.match_brackets(toks)
                    ok, err = clang_ok(new)
                    self.assertTrue(ok, f'{rule} ({ed.desc}) gives C clang refuses:\n{err[:800]}\n{new[len(PRELUDE):]}')
                if inverse:
                    s, new, ed = hits[0]
                    back, _ = run_rule(inverse, new[len(PRELUDE):])
                    self.assertIn(norm(text), [norm(b) for _, b, _ in back], f'{rule}: {inverse} does not restore the text')

    def test_sized_extern(self):
        ctx, text = ctx_for('void f(void) { send(1, samples[3]); }')
        ctx.array_sizes = {'samples': 12}
        ed = R.r_sized_extern(ctx, random.Random(0), everywhere=True)
        new = permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)
        self.assertIn('extern ushort samples[12];', new)

    def test_probes_survive_forms_only(self):
        """No rule writes the forms armcc deletes before allocation (an empty if, (void)E)."""
        for rule in ('probe_cmp', 'probe_shift', 'probe_addr'):
            outs, _ = run_rule(rule, CASES[rule][0])
            for _, new, _ in outs:
                self.assertNotRegex(new[len(PRELUDE):], r'[^\w]\(void\) *[\w(]')
                self.assertNotRegex(new[len(PRELUDE):], r'if \([^)]*\) \{ \}')

    def test_inline_refuses_a_changed_operand(self):
        outs, _ = run_rule('local_inline', 'void f(void) { ushort v; v = counter; counter = 0; send(1, v); }')
        self.assertEqual(outs, [])

    def test_swap_refuses_dependent_statements(self):
        outs, _ = run_rule('stmt_swap', 'void f(void) { status = 1; phase = status; }')
        self.assertEqual(outs, [])


class CReaderTest(unittest.TestCase):
    def test_round_trip(self):
        text = PRELUDE + '#define X(a) \\\n  ((a) + 1)\nvoid f(void) { /* c */ status = X(2); // e\n}\n'
        toks, tail = permc.tokenize(text)
        self.assertEqual(permc.render(toks, tail), text)

    def test_structure_and_expressions(self):
        ctx, _ = ctx_for('void f(void) { uchar n; if (status) { n = (uchar)(phase + 1); } else n = 0; switch (n) { case 1: break; default: break; } }')
        kinds = [n.kind for n in ctx.nodes]
        self.assertEqual(kinds.count('if'), 1); self.assertEqual(kinds.count('switch'), 1)
        self.assertEqual(kinds.count('label'), 2)
        self.assertIn('n', ctx.locals)
        stmt = next(n for n in ctx.nodes if n.kind == 'stmt' and 'phase' in ctx.src(n.start, n.end))
        e = ctx.stmt_expr(stmt)
        self.assertEqual(e.kind, 'assign'); self.assertEqual(e.kids[1].kind, 'cast')


class ObjectReaderTest(unittest.TestCase):
    def build(self, orc):
        """A small ELF32 relocatable object (two sections, two locals, one global), shuffled as
        MDK-Lite writes it when orc is true."""
        shstr = b'\0.text\0.symtab\0.strtab\0.shstrtab\0.rel.text\0'
        strtab = b'\0$t\0f\0g\0'
        text = bytes.fromhex('00207047') + b'\0\0\0\0'
        syms = [(0, 0, 0, 0, 0, 0), (1, 0, 0, 0, 0, 1), (4, 1, 4, 0x12, 0, 1), (6, 0, 0, 0x10, 0, 0)]
        symtab = b''.join(struct.pack('<IIIBBH', *s) for s in syms)
        rel = struct.pack('<II', 4, (3 << 8) | 2)
        body = text + symtab + strtab + shstr + rel
        off_text = 0x34; off_sym = off_text + len(text); off_str = off_sym + len(symtab)
        off_shs = off_str + len(strtab); off_rel = off_shs + len(shstr)
        shoff = off_rel + len(rel)
        hdrs = [(0,) * 10,
                (1, 1, 6, 0, off_text, len(text), 0, 0, 4, 0),
                (7, 2, 0, 0, off_sym, len(symtab), 3, 2, 4, 16),
                (15, 3, 0, 0, off_str, len(strtab), 0, 0, 1, 0),
                (23, 3, 0, 0, off_shs, len(shstr), 0, 0, 1, 0),
                (33, 9, 0, 0, off_rel, len(rel), 2, 1, 4, 8)]
        sh = b''
        for i, h in enumerate(hdrs):
            rec = struct.pack('<10I', *h)
            if orc: rec = self.shuffle(rec, 76 * shoff - 1 + 320 * i)
            sh += rec
        if orc:
            count = len(syms)
            symtab = b''.join(self.shuffle(symtab[16 * j:16 * j + 16], 5248 * count - 1312 * j) if j >= 2 else symtab[16 * j:16 * j + 16]
                              for j in range(count))
            body = text + symtab + strtab + shstr + rel
        ident = (b'\xf7ORC' if orc else b'\x7fELF') + bytes([1, 1, 1]) + bytes(9)
        hdr = ident + struct.pack('<HHIIIIIHHHHHH', 1, 8 if orc else 40, 1, 0, 0, shoff, 0x05000000, 0x34, 0, 0, 40, len(hdrs), 4)
        return hdr + body + sh

    @staticmethod
    def shuffle(rec, key):
        p = orcobj.permutation(key, len(rec))
        return bytes(rec[p[j]] for j in range(len(rec)))

    def test_orc_reads_as_elf(self):
        a, ai = orcobj.read(raw=self.build(False))
        b, bi = orcobj.read(raw=self.build(True))
        self.assertEqual([(s.name, s.type, s.offset, s.size) for s in a.sections.values()],
                         [(s.name, s.type, s.offset, s.size) for s in b.sections.values()])
        self.assertEqual(a.symbols, b.symbols)
        self.assertEqual(a.relocs, b.relocs)
        self.assertEqual([s.name for s in b.symbols.values()], ['$t', 'f', 'g'])
        self.assertEqual(b.relocs[1][0].tname, 'R_ARM_ABS32')
        self.assertEqual(b.sections[1].data[:4], bytes.fromhex('00207047'))

    def test_permutation_is_a_permutation(self):
        for key in (0, 1, 0xFFFFFFFF, 12345):
            for n in (16, 40):
                self.assertEqual(sorted(orcobj.permutation(key, n)), list(range(n)))

    @unittest.skipUnless(os.environ.get('EXHUME_ARMCC_LINUX') and os.environ.get('EXHUME_ARMCC_MAPPINGS'), 'needs a Linux armcc')
    def test_against_fromelf(self):
        import objplace
        home = os.environ['EXHUME_ARMCC_LINUX']
        env = dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', ARM_PRODUCT_PATH=os.environ['EXHUME_ARMCC_MAPPINGS'])
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'x.c')
            with open(src, 'w') as f:
                f.write(PRELUDE + 'ushort table[8]; static uchar s; uint counter;\n'
                        'void f(uchar n) { table[n] = state.level; s++; send(s, table[2]); counter += 5; }\n')
            obj = os.path.join(d, 'x.o')
            subprocess.run([os.path.join(home, 'bin', 'armcc'), '-c', '--cpu', 'Cortex-M3', '-O1', src, '-o', obj], check=True, env=env)
            txt = subprocess.run([os.path.join(home, 'bin', 'fromelf'), '--text', '-v', '-r', '-s', obj], capture_output=True,
                                 text=True, env=env, check=True).stdout
            with open(obj, 'rb') as f: raw = f.read()
        ref = objplace.fill(objplace.parse(txt, obj), raw)
        o, _ = orcobj.read(raw=raw)
        self.assertEqual({k: (s.name, s.size, s.offset, s.data) for k, s in ref.sections.items()},
                         {k: (s.name, s.size, s.offset, s.data) for k, s in o.sections.items() if k in ref.sections})
        self.assertEqual(ref.symbols, o.symbols)
        self.assertEqual(ref.relocs, o.relocs)


class ScoreTokensTest(unittest.TestCase):
    def test_branch_targets_become_indexes(self):
        # movs r0,#0 ; b .-2 (to the movs) ; bl (masked target compared) ; nop
        buf = bytes.fromhex('0020') + bytes.fromhex('fde7') + bytes.fromhex('00bf')
        rows = permscore.rows_of(buf, 0x1000, set(), lambda a: None)
        masked, labels = permscore.normalise(rows)
        self.assertEqual(labels[1], 0)

    def test_diff_count(self):
        a = permscore.normalise(permscore.rows_of(bytes.fromhex('00200120002000bf'), 0, set(), lambda x: None))
        b = permscore.normalise(permscore.rows_of(bytes.fromhex('00200220002000bf'), 0, set(), lambda x: None))
        self.assertEqual(permscore.diff_count(a, a)[0], 0)
        self.assertEqual(permscore.diff_count(a, b)[0], 1)

    def test_literal_values_are_compared(self):
        # ldr r0, [pc, #0] reading 0x11111111 against 0x22222222: one difference
        a = bytes.fromhex('0048') + bytes(2) + bytes.fromhex('11111111')
        b = bytes.fromhex('0048') + bytes(2) + bytes.fromhex('22222222')
        word = lambda buf: (lambda x: struct.unpack_from('<I', buf, x)[0] if x + 4 <= len(buf) else None)
        data = set(range(4, 8))
        ra = permscore.normalise(permscore.rows_of(a, 0, data, word(a)))
        rb = permscore.normalise(permscore.rows_of(b, 0, data, word(b)))
        self.assertEqual(permscore.diff_count(ra, rb)[0], 1)
        ma = permscore.normalise(permscore.rows_of(a, 0, data, word(a), fn_mask=True), True)
        mb = permscore.normalise(permscore.rows_of(b, 0, data, word(b), fn_mask=True), True)
        self.assertEqual(permscore.diff_count(ma, mb)[0], 0)


if __name__ == '__main__':
    unittest.main()
