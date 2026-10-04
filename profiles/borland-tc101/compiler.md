# Borland Turbo C++ 1.01: the compiler

What Turbo C++ 1.01 (TCC.EXE, MD5 `db4c0704f7091b8d875be038bee2bf1e`) does with C source, and what its output reveals about the source that produced it. Everything here was learned by byte-matching UW2.EXE (Ultima Underworld II, 1993): 99 C files, 337,327 bytes, recompiled to the same machine code. Rules are stated generally where the compiler's behaviour is the rule; UW2 file names (ovr154, seg038, ...) mark the evidence. The command line and name rules the tools use are in `profiles/borland-tc101/profile.toml`; the assembler is in `assembler.md`, the linker in `linker.md`.

## Switches

UW2.EXE was built medium model with 186 instructions. **The switches vary by source file**, so each source names its own in an `/* opts: ... */` comment (the profile default is `-mm -1 -G -O -Z`).

- ovr154 (`PLAYER.C`): `-mm -1 -G -O -Y -d`. `-d` (merge duplicate strings) is proven by the data segment: each repeated literal is stored once. `-Y` is overlay code: taking the address of a far function in the same file is a fixup rather than `mov ..,cs`. No `-Z`, so the compiler reloads `mov bx,[player]` and `les bx,[...]` after every store through them.
- seg012 (`SEG012.C`, resident): matches with `-mm -1 -G -O -d`; `-G` is proven, `-O`, `-Z` and `-Y` make no difference in that file.
- seg038 (`SEG038.C`, resident): `-mm -1 -G -O -d`, and no `-Z` is proven (with it `player_get_exp` comes out 8 bytes short).
- **Probably every UW2 file was compiled with `-Y`**: seg029 is resident but needs it (it passes a same-file function's address, which without `-Y` is `push cs` and with it a relocated segment push), and no file has yet needed it absent. Use `-mm -1 -G -O -Y -d` for resident files too.
- seg023 and seg036 (resident): `-mm -1 -G -O -d`; seg023 proves `-G` and `-O` (without either, functions change size).
- The UW2 file holding CycleColours (file offset 0x802C4) needed `-Z` to match its register reuse.
- **seg045 was compiled without `-Y`.** Its argument-less functions have no `push bp; mov bp,sp`, which needs `-k-`, and `-Y` forces the standard frame even with `-k-`. It is the one UW2 file known not to use `-Y` (`-mm -1 -G -O -d -k-`).

So if reloads differ in a way restructuring cannot fix, try the file with and without `-Z`.

## Tools

- `python3 tools/match.py src/FILE.C` compiles the file in headless DOS (`tools/build.py` and `tools/dosrun.mjs`, in the DOS `tools/dosbackend.mjs` picks: about half a second in emu2, several seconds in js-dos) and compares every public function with the EXE, named by `/* target: ovr154 */` in the source and looked up in `targets/ovr154.tsv`. It prints MATCH, or how many bytes differ and where.
- `--dis NAME` adds an instruction diff for one function (needs iced-x86). Jump and call targets are hidden in the diff, so a length difference shows up as the instruction that caused it.
- `--no-build` re-compares the last build.
- Only functions present in the file are reported, so a work file can hold a subset.
- Bytes written by fixups (addresses of globals, call targets, segment values) are masked. So extern names and the addresses of globals don't need to be right yet; their near/far-ness does.
- `python3 tools/verify.py src/FILE.C` checks what match.py masks: fixup targets, overlay entries and the file's `_DATA`; `--update` merges the externs into `symbols.tsv` and refuses an address that already has a different name there, or a name with a different address. Use original names (UW2: the FM Towns names), and when two files disagree, the sibling build's code decides.
- Target tables come from `python3 tools/targets.py SEGNAME > targets/SEGNAME.tsv`, built from `map/` (verified segment bases and function offsets, original names where the map confirms them). Code segments are byte-aligned, so a file starts at its first function, which may be a few bytes past the segment's paragraph; the table's `org` records that, and absolute code addresses (jump tables) count from the paragraph. The last function's size runs to the first far return after it, so it can be short if the function has an earlier `retf` or a `CB` byte in its code.
- The IDA listing is the config's `[binary] listing` (UW2: `uw2_asm.asm` from UWReverseEngineering). Use `command grep -a` on it; a default grep that skips binary files skips it. Bytes are in the EXE at the segment base in the target table plus the function offset.
- A sibling build with the original names is a second witness for what the code means: `python3 tools/sibling.py NAME_` disassembles a named function (UW2: the FM Towns build, 32-bit Watcom register-call code) with calls and globals named. The target EXE is the authority on bytes.

## Names and public order

- Turbo C keeps 32 characters of an identifier (33 with the leading underscore; match.py looks names up the same way): `update_all_critters_whilst_player_snoozes` links as `_update_all_critters_whilst_playe`.
- Two externs resolving to one address means one function was given two names; verify.py reports it. The sibling build's calls (`tools/sibling.py`) show the right one.
- Turbo C lists a file's publics in descending order of the `profiles/borland-tc101/bssorder.py` key of each name, ties in reverse order of first sight (a prototype counts). The linker turns that order into overlay stub numbering (see `linker.md`), so the stub order constrains names.
- **Ties in public order** (equal hash keys, listed in reverse order of first sight) are fixed with an early prototype rather than a rename (UW2: OVR108.C).
- **A function the original had `static` has no overlay stub entry**; verify.py reports such publics. Making it `static` keeps the bytes but match.py, which reports publics only, then counts it into the previous function's size.
- **Byte-identical functions with two sibling names at one address**: the sibling's linker (UW2: FM Towns) folded them, so which DOS copy carries which name can't be proven; say so in a comment.
- Static data doesn't appear in a sibling build's symbol table, so an unnamed table inside a file's data range was probably `static`.

## Data layout

- A file's `_DATA` holds its initialised data in definition order, including the initialisers of local arrays (emitted where the function is), and then the string-literal pool in order of first use. `verify.py` compares it with the EXE, so data the code reads by a fixed DS address may belong to the file itself: look at the bytes around it.
- **`_DATA` starts word-aligned.** A file whose data appears to start at an odd address is missing the byte before it; the even byte belongs to that file (UW2: SEG037, OVR097, OVR128, OVR152; OVR143 was missing 11 bytes).
- **`_DATA` items are not word-aligned** between one another: an int can follow a 24-byte char table at an odd address.
- **`if (0) printf("...")`** puts the string in the literal pool but emits no code: that is how unreferenced strings got into a file's data.
- **Duplicate strings**: the EXE shares one copy of repeated literals (UW2: "trap", "poison trap"), which means `-d` (merge duplicate strings). Code bytes don't depend on it; the data segment does, which is how it was proven for ovr154 and ovr139.
- **`-d` merges string tails too**: `" "` can be the last byte of `"You see "`, and `"\n"` the tail of `".\n"`.
- **A block-local initialised array** (`char num[3] = "00";`) puts its initialiser in `_DATA` where the function is.
- **Pointer tables in data** (an initialised array of far function pointers) carry fixups inside `_DATA`; `verify.py` checks each pointer against the EXE and leaves those bytes out of the comparison.
- **An empty string can be someone else's padding byte**: UW2's seg043 passes `push 98Dh` for `""`, an address inside another file's data, so the literal was merged by the linker and is declared there as an extern array.
- **`_BSS` is laid out by name, not declaration order** (file-scope data only; function-level statics are placed where they are defined): Turbo C emits them in ascending order of `(c[0] + 256*c[1] + 8*c[len-2] + 64*len) & 1023` over the name's characters (no underscore), ties keeping definition order; ints are word-aligned. Found from probe compiles on UW2's seg015; it predicted seg010's and ovr137's layouts too. `profiles/borland-tc101/bssorder.py` computes it. A static's name never reaches the linker, so when a static has no original name, choose one that lands where the EXE has it, and say in a comment that the name was chosen for layout. Public names are fixed by other files. Pick the name with bssorder.py; probe compiles are no longer needed.
- **The `_BSS` order key is computed on the name cut to 32 characters.**
- **In `_BSS`, any item wider than a byte goes at an even offset** even without `-a`, so a char followed by an int leaves a pad byte.
- **Static uninitialised data** goes in `_BSS`, which `verify.py` checks for a consistent base (no bytes to compare).
- **A file's `_BSS` is one unit**: if globals used only by another file sit between this file's globals, the block belongs to one of the two files; leave it extern until the other is matched.
- **Globals in another file's gap**: unnamed globals that the sibling keeps as statics may be declared `extern` if their home file isn't matched yet; the bytes are the same, so note them as provisional.
- **Turbo C++ 1.01 puts each `far` variable in its own paragraph-aligned segment**, `FILE<n>_FAR`, class FAR_DATA. A far segment holding several variables was assembly. Definition order sets segment order. An uninitialised far definition placed after an `extern` declaration of the same name comes out as near DGROUP data, so replace the extern rather than adding a definition after it. verify.py checks a file's own far segments (bytes, length against the segment table, fixups); how the linker orders them is in `linker.md`.
- **An array whose apparent base is two bytes early may be indexed from 1.** FM Towns indexes UW2's `color_to_map` and `color_to_obj` as `[x - 1]`, and Turbo C folds the `- 1` into the displacement.
- **An index minus one folds into the base**: `table[i - 1]` with 7-byte records gives `imul 7` and the base minus 7, so a DS address just before a table can be that table.
- **A 2D table index with offsets** folds them into the displacement: `dirs[a + 1][b + 1]` on `char[3][3]` gives `[bx+base+4]`, so a DS address inside a table can be the table indexed with +1.
- **Arrays indexed `[side][slot]`**: when two sides of a trade or similar are handled by one loop, the original used 2-D arrays; separate arrays can't reproduce the indexing.
- Struct field offsets must be exact; use `char padN[...]` to place fields.

## What the compiler tells you about the source

### Frames, locals and register variables

- **Locals** are laid out downward from `bp-2` in declaration order: the first declared local is nearest `bp`. Initialisers run in declaration order too, so `int found = -1;` declared before an initialised array is stored before the array copy.
- **Stack cleanup**: `-G` gives `add sp,N` (or `inc sp; inc sp` for two bytes) after calls, and `push bp; mov bp,sp; sub sp,N` rather than `enter`. `leave` is used at the end.
- **Block-scoped locals share stack slots**: variables declared inside different `{}` blocks can overlap in the frame, so a small `sub sp,N` with overlapping offsets means block-local declarations.
- **Odd-sized local arrays go after the scalars** (uninitialised ones; an initialised `unsigned char inst[3] = {...}` kept its place): a `char x[11]` is placed after scalar locals declared after it, whatever the order; even-sized arrays keep declaration order. An 11-byte gap can be `char x[10]` plus a padding byte.
- **Even alignment after a `char` local**: an int or array declared after a `char` starts at an even offset, leaving a one-byte gap in the frame.
- **Register variables**: with `-Z` and default `-r`, the compiler puts up to two `int`-sized locals or parameters in SI and DI. Which ones it picks depends on the declaration and use; try reordering declarations if SI and DI are swapped.
- **Which candidate gets SI is not a simple rule**: with a parameter among the candidates, the local declared first got SI (UW2: ovr154); with two plain int locals, the one declared second got SI (ovr163). Try both declaration orders; neither changes the stack layout.
- **Two explicit `register` locals**: sometimes the first declared gets SI (UW2: ovr126), sometimes the second (ovr124, a pointer and an int). Try both orders.
- **An explicit `register`** overrides the default choice: when the original has a loop counter in SI and a parameter in DI, the counter was declared `register int` after the plain locals.
- **`register` on a parameter** can be needed to put it in DI.
- **A register copy of a parameter**: when the parameter is in SI but one use reads it from the stack, the source had an explicit copy such as `register int f = which;`.
- **One register variable, many jobs**: when SI or DI holds unrelated values in turn (a strlen result, then loop counters), the source reused one variable; a third int would usually have gone on the stack.
- **A register variable used in two separate blocks** may need to be a block-scoped `register int` in each block to land in the same register as the original.
- **`= i++` folded into a char store** lets a `register int` take SI; as a separate `i++` it stayed on the stack.
- **CX as a third register variable**: in a function that makes no calls, a third int local can live in CX (`xor cx,cx ... inc cx`), with or without `register`.
- **CX for a block-scoped `register int`** in a block without calls, even if the function calls elsewhere; at function level it stays on the stack.
- **Char register variables**: in a function with no calls, char locals can live in CL and DL; the first char assigned takes CL.
- **An int also stored as a char stays on the stack** (SI and DI have no byte halves), even when DI is free.
- **DI as scratch**: a free DI may be used for a temporary pointer, pushed and popped, so `push di` in the prologue alone doesn't prove a second register variable.

### Reloads and register reuse

- **Control flow shape** changes register reuse: a nested `if` against a `continue` gives a different reload of ES or BX. When the instructions agree but the loads differ, try restructuring.
- **Reloads without `-Z`**: after any store through `player`, the next statement reloads `mov bx,[player]`. Within one statement, and from an `if` condition into its body, BX is reused.
- **A direct store to a global is reused without `-Z`**: `g = t >> 5; if ((g >> 1) == h)` keeps AL after `mov [g],al`. The forced reloads without `-Z` are after stores through pointers.
- **Stores through a far pointer parameter** don't force a reload without `-Z`, except that an if/else whose arms both store through it makes the next statement reload `les bx`. Stores through near globals always force the reload.
- **An early `return` forces a reload**: `if (flag) return;` followed by more code reloads `les bx,[arg]`; `if (!flag) { ... }` reuses ES:BX from the condition.
- **An empty else-if arm forces a reload** of the pointer before the next test; folding it into the next condition reuses BX.
- **`if (x != K) continue;` at the top of a loop body** forces a reload at the fall-through label; the nested `if (x == K) {...}` form does not.

### Types and widening

- **Parameter types**: an `int` parameter built from a byte shows `mov ah,0`; a `char` parameter doesn't. A `char` return is `mov al,N`; an `int` return is `mov ax,N`.
- **Callers reveal return types**: `!f()` compiling to `mov ah,0; neg ax; sbb ax,ax; inc ax` means `f` returns `unsigned char`; a `char` return gives `cbw`.
- **Testing an `unsigned char` return**: only `!f()` widens it (`mov ah,0; or ax,ax`); `f()`, `f() != 0`, `(int)f()` and `f() ? 1 : 0` all give `or al,al`.
- **`return f(0);`** of a char returns the callee's AL with no move.
- **`!x` against `x == 0`**: on a byte expression promoted to int, `!(...)` gives `or ax,ax` and `(...) == 0` gives `or al,al`.
- **`!c` against `c == 0` on a `char` parameter**: `!c` gives `mov al; cbw; or ax,ax`; `c == 0` gives `cmp byte [bp+N],0`.
- **`unsigned char` parameter tests**: `if (c)` gives `cmp byte [bp+N],0`; `if (!c) return;` (or `!c` in an `&&` chain) gives `mov al; mov ah,0; or ax,ax`.
- **Mask tests on a byte global**: `(g & 0x16) == 0` gives `test byte [g],16h`; `!(g & 0x16)` loads and widens first.
- **`if (((c >> n) & 1) == 0)`** gives `test al,1`; `!((c >> n) & 1)` gives `test ax,1`.
- **`x += y` against `x = x + y` on bytes**: with a byte target and a `char` right side, `+=` gives `add [bx+N],al`; the load, add, store form is `x = x + y`. With an `int` right side both give load, add, store, which reveals the right side's type.
- **`i++` against `i = i + 1` on a byte local**: `i++` gives `inc byte [bp-N]`; `i = i + 1` gives load, `inc al`, store.
- **Decrementing a char by one**: on `unsigned char`, `x = x - 1` and `x -= 1` give `add al,0FFh`; on plain `char`, `dec al`.
- **Decrement-and-test of a byte global**: `if (--g == 0)` gives `mov al,[g]; add al,0FFh; mov [g],al; or al,al`, not `dec byte`.
- **Increment of an indexed byte**: `p->a[i]++` gives `inc byte [bx+N]`. The sequence `mov al,[bx+N]; inc al; push ax; <recompute bx>; pop ax; mov [bx+N],al` is `p->a[i] = p->a[i] + 1;`.
- **A byte destination keeps the constant's spelling**: `x = w + 0xE1` gives `add al,0E1h`; `x = w - 0x1F` gives `sub al,1Fh`.
- **`cbw` on an unsigned char global** in one place means the source cast it there, as in `(signed char)z - tz`.
- **An unsigned mask against a signed int** compares unsigned (`jbe`); a signed `jle` needs `(int)` on the masked value.
- **Always-true tests on unsigned values are still compiled**: `if (b < 0) continue;` on an `unsigned char` gives a `jae` over a `jmp`.
- **`&=` with a large constant**: on an int global, `x &= 0xFF7F` gives load, AND, store (the constant is unsigned); `x &= ~0x80` gives `and word [x],0FF7Fh`.
- **`return !x;`** on an unsigned register variable gives `mov ax,di; neg ax; sbb ax,ax; inc ax`; `return x == 0;` gives a branch and `mov ax,1` / `xor ax,ax`.
- **`0xFFL - (unsigned)c`** gives `xor dx,dx; mov ax,0FFh; sub ax,bx; sbb dx,0`; without the cast, `cwd` and a full long subtract.
- **`int += long - long`** needs `(int)(...)` to give `sub ax,[lo]; add [mem],ax`.
- **`+=`/`-=` on a word global**: with an `int` right side, both `int` and `unsigned` targets give `add [g],ax` (UW2: seg015); with an unsigned bitfield on the right, an `int` target gives load, subtract, store (ovr138).

### Expressions, operand order and constants

- **Constants keep their spelling**: `memset(buf, -1, n)` pushes `0FFFFh`; the original pushing `0FFh` means the source said `0xFF`.
- **Subtracting a constant** often compiles as adding its negative (`add ax,0FDA8h` for `- 600`).
- **`~` constants**: `(a & ~0x3F) + (b & ~0x3F)` tested for zero gives `add ax,dx; jne` with no `or ax,ax`; spelling the mask `0xFFC0` adds the `or`, although both emit `and ax,0FFC0h`.
- **Constant on the right of `|`**: `(i + 0x11) | 0x400` gives `or ax,400h`; `0x400 | (i + 0x11)` gives `mov dx,400h; or dx,ax`.
- **Constants move to the end of an addition chain**: `a + b + 0x10 + c` adds `c` before `0x10`; `(int)(a + b + 0x10) + c` keeps the source order.
- **Addition operand order**: `a + (b << n)` pushes the shifted term and adds `a` after; `(b << n) + a` adds the other way round.
- **Pointer plus index keeps the source's operand order**: `base + (x + (y << 6))` and `base + ((y << 6) + x)` compile differently.
- **Operand order in a multiply**: `a * (f() - 0x40)` gives `push; mov al,a; cbw; pop dx; imul dx`; the other order gives `mov dx,ax; pop ax; imul dx`.
- **`x *= K`** gives `mov dx,K; mov ax,[x]; imul dx`; `x = x * K` loads x first.
- **`lx *= K` against `lx = lx * K` on a long** puts the constant in CX:BX or DX:AX for `N_LXMUL@`.
- **Long arithmetic in an int expression**: `start + (int)(((long)RNG() * count) / 0x8000L)` gives `N_LXMUL@` then `LDIV@`, with the final add done in 16 bits.
- **Split chained divisions**: `c = x / 26; c += 3 - y / 15 * 3;` keeps the first quotient in SI; one compound expression pushes and pops it.
- **Chained assignment stores right to left**: `*a = *b = K` stores to `*b` first.
- **Assignment evaluates the right side first**: `f(i)->w = f(i)->w & M | V;` pushes the value, calls `f` again for the target, then pops and stores. A push/pop around a repeated call is this, not a bitfield.
- **A doubled load** such as `mov di,[bp-8]` twice in a row comes from a redundant assignment in the source, for example `left = count; for (left = count; ...)`.
- **Self-assignments survive `-O`**: `x = x;` on a char local is kept as a load and a store, so a load and store of the same slot inside an apparently empty test is that.
- **Clearing bits in a global**: `g = g & ~bit;` gives `not ax` then `and dx,ax` through registers; `g |= expr` gives `or [g],ax` directly.
- **Assignment inside a condition**: `if ((x -= 16) > 0xD0)` gives `sub [x],10h; mov ax,[x]; cmp ax,...`; as two statements it becomes `cmp word [x],...`.
- **Assignment inside a condition** compares the register (`mov di,ax; cmp ax,1`, `or ax,dx` for a far pointer); as two statements it compares the variable.
- **Store and test in one expression**: `ok = (fd = open(...)) != -1` gives `mov [fd],ax; cmp ax,-1`; two statements compare the memory copy.
- **Comparing a call's result with a byte field**: `if ((v = f()) > p->b)` keeps the result in AX (`mov dl,[bx+N]; mov dh,0; cmp ax,dx`); assigning first and then comparing gives `mov al,[bx+N]; cmp ax,[bp-N]`.
- **`x++; if (x >= n)`** compares the register directly; `if (++x >= n)` copies to AX first.
- **`switch (a = b->m = c)`** reproduces chained stores followed by a switch on AX.

### Control flow, tail merging and jumps

- **Statement order is kept** even for independent assignments, so two branches that set the same fields in different orders were written that way.
- **A `jmp short $+2`** is the compiler merging identical tails, such as the same call in an `else` at two nesting levels. The source had the duplicate.
- **Shared call tails**: `if (c) f(0xF); else f(n + 0x15);` compiles to one call with two argument paths joined by a `jmp`. The ternary `f(c ? 0xF : n + 0x15)` does not.
- **Shared call tails scale**: an if/else-if chain whose branches all end in the same call compiles to one physical call reached by jumps.
- **Shared call tails cross `switch` cases** when two cases end in the same call with the same argument shapes.
- **Calls merge only when what follows matches**: in `if (a) { if (c) f(2); else f(3); X; } else if (b) { ...; X; }` the `f` calls share one call only if the trailing statement X is written in both branches.
- **`if (c) f(A); else f(B);`** gives two push paths into one call; the ternary `f(c ? A : B)` gives `mov ax,imm; push ax`.
- **Identical store tails are shared** like call tails: one branch jumps into the other's matching final instructions.
- **A repeated store or tail becomes a jump**: in `if (a) x = 0; else if (b) x = e; else x = 0;` the first `x = 0` turns into a jump to the shared final store; a whole else block repeating the function's tail becomes one `jmp` to it.
- **`jne L; mov; jmp X; L: jmp X`** (a conditional jump onto an unshortened `jmp`) comes from the same last statement written in both arms of an if/else, which `-O` merges into one copy; goto, continue and `else ;` don't reproduce it.
- **A jump into the middle of another arm's stores** means the statements after that point came after the if/else in the source, so both paths run them.
- **The first assignment and the loop step share a call tail**: `obj = f(&a->x); while (obj && c) obj = f(&obj->y);` jumps from the first call into the body's pushes. That is the compiler, not a `goto`.
- **A first assignment duplicated before a loop**: `item = f(); while (!g(item)) item = f();` tail-merges the two copies, leaving `jge L; mov; jmp X; L: jmp X` after a preceding `if`.
- **Early return against a nested block**: `if (x > K) return;` on an unsigned long gives `cmp hi; jb; jbe +3; jmp; cmp lo; ...`; wrapping the rest in `if (x <= K) {}` orders the branches differently.
- **Guard then loop**: `if (p == 0) return; while (...)` jumps straight into the loop test; `if (p != 0) { while ... }` adds a `jmp` and is 2 bytes longer.
- **How a guard is written changes the byte test**: in `if (ptr != 0 && g)` a byte global is `cmp byte [g],0`; in `if (ptr == 0 || !g) return;` it is `mov al,[g]; mov ah,0; or ax,ax`.
- **`&&` with the success body first**: `if (c >= 0 && c < 6) return c; return -1;` shares one `return -1` tail; the De Morgan form `if (c < 0 || c >= 6) return -1; return c;` duplicates the epilogue and grows.
- **Shared `return 0`**: several exits jumping to one `mov al,0` before the epilogue need the function's last statement to be a reachable `return 0;`; then early `return 0`s merge into it too (UW2: ovr125). Without a reachable final one, each early return gets its own `mov al,0; jmp`.
- **`return 0` against `break` in a switch case**: a `jcc` to a lone `jmp` in front of the final `return 0` means the case said `return 0;`; with `break` the jumps are threaded straight to the shared return and the function grows.
- **A trampoline to a shared return**: when an `||` condition's body is a shared `return 0`, each term jumps short to a one-instruction `jmp`; a `goto` or empty body threads the jumps straight to the target instead.
- **`jcc +2; jmp short` to a shared `return 0`** is a separate early-return statement whose body was replaced by a jump, not part of an `&&` chain.
- **A dead jump before an `else`** that lands on a jump to a shared return came from an explicit `return x;` at the end of the then-block.
- **Two `jmp`s in a row** come from a `return` at the end of an `if` block followed by `else if`: the return's jump plus a dead jump past the else chain, which a branch lands on and is not threaded through.
- **Unreachable code is dropped**: if both arms of an if/else return, a following `return` disappears (with a warning) and the function comes out short; a dead `jmp` to the epilogue after an else arm's return means the source had a reachable statement there.
- **A test both arms jump to** after an if/else sat after the else in the source.
- **A both-branches-jump-to-the-same-place test** (an empty `if`) keeps its compare, and its direction can't be recovered from the bytes; say so in a comment.
- **An empty-bodied `if`** keeps its test (`mov ah,0; or ax,ax`) with no jump after it.
- **An empty if-body keeps its byte test with no jump**: `if ((x = f()) == 0) ;` gives `mov [x],al; or al,al` and falls through, which reads like a debug message compiled out (a `complain()` macro that expands to nothing).
- **`x ? f() : (void)0`** leaves a `jmp short` to the next instruction after the call; an `if` doesn't.
- **A one-case `switch`** compiles to `cmp ax,K; je case; jmp short end` and reloads ES:BX at the case label; `if (x == K)` gives a single `jne`.
- **A grouped `switch` against an `||` chain**: `case 2: case 3:` compares AX twice without reloading; `if (b->q == 2 || b->q == 3)` reloads the bitfield.
- **`switch` fallthrough for a shared tail**: `case 'p': v = 0x190; case 'P': v += 0xC8; f(v); break;` reproduces two cases sharing one call; an if/else chain duplicating the call spills a temporary.
- **Switch jump tables can follow the last function**; the target table's size stops at its far return, and `match.py` accepts compiled code that runs on as long as those bytes match too.
- **A `goto`** shows as a jump straight past later code, with reloads at its label; when nothing else explains a jump over a following test, try `goto`.
- **The for-increment's comma order is kept**: `for (...; i++, p -= step)` gives `inc cx; sub [bp-4],di`; the same update written at the end of the body reverses them.
- **Same init and step**: `for (p->n--; p->n >= 0; p->n--)` jumps straight to the step; `while (--p->n >= 0)` gives `mov al; dec al; mov; or al,al`.
- **Fold a trailing break into the loop test**: `while (A && B) i++;` gives the compact test-first layout; `while (A) { if (!B) break; i++; }` lays out differently.
- **`while (f()) if (a <= b) break;`** puts the compare before the call, the call's `jne` jumping back to it.
- **Testing a far pointer in a loop condition**: the store, reload and `or ax,[bp-6]` sequence comes from the comma form, `for (...; trig = f(...), trig; ...)`. `(trig = f()) != 0` gives the shorter `or ax,dx`.

### Bitfields

- **Bitfields**: `(w >> 6) & 7` in a condition compiles to `test ax,7`. The original's `and ax,7; or ax,ax` is a bitfield read (`unsigned x:3;`).
- **Bitfields versus macros**: a real bitfield reads shift-then-mask (`shr ax,N; and ax,M`), or as a byte load with no `mov ah,0` when it sits at bit 0. Mask-then-shift, including a telltale `shr ax,0`, is a macro written `((w & mask) >> shift)`. UW2's object struct uses both.
- **A bitfield in the top bits of a word** is read with a byte load: `unsigned lo:13; unsigned cls:3;` gives `mov al,[bx+hi]; shr ax,5; and ax,7`.
- **Bitfields straddle bytes**: Borland places each field in the 16-bit window starting at the byte holding the next free bit, so a field can be read as `mov ax,[bx+61h]; shr ax,6` from an odd address. Clearing a field whose mask has 0xFF in one byte becomes a byte `and`.
- **Bitfield runs take only the bytes they need**: `unsigned b:1` followed by a char array puts the array at +1, not +2.
- **Bitfield writes**: setting a 1-bit field is a byte `and` or `or`; a field spanning two bytes is written with a word `and`/`or`.
- **`x > 0` on an unsigned bitfield** gives `or ax,ax; jbe`, not `je`, so the source said `> 0`.
- **`unsigned char` bitfields** are accepted and make the element one byte; a zero test on one gives `and ax,1; or al,al`, where an `unsigned` field gives `or ax,ax`.
- **Copying between bitfields**: an `unsigned char x:4` from a char gives `and ax,0Fh` with no `mov ah,0`; between two `unsigned` bitfields the `and ax,0Fh` appears twice.
- **A doubled mask** such as `and dx,7; and dx,7` survives only if a cast separates the two, as in a macro `((unsigned)(v) & 7) << 13` called with `x & 7`; without the cast the compiler folds them.
- **`x = p->bitfield--`** folds the decrement into the masked word, writes through DI, and leaves an unbalanced `push bx` that `leave` hides; that stray push is the sign of this source.
- **A mask macro with a ternary argument**: `b = b & 0x7F | ((v) & 1) << 7` with `cond ? 1 : 0` gives `mov al,1 / mov al,0`, then `and al,1; shl al,7; pop dx; or dl,al`; a bare `cond` gives `mov ax,1 / xor ax,ax`; a real bitfield store is `and byte [..],7Fh; shl ax,7; or [..],al`.
- **Two bitfield-macro stores in an if/else** share one store tail, each arm folding its own mask (`and ax,7` against `add ax,8; and ax,0Fh`, then one `shl ax,9` and store).
- **A stray `xor ax,ax` before a bitfield store** comes from a chained assignment such as `o->next = o->quality = 0;`.
- **A three-deep chained bitfield assignment** (`s.b0 = s.b1 = s.b6 = 0;`) stores an uninitialised DX into the middle field; two-deep chains don't. Reproduce it as written.

### Pointers, memory model and far data

- **Near data**: medium model, so globals are near (`mov bx,[828Ah]`) and DS equals SS (no `ss:` override on stack arrays). Anything reached through `les bx,[...]` is an explicit `far` pointer.
- **Argument forms**: a string literal passed as `char far *` is `push ds; push offset`; a local array is `push ss; lea ax; push ax`.
- **A null far pointer argument** `0L` pushes two `6A 00`.
- **A far function address as an argument** is two pushes with separate segment and offset fixups; `verify.py` combines them.
- **Far pointers compare by offset only** for `<`/`>=` (`mov ax,[bp+N]; cmp ax,[g]`), while `== 0` tests both halves (`or ax,dx`).
- **Far pointer subtraction is long division**: `tile - base` on far struct pointers subtracts offsets into DX:AX and calls `F_LDIV@` by the element size; `(int)(tile - base) >> 6` is needed for a plain `sar`.
- **Indexing a far byte array**: `(map + y * w)[x + 2]` gives `les bx; add bx,ax; mov al,es:[bx+di+2]`; `map[y * w + x + 2]` adds in AX and loads ES separately.
- **The index's signedness changes far-pointer indexing**: `p[i - 1]` with an int index gives `mov bx,cx; mov es,[seg]; add bx,[off]`; with an unsigned index, `les bx; add bx,cx`.
- **`p += n` on a far pointer** gives `add [bp-6],ax`; `p = p + n` reloads and stores both halves.
- **Pointer increment**: `p = p + 1;` on a near int pointer gives load, `inc ax; inc ax`, store; `p++` gives `add word [bp-2],2`.
- **`tmp = *p++` on a far pointer in a used expression** increments the offset before the load; `(tmp = *p, p++, tmp)` loads first.
- **`a = b = expr` with far pointers** stores both from DX:AX; `a = expr; b = a;` reloads.
- **Build far pointers with `MK_FP` from `<dos.h>`**: Turbo C defines it as `(void _seg *)(seg) + (void near *)(ofs)`, which evaluates the offset before the segment. No hand-written `((unsigned long)seg << 16) | off` form does that in either operand order.
- **`MK_FP` with an assignment inside**: `buf = MK_FP(ws = f(), 0)` stores the segment from AX; two statements store it from the register variable.
- **`FP_OFF(MK_FP(seg, off))`** evaluates the segment and throws it away, leaving a redundant load.
- **`FP_SEG` of a computed near pointer re-evaluates it**: `movedata(..., FP_SEG(d), FP_OFF(d), ...)` with `d = s + strlen(s)` calls `strlen` twice and pushes `ds`; a stack array gives `push ss`.
- **A DGROUP address written as a number compiles to the same bytes as the name**: `((signed char near *)0x1bf7)[c]` and `(_ctype + 1)[c]` both give `[bx+1BF7h]`, the second with a fixup. Match cannot tell them apart; write the name (UW2's SEG039 had the number, for the C library's `_ctype` table). See assembler.md, "Addresses written as numbers".
- **Far function-pointer table**: `if (tab[s][i]) tab[s][i]();` recomputes the index for the test (`mov ax,[bx]; or ax,[bx+2]`) and again for `call far [bx+tab]`.

### Calls, prototypes and the library

- **Calls to functions in the same file**: a call to a function defined EARLIER in the file compiles to `push cs; call near` (`0E E8`, 4 bytes). A call to one defined LATER compiles to a 5-byte far call. In an overlay the linker rewrites it to `nop; push cs; call near` (`90 0E E8`), which match.py treats as equal; in resident code it stays a far call to the segment's own paragraph. So if the original shows `0E E8` without the `90`, the callee comes earlier in the file: in a work file, put a stub definition of it above your function. The near call's displacement is not a fixup and is not masked, so with a stub the instructions match but one or two displacement bytes still differ; only the merged file, with the real callee at its true offset, matches every byte.
- **Calls to other files** are far calls (`9A`, or `CD 3F` overlay thunks, both masked).
- **Prototypes were not shared everywhere**: UW2's seg038 calls `advance` with an `int` argument (`push si`, no conversion) although ovr154 defines `advance(char)`, so each file declared what it called for itself. Declare a callee the way the calling file's bytes show.
- **A call through an old-style declaration** (no parameter list) pushes an `int` argument as-is; with a `char` prototype in scope the same call gives `mov al,[x]; push ax`. A caller that pushes a whole int to a char parameter had no prototype.
- **`sizeof` widened to long**: `farmalloc(sizeof(struct Bag))` pushes `6A 00, 6A 0C`. In UW2 the runtime's `farmalloc`/`farfree` are in seg005.
- **Struct assignment** of a fixed size calls `F_SCOPY@` with the size in CX.
- Library helpers (long multiply, divide and shifts) are `N_LXMUL@`, `H_LDIV@` and so on, called as far calls; long arithmetic in C produces them automatically.
- **Library inlines**: `abs()` from stdlib.h is `cwd; xor ax,dx; sub ax,dx`; `isdigit()` tests `_ctype` (UW2: at DS:1BF6, `test byte [bx+1BF7h],2`).
- **`outportb()` compiles inline** to `mov dx,port; mov al,v; out dx,al`.
- **`atoi`** from stdlib.h links as `_atol`.
- **Recognise library code**: UW2's ovr127 is Haruhiko Okumura's 1989 LZSS.C almost line for line, with its globals moved into one far work area. Well-known public code is worth looking for before reconstructing from scratch.
- **Unreferenced helper functions** with no sibling counterpart can sit inside a neighbour's range in the target table; define them `static` where they fall.

## Readability changes: what keeps the bytes

What UW2's readability pass (skills/readability-pass) showed about Turbo C++ 1.01; the gate checked each.

### Declarations and headers

- **An unused declaration costs nothing**: an `extern` or prototype the file never uses leaves no EXTDEF, so a header may declare far more than a file needs. The object does gain comment records naming each included file and its time stamp, which no tool reads.
- **Adding an `#include` changes nothing but those comment records**, provided the header declares no name the file defines in another way.
- **A header is the first sight.** Publics and uninitialised globals with equal order keys (`bssorder.py`) are ordered by first sight, so declaring one of a tie group in a header and not the others reorders them (UW2: `missile_trx`/`missile_try` in seg027, `PlayerPitch`/`playerMod` in seg035, `check_arc`/`close_arc` in ovr093). Such names stay together, in headers or out. A `#define` of the name is a sighting too, its body is not: UW1's CUTS.C renamed header declarations out of the way (`#define cutsop_wait UW2_cutsop_wait` before the includes), and once the renames went, its own declarations had to name `cutsop_wait` before `cutsop_skip` to keep the stub order (`declinv.py`'s `sights()` counts it this way).
- **A shared prototype can change a caller**: with a `char` parameter in scope a call converts its argument (`mov al,[x]; push ax`), without it an `int` is pushed as it is; an old-style declaration converts nothing. So a name whose files disagree stays declared in each file its own way (UW2: 227 names after the header step).
- **An uninitialised `far` definition after an `extern` declaration of the same name comes out as near DGROUP data**, so a far variable is never declared in a header its defining file includes.
- **`struct X;` works** as an incomplete declaration, and a later definition completes the same tag. A tag declared and never defined draws "Undefined structure" at the end of the compile, a warning only.
- **A tag first named in a prototype's parameter list has prototype scope**: `void f(struct Arc far *a);` with no `struct Arc` in scope declares a tag that dies with the prototype, and the definition of `f` against the real `struct Arc` draws "Type mismatch in redeclaration of 'f'" (UW1: `Map_Load` in map.h). A header that names a tag in a parameter list before declaring it at file scope needs `struct Arc;` above (`headergen.py` adds it, in both modes).

### Structs and fields

- **Struct sizes are evidence**: a definition by value lays out `_BSS` with the struct's size (UW2's seg031 shows `struct MotionCalc` is 23 bytes although six files declared 24), and struct copies and pointer strides show sizes too.
- **`unsigned` against `int` fields**: `p->f |= x` is `or [bx+N],ax` on an `unsigned` field and load, `or`, store on an `int` one. A comparison or `>>` on the field shows its signedness; elsewhere a cast at the one use keeps a shared type.
- **A byte view of a word field**: `(unsigned char)s[i].value` compiles like reading an `unsigned char` field at that offset.
- **Fields at the same offset compile alike whatever their names**: a scalar field and the matching element of an array field, a bitfield and the same bits in another partition of its word, `(&p->a)[i]` and an array indexed by `i`. Only the bits a field covers, its unit (a `char` or an `int` bitfield) and its type matter.
- **No anonymous unions in C**: a word read both whole and as bitfields is a named union, and every access names the view.
- **A field compiles like the cast it replaces** when the address is the same and reached the same way (`*(unsigned far *)((char far *)o + 0x16)` is `o->home`; `&a[i]` of 0x30-byte records is `(char *)a + i * 0x30`). Not when the scaling differs: `((unsigned *)p)[i * 3 + 2]` scales after the add, where `p[i].f` folds the field's offset into the displacement. Not when the base pointer differs: code that keeps a pointer at a later field and reads before it (`[bx-4]`) keeps that pointer.
- **Pointer types of the same address are free**: retyping `unsigned far *` link variables as a union pointer changed no byte, and cleared the "Suspicious pointer conversion" warnings that sharing the prototypes had drawn (UW2: 234).

### Constants and macros

- **A `#define` whose value is the literal compiles the same**, so keep the literal's spelling: hex values from 0x8000 are `unsigned` and decimal ones `long`; a name never replaces a decimal `32768` with `0x8000`.
- **Enum constants compile like the `int` literal** in `case` labels, comparisons with `char`, bitfield values, array indices and arguments. Only values below 0x8000 suit an enum.
- **Constant expressions fold** before code generation (`ERR_LOWMEM | 2` pushes the single value). Rewriting an inverse mask as `~NAME`, or splitting a literal that is combined with variables, can change the code (see `~` constants under "Expressions"), so those stay literals.
- **Setter macros fold constants**: `x = x & 0xF00F | ((0) & 0xFF) << 4` compiles like `x &= 0xF00F`. Masking the argument costs an `and` only when the argument is a variable not already masked; `((v) & 7)` of `x & 7` merges into one `and` unless the macro casts first (`(unsigned)(v) & 7`), which keeps both. A shift by 0 always costs `shr ax,0`. A file that compiles a shared macro differently needs a second spelling of it.
- **With `-d`**, the literal pool shares duplicates and tails, so replacing a literal with a `#define` of the same literal changes nothing, while a named `char` array is data in definition order, outside the pool.

### File names

- **A C file's code segment is `FILE_TEXT` and its far variables' segments `FILE<n>_FAR`**, after the file name, so renaming a file renames its segments. The EXE keeps no segment names; see linker.md for when a name matters.

## Porting: what the compiler's output decides

A port compiles the same C with a modern compiler, so what Turbo C++ 1.01 made of each construct is the reference (docs/port.md). Read each answer from our own build of the matched source (`tools/match.py --dis NAME` shows the original's instructions with our names), never guess it, and record it in a portable.h macro whose Turbo C expansion is the original tokens:

- **`int` is 16 bits and does not promote; `long` is 32.** Headings, counters and products wrap at 16 bits. A hex literal from 0x8000 to 0xFFFF is `unsigned`, a decimal one above 32767 is `long`. An unsigned bitfield stays unsigned in a comparison. `tools/intaudit.py` gives every expression the type Turbo C gives it.
- **Structs are byte-aligned (no `-a`), and bitfields go in the 16-bit window that starts at the byte holding the next free bit**, so a field can straddle a word boundary. clang packs a struct the same way with `#pragma pack(1)` and 16-bit bitfield types; `tools/layoutcheck.py` proved all 38 of UW2's pointer-free records identical, the ten with bitfields among them.
- **A function with no return statement leaves AX** with what its last expression or call left there, and callers that use the result read it: `AX_RESULT(v)` or `AX_LAST(call)`, the value read from the code (UW2: five functions).
- **A local read before it is set holds stack junk**: what the calls before it and the timer interrupts left in its slot. `--dis` shows the slot (`[bp-N]`); `STACK_JUNK(v)` gives the replay build and the port the value with which DOS takes the path it takes when the slot holds zero. The timer interrupts push onto the game's stack, so two DOS runs of one recording can differ there: that is how such a local is found. A port built with `-ftrivial-auto-var-init=pattern` that still replays every session identically reaches no other.
- **A local array the code overruns on purpose** writes into the slots Turbo C laid out after it (`FRAME_LEN`, `FRAME_TAIL`), and **two locals read as one record** rely on their order in the frame (`READ_PAIR`, `WRITE_PAIR`); DGROUP arrays overrun into the next variable in link order, which a host compiler need not keep (x86-64 aligns arrays of 16 bytes or more on 16-byte boundaries; arm64 happened not to).
- **Names keep 32 characters with the underscore** (31 without), so two C names that differ after that are one symbol in DOS.
- **`char` is signed.** Pass `-fsigned-char` on every host.
- **The C library**: `rand` is a 32-bit LCG, multiplier 015A4E35h, increment 1, seed 1, returning the high word's low 15 bits (`RAND_MAX` 7FFFh), and `srand` stores only the seed's low word; its seed is the DGROUP word at `srand`'s byte 7 (`mov [Seed],ax` at byte 6). `clock()` counts 18.2 Hz BIOS ticks. Handles open in text mode unless `O_BINARY` (`_fmode` is `O_TEXT`): reads drop the CR of CR LF and stop at Ctrl-Z, writes add a CR before each LF. `O_RDONLY` is 1. `mkdir` takes one argument. `_ctype` is a 257-entry table in DGROUP that code may index directly. runtime/port/sys/borland.c has all of it.
- **C0 checks for null pointer writes** at exit: it sums the bytes of DGROUP's null area (stock C0 from DS:0; UW2's from DS:4 to DS:30h) and prints "Null pointer assignment" if the sum changed. A near null pointer reads DS:0, which holds whatever the link put just below `_DATA`; a far one reads the interrupt vector table.
