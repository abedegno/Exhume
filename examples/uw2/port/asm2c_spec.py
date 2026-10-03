"""UW2's spec for tools/asm2c.py ([asm2c] spec): the 26 assembly modules UW2Decomp's port
translates (seg004, the 3D renderer; the seg003 graphics library modules it calls with
registers; IMATH from seg021), the code segments and the routines the port has as hand-written C
instead (docs/port.md, "The static recompiler"). The overrides, C for single instructions, are
UW2Decomp's own (exhume.toml's [asm2c] overrides: its tools/asm2c.py).
Paths are relative to the UW2Decomp checkout. With this spec, Exhume's asm2c.py --check finds
UW2Decomp's committed translations up to date, and regenerating them reproduces every file
(examples/uw2/port/README.md).
"""

# (source, output, C name). The order is the segments' order.
MODULES = [
    ('src/3d/EXPAND.ASM', 'src/port/3d/expand_x.c', 'EXPAND'),
    ('src/3d/SPHERE.ASM', 'src/port/3d/sphere.c', 'SPHERE'),
    ('src/3d/SMOOTH.ASM', 'src/port/3d/smooth.c', 'SMOOTH'),
    ('src/3d/INTERP.ASM', 'src/port/3d/interp.c', 'INTERP'),
    ('src/3d/INSTANCE.ASM', 'src/port/3d/instance.c', 'INSTANCE'),
    ('src/3d/TMAPOPS.ASM', 'src/port/3d/tmapops.c', 'TMAPOPS'),
    ('src/3d/CLIP.ASM', 'src/port/3d/clip.c', 'CLIP'),
    ('src/3d/PERTLP.ASM', 'src/port/3d/pertlp.c', 'PERTLP'),
    ('src/3d/PERTP.ASM', 'src/port/3d/pertp.c', 'PERTP'),
    ('src/3d/PROJPOLY.ASM', 'src/port/3d/projpoly.c', 'PROJPOLY'),
    ('src/3d/SCANLINE.ASM', 'src/port/3d/scanline.c', 'SCANLINE'),
    ('src/3d/TEXMAP.ASM', 'src/port/3d/texmap.c', 'TEXMAP'),
    ('src/3d/TEXMAPV.ASM', 'src/port/3d/texmapv.c', 'TEXMAPV'),
    ('src/3d/PGCACHE.ASM', 'src/port/3d/pgcache_x.c', 'PGCACHE'),
    ('src/gfx/GRMISC.ASM', 'src/port/gfx/grmisc.c', 'GRMISC'),
    ('src/gfx/GRENTRY.ASM', 'src/port/gfx/grentry_x.c', 'GRENTRY'),
    ('src/gfx/SCALEBM.ASM', 'src/port/gfx/scalebm.c', 'SCALEBM'),
    ('src/gfx/VIDMODE.ASM', 'src/port/gfx/vidmode_x.c', 'VIDMODE'),
    ('src/gfx/GRLIBF.ASM', 'src/port/gfx/grlibf_x.c', 'GRLIBF'),
    ('src/gfx/GRLIBG.ASM', 'src/port/gfx/grlibg.c', 'GRLIBG'),
    ('src/gfx/GRLIBH.ASM', 'src/port/gfx/grlibh.c', 'GRLIBH'),
    ('src/gfx/GRLIBI.ASM', 'src/port/gfx/grlibi_x.c', 'GRLIBI'),
    ('src/gfx/GRDISP.ASM', 'src/port/gfx/grdisp.c', 'GRDISP'),
    ('src/gfx/GRLIBM.ASM', 'src/port/gfx/grlibm.c', 'GRLIBM'),
    ('src/gfx/GRLIBN.ASM', 'src/port/gfx/grlibn.c', 'GRLIBN'),
    ('src/sys/IMATH.ASM', 'src/port/sys/imath_x.c', 'IMATH'),
]
MODTAB = 'src/port/x86/modtab.c'

# The code segments, by the prefix of the target names: the EXE's paragraph and the C name of
# the host memory holding its bytes (None: the module never reads its code segment as data).
CODESEGS = {'seg003_0272': (0x0085, 'CODE003'), 'seg004_0849': (0x065C, 'CODE004'), 'seg021_22FD': (0x2110, None)}

# C written by hand for one instruction, (module, address) -> (C, why), and the patched
# instructions those handle: C for UW2's own instructions, so they stay in UW2Decomp, in its
# tools/asm2c.py's OVERRIDES and PATCH_OVERRIDDEN, which exhume.toml's [asm2c] overrides names.
OVERRIDES = {}
# Routines of a translated module that the port has as hand-written C instead, by module: the
# ranges of offsets [lo, hi) not translated, and where the C is (docs/PORT.md, "One
# implementation per routine"). A jump or fall-through into a range leaves the module
# (ASM_JMP), and asm_exec sends a call or jump there to its glue (x86/glue.c) or, in seg003, to
# seg003_call (gfx/grcore.c). The static targets in the ranges that the translated code reaches
# are listed in modtab.c (asm_hand_calls), and the port checks at start-up that each has C.
HANDWRITTEN = {
    'IMATH': [
        (0x0A30, 0x0B78, 'sys/imath.c: lsqrt (_A78, far _A30), sincos (_A38, far _A34), fast_sincos (_A69)'),
    ],
    'EXPAND': [
        (0x0039, 0x0063, '3d/expand.c: exp_4str'),
        (0x0063, 0x00CB, '3d/expand.c: build_pal (_63)'),
        (0x00D9, 0x011C, '3d/expand.c: exp_4run'),
        (0x018D, 0x0260, '3d/expand.c: run (_18D) and decode (_194)'),
    ],
    'GRENTRY': [
        (0x0729, 0x0764, 'gfx/grentry.c: fbuf_draw_ylrpp_x (_729)'),
        (0x07A8, 0x09C9, 'gfx/grentry.c: setup_frame_buf (_7A8), cFBtoScreen (_7F9) and its copier (_888)'),
        (0x09FC, 0x0A7F, 'gfx/grentry.c: fbuf_save_vylr (_9FC)'),
        (0x0B9B, 0x0BC0, 'gfx/grentry.c: fbuf_setcolor (_B9B)'),
    ],
    'VIDMODE': [
        (0x21D4, 0x222B, 'gfx/vidmode.c: show (_21D4), _21ED, fbshow (_2214)'),
        (0x2250, 0x249C, 'gfx/vidmode.c: vcopy (_2250) and the row routines L2296, L2373, L243F'),
        (0x283D, 0x2914, 'gfx/vidmode.c: init_graphics (_283D), the mode (_286C)'),
        (0x295F, 0x2B62, 'gfx/vidmode.c: the display start, the virtual screen, line compare, page flips, Ytab, edge masks'),
        (0x2B9B, 0x2C9A, 'gfx/vidmode.c: SetVideoMode (_2B9B), the window guards (_2BF9, _2C44)'),
        (0x2D83, 0x2E2E, 'gfx/vidmode.c: the solid span writer (_2D83), the group save (_2DF3)'),
        (0x2F18, 0x2FD1, 'gfx/vidmode.c: the copy span writers (_2F18, _2F96)'),
        (0x3094, 0x30CF, 'gfx/vidmode.c: plot (_3094, _30AC)'),
        (0x30FB, 0x3121, 'gfx/vidmode.c: read a pixel (_30FB)'),
        (0x3195, 0x31E5, 'gfx/vidmode.c: set_the_color (_3195), init_colors (_31BE)'),
        (0x31E7, 0x3206, 'gfx/vidmode.c: set_the_window (_31E7)'),
    ],
    'GRLIBF': [
        (0x3206, 0x321B, 'gfx/grcore.c: the video memory bump allocator (_3206, through _49AE)'),
        (0x326D, 0x3299, 'gfx/grlibf.c: the whole screen (_326D), copy_visible_to_hidden (_327D), copy_hidden_to_visible (_328F)'),
        (0x3324, 0x336E, 'gfx/grlibf.c: uvline (_3324)'),
        (0x336E, 0x3371, 'gfx/grcore.c: box\'s clip (_336E, clip_rect)'),
        (0x33BE, 0x3423, 'gfx/grcore.c: clip_rect (_33BE) and rectangle (_3416, _341E)'),
        (0x3423, 0x347E, 'gfx/grlibf.c: clear_window (_3423), urectangle (_342E)'),
        (0x34AE, 0x34C2, 'gfx/grlibf.c: uhline (_34AE)'),
    ],
    'GRDISP': [
        (0x5363, 0x536B, 'sys/c3dentry.c: cInit3d\'s view window (_5363)'),
    ],
    'GRLIBI': [
        (0x3B36, 0x3B3E, 'gfx/grlibi.c: string_to_screen (_3B36)'),
        (0x3BE2, 0x3C2F, 'gfx/grlibi.c: the text masks (_3BE2), setup_font (_3BFD)'),
        (0x3C61, 0x4126, 'gfx/grlibi.c: the single-colour blitter (_3C61)'),
        (0x4225, 0x43F0, 'gfx/grlibi.c: the rasteriser (4225h), string_width (_43C5)'),
    ],
}

# The C names UW2Decomp's overrides use, declared in their modules' files.
EXTERNS = {
    'PGCACHE': ['extern unsigned char Palettes[];         /* LOADGR.C */'],
    'TMAPOPS': ['uint32_t port_sprite_draw(void);        /* 3d/render.c */'],
}
