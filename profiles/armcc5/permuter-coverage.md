# Source levers of compiler.md against the permuter's rules

This is an audit of `compiler.md` (read end to end), `match-brief.md` and the armcc5 part of
`skills/match-file`: every lever that is an edit to the source, with the rule of
`tools/permrules.py` that makes it. A rule was counted as covering a lever only after reading what
the rule does, not from its name. Build settings, linker facts, tool usage and facts about reading
an image are not levers and are left out; the few that a source can carry (a pragma, the file's
options line) are in.

"Before" is the state before this audit; "now" names the rule that makes the edit today. A rule's
meaning is `same` (proved from the tokens to be the same program), `type` (may change what the
program does, or cannot be proved) or `probe` (adds dead code). Line numbers are those of
`compiler.md` when the audit was made; `match-brief.md` and the skill repeat levers of
`compiler.md` and add none.

## Summary

- 88 source levers found (a row of the tables below; one row can stand for several lines of the notes).
- Before the audit 35 had a rule, 21 were covered in part (another direction, another operand kind, or
  only through two or three moves in a row) and 32 had none.
- Now 82 have a rule: 35 new rules, and four older rules extended (`decl_order`, `const_type`,
  `expr_split`, `ptr_base`).
- 6 are left: five uncovered, each with its reason, and one that is not a lever (the last section).
- A second audit (the older tools, the other documents, the projects' lesson notes) added 19 rows:
  12 more rules, among them `uniform` (one edit at every look-alike site), and seven older rules
  extended. Its own section lists them and what it left.

## The levers

### Values, locals and where they are read

| Line | Lever (before against after) | Before | Now |
|---|---|---|---|
| 41, 154 | a zero set before a loop against in the `for` header (zeros keep source order) | `loop_form`, `stmt_move` | same |
| 50, 66, 163, 444 | a global or field read at each use against cached in a local | `local_cache`, `local_inline`, `inline_group` | same |
| 163 | several fields loaded into locals before any store | partly (one read per move) | `cache_group` (every read of a run of statements at once) |
| 436, 166 | a field read once into a local for two uses; an extra local for one value | `local_cache` | same |
| 418 | a value assigned at the top and used in three branches, against the same assignment just before each use | none | `sink_assign` (and its reverse) |
| 41, 424, 730 | where a shared constant is first made: initialiser, an assignment later, just before first use | partly (`init_decl` then `stmt_move`: two or three moves) | `sink_assign` (one move, from a statement or from an initialiser) |
| 408 | a just-cleared variable passed in place of a literal zero | none | `known_const` |
| 429, 433 | a constant held in a narrow local; a store written as a copy of a variable that holds the constant | partly (`const_local`: a new local only) | `known_const` (any variable known to hold it) |
| 847, 960 | the compared value stored in the arm of `if (x == K)` | partly (`dup_store` adds a store) | `known_const` |
| 55, 104, 131, 147, 159 | a running index against the constant it is known to be (`buf[n++]` after `n = 0`, `buf[0]`), a step against a store of the next constant | none | `known_const` (both ways; `v++` as `v = K + 1`) |
| 325 | a constant written as the formula its siblings use, where the formula's variables are known | none | `formula_copy` |
| 129, 150 | a sub-expression split into a local of its own | partly (`temp_expr`: the whole value; `arg_local`: an argument) | `subexpr_local` |
| 180 | a value built through the store's own target in two statements | partly (`expr_split`: + - \| & ^ only) | `via_target` (any operator, when the copy is exact) |
| 125 | `i = (g >> 4) + 1;` against `i = g >> 4; i++;` | partly (two moves, no way back from `i++`) | `expr_split` (extended: `i++` is a term of 1) |
| 403, 149 | a block temporary of another integer type for a copied field | `temp_expr`, `common_temp`, `retype` | same |
| 117, 544 | declaration order of locals, also inside one declarator list | partly (whole declarations only) | `decl_order` (extended: two names of one list) |
| 102, 905 | scope of a local (function, block) | `scope` | same |
| 167 | an initialised local array declared in a switch case against at function scope | none (`scope` skips initialised locals) | `scope_init` |
| 116, 743 | a local's integer type | `retype` | same |
| 926 | a local that only held a loop bound removed | `local_inline` | same |
| 928 | an expression passed straight to a parameter against through a temporary | `arg_local` | same |
| 143 | the bytes of a halfword through a union local | none | `union_split` |
| 172, 665, 706, 855 | a pointer local to a global or record against direct access | `pointer_direct`, `obj_ptr`, `ptr_index`, `ptr_base` | same |
| 142 | `dst = p->buf; dst += 3;` against `&p->buf[3]` | partly (`ptr_base`: named arrays only) | `ptr_base` (extended: member arrays and pointers) |
| 841 | `(p + 0x40)[7]` against `p[0x47]` | none | `index_split` |

### Statements and expressions

| Line | Lever | Before | Now |
|---|---|---|---|
| 46 | `x < 10` against `x <= 9` | `inequality` | same |
| 64, 92, 140, 146, 683 | which arm is tested first / out of line | `if_swap`, `cond_explicit` | same |
| 693 | early return against an if/else at the end; `else` after a jump | none | `early_exit` |
| 696, 920, 138 | a flat chain against nested ifs; `&&` against nesting | `nest`, `if_swap`, `flatten_else` | same |
| 68, 74, 157 | the three clamp forms | partly (`ternary`, `flag_else`: not copy-then-test) | `clamp_form` |
| 113, 119, 161 | one store after a join against a store in each arm | `ternary`, `if_hoist`, `local_inline` | same |
| 710 | a store three paths end with, written once after an else-if chain or in each arm | partly (`if_hoist`: two arms) | `chain_tail` |
| 70, 858 | a cast or mask on an operand that changes no value | partly (`cast_assign`: the whole value) | `narrow_operand` |
| 115 | `(T)-E` against `-(T)E` | none | `cast_neg` |
| 89 | read-modify-write in one expression against two statements | `expr_split`, `compound` | same |
| 89 | `& 0xf0` against `&= ~0xf` | none | `mask_form` |
| 673 | a mask applied in two steps (a load can stand between) | none | `mask_form` (two statements; `stmt_move` then moves one) |
| 72, 835, 852 | `+ 5u`, `(unsigned)v >> 8`, a float constant's type | partly (`const_type`: not the shift) | `const_type` (extended: the left of `>>`) |
| 86 | `for (i = 8; i != 0; i--)` against `do { } while (--i)` | partly (`loop_test`: no step in the test) | `loop_step` |
| 106 | `*p++` against `x = *p; p++;` | partly (`postinc_index`: an index only) | `step_fold` |
| 144, 55 | `dst[n] = x; n++;` against `dst[n++] = x;`, also for a field index | partly (locals only) | `postinc_index`, `index_step` |
| 145 | a decrement before the use against in the index | partly (`preinc_cond`: an if's test only) | `step_fold` |
| 127, 138 | range test forms | `range_test` | same |
| 169, 178 | chained stores and their order; `n = g.len = f();` | `chain_store`, `embed_assign` | same |
| 923 | an assignment inside an expression | `embed_assign` | same |
| 176, 839 | operand order of a compare or a commutative operator, `buf[2 + i]` | `operand_order`, `byte_read` | same |
| 910 | a multiplication by a power of two against a shift | `shift_mul` | same |
| 88 | a result local against direct returns | `return_local` | same |
| 123, 653 | which global the source touches first (pool order) | `stmt_swap`, `stmt_move`, `compound_swap` | same |
| 659 | a side effect in the array operand of a store (`(c = 0, buf)[idx] = val;`, `(p = buf)[k = idx] = val;`) | none | `array_operand` |
| 699, 901 | order of case bodies, empty cases and their places | `case_edit` | same |
| 53, 847 | an if-chain against a switch | `if_switch` | same |
| 960 | `a - b == 0` against `a == b` | none | `cmp_diff` |
| 844 | `E + K - K` on a stored value (loses what armcc knew of it) | none | `cmp_diff` (its second form) |
| 852 | `abs()` against a conditional negate | `abs_form` | same |

### Dead code that reaches the allocator

| Line | Lever | Before | Now |
|---|---|---|---|
| 378, 1068 | an unread comparison stored in a block local | `probe_cmp` (with enumeration) | same |
| 366 | an unread comparison of two globals' addresses | `probe_addr` | same |
| 350, 373 | an unread comparison of an element's address (`&tbl[i + 1] != 0`, a global's address against it) | none | `probe_elem` |
| 355 | unread pointer locals held from a block's top to a comparison | none | `probe_ptr` |
| 84, 341, 345, 347 | a test whose two outcomes meet (`if (c) continue;` at a loop's end, before a case's `break`, `return` at the end) | none | `probe_test` |
| 424 | a dead store that moves where a shared constant is made | `dup_store` | same |
| 438 | an unread later use that keeps a copy's source live | `probe_cmp`, `probe_move` | same |
| 959 | an unread local with an initialiser | `dead_local`, `pad_local` | same |
| 866, 869, 873, 957 | forms that leave a `nop` at -O0: `&& 1`, `do { } while (0)`, `else (void)0;`, `default: break;` | partly (`dup_store`, `case_edit`, `ins_block` for some) | `nop_form` |

### Helpers

| Line | Lever | Before | Now |
|---|---|---|---|
| 275, 320 | one-line getters for reads | `getter`, `index_helper` | same |
| 320, 272 | a value expression (or a clamp) through an inline helper | partly (reads only) | `expr_helper` |
| 268, 279, 292 | a run of statements, or a block with its result, moved into an inline helper | none | `outline_block` (and its reverse) |
| 281, 303, 313 | an address returned by a helper (`*h(i) = v;`), a struct through a view helper (`view()->f`) | none | `addr_helper` (and its reverse) |
| 287 | `static __inline` is a hint: helpers are written `static __forceinline` | all helper rules | same |
| B7 | setters | `setter`, `index_helper` | same |

### Declarations in the file

| Line | Lever | Before | Now |
|---|---|---|---|
| 77, 110, 808 | a callee `static`, global or `__weak` | `callee_linkage` | same |
| 133, 155, 244, 914 | the return or parameter type a caller saw; the function's own parameter type | partly (`unprototyped` only) | `signature_type` |
| 724 | a global `volatile` in this unit | `global_type` | same |
| 724 | a volatile byte tested twice in a row | none | `repeat_test` |
| 413 | one read through a volatile or wider view | partly (`volatile_read`: values of stores only) | `view_access` |
| 817, 833 | a named access against one through a cast of its address | none for scalars (`ptr_index` for records) | `view_access` |
| 121, 669 | `__packed` on or off a struct | none | `packed_struct` |
| 97, 735 | a global declared as a struct against a plain array | none | `struct_array` (struct to array, for a struct of one integer type) |
| 175, 833 | sign of a struct field's declared type | none | `field_type` |
| A6 | a sized extern array | `sized_extern` | same |
| 81 | `#pragma O0` round one function | none | `func_pragma` |
| 94, 256, 751 | the file's options line (`--no_data_reorder`, `--bss_threshold=N`) | none | `file_opts` (the workers compile such a variant with its own line) |

## Second audit: the older tools, the other documents and the projects' lesson notes

The same question put to the remaining sources of tested levers. The project notes are private:
only the kind of edit is recorded here, in generic terms.

Levers found per source (kinds of source edit, counted once per source; most were already in the
tables above):

| Source | Kinds found | Already in the tables | New rows below |
|---|---|---|---|
| `tools/armidioms.py` (its nine modes) | 9 | 6 | 3 |
| `tools/armpermute.py` (the old permuter's moves) | 13 | 13 | 0 |
| `tools/armoutline.py` (helper forms, `--sweep`) | 6 | 4 | 2 |
| `tools/armbrute.py` (`--table` and `--pool` spellings), `armvariant.py`, `armreduce.py`, `armfixdb.py` | 8 | 8 | 0 |
| other documents of this repository (`docs/`, the skills, `linker.md`) | 3 | 3 | 0 |
| lesson notes of one firmware family (five notes files) | 58 | 44 | 14 |
| notes, commit subjects and memory notes of two other trees | 70 | 61 | 9 (5 of them shared with the row above) |

New rows:

| Source | Lever | Before | Now |
|---|---|---|---|
| idiom tool | a literal held in a new function-scope local, set just before the statement or at the top of the body | partly (`const_local`: a block local) | `literal_local` |
| idiom tool, notes | a literal held in a local the function already has, which is dead there | none | `literal_local` |
| idiom tool | a dead `x = 0;` before any store to x | partly (`dup_store`: only a target with a constant store elsewhere) | `dead_zero` |
| outline tool | a helper whose run-only locals are declared inside it; stored locals passed by address | partly (`outline_block`: by value and one returned) | `outline_block` (extended) |
| notes | one later read of a local written as its value expression again; a second store taken from the source, not from the first target; one bound of a range test on a fresh read and the other on the copy | partly (`local_inline` removes the local at every use) | `copy_source` (and the reverse) |
| notes | a statement under a test that is always true where it stands | none | `known_guard` (where the fact can be proved) |
| notes | a range guard round a switch | none | `switch_guard` |
| notes | a flag stored in each arm against the stored test (`x = (c);`) | partly (`flag_else`, `ternary`) | `bool_store` |
| notes | how an accumulation starts (`id = 0; id \|= a;` against `id = a;`) | none | `accum_start` |
| notes | grouping and term order of a sum of three or more terms | partly (`operand_order`: two operands) | `regroup` |
| notes | other forms of the unread comparison: ahead of an if's test with a comma, `while (E) break;`, `if (E) goto L; L: ;`, stored in one of the function's own dead locals, an empty `switch (0)`; a call argument behind a comma | partly (removal of the comma form only) | `probe_forms` |
| notes | a side effect on the stored value (`buf[i] = (c = 0, v);`) | none | `array_operand` (extended) |
| notes | an unused extra parameter | none | `extra_param` |
| notes | a store or load through a cast of the other sign (`*(short *)&g = v;`) | partly (`view_access`: same type, volatile, wider) | `view_access` (extended) |
| notes | a constant written from a variable that holds a nearby constant (`dmin = cmin - 5;`) | none | `known_const` (extended) |
| notes | a wider constant that is cut to the same stored value; a wrap written as a mask, a cast or a modulo | none | `wrap_form` |
| notes | an identity helper round one value | none | `expr_helper` (extended) |
| notes | a block that opens with a copy of one of the function's declaration lines and uses none of its names | partly (`pad_local`) | `pad_local` (extended) |
| notes, the user's standing guidance | one idiom at every look-alike site (macro-like copy-paste), or at a run of them | partly (`everywhere` of some rules) | `uniform` (below) |

Recorded as tried with no effect, and so given no rule: spellings of a constant (a suffix that
does not change its type, an enum, a `static const`, a folded expression), spellings of an
equality (`!(x - K)`, `(x ^ K) == 0`), the `register` keyword, a file-scope register binding,
the file's prologue (order of declarations, headers, unused declarations: the `file_order` rule
covers the one part that had an effect), line layout, storing a call's ignored result, an
explicit run-time helper call for a conversion.

Left uncovered from these sources:

| Lever | Why |
|---|---|
| an object read through a parameter that points to it against the global itself | the rule would have to know that the two are one object; nothing in the file says so |
| where a callee or a global is defined (this file or another, before or after), one aggregate against separate globals, an address written as a symbol | file and data ownership, as in the first audit; the map decides them |
| a bit-field member store against a mask through a cast byte address; an aligned struct cast of a base against packed fields of a global at an odd address | need the bit and byte layout of the struct and the object's address |
| a narrower or wider cast view of a struct field | `view_access` takes named scalar globals; a field needs its layout to be read back safely |
| function-local statics, a pointer cursor over a global counter, a result through an out-parameter | recorded as ruled out in every case noted (the first changes storage); `outline_block` makes the out-parameter form for a helper |

### One edit at every look-alike site

`uniform` groups the function's statements (and runs of two) by token shape: keywords,
operators, type names and called names as written, every other identifier by where it first
appears in the statement, constants and one-token array indices abstracted. A group needs three
members. It takes an edit that some rule makes inside one member and has the same rule make it
at the other members, by steering the rule's own choice of site there, so the rule's
preconditions are checked at each member; a member where the rule does not make the same edit
is left alone and counted in the description (`at 5 of 6 look-alike sites (1 refused)`). It
applies to every member, to a contiguous range of them, or to a random half. Its `enumerate`
hook gives the sweep, for each group and each rule, the edits found at the group's first member
made at all members.

## Sweep coverage

A rule whose sites form a large structured space carries an `enumerate` hook, so the permuter's
sweep lists every single edit instead of drawing: `known_const` (every literal and every read a
known variable can stand for), `view_access`, `subexpr_local`, `outline_block` (every run of one to
five statements), `cache_group` (every run), `probe_test`, `probe_elem`, `probe_ptr` (every second
point), `array_operand`, `nop_form`, and from the second audit `dead_zero`, `literal_local`,
`copy_source`, `known_guard`, `probe_forms` and `uniform`. The other new rules have at most a few edits per site
and a few sites per function; the sweep's fixed-seed draws (until 60 in a row give nothing new)
list them all.

## Left uncovered

| Line | Lever | Why |
|---|---|---|
| 35, 38, 62, 152, 741, 831 | a 1-bit or n-bit bit-field against a mask on the word (and the bit-field's base type) | needs the bit positions of the struct's fields; the layout probe reads offsets and sizes only, and the edit rewrites a struct declaration together with every use |
| 59, 100, 248, 745 | which globals the file defines (and as one array or two) | data ownership: the object's data changes, which the data stage and the gate decide, not a function's score |
| 251, 823 | one struct against separate globals for the same address (a second pool entry) | engine gap: the new name needs an `at:` pin at an address the rule does not have, and the scorer reads the pins of the base text only |
| 310 | the shape of the return inside an inline helper | the rules edit the function being matched; a helper is another function (run the permuter on a file whose target inlines it, with the helper's text as the function, is not supported) |
| 97 | a plain array declared as a struct (the reverse of `struct_array`) | there is no struct type to name unless the file already has one of that shape |
| 897 | removing trailing case bodies | not a lever: it is the observation that registers at -O0 depend on the whole body |
