"""UW2's targets for tools/fuzzasm.py ([fuzz] targets): 35 routines in seven modules, run on the
original's bytes in Unicorn and on UW2Decomp's port in tools/fuzzhost.c with
examples/uw2/port/fuzzhost-uw2.c (docs/port.md, "Fuzzing single routines").

| Module | Targets | What they test |
| --- | --- | --- |
| IMATH (seg021) | 9 | sincos and lsqrt through the glue into sys/imath.c; cSinCos, cFstSinCos and cSqRt, its C entries; the translated arcsine, arccosine and atan2; cAtan2, C into the translation |
| INSTANCE (seg004) | 5 | mm3x9_bpsi, mm3x9t (with the saturation slip in UW2Decomp's FINDINGS.md), mm9x9, code_pnt, mxmul |
| SPHERE | 2 | sphere_check; get_dist, which nothing in the EXE calls |
| GRENTRY (seg003) | 7 | the frame buffer's span writers on a frame buffer laid out as cPlaceFB lays it out, cFillFB, cDimFB, cLiteFB |
| CLIP | 1 | _asm_clip_polygon, polygons across the frustum and behind the eye |
| EXPAND | 11 | every image decoder, the palette builder and the record decoder, hand-written and translated, alone and through seg004_uncmp |

Each T gives the routine's code segment (an EXE paragraph) and offset, how DOS calls it (near
or far) and how the port does (the same, or one of fuzzhost-uw2.c's call kinds), a generator of
registers and memory, and what to compare: the registers and flags its callers read, and
memory, with ranges left out and a note saying why.
"""
from fuzzasm import T, base_regs, setw, word, EDGE16, ALLFLAGS, GPR, LOAD, SCRATCH, STACK
import struct

FD71 = 0x60B9 + LOAD                   # seg021's data, dseg062_62a6
SEG004_DATA = 0x4FAF + LOAD            # seg052_519C, the renderer's data
CMPBUF = 0x43A0 + LOAD                 # cmpbuf1_start
# the regions the port has (fuzzhost-uw2.c's fuzz_regions): memory outside them that the
# original changes is memory the port cannot model, and is reported
REGIONS = [(0x37050 + LOAD * 16, 0x6061C + LOAD * 16), (FD71 * 16, FD71 * 16 + 0xC40),
           (0x850 + LOAD * 16, 0x850 + LOAD * 16 + 0x065C0 - 0x850 + 0x10010),
           (SCRATCH * 16, SCRATCH * 16 + 0x10000), (STACK * 16, STACK * 16 + 0x10000)]


def g_imath(inputs):
    """IMATH: DS = seg021's data, the given 16-bit inputs, edge values first."""
    def gen(rng, k):
        r = base_regs(rng); r['ds'] = FD71
        for i, name in enumerate(inputs):
            v = word(rng, (k + i * 7) % (len(EDGE16) * 2) if k < len(EDGE16) * 2 else 99)
            setw(r, name, v)
        return r, []
    return gen


def g_sqrt(rng, k):
    """CX:BX for the square root: every magnitude, the four guesses' boundaries, the largest
    values (whose first quotient overflows: DOS faults)."""
    r = base_regs(rng); r['ds'] = FD71
    edges = [0, 1, 2, 3, 0xFF, 0x100, 0x101, 0xFFFF, 0x10000, 0xFFFFFF, 0x1000000, 0x3FFFFFFF, 0x40000000,
             0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFE0001, 0xFFFE0000]
    if k < len(edges): v = edges[k]
    else: v = rng.getrandbits(rng.choice([8, 16, 24, 32]))
    setw(r, 'cx', v >> 16); setw(r, 'bx', v)
    return r, []


def g_matrix(row_at_bp_ss=True, transposed=False):
    """mm3x9_bpsi and mm3x9t: a row of three 1.15 words and a 3 x 3 matrix, mostly in range,
    with overflowing products and 8000h now and then (the saturation paths)."""
    def gen(rng, k):
        r = base_regs(rng); r['ds'] = SCRATCH
        def v():
            c = rng.random()
            return rng.choice([0x7FFF, 0x8000, 0x8001, 0xFFFF, 0]) if c < 0.25 else rng.getrandbits(16)
        a = struct.pack('<3H', *[v() for _ in range(3)])
        m = struct.pack('<9H', *[v() for _ in range(9)])
        setw(r, 'bp', 0x200); setw(r, 'si', 0x100)
        # [bp] is SS-relative: the row (or the matrix, transposed) in the stack segment
        return r, [(STACK * 16 + 0x200, m if transposed else a), (SCRATCH * 16 + 0x100, a if transposed else m)]
    return gen


def g_mm9x9(rng, k):
    r = base_regs(rng); r['ds'] = SCRATCH; r['es'] = SCRATCH
    m1 = struct.pack('<9H', *[rng.getrandbits(16) for _ in range(9)])
    m2 = struct.pack('<9H', *[rng.getrandbits(16) for _ in range(9)])
    setw(r, 'bp', 0x200); setw(r, 'si', 0x100)
    return r, [(STACK * 16 + 0x200, m1), (SCRATCH * 16 + 0x100, m2)]


def g_point(rng, k):
    r = base_regs(rng); r['ds'] = SEG004_DATA
    for n in ('bx', 'cx', 'bp'):
        setw(r, n, word(rng, rng.randrange(len(EDGE16) * 2)))
    return r, []


def g_image(fmt):
    """An image for the EXPAND decoders: its size word and data at E000:0100 (AX the paragraph,
    BP the size word's offset), a 16 or 32 byte aux palette at E000:0000 (DS:SI), and DH a
    lightabs row (0..15, or FFh for none). The run-length forms get streams of valid words with
    every record kind: repeats, runs, the extended counts and the repeat-count records."""
    def stream(rng, nbits, n):
        out = []
        while len(out) < n:
            c = rng.random()
            if c < 0.5: out += [rng.randrange(3, 1 << nbits), rng.randrange(1 << nbits)]
            elif c < 0.6: out += [1]
            elif c < 0.7: out += [0, rng.randrange(1, 1 << nbits), rng.randrange(1 << nbits), rng.randrange(1 << nbits)]
            elif c < 0.75: out += [2, rng.randrange(1, 3), rng.randrange(3, 1 << nbits), rng.randrange(1 << nbits), 1]
            run = rng.randrange(0, 1 << nbits)
            out += [run] + [rng.randrange(1 << nbits) for _ in range(run)]
        return out[:n]

    def gen(rng, k):
        r = base_regs(rng)
        size = [1, 2, 3, 7, 8, 9][k] if k < 6 else rng.randrange(1, 600)
        dh = rng.choice([0xFF, 0, 5, 15]) if k % 3 else 0xFF
        if fmt == 4: data = bytes(rng.getrandbits(8) for _ in range(size))
        elif fmt == 10: data = bytes(rng.getrandbits(8) for _ in range(size))
        elif fmt == 8:
            w = stream(rng, 4, size); w += [0] * (len(w) & 1)
            data = bytes(w[i] << 4 | w[i + 1] for i in range(0, len(w), 2))
        else:
            w = stream(rng, 5, size); w += [0] * (-len(w) % 8)
            bits = ''.join(f'{x:05b}' for x in w)
            data = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))
        img = struct.pack('<H', size) + data
        pal = bytes(rng.getrandbits(8) for _ in range(32))
        r['ds'] = SCRATCH
        setw(r, 'si', 0); setw(r, 'ax', SCRATCH + 0x10); setw(r, 'bp', 0); setw(r, 'dh', dh)
        setw(r, 'bx', fmt)
        return r, [(SCRATCH * 16, pal), ((SCRATCH + 0x10) * 16, img)]
    return gen


def g_pal(rng, k):
    r = base_regs(rng); r['ds'] = SCRATCH
    setw(r, 'si', rng.randrange(0, 0x100)); setw(r, 'cx', rng.choice([1, 2]))
    setw(r, 'ax', SCRATCH + 0x20); setw(r, 'dh', rng.choice([0xFF, 0, 1, 7, 15]))
    return r, [(SCRATCH * 16, bytes(rng.getrandbits(8) for _ in range(0x140)))]


def g_records(nbits):
    """The record decoder alone (_18D): DS:SI the unpacked words, CX their number, ES:DI the
    output, BX 0 (uncmp_pal)."""
    def gen(rng, k):
        r = base_regs(rng)
        n = rng.randrange(1, 400)
        words = []
        while len(words) < n:
            c = rng.random()
            if c < 0.5: words += [rng.randrange(3, 1 << nbits), rng.randrange(1 << nbits)]
            elif c < 0.6: words += [1]
            elif c < 0.7: words += [0, rng.randrange(1, 1 << nbits), rng.randrange(1 << nbits)]
            elif c < 0.75: words += [2, rng.randrange(1, 3), rng.randrange(3, 1 << nbits), rng.randrange(1 << nbits), 1]
            run = rng.randrange(0, 1 << nbits)
            words += [run] + [rng.randrange(1 << nbits) for _ in range(run)]
        words = words[:n]
        # the buffers exp_4run and exp_5run give it, the only ones 3d/expand.c's _18D takes
        r['ds'] = CMPBUF; r['es'] = CMPBUF
        setw(r, 'si', 0); setw(r, 'cx', len(words)); setw(r, 'di', 0x5400); setw(r, 'bx', 0)
        return r, [(CMPBUF * 16, bytes(words))]
    return gen


def g_clip(rng, k):
    """_asm_clip_polygon: CX vertices of 32 bytes (x, y, z, sx, sy, u, v, l, 16.16) at DS:DC00
    (seg004's data), around the view frustum: mostly in front of the eye, some behind it, some
    on its planes."""
    r = base_regs(rng); r['ds'] = SEG004_DATA
    n = [3, 3, 4, 16][k] if k < 4 else rng.randrange(3, 17)
    vs = b''
    for _ in range(n):
        z = rng.choice([rng.randrange(1, 0x400), rng.randrange(-0x40, 0x40), 0x100])
        x = rng.choice([rng.randrange(-3 * abs(z) - 1, 3 * abs(z) + 2), z, -z])
        y = rng.choice([rng.randrange(-3 * abs(z) - 1, 3 * abs(z) + 2), z, -z])
        f = lambda v: (v << 16 | rng.getrandbits(16)) & 0xFFFFFFFF
        vs += struct.pack('<8I', f(x), f(y), f(z), 0, 0, f(rng.randrange(0, 64)), f(rng.randrange(0, 64)),
                          rng.getrandbits(20))
    setw(r, 'si', 0xDC00); setw(r, 'cx', n)
    return r, [(SEG004_DATA * 16 + 0xDC00, vs)]


def g_dist(rng, k):
    """get_dist: three 32-bit eye-relative coordinates at seg004's ds:16h, 1Ah, 1Eh."""
    r = base_regs(rng); r['ds'] = SEG004_DATA
    vals = [rng.choice([0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, rng.getrandbits(32),
                        rng.getrandbits(20), -rng.getrandbits(20) & 0xFFFFFFFF]) for _ in range(3)]
    return r, [(SEG004_DATA * 16 + 0x16, struct.pack('<3I', *vals))]


def g_sphere(rng, k):
    """sphere_check: BX, AX, BP the object origin, SI the radius, CL the shift, against the view
    matrix the EXE's data holds."""
    r = base_regs(rng); r['ds'] = SEG004_DATA
    for n in ('bx', 'ax', 'bp'): setw(r, n, word(rng, rng.randrange(len(EDGE16) * 2)))
    setw(r, 'si', rng.choice([0, 1, 0x40, 0x100, 0x7FFF, rng.getrandbits(16)]))
    setw(r, 'cl', rng.randrange(0, 9))
    return r, []


SEG003_DATA = 0x370D + LOAD            # seg_370D, the graphics library's data


def fb_frame(rng):
    """A frame buffer as cPlaceFB lays it out (setup_frame_buf, GRENTRY.ASM): w by h, the row
    table at seg_370D:095C (row y at 2 + y * (w + 2), rows past h at 69D4h), the frame buffer
    segment (stdat) at 0958 as the EXE has it. Returns (w, h, preset)."""
    w = rng.choice([320, 320, 32, rng.randrange(8, 321)])
    h = rng.choice([200, 112, rng.randrange(1, 201)])
    rows = [2 + y * (w + 2) for y in range(h)] + [0x69D4] * (200 - h)
    return w, h, [(SEG003_DATA * 16 + 0x95C, struct.pack('<200H', *rows))]


def spans(rng, w, h, n, extra):
    """n span records (y, left, right, then the extra words), left and right in either order,
    ended by a word with its top bit set."""
    out = b''
    for _ in range(n):
        a, b = rng.randrange(w), rng.randrange(w)
        out += struct.pack('<3H', rng.randrange(h), a, b) + struct.pack(f'<{len(extra)}H', *[f(rng) for f in extra])
    return out + struct.pack('<H', 0x8000 | rng.getrandbits(15))


def g_fb_solid(rng, k):
    """_9C9, the frame buffer's solid span writer: 6-byte spans at seg_370D:4E00, the colour at
    4111."""
    r = base_regs(rng); r['ds'] = SEG003_DATA
    w, h, pre = fb_frame(rng)
    sp = spans(rng, w, h, rng.randrange(0, 24), [])
    setw(r, 'si', 0x4E00)
    return r, pre + [(SEG003_DATA * 16 + 0x4E00, sp), (SEG003_DATA * 16 + 0x4111, bytes([rng.getrandbits(8)]))]


def g_fb_rows(rng, k):
    """_6F8 and _729, linear bitmap rows into the frame buffer (8-byte records: y, left, right,
    the source's offset in the segment at seg_370D:55EC), on seg_370D as their stack, as the
    library runs them; the source, with zeros for _729's transparency, in the scratch segment."""
    r = base_regs(rng); r['ds'] = SCRATCH; r['ss'] = SEG003_DATA; r['esp'] = 0x4FA0
    w, h, pre = fb_frame(rng)
    recs = b''
    for _ in range(rng.randrange(0, 16)):
        a = rng.randrange(w); b = rng.randrange(a, w)
        recs += struct.pack('<4H', rng.randrange(h), a, b, rng.randrange(0, 0x0F00))
    recs += struct.pack('<H', 0x8000)
    src = bytes(rng.choice([0, 0, rng.getrandbits(8)]) for _ in range(0x1000))
    setw(r, 'si', 0x4C00)
    return r, pre + [(SEG003_DATA * 16 + 0x4C00, recs), (SEG003_DATA * 16 + 0x55EC, struct.pack('<H', SCRATCH)),
                     (SCRATCH * 16, src)]


def g_fb_smooth(rng, k):
    """_C13, Gouraud spans into the frame buffer: 10-byte records (y, left, right, left and right
    intensity, 8.8 with the lightabs row in the high byte) at DS:SI, the solid vectors at
    L0BC3 and L0BC5 as the EXE has them, the base colour at seg004:6D20."""
    r = base_regs(rng); r['ds'] = SEG003_DATA; r['ss'] = SEG003_DATA; r['esp'] = 0x4FA0
    w, h, pre = fb_frame(rng)
    inten = lambda rng: rng.choice([0, 0x0F00, rng.randrange(0, 0x1000)])
    sp = spans(rng, w, h, rng.randrange(0, 12), [inten, inten])
    setw(r, 'si', 0x4C00)
    return r, pre + [(SEG003_DATA * 16 + 0x4C00, sp), ((S4 + LOAD) * 16 + 0x6D20, bytes([rng.getrandbits(8)]))]


def g_fb_al(rng, k):
    r = base_regs(rng); r['ds'] = SEG003_DATA
    setw(r, 'ax', rng.getrandbits(16))
    return r, []


def g_fb_dim(rng, k):
    r = base_regs(rng); r['ds'] = SEG003_DATA
    setw(r, 'cl', rng.randrange(0, 16)); setw(r, 'ch', rng.getrandbits(8))
    return r, [((0x3CF5 + LOAD) * 16 + 2 + i * 0x800, bytes(rng.getrandbits(8) for _ in range(64))) for i in range(13)]


def g_fb_lite(rng, k):
    r = base_regs(rng); r['ds'] = SEG003_DATA
    setw(r, 'cx', rng.choice([1, 1, 2, 3]))
    return r, [((0x3CF5 + LOAD) * 16 + 2 + i * 0x800, bytes(rng.getrandbits(8) for _ in range(64))) for i in range(13)]


IM, S4 = 0x2110, 0x065C
# L01A6, the byte the record decoder patches to a ret and back: the code has 2Bh there (sub ax,ax
# as 2B C0) and the decoder writes back 29h (sub ax,ax as 29 C0), so after the first repeat-count
# record DOS's code segment holds the other encoding of the same instruction; 3d/expand.c keeps
# the patch as a flag and leaves the byte as it was
L01A6 = [((S4 + LOAD) * 16 + 0x1A6, (S4 + LOAD) * 16 + 0x1A7)]
L01A6_NOTE = ('the byte at L01A6 is not compared: DOS restores it as 29h where the code had 2Bh, the same '
              'instruction (sub ax,ax) in its other encoding; the C keeps the patch as a flag')
DECODER_NOTE = ('only AX (the pixels\' paragraph) and the memory are compared: every caller of uncmp_tab '
                '(do_uwobj, do_uwcrit, cFrmtoRaw) reads AX alone after the call, and the C sets no other register')
NOFLAGS = ()
TARGETS = [
    # IMATH (seg021): the integer sines, square roots and arctangents
    T('imath.sincos_far', IM, 0xA34, 'far', g_imath(['bx']), flags=NOFLAGS,
      what='sincos (_A34 -> _A38): sys/imath.c through the glue'),
    T('imath.lsqrt_far', IM, 0xA30, 'far', g_sqrt, flags=NOFLAGS,
      what='lsqrt (_A30 -> _A78): sys/imath.c through the glue'),
    T('imath.cSinCos', IM, 0xA38, 'near', g_imath(['bx']), port='csincos', regs=('eax', 'ebx'), flags=NOFLAGS,
      what="cSinCos: sys/imath.c's C entry, against _A38 (AX, BX the 16-bit results)"),
    T('imath.cFstSinCos', IM, 0xA69, 'near', g_imath(['bx']), port='cfst', regs=('eax', 'ebx'), flags=NOFLAGS,
      what="cFstSinCos: sys/imath.c's C entry, against _A69"),
    T('imath.cSqRt', IM, 0xA78, 'near', g_sqrt, port='csqrt', regs=('edi',), flags=NOFLAGS,
      what="cSqRt: sys/imath.c's C entry, against _A78 (DI)"),
    T('imath.asin', IM, 0xB78, 'near', g_imath(['ax']), what='_B78, arcsine: the translation (sys/imath_x.c)'),
    T('imath.acos', IM, 0xBA2, 'near', g_imath(['bx']), what='_BA2, arccosine: the translation'),
    T('imath.atan2', IM, 0xBD4, 'near', g_imath(['ax', 'bx']), what='_BD4, atan2: the translation'),
    T('imath.cAtan2', IM, 0xBD4, 'near', g_imath(['ax', 'bx']), port='catan2', regs=('ecx',), flags=NOFLAGS,
      ignore=[(FD71 * 16 + 0x400, FD71 * 16 + 0x510)],
      what="cAtan2: sys/imath.c's C entry into the translation, against _BD4 (CX)",
      note="outside the renderer cAtan2 runs on seg021's private stack (FD71:0510 down), as "
           "C3DENTRY.ASM's entry does: what it pushes there is not compared"),
    # INSTANCE (seg004): the 3 x 3 matrix products and the clip codes
    T('instance.mm3x9_bpsi', S4, 0x3B41, 'near', g_matrix(), what='mm3x9_bpsi: the translation (3d/instance.c)'),
    T('instance.mm3x9t', S4, 0x3BE8, 'near', g_matrix(transposed=True), what='mm3x9t: the translation'),
    T('instance.mm9x9', S4, 0x3B04, 'near', g_mm9x9, what='mm9x9: the translation (writes ES:14B2..14C3)'),
    T('instance.code_pnt', S4, 0x3A88, 'near', g_point, what='code_pnt, the clip codes: the translation'),
    T('instance.mxmul', S4, 0x3534, 'near', g_point, what="mxmul with the EXE's body: the translation"),
    # SPHERE (seg004): the bounding-sphere test and the distance estimate nothing in the EXE calls
    T('sphere.sphere_check', S4, 0x260, 'near', g_sphere, what='sphere_check: the translation (3d/sphere.c)'),
    T('sphere.get_dist', S4, 0x40F, 'near', g_dist, what='get_dist (no caller in the EXE): the translation'),
    # GRENTRY (seg003): the frame buffer's span writers and fills, on a frame buffer laid out as
    # cPlaceFB lays it out
    T('grentry.solid_spans', 0x0085, 0x9C9, 'near', g_fb_solid, what='_9C9, the solid span writer: the translation (gfx/grentry_x.c)'),
    T('grentry.rows', 0x0085, 0x6F8, 'near', g_fb_rows, what='_6F8, bitmap rows into the frame buffer: the translation'),
    T('grentry.rows_x', 0x0085, 0x729, 'near', g_fb_rows, regs=('esp', 'ds', 'es', 'ss'), flags=NOFLAGS,
      what='_729, the same skipping colour 0: gfx/grentry.c through seg003_call',
      note='the span writers\' callers read no register they leave, so only the memory is compared'),
    T('grentry.smooth', 0x0085, 0xC13, 'near', g_fb_smooth, what='_C13, Gouraud spans: the translation'),
    T('grentry.fill', 0x0085, 0x6E4, 'far', g_fb_al, heavy=True, what='_6E4, cFillFB: the translation'),
    T('grentry.dim', 0x0085, 0x764, 'far', g_fb_dim, heavy=True, what='_764, cDimFB: the translation'),
    T('grentry.lite', 0x0085, 0x788, 'far', g_fb_lite, heavy=True, what='_788, cLiteFB: the translation'),
    # CLIP (seg004): the frustum clipper
    T('clip.polygon', S4, 0x4686, 'near', g_clip, what='_asm_clip_polygon: the translation (3d/clip.c)'),
    # EXPAND (seg004): the image decoders
    T('expand.pal_63', S4, 0x63, 'near', g_pal, regs=('ebx', 'ecx', 'esi', 'ds', 'es'), flags=NOFLAGS,
      what='_63, uncmp_pal: 3d/expand.c through the glue',
      note='AL (the last byte translated), DX (the image paragraph) and DI (past uncmp_pal) are not '
           'set by the C: every caller loads all three again before reading them'),
    T('expand.exp_8str', S4, 0x20, 'near', g_image(4), what='exp_8str: the translation (3d/expand_x.c)'),
    T('expand.exp_4str', S4, 0x39, 'near', g_image(10), regs=('eax',), flags=NOFLAGS,
      what='exp_4str: 3d/expand.c through the glue', note=DECODER_NOTE),
    T('expand.exp_4run', S4, 0xD9, 'near', g_image(8), regs=('eax',), flags=NOFLAGS, ignore=L01A6,
      what='exp_4run: 3d/expand.c through the glue', note=DECODER_NOTE, heavy=True),
    T('expand.exp_5run', S4, 0x11C, 'near', g_image(6), regs=('eax',), flags=NOFLAGS, ignore=L01A6,
      what='exp_5run: the translation, with _63 and _18D hand-written C', note=DECODER_NOTE, heavy=True),
    T('expand.uncmp4', S4, 0x20, 'near', g_image(4), port='uncmp', regs=('eax',), flags=NOFLAGS,
      ignore=[(FD71 * 16 + 0x400, FD71 * 16 + 0x510)],
      what="seg004_uncmp, format 4 (cFrmtoRaw's C into the translated exp_8str)", note=DECODER_NOTE),
    T('expand.uncmp6', S4, 0x11C, 'near', g_image(6), port='uncmp', regs=('eax',), flags=NOFLAGS,
      ignore=[(FD71 * 16 + 0x400, FD71 * 16 + 0x510)] + L01A6,
      what="seg004_uncmp, format 6 (cFrmtoRaw's C into the translated exp_5run)", note=DECODER_NOTE, heavy=True),
    T('expand.uncmp8', S4, 0xD9, 'near', g_image(8), port='uncmp', regs=('eax',), flags=NOFLAGS, ignore=L01A6,
      what="seg004_uncmp, format 8 (cFrmtoRaw's C exp_4run)", note=DECODER_NOTE, heavy=True),
    T('expand.uncmp10', S4, 0x39, 'near', g_image(10), port='uncmp', regs=('eax',), flags=NOFLAGS,
      what="seg004_uncmp, format 0Ah (cFrmtoRaw's C exp_4str)", note=DECODER_NOTE),
    T('expand.records4', S4, 0x18D, 'near', g_records(4), regs=('ebx', 'edx', 'esi', 'edi', 'ebp', 'ds', 'es'),
      flags=NOFLAGS, ignore=L01A6, what='_18D, the record decoder: 3d/expand.c through the glue',
      note='AX and CX are left as the last record had them in DOS and not set by the C: its callers '
           '(exp_4run, exp_5run) load AX from ES at once and never read CX', heavy=True),
    T('expand.records5', S4, 0x18D, 'near', g_records(5), regs=('ebx', 'edx', 'esi', 'edi', 'ebp', 'ds', 'es'),
      flags=NOFLAGS, ignore=L01A6, what='_18D on 5-bit words', heavy=True),
]
