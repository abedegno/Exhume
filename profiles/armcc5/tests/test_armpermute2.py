"""armpermute2.py's parts: the object reader (orcobj), the C reader (permc), the rewrite rules
(permrules), the scorer's instruction tokens and line rows (permscore), and the search's shared
parts (armpermute2: the set of texts made, the acceptance key, the focus, generation in a worker).

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
    'common_temp': ('void f(ushort v) { counter = v * 10; send(1, counter); }', 'counter = pmt_v0; send(1, pmt_v0);', 'common_temp'),
    'inline_group': ('void f(void) { uchar a, b; a = table[1] >> 8; b = table[1] & 0xff; if (status) { phase = a; counter = b; } else { phase = b; } }',
                     'void f(void) { if (status) { phase = (uchar)(table[1] >> 8); counter = (uchar)(table[1] & 0xff); }', None),
    'remove_dead': ('void f(uchar n) { uchar u; { unsigned int z; z = (unsigned int)(n == 3); } n = 4; status = 1; status = 1; }',
                    'void f(uchar n) { status = 1; }', None),
    'flatten_else': ('void f(void) { if (status) phase = 1; else { if (counter) phase = 2; else phase = 3; } }', 'else if (counter)', 'flatten_else'),
    'obj_ptr': ('void f(void) { send(state.flags, state.mode); }', 'pmt_r0->flags', 'pointer_direct'),
    'goto_and': ('void f(void) { if ((status & 4) == 0 && phase == 2) counter = 1; else counter = 2; }', 'goto', 'goto_and'),
    'merge_ifs': ('void f(void) { if (status == 1) phase = 1; if (counter == 2) phase = 1; }', 'status == 1 || counter == 2', 'merge_ifs'),
    'flag_else': ('void f(void) { phase = 0; if (status == 1) phase = 1; }', 'else phase = 0', 'flag_else'),
    'loop_form': ('void f(void) { ushort k; k = 0; while (k < 3) { send(1, k); k++; } }', 'for (k = 0; k < 3; k++)', 'loop_form'),
    'index_helper': ('void f(void) { send(table[3], table[4]); }', 'send(pmt_at0(3), pmt_at0(4))', 'helper_inline'),
    'helper_inline': ('static __forceinline ushort get(unsigned int k) { return table[k]; }\nvoid f(void) { send(1, get(3)); }', 'send(1, table[3])', None),
    'macro_body': ('#define PUT(v) do { table[1] = (v); counter++; } while (0)\nvoid f(void) { PUT(status); phase = 1; }',
                   '#define PUT(v) { table[1] = (v); counter++; }', None),
    'macro_expand': ('#define PUT(v) do { table[1] = (v); counter++; } while (0)\nvoid f(void) { PUT(status); }',
                     'do { table[1] = (status); counter++; } while (0);', 'macro_fold'),
    'macro_fold': ('#define PUT(v) do { table[1] = (v); counter++; } while (0)\nvoid f(void) { do { table[1] = (phase); counter++; } while (0); }',
                   'PUT(phase);', 'macro_expand'),
    'file_order': ('extern uchar alpha;\nextern ushort beta;\nvoid f(void) { alpha = 1; beta = 2; }', 'extern ushort beta; extern uchar alpha;', 'file_order'),
    'normalise': ('void f(void) { uchar a; { unsigned int z; z = (unsigned int)(status == 3); } a = phase; send(a, 1); }', '{ send(phase, 1); }', None),
    'stmt_move': ('void f(void) { uchar n; n = 1; status = 2; phase = 3; counter = 4; send(n, 0); }', 'phase = 3; n = 1; status = 2;', 'stmt_move'),
    'compound_swap': ('void f(void) { if (status) phase = 1; while (counter < 3) counter++; }',
                      'while (counter < 3) counter++; if (status) phase = 1;', 'compound_swap'),
    'if_hoist': ('void f(void) { if (status) { counter = 0; phase = 1; } else { counter = 0; phase = 2; } }',
                 'counter = 0; if (status) { phase = 1; } else { phase = 2; }', 'if_hoist'),
    'loop_test': ('void f(void) { while (counter < 3) { send(1, 2); counter++; } }', 'if (counter < 3) do {', 'loop_test'),
    'local_merge': ('void f(void) { uchar a; uchar b; a = status; send(a, 1); b = phase; send(b, 2); }',
                    'uchar a; a = status; send(a, 1); a = phase; send(a, 2);', None),
    'local_split': ('void f(void) { uchar a; a = status; send(a, 1); a = phase; send(a, 2); }', 'pmt_s0 = phase; send(pmt_s0, 2);', 'local_merge'),
    'local_reuse': ('void f(void) { uchar a; uchar b; a = status; send(a, 1); b = phase; send(b, 2); a = 3; send(a, 3); }',
                    'uchar a; a = status; send(a, 1); a = phase; send(a, 2); a = 3;', None),
    'embed_assign': ('void f(void) { uchar k; k = status; send(k, 1); phase = k; }', 'send(k = status, 1)', 'embed_assign'),
    'abs_form': ('extern int abs(int);\nvoid f(short v) { v = abs(v); send(1, v); }', 'if (v < 0) v = -v;', 'abs_form'),
    'expr_split': ('void f(ushort a, uchar b) { ushort v; v = a | b << 8; send(1, v); }', 'v = a; v |= b << 8;', 'expr_split'),
    'return_local': ('ushort f(ushort v) { if (v > 3) { return v * 2; } return 7; }', 'pmt_r0 = v * 2; return pmt_r0; } return 7;', 'return_local'),
    'ptr_base': ('void f(void) { ushort *p; p = &table[3]; send(1, *p); }', 'p = table; p += 3;', 'ptr_base'),
    'byte_read': ('void f(uchar *p) { ushort v; v = p[0] << 8 | p[1]; send(1, v); }', 'p[0] * 256 + p[1]', 'byte_read'),
    'const_type': ('void f(ushort v) { counter = v * 8 / 10; }', 'v * 8u / 10', 'const_type'),
    'arg_local': ('void f(ushort v) { send(1, v * 2); }', '{ ushort pmt_a0 = (v * 2); send(1, pmt_a0); }', 'arg_local'),
    'setter': ('void f(void) { status = 5; }', 'pmt_put0(&status, 5);', 'setter'),
    'memcpy_loop': ('extern void *memcpy(void *, const void *, unsigned int);\nextern ushort copy[8];\n'
                    'void f(void) { uchar i; for (i = 0; i < 8; i++) copy[i] = table[i]; send(i, 0); }',
                    'memcpy(copy, table, 8 * sizeof(ushort)); i = 8;', 'memcpy_loop'),
    'global_type': ('void f(void) { phase = status; }', 'signed char status;', None),
    'callee_linkage': ('#define __weak\nvoid g(void) { status = 1; }\nvoid f(void) { g(); }', 'static void g(void)', 'callee_linkage'),
    'sub_neg': ('void f(ushort v) { counter = v - 1; }', 'v + -1', 'sub_neg'),
    'factor': ('void f(ushort a, ushort b) { counter = a * 4 + b * 4; }', '(a + b) * 4', 'factor'),
    'arrow_deref': ('void f(State *p) { p->mode = 1; }', '(*p).mode', 'arrow_deref'),
    'index_deref': ('void f(uchar n) { table[n] = 5; }', '*(table + n)', 'index_deref'),
    'pad_local': ('void f(void) { status = 1; }', 'pmt_pad0', None),
}

STRUCT = '''typedef struct { uchar a; uchar b; uchar c; ushort d; } Rec;
extern Rec recs[];
'''
REC_LAYOUT = {'Rec': [('a', 0, 1, None, None), ('b', 1, 1, None, None), ('c', 2, 1, None, None), ('d', 4, 2, None, None)]}


class RuleTest(unittest.TestCase):
    def test_every_rule_has_a_case(self):
        self.assertEqual(set(CASES) | {'sized_extern', 'ptr_index', 'size_and'}, {r.id for r in R.RULES})

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

    def test_ptr_index(self):
        body = STRUCT + 'void f(void) { uchar x, y; uchar *p; p = (uchar *)&recs[0]; x = p[1]; p += 2; y = *p; send(x, y); }'
        ctx, text = ctx_for(body)
        ctx.layouts = REC_LAYOUT
        ed = R.r_ptr_index(ctx, random.Random(0), everywhere=True)
        new = permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)
        self.assertIn(norm('x = recs[0].b; y = recs[0].c; send(x, y);'), norm(new))
        self.assertNotIn('*p', new[len(PRELUDE):])
        self.assertTrue(clang_ok(new)[0])
        # an offset with no field of that size is refused
        ctx, _ = ctx_for(STRUCT + 'void f(void) { uchar *p; p = (uchar *)&recs[0]; send(p[3], 0); }')
        ctx.layouts = REC_LAYOUT
        self.assertIsNone(R.r_ptr_index(ctx, random.Random(0), everywhere=True))

    def test_normalise_structural(self):
        """Pointer arithmetic, a goto chain and the locals of a fitted source come out plainly in one move."""
        body = STRUCT + ('void f(void) { uchar x, y, z; uchar *p; x = recs[0].a; p = &recs[0].b; y = p[0]; z = p[1]; '
                         'if (x != 0) goto L; if (y != 0) goto L; if (z != 0) { L: status = 1; } else status = 0; phase = x; }')
        ctx, text = ctx_for(body)
        ctx.layouts = REC_LAYOUT
        want = norm('void f(void) { if (recs[0].a != 0 || recs[0].b != 0 || recs[0].c != 0) status = 1; else status = 0; phase = recs[0].a; }')
        found = False
        for seed in range(40):
            ed = R.r_normalise(ctx, random.Random(seed), full=True)
            if ed is None: continue
            new = permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)
            self.assertTrue(clang_ok(new)[0], new)
            found = found or want in norm(new)
        self.assertTrue(found)

    def test_size_and(self):
        ctx, text = ctx_for('void f(void) { uchar a; a = samples[3] >> 8; send(a, 1); }')
        ctx.array_sizes = {'samples': 12}
        outs = []
        for seed in range(60):
            ed = R.r_size_and(ctx, random.Random(seed))
            if ed: outs.append(norm(permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)))
        self.assertTrue(any('samples [ 12 ]' in o and 'send ( ( uchar ) ( samples [ 3 ] >> 8 ) , 1 )' in o for o in outs))

    def test_dead_score(self):
        ctx, _ = ctx_for('void f(uchar cid) { uchar n; n = status; cid = 4; status = 1; status = 1; '
                         '{ unsigned int z; z = (unsigned int)(n == 3); } phase = (uchar)phase; }')
        # the probe 1, the cast a half, the dead store to the parameter and the repeated store a quarter each
        self.assertEqual(R.dead_score(ctx), 2.0)
        ctx, _ = ctx_for('void f(uchar cid) { uchar n; n = status; if (n) cid = 4; send(cid, n); }')
        self.assertEqual(R.dead_score(ctx), 0)
        ctx, _ = ctx_for('void f(void) { uchar i; for (i = 0; i < 4; i++) { send(i, 0); } }')
        self.assertEqual(R.dead_score(ctx), 0)

    def test_common_temp_through_a_cast_store(self):
        outs, _ = run_rule('common_temp', 'void f(ushort v) { *(short *)&counter = v * 10; send(12, counter); }')
        self.assertTrue(any(norm('counter = pmt_v0; send(12, pmt_v0);') in norm(n) for _, n, _ in outs))

    def test_dup_store_keeps_a_value_a_call_reads(self):
        outs, _ = run_rule('dup_store', 'void f(void) { status = 3; phase = read_byte(); phase = 4; }')
        self.assertFalse(any('phase = 3; phase = read_byte()' in n for _, n, _ in outs))

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

    def test_inline_across_a_store_to_another_object(self):
        outs, _ = run_rule('local_inline', 'void f(uchar *p) { uchar h; h = table[2] >> 8; status = 1; send(h, 0); }')
        self.assertTrue(any('send((uchar)(table[2] >> 8), 0)' in n for _, n, _ in outs))
        outs, _ = run_rule('local_inline', 'void f(ushort *p) { uchar h; h = table[2] >> 8; *p = 1; send(h, 0); }')
        self.assertEqual(outs, [])

    def test_swap_refuses_dependent_statements(self):
        outs, _ = run_rule('stmt_swap', 'void f(void) { status = 1; phase = status; }')
        self.assertEqual(outs, [])


class MovesTest(unittest.TestCase):
    """Moves kept as patches: replayed on another text, left out in a reduction, recovered from
    an island text that has no move list."""

    def test_patch_replay_and_leave_out(self):
        import armpermute2 as A
        ctx, text = ctx_for('void f(void) { uchar a; a = phase; send(a, 1); state.level = 5; table[1] = 7; table[2] = 8; counter = 0; status = 0; }')
        t0 = ctx.toks
        e1 = R.r_local_inline(ctx, random.Random(0), everywhere=True)
        m1 = {'rule': 'x', 'desc': '', 'patch': A.make_patch(t0, e1.spans)}
        t1 = R.apply(t0, e1.spans)
        c1 = R.remake(ctx, t1)
        e2 = R.r_chain_store(c1, random.Random(1))
        m2 = {'rule': 'y', 'desc': '', 'patch': A.make_patch(t1, e2.spans)}
        t2 = R.apply(t1, e2.spans)
        self.assertEqual(A.norm_hash(A.apply_all(t0, [m1, m2])), A.norm_hash(t2))
        only2 = A.apply_all(t0, [m2])                # the second move without the first
        self.assertIsNotNone(only2)
        self.assertNotEqual(A.norm_hash(only2), A.norm_hash(t2))
        # a compound move is kept as one patch per step and replays to the same text
        body = STRUCT + ('void f(void) { uchar x, y, z; uchar *p; x = recs[0].a; p = &recs[0].b; y = p[0]; z = p[1]; '
                         'if (x != 0) goto L; if (y != 0) goto L; if (z != 0) { L: status = 1; } else status = 0; phase = x; }')
        c8, _ = ctx_for(body)
        c8.layouts = REC_LAYOUT
        ed = R.r_normalise(c8, random.Random(2), full=True)
        mv = A.make_move('normalise', ed.desc, c8.toks, ed)
        self.assertIn('seq', mv)
        self.assertEqual(A.norm_hash(A.apply_move(c8.toks, mv)), A.norm_hash(R.apply(c8.toks, ed.spans)))
        lm = A.legacy_moves(t0, t2)
        self.assertEqual(A.norm_hash(A.apply_all(t0, lm)), A.norm_hash(t2))


FOCUS_BODY = '''void f(ushort v)
{
    uchar a;
    ushort b;
    a = phase;
    status = 1;
    phase = 2;
    send(a, 1);
    counter = v * 10;
    table[1] = 7;
    table[2] = 8;
    b = samples[3];
    send(2, b);
}
'''


class SearchPartsTest(unittest.TestCase):
    """What the parent and the workers share: the set of texts made, the acceptance key with
    its per-move cost, the line bookkeeping of the focus, and variant generation as a worker
    runs it (no compiler)."""

    def test_shared_set(self):
        import armpermute2 as A
        s = A.SharedSet(8)
        h = A.norm_hash(permc.tokenize('int x;')[0])
        self.assertTrue(s.add(h)); self.assertFalse(s.add(h)); self.assertIn(h, s)
        self.assertNotIn(('retype', 'v'), s)
        self.assertTrue(s.add(('retype', 'v'))); self.assertIn(('retype', 'v'), s)
        for k in range(400): s.add(('x', k))            # more than the table holds: still answers
        self.assertFalse(s.add(('x', 399)))

    def test_move_penalty(self):
        import armpermute2 as A
        self.assertEqual(A.pen_key((0, 0, 0), 2, 9, 3.0, 0.15), (0, 0, 0))             # exact: no penalty
        short, long_ = A.pen_key((1, 0, 48), 0, 2, 3.0, 0.15), A.pen_key((1, 0, 48), 0, 111, 3.0, 0.15)
        self.assertLess(short, long_)                                                # the shorter list wins a tie
        self.assertLess(A.pen_key((1, 0, 60), 0, 0, 3.0, 0.15), long_)               # 111 moves for 12 instructions: no gain
        self.assertLess(A.pen_key((1, 0, 47), 0, 3, 3.0, 0.15), short)               # one instruction for one move: a gain
        self.assertEqual(A.pen_key((1, 0, 48), 1.5, 2, 3.0, 0.0), (1, 0, 52.5))      # the dead-code penalty as before

    def test_lines(self):
        import armpermute2 as A
        text = 'int a;\n/* c\n   c */ int b;\n\nint c;\n'
        toks, _ = permc.tokenize(text)
        self.assertEqual([l for t, l in zip(toks, A.token_lines(toks)) if t[1] == 'int'], [1, 3, 5])
        old = '\n'.join(f'line {k}' for k in range(1, 21))
        new = old.replace('line 5\n', 'line 5\nnew a\nnew b\n').replace('line 12\n', '').replace('line 15', 'LINE 15')
        self.assertEqual(A.remap_lines(old, new, [2, 5, 9, 15, 18, 20]), (2, 5, 11, 16, 19, 21))
        self.assertEqual(A.remap_lines(old, old, [1, 20]), (1, 20))

    def test_edit_focus(self):
        import armpermute2 as A
        ctx, text = ctx_for(FOCUS_BODY)
        tl = A.token_lines(ctx.toks)
        first = text[:text.index('void f')].count('\n') + 1           # line of `void f`
        hot = {first + 5, first + 6}                                    # status = 1; phase = 2;
        seen = set()
        for seed in range(60):
            ed = R.r_stmt_swap(ctx, random.Random(seed))
            if ed: seen.add((A.edit_focus(ctx, tl, ed, hot), ed.desc))
        self.assertIn(True, {f for f, _ in seen}); self.assertIn(False, {f for f, _ in seen})
        for f, desc in seen:
            self.assertEqual(f, 'status = 1' in desc or 'phase = 2' in desc, desc)
        # a declaration edit and an edit outside the body are exempt; so is a compound move
        self.assertIsNone(A.edit_focus(ctx, tl, R.r_retype(ctx, random.Random(0)), hot))
        ctx.array_sizes = {'samples': 12}
        self.assertIsNone(A.edit_focus(ctx, tl, R.r_sized_extern(ctx, random.Random(0), everywhere=True), hot))
        ed = R.r_normalise(ctx, random.Random(1), full=True)
        self.assertTrue(ed.parts); self.assertIsNone(A.edit_focus(ctx, tl, ed, hot))
        # a local inlined is judged by its uses, not by the declaration it removes
        ed = next(e for e in (R.r_local_inline(ctx, random.Random(k), everywhere=True) for k in range(40)) if e and 'b' in e.desc.split())
        self.assertFalse(A.edit_focus(ctx, tl, ed, hot))
        self.assertTrue(A.edit_focus(ctx, tl, ed, {first + 12}))

    def gen(self, **kw):
        import armpermute2 as A
        ctx, text = ctx_for(FOCUS_BODY)
        g = A.Gen('f', ctx.tail, ctx.types, R.RULES, **kw)
        return A, g, A.Base(text, ctx.toks, g.make_ctx(ctx.toks))

    def test_worker_generation(self):
        """A variant comes from the base and the seed alone, and its moves replay to its text."""
        A, g, base = self.gen()
        n = 0
        for seed in range(40):
            m = g.mutate(base, random.Random(seed))
            if m is None: continue
            n += 1
            toks, trail, made = m
            again = g.mutate(base, random.Random(seed))
            self.assertEqual(A.norm_hash(again[0]), A.norm_hash(toks))
            moves = [A.make_move(*x) for x in made]
            self.assertEqual(len(moves), len(trail))
            self.assertEqual(A.norm_hash(A.apply_all(base.toks, moves)), A.norm_hash(toks))
        self.assertGreater(n, 30)
        # the shared set drops a text made before; a tabu move is not made again
        g.seen = A.SharedSet(10)
        toks, trail, made = g.mutate(base, random.Random(3))
        self.assertTrue(g.seen.add(A.norm_hash(toks))); self.assertFalse(g.seen.add(A.norm_hash(toks)))
        g.tabu = A.SharedSet(10)
        g.tabu.add((made[0][0], made[0][1]))
        again = g.mutate(base, random.Random(3))
        self.assertTrue(again is None or (again[2][0][0], again[2][0][1]) != (made[0][0], made[0][1]))

    def test_focus_keeps_hot_edits(self):
        """With --focus-cold 0 every edit made touches a hot line or is exempt."""
        A, g, base = self.gen(focus=True, focus_cold=0.0, p_pair=0.0)
        first = base.text[:base.text.index('void f')].count('\n') + 1
        hot = {first + 9, first + 10}                                   # the table stores
        st, n = __import__('collections').Counter(), 0
        for seed in range(80):
            m = g.mutate(base, random.Random(seed), hot=hot, st=st)
            if m is None: continue
            n += 1
            rule, desc, toks, ed = m[2][0]
            self.assertIsNot(A.edit_focus(base.ctx, base.lines(), ed, hot), False, desc)
        self.assertGreater(n, 20)
        self.assertGreater(sum(v for (r, k), v in st.items() if k == 'cold'), 0)
        self.assertEqual(sum(v for (r, k), v in st.items() if k == 'cold_kept'), 0)


SWEEP_BODY = '''void f(uchar mode)
{
    uchar n;
    n = status;
    { uint z; z = (uint)(n == 9); }
    if (mode == 'C') {
        send(1, 300);
        counter = 300;
    } else {
        send(2, n);
    }
    phase = n;
}
'''


class SweepTest(unittest.TestCase):
    """The systematic first stage: what the probe rules enumerate, and the list of single edits."""

    def test_probe_enumeration(self):
        ctx, text = ctx_for(SWEEP_BODY)
        cmp_ = R.r_probe_cmp.enumerate(ctx)
        points = {p[0] for p in R._probe_points(ctx)}
        self.assertEqual(len(points), len(ctx.points()) - 2)          # not inside the probe the function has
        t1 = [(ed.desc, fam) for tier, ed, fam in cmp_ if tier == 1]
        # the test the function makes near a point is repeated there, in the arm and elsewhere
        self.assertTrue(any(d.startswith("probe mode == 'C' @") for d, _ in t1))
        self.assertEqual(len({d for d, f in t1 if d.startswith("probe mode == 'C' @")}), len({R._line(ctx, i) for i in points}))
        self.assertIn('cmp mode', {f for _, f in t1})
        self.assertTrue(all(tier in (1, 2, 3, 4) for tier, _, _ in cmp_))
        # no probe reads the block local of the probe the function already has
        self.assertFalse(any(' z ' in ed.desc or '<< z)' in ed.desc for _, ed, _ in cmp_ + R.r_probe_shift.enumerate(ctx)))
        sh = R.r_probe_shift.enumerate(ctx)
        # the shared constant (300, used twice) is tried with a local at a sample of the points
        # first (tier 1.5), then at every point
        self.assertIn('shift 300', {fam for tier, _, fam in sh if tier == 1.5})
        self.assertGreater(len([1 for tier, ed, fam in sh if fam == 'shift 300' and tier == 2]), 4)
        for tier, ed, fam in cmp_[:40] + sh[:40] + [x + (None,) for x in R.r_probe_addr.enumerate(ctx)[:10]]:
            new = permc.render(R.apply(ctx.toks, ed.spans), ctx.tail)
            self.assertIn('z = (unsigned int)(', new)
            self.assertTrue(clang_ok(new)[0], ed.desc)
        rm = R.r_probe_remove.enumerate(ctx)
        self.assertEqual(len(rm), 1)
        self.assertNotIn('n == 9', permc.render(R.apply(ctx.toks, rm[0][1].spans), ctx.tail))

    def test_sweep_list(self):
        """Every rule's single edits, the same whatever the seed; probes by tier after the first pass."""
        import armpermute2 as A, types
        ctx, text = ctx_for(SWEEP_BODY)
        me = types.SimpleNamespace(origin_ctx=ctx, origin_toks=ctx.toks, simplify=0.25, rules=list(R.RULES), sweep_max=3000,
                                   base_weights={r.id: r.weight for r in R.RULES})
        a = A.Search.sweep_list(me)
        b = A.Search.sweep_list(me)
        self.assertEqual([(t, r, e.desc) for t, r, e, s_, f in a], [(t, r, e.desc) for t, r, e, s_, f in b])
        self.assertEqual([t for t, *_ in a], sorted(t for t, *_ in a))
        rules = {r for _, r, *_ in a}
        self.assertTrue({'probe_cmp', 'probe_shift', 'probe_addr', 'local_cache', 'retype', 'normalise'} <= rules, rules)
        texts = [A.norm_hash(R.apply(ctx.toks, e.spans)) for _, _, e, _, _ in a]
        self.assertGreater(len(set(texts)), 0.9 * len(texts))
        self.assertGreater(len(a), 150)
        me.rules = [r for r in R.RULES if r.id == 'probe_remove']; me.simplify = 0     # --only is respected
        self.assertEqual({r for _, r, *_ in A.Search.sweep_list(me)}, {'probe_remove'})
        me.rules = [r for r in R.RULES if r.id == 'probe_cmp']; me.sweep_max = 7
        self.assertEqual(len(A.Search.sweep_list(me)), 7)

    def test_pair_of_neighbouring_singles_replays(self):
        """Two singles of the original made side by side still apply one after the other (the
        second's context was changed by the first: it is found with less of it)."""
        import armpermute2 as A
        ctx, text = ctx_for('void f(uchar c) { if ((c >= 0x60 && c <= 0x64) || (c >= 0x92 && c <= 0x96)) status = 1; }')
        r = R.BY_ID['range_test']
        singles = A.list_singles(ctx, r, 100, r.weight)
        self.assertEqual(len(singles), 2)
        moves = [A.make_move('range_test', ed.desc, ctx.toks, ed) for _, ed, _ in singles]
        both = A.apply_all(ctx.toks, moves)
        self.assertIsNotNone(both)
        new = norm(permc.render(both, ctx.tail))
        self.assertIn(norm('c - 0x60U <= 4'), new); self.assertIn(norm('c - 0x92U <= 4'), new)
        self.assertTrue(clang_ok(permc.render(both, ctx.tail))[0])

    def test_typedef_with_several_names(self):
        ti = permc.TypeInfo()
        ti.scan('typedef unsigned int uint, undefined4;\ntypedef unsigned char uchar, *puchar;\ntypedef struct { int a, b; } S;')
        self.assertEqual((ti.typedefs['uint'], ti.typedefs['undefined4'], ti.typedefs['puchar']), ('unsigned int', 'unsigned int', 'unsigned char *'))
        self.assertTrue({'uint', 'undefined4', 'uchar', 'puchar', 'S'} <= ti.names)
        self.assertIn('{', ti.typedefs['S'])

    def test_code_hash(self):
        """A result's hash is of the function's bytes and changed neighbours, not of the object file."""
        a = permscore.Result(True, False, 3, 4, 4, (), code=b'\x00\x20\x70\x47')
        b = permscore.Result(True, False, 3, 4, 4, (), code=b'\x00\x20\x70\x47')
        c = permscore.Result(True, False, 3, 4, 4, ('g',), code=b'\x00\x20\x70\x47')
        self.assertEqual(a.chash, b.chash); self.assertNotEqual(a.chash, c.chash)
        self.assertEqual(permscore.Result().chash, '')


class TrustKeyTest(unittest.TestCase):
    """The structural count comes before the register count in a result's key."""

    @staticmethod
    def code(*hws):
        return b''.join(struct.pack('<H', h) for h in hws)

    def counts(self, want, got):
        e = permscore.rows_of(want, 0, set(), lambda x: None)
        g = permscore.rows_of(got, 0, set(), lambda x: None)
        full = permscore.diff_count(permscore.normalise(e), permscore.normalise(g))[0]
        st = permscore.diff_count(permscore.normalise_struct(permscore.struct_rows(e)), permscore.normalise_struct(permscore.struct_rows(g)))[0]
        return full, st

    @unittest.skipUnless(__import__('importlib.util').util.find_spec('capstone'), 'needs capstone')
    def test_structure_against_registers(self):
        # ldrb r1,[r0,#3] ; adds r1,#1 ; strb r1,[r0,#0] ; bx lr
        want = self.code(0x78c1, 0x3101, 0x7001, 0x4770)
        ties = self.code(0x78c2, 0x3201, 0x7002, 0x4770)            # r2 in place of r1: three ties, no structure
        self.assertEqual(self.counts(want, ties), (3, 0))
        sx = self.code(0x78c1, 0xb249, 0x3101, 0x7001, 0x4770)      # the same registers and an sxtb the image lacks
        self.assertEqual(self.counts(want, sx), (1, 1))
        wide = self.code(0xf898, 0x1003, 0x3101, 0xf888, 0x1000, 0x4770)   # the base in r8: wide loads, the same structure
        self.assertEqual(self.counts(want, wide)[1], 0)
        copy = self.code(0x78c1, 0x460a, 0x3201, 0x7002, 0x4770)    # a register copy in between: not structure
        self.assertEqual(self.counts(want, copy)[1], 0)
        swap = self.code(0x78c1, 0x7001, 0x3101, 0x4770)            # two instructions in the other order: structure
        self.assertGreater(self.counts(want, swap)[1], 0)
        # one wrong instruction outranks any number of ties; the old count says the opposite
        a = permscore.Result(True, False, 3, 8, 8, (), code=b'a', struct=0)
        b = permscore.Result(True, False, 1, 8, 8, (), code=b'b', struct=1)
        self.assertLess(a.key(), b.key())
        self.assertEqual(a.text(), '3 (structure 0, registers 3)')
        permscore.Result.flat = True
        try:
            self.assertLess(b.key(), a.key()); self.assertEqual(a.text(), '3')
        finally:
            permscore.Result.flat = False
        import armpermute2 as A
        self.assertEqual(A.pen_key(a.key(), 1, 2, 3.0, 0.15), (1, 0, 0, 3 + 3.0 + 0.3))
        self.assertEqual(permscore.Result(True, True, 0, 8, 8, ()).key(), (0, 0, 0, 0))


class LineRowsTest(unittest.TestCase):
    def test_line_program(self):
        """A DWARF 3 line program as armcc -g writes it (2-byte instructions, line base 0, range 6,
        opcode base 13), its address relocated to a Thumb function symbol."""
        import objplace
        files = b'\0' + b'/w/x.c\0\0\0\0' + b'\0'
        head = bytes([2, 0, 0, 6, 13]) + bytes([0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1]) + files
        prog = (bytes([0, 5, 2]) + struct.pack('<I', 4)      # set address: symbol + 4
                + bytes([3, 9, 1])                           # line 10, row
                + bytes([13 + 2 + 6 * 1])                    # special: one instruction on, two lines down, row
                + bytes([2, 3, 3, 0x7F, 1])                  # three instructions on, one line up, row
                + bytes([0, 1, 1]))                          # end of sequence
        unit = struct.pack('<HI', 3, len(head)) + head + prog
        data = struct.pack('<I', len(unit)) + unit
        raw = bytes(0x40) + data
        addr_at = 4 + 6 + len(head) + 3
        o = objplace.Obj('', {1: objplace.Sec('.text', 'SHT_PROGBITS', 'SHF_ALLOC SHF_EXECINSTR', 0, 0x200, 4),
                              2: objplace.Sec('.debug_line', 'SHT_PROGBITS', '', 0x40, len(data), 1)},
                         {1: objplace.Sym('f', 0x101, 'Gb', 1, 'Code')},
                         {2: [objplace.Rel(addr_at, 2, 'R_ARM_ABS32', 1, 'f')]})
        self.assertEqual(permscore.line_rows(o, raw), {1: [(0x104, 10, '/w/x.c'), (0x106, 12, '/w/x.c'), (0x10C, 11, '/w/x.c')]})


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



import re      # noqa: E402

# ---------------------------------------------------------------- every rule, many seeds: well-formed C, same behaviour

# Larger synthetic functions: each rule is also run on all of these (the property test), and the
# rules that claim the same meaning are run on them as programs (the behaviour test).
BEHAVE = {
    'stmt_move': ['void f(uchar *p) { uchar n; ushort v; n = status; v = table[1]; p[0] = 1; if (n) { counter = v; } send(n, v); '
                  'phase = p[1]; status = 3; for (n = 0; n < 3; n++) table[n] = v; counter += 2; }'],
    'compound_swap': ['void f(uchar *p) { uchar i; if (status) phase = p[0]; for (i = 0; i < 4; i++) table[i] = i; '
                      'if (counter > 2) { p[1] = 3; } while (state.level > 60000) state.level++; }',
                      'void f(uchar *p) { uchar i; ushort t; t = 0; if (status) phase = 2; for (i = 0; i < 4; i++) t += i; '
                      'if (counter > 2) { table[1] = 3; } while (state.level > 60000) state.level++; switch (t) { case 6: t = 1; break; } send(t, p[0]); }'],
    'if_hoist': ['void f(void) { uchar n; n = phase; if (status) { send(1, n); counter = 5; phase = 0; } else { send(2, n); counter = 6; phase = 0; } '
                 'table[0] = n; if (n > 2) table[1] = 1; else table[1] = 2; }',
                 'ushort f(ushort v) { ushort r; if (v > 100) { r = 100; status = 1; } else { r = v; status = 1; } return r; }'],
    'loop_test': ['void f(void) { uchar i; i = 0; do { send(i, 1); i++; } while (i < 4); for (i = 0; i < 3; i++) { if (i == status) continue; '
                  'send(9, i); } while (1) { if (counter > 4) break; counter++; } for (;;) { counter += 3; if (counter > 20) break; } }'],
    'local_merge': ['void f(void) { uchar i; uchar j; ushort t; for (i = 0; i < 3; i++) send(i, 1); t = table[0]; '
                    'for (j = 0; j < 2; j++) { t += j; send(j, t); } for (i = 0; i < 2; i++) { t = samples[i]; send(i, t); } counter = t; }',
                    'void f(void) { uchar a; uchar b; a = status; while (counter < 3) { b = a; a = phase; send(a, b); counter++; } }',
                    'void f(void) { uchar i; uchar j; ushort t; ushort u; t = table[0]; for (i = 0; i < 3; i++) { t += i; } send(1, t); '
                    'u = samples[1]; for (j = 0; j < 2; j++) { u += j; if (u > 9) break; } send(2, u); }',
                    # a is live across b's two lives; a value carried round the loop past b's life; a life left by a break
                    'void f(void) { uchar a; uchar b; a = status; b = phase; send(b, 1); b = 7; send(b, 2); send(a, 3); }',
                    'void f(void) { uchar a; uchar b; a = status; while (counter < 4) { send(a, 0); b = counter; send(b, 1); counter++; } a = 9; send(a, 1); }',
                    'void f(void) { uchar a; uchar b; uchar c; a = 1; for (c = 0; c < 3; c++) { b = c + status; if (b == 2) break; send(b, 4); a = c; } '
                    'send(a, 5); b = 6; send(b, c); }',
                    'void f(void) { uchar a; uchar b; a = phase; if (status) { b = 5; send(b, 0); } send(a, 1); switch (counter) { case 1: b = 1; '
                    'send(b, 2); break; case 2: a = 4; b = a; send(b, 3); break; } }',
                    # the way back is a loop or a goto
                    'void f(void) { uchar a; uchar b; a = 0; while (counter < 3) { send(a, 1); a = counter + 1; b = phase; send(b, 2); counter++; } }',
                    'void f(void) { uchar a; a = 0; while (counter < 3) { send(a, 1); a = phase; counter++; } }',
                    'void f(void) { uchar a; a = status; again: send(a, 1); counter++; a = phase + counter; if (counter < 3) goto again; }',
                    'void f(void) { uchar a; uchar b; a = status; again: send(a, 1); counter++; b = phase; send(b, 2); if (counter < 3) goto again; }'],
    'embed_assign': ['void f(void) { uchar k; ushort t; k = phase; if (k > 2) status = k; t = table[k & 7]; send(k, t); t = read_byte(); '
                     'switch (t) { case 1: phase = 9; break; default: phase = t; } k = status; send(read_byte(), k); }',
                     # a store that must stay where it is: evaluated only sometimes, or after another call
                     'void f(void) { uchar k; k = read_byte(); if (status && k) counter = 1; send(k, 0); k = read_byte(); send(read_byte(), k); '
                     'k = 0; if (phase && (k = read_byte()) > 3) counter = k; send(k, 1); }'],
    'abs_form': ['extern int abs(int);\nvoid f(void) { short v; int w; v = (short)table[0]; w = (int)counter - 3; send(1, abs(v)); '
                 'if (w < 0) w = -w; counter = w; v = v < 0 ? -v : v; table[1] = v; }'],
    'expr_split': ['void f(void) { ushort s; uchar b; s = table[0]; s += table[1] + table[2] - table[3]; b = status; b = b | 4; '
                   'b |= phase << 1; counter = s; counter -= b + 1; state.level = table[4] + s - b; phase = b; }',
                   'void f(ushort *p) { table[1] = p[0] + table[1] + p[2]; p[1] = table[0] | p[1] | 3; table[2] += 1; table[2] += p[2]; }',
                   # floats do not regroup, and a float is not an int
                   'void f(void) { float s; float x; x = table[0] / 8.0f; s = x + 0.3f; s += x * 3 + 0.1f; s += 1e9f; s -= 1e9f; '
                   'counter = (uint)(s * 16); state.level = (ushort)(x * 100.0f - s * 100.0f + s * 0.5f); }'],
    'return_local': ['uchar f(ushort v) { uchar r; r = 0xff; if (v > 1000) return 1; if (status) { r = phase; return r; } return v; }'],
    'ptr_base': ['void f(void) { ushort *p; uchar *q; p = &table[status + 1]; *p = 7; q = (uchar *)&state; send(1, *(table + 2)); '
                 'p = table; p += 2; send(2, *p); send(3, *&table[phase & 3]); send(4, *q); }'],
    'byte_read': ['ushort f(uchar *p) { ushort v; v = (p[2] << 8) + p[3]; send(1, p[0] * 256 | p[1]); return v + (status << 8 | phase); }',
                  'void f(short *p) { ushort hi; int lo; hi = table[0]; lo = (short)table[1]; counter = hi << 8 | lo; state.level = p[0] * 256 + p[1]; }'],
    'arg_local': ['void f(uchar *p) { send(p[0], table[1] + 1); send(status + 300, counter); counter = read_byte() + 1; send(1, read_byte()); }',
                  # a read that must not move before the call beside it
                  'uchar bump(void) { status++; return status; }\nvoid f(void) { send(bump(), status + 300); '
                  'counter = bump() + (table[status & 1] << 8 | phase); }'],
    'setter': ['void f(uchar n) { status = n + 300; table[n & 7] = counter; state.mode = 2; table[2] = 1; table[3] = 2; }'],
    'memcpy_loop': ['extern void *memcpy(void *, const void *, unsigned int);\nextern ushort copy[8];\nextern uchar bytes[8];\n'
                    'void f(uchar *p) { uchar i; memcpy(copy, table, 16); memcpy(bytes, p, 4); i = 4; send(i, bytes[1]); '
                    'for (i = 0; i < 3; i++) table[i] = copy[i]; }'],
    'factor': ['void f(State *p, uchar n) { int a; a = (int)counter - n - 3; p->limits[n & 3] = table[n & 7] - (a + -2); '
               'counter = (a << 2) + (n << 2); state.level = (a - n) * 3; (*p).mode = n; }'],
}
# harder ground for every rule (and for chains of rules): jumps, values carried round loops, a switch that
# falls through, a local whose address is taken, pointers into the globals, calls whose order shows
BEHAVE['stress'] = [
    'void f(void) { uchar a; uchar b; a = status; if (a > 1) goto out; b = phase; send(a, b); a = 3; counter = a; out: phase = a; send(9, a); }',
    'void f(void) { uchar i; ushort acc; ushort prev; acc = 0; prev = table[0]; for (i = 1; i < 6; i++) { ushort cur; cur = table[i]; '
    'if (cur > prev) acc += cur - prev; else acc += 1; prev = cur; if (acc > 60000) break; } counter = acc; send(i, prev); }',
    'void f(uchar n) { uchar r; r = 0; switch (n & 3) { case 0: r = 1; case 1: r += 2; break; case 2: status = n; r = status; break; '
    'default: r = phase; } send(r, n); table[r & 7] = n; }',
    'void f(uchar *p) { uchar a; uchar *q; a = p[0]; q = &a; *q = a + 1; p[1] = a; a = *p; *q += 2; send(a, p[1]); status = a; phase = *q; }',
    'void f(void) { uchar i; ushort s; i = 0; s = 0; do { i++; if (i == status) continue; s += table[i & 7]; } while (i < 5); '
    'while (s > 1000) { s -= 1000; if (s == 1234) break; } counter = s; }',
    'ushort f(ushort v) { ushort r; uchar k; k = status; if (k == 1) { r = v + 1; counter = r; } else if (k == 2) { r = v + 2; counter = r; } '
    'else { r = 0; counter = r; } if (r > 100) return r - 100; k = phase; send(k, r); return r + k; }',
    'void f(State *p) { ushort t; uchar i; t = p->level; state.level = t + 1; p->limits[1] = state.limits[2]; '
    'for (i = 0; i < 4; i++) { p->limits[i] = t; t = state.limits[i] + i; } p->flags = t; send(p->mode, state.level); }',
    'void f(void) { int a; int b; uchar c; a = read_byte(); b = read_byte(); c = a - b; send(c, a); if (a > b) { send(1, a - b); c = a; } '
    'else { send(2, b - a); c = b; } counter = c * 4 + a * 4; status = c; }',
    'extern void *memcpy(void *, const void *, unsigned int);\nextern uchar bytes[8];\nextern uchar other[8];\n'
    'ushort f(uchar *p) { uchar i; ushort v; for (i = 0; i < 8; i++) bytes[i] = other[i]; v = bytes[0] << 8 | bytes[1]; '
    'memcpy(other, bytes, 4); i = 4; p[i & 3] = v; return (p[0] << 8) + bytes[i]; }',
]
BEHAVE['byte_read'].append(BEHAVE['arg_local'][-1])
BEHAVE['local_split'] = BEHAVE['local_reuse'] = BEHAVE['local_merge']
BEHAVE['sub_neg'] = BEHAVE['arrow_deref'] = BEHAVE['index_deref'] = BEHAVE['factor']
OLD_RULES = ['probe_cmp', 'probe_shift', 'probe_addr', 'probe_remove', 'probe_move', 'local_inline', 'local_cache', 'getter', 'pointer_direct',
             'stmt_swap', 'if_swap', 'nest', 'test_order', 'retype', 'init_decl', 'const_local', 'temp_expr', 'dead_local', 'ptr_store',
             'dup_store', 'if_switch', 'goto_or', 'chain_store', 'postinc_index', 'preinc_cond', 'range_test', 'bit_test', 'ternary',
             'shift_mul', 'cast_assign', 'compound', 'case_edit', 'scope', 'decl_order', 'split_decl', 'unprototyped', 'sized_extern',
             'operand_order', 'volatile_read', 'cond_explicit', 'inequality', 'ins_block', 'common_temp', 'inline_group', 'remove_dead',
             'flatten_else', 'ptr_index', 'obj_ptr', 'goto_and', 'merge_ifs', 'flag_else', 'index_helper', 'helper_inline', 'normalise',
             'size_and', 'loop_form']
NEW_RULES = [r.id for r in R.RULES if r.id not in OLD_RULES]
# Old rules whose output is not the same program on the synthetic cases (found by the behaviour test;
# each is described in a comment where it is listed): they are left out of the old-rule check.
KNOWN_UNSOUND = {}
REAL_TREE = os.path.expanduser(os.environ.get('EXHUME_PERM_REAL_TREE', '/nonexistent'))
REAL_UNIT = os.path.join(REAL_TREE, os.environ.get('EXHUME_PERM_REAL_UNIT', 'missing.c'))
REAL_FUNCS = [f for f in os.environ.get('EXHUME_PERM_REAL_FUNCS', '').split(',') if f] or ['missing']


def variants(ctx, rule, seeds, test=None, deep=None):
    """[(seed, new text, Edit)] of the distinct results of a rule on a parsed base; with test, each must
    apply and differ from the base, and (the first `deep` of them, or all) tokenize with balanced
    brackets and parse again."""
    out, seen = [], set()
    base = tuple(t[1] for t in ctx.toks)
    fn = R.BY_ID[rule].fn
    for seed in range(seeds):
        ed = fn(ctx, random.Random(seed), everywhere=(seed % 2 == 0))
        if ed is None: continue
        toks = R.apply(ctx.toks, ed.spans)                  # an overlap raises: a rule's bug
        k = tuple(t[1] for t in toks)
        if test is not None: test.assertTrue(k != base, f'{rule} ({ed.desc}) changed nothing')
        if hash(k) in seen: continue
        seen.add(hash(k))
        new = permc.render(toks, ctx.tail)
        if test is not None and (deep is None or len(out) < deep):
            t2, _ = permc.tokenize(new)
            test.assertEqual(tuple(t[1] for t in t2), k, f'{rule} ({ed.desc}): the text does not read back as the tokens written')
            permc.match_brackets(t2)
            R.remake(ctx, t2)
        out.append((seed, new, ed))
    return out


def defined_functions(text):
    """Names of the functions a C text defines at top level."""
    toks, _ = permc.tokenize(text)
    pair = permc.match_brackets(toks)
    out, i = [], 0
    while i < len(toks) - 1:
        if toks[i][1] == '{': i = pair[i] + 1; continue
        if toks[i][0] == 'id' and toks[i + 1][1] == '(' and pair[i + 1] + 1 < len(toks) and toks[pair[i + 1] + 1][1] == '{':
            out.append(toks[i][1])
        i += 1
    return out


def top_level_declarations(text):
    """The file-scope object declarations of a C text (no function bodies or headers), as one string."""
    toks, _ = permc.tokenize(text)
    pair = permc.match_brackets(toks)
    out, cur, i = [], [], 0
    while i < len(toks):
        w = toks[i][1]
        if w == '{': cur = []; i = pair[i] + 1; continue
        cur.append(w)
        if w == ';' or toks[i][0] == 'pp':
            if '(' not in cur: out.append(' '.join(cur))
            cur = []
        i += 1
    return '\n'.join(out)


# rules that edit above the function in ways that can share one file: a macro redefined before each
# variant, extern declarations in another order
LOOSE_BUNDLE = {'macro_body', 'macro_expand', 'macro_fold', 'file_order'}


def bundle(texts, loose=False):
    """One C text holding several variants of a PRELUDE + body file, the functions each defines renamed
    with its number: (text, [first line of each variant]); None when a variant does not start with the
    PRELUDE (an edit above the function's file part) or declares a type."""
    parts, lines, line = [PRELUDE], [], PRELUDE.count('\n') + 1
    decls = set()
    for k, text in enumerate(texts):
        if not text.startswith(PRELUDE) or 'typedef' in text[len(PRELUDE):]: return None
        body = text[len(PRELUDE):]
        decls.add(top_level_declarations(body))
        if len(decls) > 1 and not loose: return None        # a declaration edited: the variants cannot share a file
        names = sorted(set(defined_functions(body)))
        chunk = ''.join(f'#define {n} {n}_v{k}\n' for n in names) + body.rstrip('\n') + '\n' + ''.join(f'#undef {n}\n' for n in names)
        lines.append(line); line += chunk.count('\n')
        parts.append(chunk)
    return ''.join(parts), lines


def clang_errors(paths, args):
    """{path: [error lines]} from one clang -fsyntax-only run over several files."""
    r = subprocess.run([CLANG, '-fsyntax-only', '-Wno-everything', '-ferror-limit=0'] + args + paths, capture_output=True, text=True)
    out = {}
    for ln in r.stderr.splitlines():
        m = re.match(r'(.*?):(\d+):\d+: (?:fatal )?error: (.*)', ln)
        if m: out.setdefault(m.group(1), []).append((int(m.group(2)), m.group(3)))
    return out


SYN_ARGS = ['--target=arm-none-eabi', '-std=gnu99', '-D__forceinline=__inline__', '-D__weak=']         # armcc's keywords, for clang


def stub_includes(d):
    """A directory with the two library headers a rule may add an include of."""
    inc = os.path.join(d, 'inc'); os.makedirs(inc, exist_ok=True)
    with open(os.path.join(inc, 'string.h'), 'w') as f: f.write('void *memcpy(void *, const void *, unsigned int);\n')
    with open(os.path.join(inc, 'stdlib.h'), 'w') as f: f.write('int abs(int);\n')
    return ['-isystem', inc]


class PropertyTest(unittest.TestCase):
    """Every rule over a corpus with many seeds: each result applies, tokenizes with balanced brackets,
    parses again, differs from its input, and (with clang) is C that clang accepts."""
    SEEDS = int(os.environ.get('EXHUME_PERM_SEEDS', '200'))
    CROSS_SEEDS = int(os.environ.get('EXHUME_PERM_CROSS_SEEDS', '30'))

    def test_synthetic(self):
        cross = sorted({b for bs in BEHAVE.values() for b in bs})
        groups, total, per_rule = [], 0, {}
        for r in R.RULES:
            for body in [CASES[r.id][0]] if r.id in CASES else []:
                ctx, text = ctx_for(body)
                outs = variants(ctx, r.id, self.SEEDS, self)
                self.assertTrue(outs, f'{r.id} found no site on its own case')
                groups.append((r.id, body, [n for _, n, _ in outs[:12]], [e.desc for _, _, e in outs[:12]]))
                total += len(outs); per_rule[r.id] = len(outs)
            for body in cross:
                ctx, text = ctx_for(body)
                outs = variants(ctx, r.id, self.CROSS_SEEDS, self)
                if outs: groups.append((r.id, body, [n for _, n, _ in outs[:4]], [e.desc for _, _, e in outs[:4]]))
                total += len(outs); per_rule[r.id] = per_rule.get(r.id, 0) + len(outs)
        print(f'\nproperty (synthetic): {len(R.RULES)} rules, {self.SEEDS} seeds on each own case and {self.CROSS_SEEDS} on each of '
              f'{len(cross)} larger functions: {total} distinct variants', file=sys.stderr)
        if not CLANG: return
        with tempfile.TemporaryDirectory() as d:
            args = SYN_ARGS + stub_includes(d)
            files, where = [], {}
            for g, (rule, body, texts, descs) in enumerate(groups):
                b = bundle(texts)
                if b is not None:
                    p = os.path.join(d, f'g{g}.c')
                    with open(p, 'w') as f: f.write(b[0])
                    files.append(p); where[p] = (rule, texts, descs, b[1])
                else:
                    for k, text in enumerate(texts):
                        p = os.path.join(d, f'g{g}_{k}.c')
                        with open(p, 'w') as f: f.write(text)
                        files.append(p); where[p] = (rule, [text], [descs[k]], [1])
            bad = []
            for i in range(0, len(files), 60):
                for p, errs in clang_errors(files[i:i + 60], args).items():
                    rule, texts, descs, lines = where[p]
                    for ln, msg in errs:
                        k = max(j for j, l0 in enumerate(lines) if l0 <= ln) if ln >= lines[0] else 0
                        bad.append(f'{rule} ({descs[k]}): {msg}\n    {" ".join(texts[k][len(PRELUDE):].split())[:400]}')
            self.assertFalse(bad, f'{len(bad)} results clang refuses:\n' + '\n'.join(bad[:12]))

    @unittest.skipUnless(os.path.isfile(REAL_UNIT), 'the firmware tree is not on this machine')
    def test_real_unit(self):
        import permscreen
        with open(REAL_UNIT, encoding='latin1') as f: text = f.read()
        toks, tail = permc.tokenize(text)
        ti = permc.file_types(REAL_UNIT, text, [])
        ti.scan_globals(toks)
        pair = permc.match_brackets(toks)
        seeds = int(os.environ.get('EXHUME_PERM_REAL_SEEDS', '25'))
        chosen, total = [], 0
        for func in REAL_FUNCS:
            ctx = R.Ctx(toks, tail, func, ti, pair)
            for r in R.RULES:
                outs = variants(ctx, r.id, seeds, self, deep=2)
                total += len(outs)
                if outs: chosen.append((r.id, func, outs[0][1], outs[0][2].desc))
        print(f'\nproperty (real unit): {len(R.RULES)} rules x {len(REAL_FUNCS)} functions x {seeds} seeds: {total} distinct variants, '
              f'{len(chosen)} compiled by clang', file=sys.stderr)
        if not CLANG: return
        scr = permscreen.Screen(REAL_UNIT, [], text)
        args = [a for a in scr.args if not a.startswith('-W') and a != '-fsyntax-only']
        with tempfile.TemporaryDirectory() as d:                    # variants are written outside the repository and removed
            args += stub_includes(d)
            base = os.path.join(d, 'base.c')
            with open(base, 'w', encoding='latin1') as f: f.write(text)
            files = [base]
            for k, (rule, func, new, desc) in enumerate(chosen):
                p = os.path.join(d, f'v{k}.c')
                with open(p, 'w', encoding='latin1') as f: f.write(new)
                files.append(p)
            errs = {}
            for i in range(0, len(files), 40): errs.update(clang_errors(files[i:i + 40], args))
            allowed = {m for _, m in errs.get(base, [])}                # armcc accepts some C clang refuses
            bad = []
            for k, (rule, func, new, desc) in enumerate(chosen):
                extra = [m for _, m in errs.get(os.path.join(d, f'v{k}.c'), []) if m not in allowed]
                if extra: bad.append(f'{rule} on {func} ({desc}): {extra[0]}')
            self.assertFalse(bad, f'{len(bad)} results with errors the unit does not have:\n' + '\n'.join(bad[:15]))


HARNESS = r'''
static unsigned t_seed, t_reads;
static unsigned t_rand(void) { t_seed ^= t_seed << 13; t_seed ^= t_seed >> 17; t_seed ^= t_seed << 5; return t_seed >> 3; }
static void t_fill(void *p, unsigned n) { unsigned char *b = p; while (n--) *b++ = (unsigned char)t_rand(); }
static void t_dump(const char *name, const void *p, unsigned n) { const unsigned char *b = p; printf("%s", name); while (n--) printf(" %02x", *b++); printf("\n"); }
void send(uchar code, ushort value) { printf("send %u %u\n", code, value); }
int read_byte(void) { t_reads++; printf("read\n"); return (int)((t_seed * 7 + t_reads * 13) & 0xff); }
int t_abs(int x) { return x < 0 ? -x : x; }
void *t_memcpy(void *d, const void *s, unsigned int n) { unsigned char *a = d; const unsigned char *b = s; while (n--) *a++ = *b++; return d; }
'''


STATES = 30         # states of the globals each variant is run from


def host_program(ctx, texts, loose=False):
    """A host C program that runs every variant (of one PRELUDE + body file) over STATES states of the
    globals and prints, per variant and seed, the calls made, the value returned and the globals. The
    pointers passed point into the globals themselves, so a rule that assumes a store through a pointer
    cannot reach a named object shows up."""
    b = bundle(texts, loose)
    if b is None: return None
    g = ctx.types.globals
    names = sorted(n for n, v in g.items() if v[2] and n not in ctx.types.functions)
    defs = ''.join(f'{g[n][0]} {n}{(g[n][1] or "").replace("[ ]", "[16]")};\n' for n in names)
    t = ctx.toks
    at = next(i for i in range(ctx.start, ctx.lb) if t[i][1] == ctx.func and t[i + 1][1] == '(')
    rt = ' '.join(x[1] for x in t[ctx.start:at] if x[1] not in ('static', 'extern'))
    args = []
    for p, ty in ctx.params.items():
        if ty.strip().endswith('*'): args.append(f'({ty})&state' if 'State' in ty else f'({ty})table' if 'ushort' in ty else f'({ty})&state')
        else: args.append(f'({ty})t_rand()')
    out = ['int printf(const char *, ...);\n', b[0], defs, HARNESS]
    for k in range(len(texts)):
        fill = ''.join(f'    t_fill(&{n}, sizeof {n});\n' for n in names)
        call = f'{ctx.func}_v{k}({", ".join(args)})'
        call = f'    {call};\n' if rt == 'void' else f'    printf("ret %ld\\n", (long){call});\n'
        dump = ''.join(f'    t_dump("{n}", &{n}, sizeof {n});\n' for n in names)
        # the three globals the cases branch and loop on take every small value: loops run 0 to 4 times
        out.append(f'static void run_{k}(unsigned s) {{\n    t_seed = s * 2654435761u + 0x9e3779b9u; t_reads = 0;\n{fill}'
                   f'    counter = s % 5; status = (s / 5) % 3; phase = t_rand() % 5;\n{call}{dump}}}\n')
    out.append('int main(void) {\n' + ''.join(f'    printf("== {k}\\n"); for (unsigned s = 0; s < {STATES}; s++) run_{k}(s);\n' for k in range(len(texts)))
               + '    return 0;\n}\n')
    return ''.join(out)


def behaviour(ctx, text, outs, d, tag):
    """[(description, variant text)] of the variants that do not behave as text does; None when the
    variants cannot share one program (an edit above the function's part of the file)."""
    texts = [text] + [n for _, n, _ in outs]
    prog = host_program(ctx, texts, tag.rsplit('_', 1)[0] in LOOSE_BUNDLE)
    if prog is None: return None
    src, exe = os.path.join(d, tag + '.c'), os.path.join(d, tag)
    with open(src, 'w') as f: f.write(prog)
    r = subprocess.run([CLANG, '-O0', '-w', '-std=gnu99', '-D__forceinline=__inline__', '-Dabs=t_abs', '-Dmemcpy=t_memcpy', src, '-o', exe],
                       capture_output=True, text=True)
    if r.returncode: raise AssertionError(f'{tag}: the host program does not compile:\n{r.stderr[:1500]}')
    run = subprocess.run([exe], capture_output=True, text=True, timeout=60)
    parts = re.split(r'^== \d+\n', run.stdout, flags=re.M)[1:]
    if run.returncode or len(parts) != len(texts): raise AssertionError(f'{tag}: the host program failed (exit {run.returncode})')
    return [(outs[k - 1][2].desc, outs[k - 1][1]) for k in range(1, len(texts)) if parts[k] != parts[0]]


def run_programs(jobs, d):
    """behaviour() of each job (rule or None, ctx, text, outs, tag), three host compiles at a time."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:
        return list(ex.map(lambda j: behaviour(j[1], j[2], j[3], d, j[4]), jobs))


@unittest.skipUnless(CLANG, 'needs clang')
class BehaviourTest(unittest.TestCase):
    """The rules that claim the same meaning (or add dead code only), run as programs: input and
    output are compiled for the host at -O0 with stubs for the PRELUDE's externs and run over thirty
    states of the globals; the calls made (with arguments), the value returned and the final globals
    must be equal."""
    SEEDS = int(os.environ.get('EXHUME_PERM_SEEDS', '200'))

    def check(self, rules, known=()):
        bad, n, jobs = [], 0, []
        for rule in rules:
            if rule not in CASES: continue
            for j, body in enumerate([CASES[rule][0]] + BEHAVE.get(rule, [])):
                ctx, text = ctx_for(body)
                outs = [o for o in variants(ctx, rule, self.SEEDS) if o[2].meaning != 'type'][:24]
                if outs: jobs.append((rule, ctx, text, outs, f'{rule}_{j}'))
        with tempfile.TemporaryDirectory() as d:
            for (rule, ctx, text, outs, tag), res in zip(jobs, run_programs(jobs, d)):
                self.assertIsNotNone(res, f'{rule}: its variants cannot be put in one host program')
                n += len(outs)
                if rule in known: continue
                bad += [f'{rule} ({desc}):\n    {" ".join(new[len(PRELUDE):].split())[:500]}' for desc, new in res]
        return bad, n, len(jobs)

    def test_new_rules(self):
        bad, n, built = self.check(NEW_RULES)
        print(f'\nbehaviour (new rules): {n} variants in {built} programs', file=sys.stderr)
        self.assertFalse(bad, f'{len(bad)} variants behave differently:\n' + '\n'.join(bad[:12]))

    def test_old_rules(self):
        bad, n, built = self.check([r for r in OLD_RULES if r in CASES], KNOWN_UNSOUND)
        print(f'\nbehaviour (old rules): {n} variants in {built} programs', file=sys.stderr)
        self.assertFalse(bad, f'{len(bad)} variants behave differently:\n' + '\n'.join(bad[:12]))


    def test_chains(self):
        """Several rules one after another (as the search applies them) on the larger functions: the last
        text of each chain behaves as the first."""
        class Made:
            meaning = 'same'
            def __init__(self, desc): self.desc = desc
        rules = [r for r in R.RULES if r.id not in ('retype', 'const_type', 'global_type', 'callee_linkage', 'sized_extern', 'ptr_index', 'size_and')]
        chains = int(os.environ.get('EXHUME_PERM_CHAINS', '10'))
        bad, n, jobs = [], 0, []
        for bi, body in enumerate(sorted({b for bs in BEHAVE.values() for b in bs})):
            ctx0, text = ctx_for(body)
            outs = []
            for c in range(chains):
                rng = random.Random(bi * 1000 + c)
                ctx, trail = ctx0, []
                for step in range(5):
                    r = rng.choice(rules)
                    ed = r.fn(ctx, rng, everywhere=rng.random() < 0.3)
                    if ed is None or ed.meaning == 'type': continue
                    ctx = R.remake(ctx, R.apply(ctx.toks, ed.spans))
                    trail.append(f'{r.id} ({ed.desc})')
                new = permc.render(ctx.toks, ctx.tail)
                if trail and new.startswith(PRELUDE): outs.append((c, new, Made(' / '.join(trail))))
            if outs: jobs.append((None, ctx0, text, outs, f'chain{bi}'))
        with tempfile.TemporaryDirectory() as d:
            for (_, ctx0, text, outs, tag), res in zip(jobs, run_programs(jobs, d)):
                if res is None: continue
                n += len(outs)
                bad += [f'{desc}:\n    {" ".join(new[len(PRELUDE):].split())[:500]}' for desc, new in res]
        print(f'\nbehaviour (chains of up to 5 rules): {n} chains', file=sys.stderr)
        self.assertFalse(bad, f'{len(bad)} chains end in another program:\n' + '\n'.join(bad[:8]))


class RefusalTest(unittest.TestCase):
    """Sites a rule must leave alone."""

    def none(self, rule, body, pred=None):
        outs, _ = run_rule(rule, body)
        outs = [n for _, n, _ in outs if pred is None or pred(n)]
        self.assertEqual(outs, [], f'{rule} should refuse: {body}')

    def test_moves(self):
        # a store through a pointer does not pass a read of a global, nor a call a global store
        self.none('stmt_swap', 'void f(uchar *p) { *p = 1; phase = status; }')
        self.none('stmt_move', 'void f(uchar *p) { p[0] = 1; counter = 2; phase = status; }', lambda n: 'phase = status; counter = 2; p[0]' in n)
        self.none('stmt_move', 'void f(void) { status = 1; send(1, 2); phase = 3; counter = 4; }', lambda n: 'send(1, 2); status = 1' in n)
        # nothing moves past a jump out or a label
        self.none('stmt_move', 'void f(void) { status = 1; if (phase) return; counter = 2; if (phase > 2) goto L; table[0] = 1; L: table[1] = 2; }')
        self.none('compound_swap', 'void f(void) { if (status) phase = 1; if (phase) counter = 2; }')
        self.none('if_hoist', 'void f(void) { if (status) { status = 0; phase = 1; } else { status = 0; phase = 2; } }', lambda n: 'status = 0; if' in n)

    def test_locals(self):
        # lives that overlap, a value carried round a loop, an address taken
        self.none('local_merge', 'void f(void) { uchar a; uchar b; a = status; b = phase; send(a, b); }')
        self.none('local_merge', 'void f(void) { uchar a; uchar b; while (counter < 3) { send(a, 1); a = 2; b = phase; send(b, 0); counter++; } }')
        self.none('local_split', 'void f(void) { uchar a; a = 0; while (counter < 3) { send(a, 1); a = phase; counter++; } }')
        self.none('local_split', 'void f(void) { uchar a; uchar *p; p = &a; a = status; send(a, *p); a = phase; send(a, *p); }')
        self.none('local_reuse', 'void f(void) { uchar a; uchar b; b = status; a = phase; send(a, 1); send(b, 2); }')

    def test_expressions(self):
        self.none('embed_assign', 'void f(void) { uchar k; k = status; if (phase && k) counter = 1; send(k, 0); }')
        self.none('embed_assign', 'void f(void) { uchar k; k = read_byte(); send(read_byte(), k); }')
        self.none('abs_form', 'extern int abs(int);\nvoid f(void) { counter = abs(counter); }')
        self.none('expr_split', 'void f(void) { ushort v; v = table[0]; v = table[1] + v; counter = v; }', lambda n: 'v = table[1]; v += v' in n)
        self.none('expr_split', 'void f(float x, float y) { float s; s = 1; s += x + y; counter = s; }')
        self.none('sub_neg', 'void f(uchar *p, uchar *q) { counter = p - q; }')
        self.none('byte_read', 'void f(short *p) { counter = p[0] << 8 | p[1]; }', lambda n: '+' in n[len(PRELUDE):])     # | is not + for a short
        self.none('memcpy_loop', 'extern void *memcpy(void *, const void *, unsigned int);\n'
                                 'void f(void) { uchar i; for (i = 0; i < 4; i++) table[i] = table[i]; }')

    def test_includes_are_added(self):
        outs, _ = run_rule('memcpy_loop', 'extern ushort copy[8];\nvoid f(void) { uchar i; for (i = 0; i < 8; i++) copy[i] = table[i]; }')
        self.assertTrue(outs and all('#include <string.h>' in n for _, n, _ in outs))
        outs, _ = run_rule('abs_form', 'void f(short v) { if (v < 0) v = -v; send(1, v); }')
        self.assertTrue(any('#include <stdlib.h>' in n and 'abs(v)' in n for _, n, _ in outs))

    def test_copy_through_a_pointer_is_not_called_the_same(self):
        outs, _ = run_rule('memcpy_loop', 'extern void *memcpy(void *, const void *, unsigned int);\n'
                                          'void f(uchar *p, uchar *q) { uchar i; for (i = 0; i < 8; i++) p[i] = q[i]; }')
        self.assertTrue(outs and all(ed.meaning == 'type' for _, _, ed in outs))

    def test_loop_form_reaches_the_two_index_header(self):
        """A copy loop with a second counter stepped in its body reaches the two-variable for header."""
        body = ('extern uchar out_buf[64], reply[64];\nextern uchar reply_length;\n'
                'void f(void) { uchar k, position; position = 13; for (k = 0; k < reply_length; k++) { out_buf[position] = reply[k]; position++; } }')
        outs, _ = run_rule('loop_form', body, seeds=400)
        self.assertTrue(any(norm('for (k = 0, position = 13; k < reply_length; k++, position++)') in norm(n) for _, n, _ in outs))


@unittest.skipUnless(os.environ.get('EXHUME_PERM_TIMING') and os.path.isfile(REAL_UNIT), 'set EXHUME_PERM_TIMING=1 (needs the firmware tree)')
class TimingTest(unittest.TestCase):
    def test_time_per_rule(self):
        """Milliseconds per call of each rule on the large real function: on a base parsed afresh (first
        call, which fills the rule's caches) and the mean of the calls after it."""
        import time
        with open(REAL_UNIT, encoding='latin1') as f: text = f.read()
        toks, tail = permc.tokenize(text)
        ti = permc.file_types(REAL_UNIT, text, [])
        ti.scan_globals(toks)
        pair = permc.match_brackets(toks)
        rows = []
        for r in R.RULES:
            first, rest, hits = [], [], 0
            for rep in range(3):
                ctx = R.Ctx(toks, tail, REAL_FUNCS[0], ti, pair)
                for seed in range(31):
                    t0 = time.perf_counter()
                    ed = r.fn(ctx, random.Random(seed * 7 + rep), everywhere=False)
                    dt = (time.perf_counter() - t0) * 1000
                    (first if seed == 0 else rest).append(dt)
                    hits += ed is not None
            rows.append((sum(rest) / len(rest), r.id, r.weight, sum(first) / len(first), max(rest), hits))
        print('\nrule                weight  first ms  mean ms   max ms  sites/93', file=sys.stderr)
        for mean, rid, w, first, mx, hits in sorted(rows, reverse=True):
            print(f'{rid:18} {w:6} {first:9.2f} {mean:8.2f} {mx:8.2f} {hits:6}', file=sys.stderr)


if __name__ == '__main__':
    unittest.main()
