# Borland C++ 2.0 recovery observations

These controls use BC20/BIN/BCC.EXE from the Turbo/Borland recovery project.
They supplement the older borland-tc101 observations; do not assume every
optimizer behavior is shared across versions or backend choices.

Earlier definitions affect call encoding. In STCTYPE a far cdecl call to an
extern produces five bytes (9A + pointer), while compiling the actual recovered
STERR definition first produces PUSH CS / E8 (four bytes). STERR is 239 bytes;
STCTYPE is 202. The compiler resolves that internal relative call without an
OMF fixup. A declaration alone cannot reproduce the earlier-definition context.
The verified context build compiles actual C, proves the helper independently,
then splits the object and externalizes the relative call as a normal OMF
relocation to the original body. No instructions are inserted. Evidence:
work-in-progress/STCTYPE/context-spike/{REPORT.txt,SPLITTER-REPORT.md,split-proof.json}.

The -B compile-via-TASM backend can change optimizer output. STEND compiled
directly with -O emits 293 bytes after common-tail merging. Without -O it emits
362 bytes but three branches target intermediate jumps. With -O -B it emits
all original 362 bytes and original branch displacements. The source contains
no inline assembly; BCC generates and assembles its own assembly output.
This is not a general solution for branch-layout stalls: STBINOP -B emits3135
bytes rather than3139 and retains the two unwanted short threaded branches.
Evidence: work-in-progress/STEND/NOTES.txt and STBINOP-B/NOTES.txt.

In LEXFIN, -G- -O selects the original seven-key linear CS switch table;
-G -O selected a comparison tree. The verified source emits252 instruction
bytes followed by14 literal-key bytes and14 relocated target bytes. This is
an observed control for this function, not a rule for every switch.
Evidence: work-in-progress/LEXFIN/NOTES.txt and ownership/proof.json.

LEXPATH's direct backend emits293 bytes, missing an original unmasked NOP
at function offset5F. Adding only -B emits294 matching bytes. The source
contains no padding; the exact compiler/assembler mechanism behind that NOP
was not established. Evidence: work-in-progress/LEXPATH/NOTES.txt.

A genuine context helper and primary can need different -G policies.
LEXSKIP's global -G preserves the real LEXTOKEN helper's283 bytes; a
file-scope `#pragma option -G-` in the appended primary changes subsequent
code to its original size-oriented cleanup. The verified combination also
uses -B. Its two real directive decrement paths merge into the original
shared tail. Necessity of -B alone was not isolated. Evidence:
work-in-progress/LEXSKIP and helper-near-callback/actual-proof.json.

Real local lifetimes can determine implicit switch-slot ordering. In LEXSW,
a function-scope far result and uninitialized character gave selectorBP-8,
characterBP-2 and resultBP-6/-4. Declaring the result only in its identifier
case and initializing the character inside the iteration block after the
buffer reset gives original selectorBP-2, characterBP-4 and resultBP-8/-6.
These meaningful scope changes corrected all26 displacement differences;
no unused locals or padding were added. Evidence: work-in-progress/LEXSW
builds13/14, proof-final.json and the original-exact gate176.

In resident RTOUPPER, default register-variable policy caches a byte parameter
in DL (or int parameter in DX), despite no explicit register declaration.
Removing -Z and explicit -Z- do not change this output. Adding -r- to the
ordinary int parameter/repeated unsigned-byte-cast source produces original
repeated stack loads and exact36 bytes. RSCPY's ordinary length/move/return
wrapper likewise matches43 bytes with -r-. This is tested library parameter
behavior, not a blanket setting for other units. Evidence: RTOUPPER/REPORT.md
and RSCPY/REPORT.md; full linked gate remains the admission cross-check.


BC20 `_loadds` generated the exact DS save/load/restore protocol for the
55-byte DOS control handler RCTRL23. Two earlier inline `mov ax,seg SYMBOL`
controls emitted two extra zero bytes; their cause was not established.
Using the actual attribute, with only STC left as hardware assembly, produced
original CODE immediately. Context splitting preserves its zero-addend
SEGMENT16 reference to empty _DATA/DGROUP, pinned to the exact entry prologue;
it does not rebase this as a self-CODE reference. Evidence: RCTRL23,
context-loadds-v1/review, and original-exact gates210/212.

The actual `interrupt` keyword generated all original32 bytes of the INT1B
flag handler: nine register saves/restores, DS setup and IRET. The C body only
stores1 to the existing unsigned WORD LINE_HOOK_FLAG. No inline assembly or
register transcription was needed. Evidence: RIRQ1B and its independent review.

Far qualifiers on comma-separated pointer declarations apply per declarator.
In MAIN13C the later handler-address arrays needed separate far declarations;
using a typedef for the common far pointer type gives all vector variables
the intended four-byte representation. Actual failed/intermediate compiler
controls and the exact final792-byte helper+primary build are retained under
RMAIN13C-build; do not repair these type errors with casts or fake CS formals.

In C4VALUE, changing only -G to -G- on the same C/header/other options changes
475 CODE bytes to the original448. Size mode shares three returned-C4FF call
tails with the first error block. The earlier assignment-only control restored
the original arena store from AX through a genuine chained assignment; its
other474 bytes stayed unchanged. Both controls retain the actual WORD result
forwarded by C4FF. Evidence: C4VALUE-control2/control3 and their source, size,
and positive reviews under work-in-progress.

In C4FLAT, reusing the saved flag's WORD local for later parent handles spills
through BP-4 and produces347 bytes. Giving each call-free parent block its own
ordinary initialized WORD local preserves the flag across the mutating helper
and lets the parents use DX, producing all original341 bytes with the same
four-byte frame. Header, options, widths, calls and read/store order stay the
same. No explicit register binding is added. Evidence: C4FLAT-control1/control2,
C4FLAT-lifetime-review and C4FLAT-positive-review under work-in-progress.


Seeing a source value in SI or DI does not prove that its declaration used
`register`. BC20 can allocate an ordinary automatic WORD there. The original
source-symbol selection pass (record156, accepted STAOV09D/09E/0A1/0A2) is
separate from the expression allocator in record157. Authenticated keyword,
storage-class and constructor paths show that explicit `register` is a
sufficient producer of a priority flag which starts its score at8000h;
weighted references, masks, category adjustments and eligibility still apply.
The first restricted pass leaves internal IDs8/9, mapped to SI/DI by original
DS:54CE and the accepted PUSH/POP emitter. This is not a universal allocation
rule or a claim about original identifiers or historical flags.

Three isolated controls demonstrate the distinction. C8CLEAN's two explicit
register locals produced cursorDI/nodeSI; making only node ordinary restored
all131 original bytes with cursorSI/nodeDI. C7CHAIN likewise restored all479
bytes by keeping RESULT register and making NODE ordinary. C5CHECK restored
all710 by keeping SYMBOL register and making NODE ordinary. In each case,
body, types, operations, scopes, names, options and guard stayed unchanged.
Earlier formal/scope changes produced identical wrong CODE. These observations
justify investigating actual priority classes, not adding dead uses, markers,
register painting or name/option matrices. Evidence: BC20-register-selection-
audit, BC20-register-root, the three priority reviews, and controls C8CLEAN3,
C7CHAIN3 and C5CHECK5 under work-in-progress.

The mask helper5B9E's caller consumes the previous WORD mask in AX. Correcting
its old void definition to unsigned WORD and explicitly returning its existing
saved value emits the same21 bytes and identical decoded CODE/data, fixups,
externs, PUBLICs, segments and groups: load tracking
elides a redundant local reload after the global OR. Evidence: BC20-mask-return-
control and its independent review. This corrects a return contract without
adding source coverage or relying on an implicit residual-register result.

In the complete seven-function C5UNIT context, changing only -G to -G- on
source2 removes one duplicated returned-C4FF suffix (PUSH AX, PUSH SI, FAR
CALL), changing2810 CODE bytes to2803. It preserves the first1588 instruction
bytes, including the three previously accepted bodies. Changing only -B on
that source then produces the original2811 bytes: the compiler-generated ASM,
assembled automatically and unedited by TASM, retains the separate eight-byte
selector reload and child-result store after C5WRAP. All2811 bytes and208
fields verify; all three old bodies remain exact. The isolated controls2/3/4
and their objects are retained. This is a measured backend difference for
this genuine context, not a general -B rule or a historical-flags claim.
Evidence: C5UNIT-control2/control3/control4, C5UNIT-size-policy-review,
C5UNIT-backend-review and the positive/native reviews in work-in-progress.

The earlier typed-branch conditional-assignment rewrite did not change this
unit's store sharing. With ARGUMENT made ordinary, it changed only SI/DI role
bytes; allocation and source-shape changes were combined, so this is not a
fourth isolated register-priority experiment. STEND's older source/log
backend evidence lacks its historical negative293-byte object; C5UNIT's
paired objects provide the direct retained comparison here. Source FAR32
fixups have LOC3; native/import SEGMENT16 fields have LOC2. The diagnostic-only
C5UNIT-control2 count error is corrected by C5UNIT-fixup-type-erratum without
changing that immutable negative packet.

C7UNIT demonstrates two actual TASM 2.51 instruction-shortening effects.
Its unchanged compiler-generated assembly, captured with -S, reproduces the
-B control's complete 4254 CODE bytes under default /ml assembly. TASM's
/jNOSMART preserves the original WORD OR imm16 (instead of imm8) and leaves
the forward same-segment FAR call as an ordinary relocated 9A input for the
existing TLINK overlay rewrite. That assembly is4255 bytes. Adding /m2 removes
the one extra NOP reserved while shortening a forward JMP, yielding4254 and
zero fixed-byte differences. No generated assembly text was edited.
BCC prefixes TASM options with slash itself: the normal successful C flags
are -mm -G- -O -Z -p -Y -B -TjNOSMART -Tm2. Passing -T/jNOSMART instead
failed with Bad switch // before producing an object. Complete control10
passes WHOLE4254 and strict302 fields (55FAR32, including one ownCODE forward
field, plus247 DS16); native original54 selector fields are a distinct count.
The paired G to G- control also removes30 bytes of duplicate return/coercion
paths and restores CONST's SI/DI roles. These are measurements of this unit,
not historical flag recovery or a general rule. Control3's -O- expands the
chosen function and -O. does not restore later codegen; it remains negative.
Evidence: work-in-progress/C7UNIT-control{7,8,9,10}, C7UNIT-assembly-producer,
C7UNIT-assembler-{nosmart,multipass}, C7UNIT-field-count-erratum, and gate247.

C12SCAN's two BYTE accumulations distinguish compound assignment from a plain
assignment expression under otherwise identical -mm -G- -O -Z -p -Y -B
-TjNOSMART -Tm2. `valid &= helper(...)` emits3-byte AND [BP-1],AL at each site.
`valid = valid & helper(...)` emits the original8-byte loadDL/ANDDL,AL/storeDL.
Only these two operators change: the whole unit grows386→396, WHOLE/strict31,
with its previous153-byte helper and all field identities unchanged. No extra
state, qualifier, evaluation short-circuit or fake helper is added. Appending
the next real121/133-byte helper/caller pair gives whole650/strict51 on its
first control, with153/396 preserved. This is a paired observation, not a rule
for arbitrary expressions or compilers. Evidence: C12SCAN-control{1,2},
C12PREFIX650-control1, independent review and full gate249.

Under the same -mm -G- -O -Z -p -Y -B -TjNOSMART -Tm2 policy,
C10TYPE distinguishes an unsigned BYTE-return comparison to zero from unary
logical NOT. Explicit int/unsigned-WORD casts followed by ==0 still emit
OR AL,AL at two recursive sites. Replacing only those expressions with
!X00A_00(...) emits the original MOV AH,0; OR AX,AX (B4000BC0) at0365/04EF,
restoring whole1581 and strict98 fields. The header, BYTE ABI, language and
options are unchanged. Accepted C7UNIT already supplies the same API under
&& !call and C12CHECK supplies another true BYTE producer with !call; their
objects and original bytes show the same extension. This is an observed
operator/context distinction, not a universal Boolean code-generation rule.
The -G and C++ language controls were broad negative results. The installed
compiler help confirms -b (enums as integers) is default, so no packed-enum
premise or enum-return substitute is involved. Evidence: BC20-byte-not-producer,
C10UNIT-control{1,3,4,5}, C10BOOL-type-audit and C10UNIT-independent-review.

C14UNIT's enum case provides a measured switch-entry distinction with the
same options. An upfront `case 0x15: goto named_type` binds that table slot
directly to the later class action and omits the original two-byte entry
trampoline. Giving enum kind21 its own real C14LENGTH call and return, with
the identical operation also present in the mutually exclusive class cases,
produces the original EB78 at0247 by common-tail sharing. This retains the
same one call per dynamic path, without dummy operations or padding. Together
with the independently established unary-NOT BYTE predicate, whole3097 and
strict270 fields match. All18 routines, four real tables totaling292 bytes,
genuine dead JMP0270 and real516/76-byte frames remain exact. The separate
-O- control expands3222 and is negative. These observations do not establish
historical source spelling or the compiler's precise internal pass order.
Evidence: C14TYPE-enum-flow-proposal, C14UNIT-source-proposal{4,6},
C14UNIT-control{3,4,5} and C14UNIT-independent-review.

C13UNIT provides paired source observations under the same policy. Reversing
EMIT/COLLECT register declaration order alone leaves the wrong SI/DI roles.
Removing only the symbol's explicit register hint gives priority to the real
loop index/member traversal, restoring original roles without extra locals.
Its FAR six-byte slot array also distinguishes evaluating a FAR array base
before indexing from attaching the segment to an already computed byte
record offset: the latter restores the native IMUL/base/segment order. For
three zero-index stack-based fields, existing W(segment,slot+2/+4) views put
MOV BX,[BP-2] before MOV ES,segment as in the original; folded structure-base
member expressions reverse those loads. SLOT(end,-1), the actual previous
record, emits ADD AX,FFFA instead of SUB AX,6 from SLOT(end-6,0).

The same unit's type size is one genuine DWORD at type+9. Assigning the
unsigned WORD result through that DWORD view emits the original high WORD
zero and low WORD value with shared ES/BX, reducing16 bytes including loop
branch relaxation compared with two separate WORD lvalues. Sending a branch
result directly to its real output call in each exclusive path, rather than
assigning an unused final local before a shared output, removes5 bytes of
store/reload and retains the native common call tail. These source forms do
not introduce operations, helpers, formals, padding or hardware state. The
whole4336-byte third control passes strict351 fields; first4353 and second4336
controls remain frozen negatives. Evidence: C13UNIT-control{1,2,3},
C13UNIT-source-proposal2, C13UNIT-register-pointer-proposal and independent
source/ABI review. These are measured local producer facts, not allocator
or optimizer rules for arbitrary code.

C16UNIT confirms a Boolean-value boundary in a comma expression. In its0130
loop guard and1CF5 BYTE-result predicate, `(peek(),TOKEN_TYPE!=0x67)` forces an
inner AX1/0 materialization followed by the enclosing Boolean test, adding11
bytes at each site. `(peek(),TOKEN_TYPE)!=0x67` returns the real WORD token
through the comma and performs the comparison in the enclosing condition.
The call, one token read and short-circuit order are identical; both native
paths then match. The genuine3-byte Spec result at0CE8 also uses a compiler
hidden FAR output pointer plus its4-byte-rounded input, RETF8; no explicit
output formal is needed. A chain of its three real WORD-zero initializers
restores one AX zero producer and three stores rather than three immediates.
The complete20-routine8094-byte third control passes strict594 fields and
retains all166 bytes of compiler CS tables with zero owned DATA/BSS.

An accepted ordinary-C donor, C12CHECK's first153 bytes, has two X01906 calls,
each followed by explicit return0, but only one physical call field at0064.
The second arm prepares its actual arguments then jumps008E to CALL0063.
C16's two token-diagnostic arms similarly share the native PUSHcode/CALL tail
when each ends with its existing explicit return0; falling through to one
outside return left6 extra bytes. In C15's BE4F loop, explicit continue after
both successful append calls shares one call/result-assignment/update tail;
one-continue/one-fallthrough and both-fallthrough forms duplicated it. The
continues perform the same real for update once, with no added operations.
That C15 body is literal-exact in control5, though the whole unit remains
negative. This is measured termination-context evidence, not a claim about
optimizer pass order or a rule for all calls.

Diagnostic payload domains must come from the formatter and actual messages.
All six first payloads in each C15/C16 unit are consumed by %s; original
formatter code loads a FAR pointer with LES and reads through NUL. Their
actual name/type producers return a DS buffer pointer in DX:AX, even where
the current canonical API is LONG. A consistent unit-local FAR-string first
payload declaration, with explicit pointer views of those existing LONG
results, preserves the real10-byte argument footprint and restores literal
FAR-string push order. It is not evidence that all diagnostic payloads are
strings. Evidence: C15UNIT-diagnostic-payload-review, C16UNIT-diagnostic-
payload-review, C15UNIT-shared-termination-donor, C15C16-termination-proposal,
C16UNIT-control{1,2,3}, C16UNIT-independent-review and C15UNIT-control5.

Whole C17 CO011 guard context, measured under unchanged -mm -G- -O -Z -p
-Y -B -TjNOSMART -Tm2: the first complete six-function control emits910 of
916 bytes. In the private LONG measure routine, the positive nested
`if(length>0x10000L)` omits the original two-byte shared-return branch.
The equivalent genuine early guard `if(length<=0x10000L)return length;`,
after the actual adjustment-output zero store, restores it. In two other
routines, positive `if(PARSE_ENABLED)` outer bodies use a direct BYTE CMP;
`if(!PARSE_ENABLED)return;` restores original AL load/AH zero/OR AX and the
two extra bytes in each routine. Actual BYTE storage is unchanged. All
other source operations, callbacks, locals, headers and flags stay equal.
Control2 is WHOLE916/strict0; root materialization checks all44 real fields
and every byte in all6 functions. This is specific source-context evidence,
not a universal instruction-selection rule. Frozen evidence:
C17UNIT-source-proposal2, C17UNIT-control{1,2},
C17UNIT-control{1,2}-review and C17UNIT-root-review.

C18 gives a measured Boolean-assignment context within a still-negative whole
unit. Controls2/3 retain identical headers, flags and ABI. In private0000,
`direct = compound ? 0 : 1` still emits three narrow BYTE mask tests and AL
immediate result arms, thirteen bytes short. Expanding the same predicate
by DeMorgan into ordered OR rejection terms, with ordinary unary NOT on
each genuine BYTE mask, restores three AL-load/AH-zero/TEST-AX sequences
and the original AX 0/1 result. The real BYTE direct object is unchanged;
its later comparison remains `direct==0`, producing original CMP BYTE.
Control3's entire465-byte private routine is literal-exact after all actual
fields and call identities are resolved. Twelve of thirteen C18 routines
match; the remaining three-byte X012_03 common error-branch funnel is NOT
solved. Splitting the two error gotos and exposing the inverse guard's real
normal-entry label both failed; control4's six decoded object structures
are identical to control3. Do not use those failed contexts as matching
evidence or repeat spelling variants without a new donor. Evidence:
C18UNIT-source-proposal{2,3,4}, C18UNIT-control{2,3,4},
C18UNIT-control{2,3}-review. This measures particular source contexts, not a
universal BYTE-promotion rule or compiler pass order.

C18's remaining error funnel is solved in source proposal8/control8. The
first two ordered rejection paths now report the existing error and return
its existing constructor directly, rather than jumping to the later shared
error label. The later label still serves the other errors. Each dynamic
error path performs exactly one report followed by one constructor; the
header, other twelve bodies, locals and compiler options are unchanged.
BCC emits the original nearby shared error jump, restoring all 5,553 bytes
and all 462 real relocation fields (WHOLE and strict verification pass).
The preceding statement that this funnel is unsolved records controls3/4
only and is superseded by control8. Do/while-zero and for-ever guard-region
controls5/6 were decoded-identical to control3 and failed. An unchanged-ASM
capture proves BCC already emitted the unwanted funnel before TASM; no
assembler rewrite or internal compiler pass order is inferred. A paired -G
probe also failed. Evidence: C18UNIT-source-proposal8-direct-error-return,
C18UNIT-control8, C18UNIT-control8-review and C18UNIT-asm-stage-audit.

C22 gives another measured Boolean context. In the actual diagnostic guard,
`!FALLBACK_ENABLED || (tag & 3) != 1` widens the BYTE flag to AX and adds two
bytes. The equivalent `!(FALLBACK_ENABLED && (tag & 3) == 1)` uses the original
CMP BYTE, preserving short-circuit reads for every flag value and restoring
the complete 654-byte unit, including its eight-byte CS jump table. The
header, callbacks and locals are unchanged. Source proposal2/control2 passes
WHOLE and strict verification with 59 fields; both true bodies are literal
exact. This observation does not establish a universal Boolean rule.

C23 measures explicit register-hint priority in a small complete unit. With
both the expression formal and local registry handle marked register,
X017_00 uses DI for the expression and SI for the registry handle; the
original uses SI and DI respectively. Control2 removes only the formal
hint and is identical to control1 in all six decoded object structures.
Control3 starts from the initial source, retains the formal hint, and removes
only the local registry-handle hint. Automatic allocation then restores
original SI/DI usage and all 491 bytes, 46 real fields and both PUBLICs
(WHOLE/strict pass). The other routine was already exact. All statements,
header, types, frames and options are unchanged. This is a measured priority
context, not a universal register-assignment rule. Evidence:
C23UNIT-source-proposal{2,3}, C23UNIT-control{1,2,3}, and their root reviews.

C27 measures scalar-valued versus Boolean-valued comma expressions. Moving
scanner work into the real loop condition leaves an extra eleven bytes when
the comma's right operand is itself `TOKEN_TYPE.kind != 0x67`: BCC creates
an AX 0/1 result and then tests it. Letting the comma yield the actual WORD
peek and comparing outside it, `(scan(), TOKEN_TYPE.kind) != 0x67`, restores
the original direct comparison and the complete 2,409-byte unit. The other
five functions, header, ABI and operations are unchanged. Source proposal3 /
control3 passes WHOLE and strict verification. C26's related scalar-index
comma removes the same Boolean overhead but does not recover its missing
EB00 loop-entry jump; control4 is decoded-identical to best control2. Do not
claim the C27 observation solves zero-displacement loop entries in general.

C28 measures two further contexts in a complete 2,052-byte unit. Genuine
nonboundary early returns in the BYTE I/O functions restore the original ES
reloads and read-return funnel; reversing the pure signed seek comparison
restores its operand order. The first nineteen functions then match. Its
one-based mode table is the real four slots at DS:6420, not an invented
array at DS:641E, which overlaps a different FAR pointer. Direct indexing
`slots[kind-1]` emits DEC instructions; a fixed-width integer-address spelling
expands further. The prebiased near-pointer expression `(slots-1)[kind]`
produces the original displacement and complete literal match. This is an
explicit Borland16 near-offset model, not portable ISO pointer-bounds
semantics: actual callers close kinds to 1..4, and every final dereference
is one of the four real slots. No storage, prototype, signedness, padding
or extra operation is introduced. Source proposal3/control3 passes WHOLE /
strict with all22 bodies and both real CS tables. Evidence packets retain
all failed contexts rather than treating effective-address equivalence as
a compiler-output guarantee.

C29's two complete controls distinguish BYTE predicate and declaration
contexts. Control1 emits 2,169 bytes against the original 2,167; six of eight
functions are literal exact. In 008C, `!X007_02(expression)` introduces
`MOV AH,0 / OR AX,AX` where the original tests AL directly. In proposal2,
`X007_02(expression)==0` retains the same zero predicate for all BYTE values.
The same proposal removes the type formal's explicit `register` hint while
retaining the expression local's hint, and moves 03B1's real scalar local
before its six-byte Location declaration. The final allocation uses the
original SI/DI roles and BP-2 scalar / BP-8 aggregate. The header, parameter
types, six previously exact bodies, operations and call order are unchanged.
Control2 reproduces all eight bodies and all 2,167 bytes, WHOLE / strict pass.
These are measurements of the combined source contexts, not an isolated
register-allocator experiment or a rule that unary NOT always widens: other
BYTE unary-NOT predicates in the same unit already matched and remain intact.
Evidence: C29UNIT-source-draft, source-proposal2, control1/control2 and their
literal object reviews. The genuine unused final BYTE formal still warns;
adding a dummy use would change the recovered program.
C29's pre-admission layout review also found that the draft token view totaled
17 bytes despite a 19-byte claim. Proposal3 corrects its unused payload from
8 to the original 10 bytes at offset9; the used kind/location offsets and
C source stay unchanged. Fresh control3 passes WHOLE / strict and all six
decoded object structures equal control2. This corrects the declaration and
supersedes the earlier total-size claim; the byte match alone could not
validate unused members of a struct.

C31 measures caller transport separately from callee byte consumption. Its
original 0069 pushes the actual INIT_OPTION WORD for two APIs whose complete
callees read only BP+6 BYTE and RETF2. Original 0154 uses MOV AX,1/2 for four
calls to X056_0E; that callee reads only BP+12 BYTE and RETF8. Canonical BYTE
prototypes instead produce MOV AL / PUSH AX. Control1 is 1,061 versus 1,066,
with three of five bodies literal exact. Scoped FAR Pascal WORD-transport
call views at only those six sites preserve the canonical BYTE declarations,
argument order, physical two-byte slots and consumed low bytes. Control2
matches all five bodies and all 1,066 bytes with 147 strict-verified fields.
The BC16 physical ABI scope is explicit; function-pointer prototype views
make no portable ISO type-compatibility claim. No upper-byte read or argument
address escape appears in the actual complete callees. The header, first
1,066-byte unit's genuine object layouts, other three bodies and runtime
helpers remain unchanged. See C31 source proposal2 and controls1/2; source
and native admission are separate from this measured producer observation.

C30 and C26 measure a distinct initialization/body context for a real scanner
loop. One-call do/for/condition spellings omitted the original two-byte entry
jump. Writing a genuine mandatory initial advance, followed by a pretested
while whose body advances again, preserves exactly one advance before each
query. Under these unit contexts the compiler shares the two lexical call
sites into one physical block and retains the original EB00 initialization
entry; the loop backedge reaches CALL directly. C30 source4/control4 is whole
1,043 bytes/all five functions/strict0; C26 source5/control5 is whole947/both
functions/strict0. Their original EB00+CALL bytes and canonical X02D_01 field
identity are independently pinned. C26 retains mask0800 then conditional
kind8 queries; C30 retains mask2000. No dummy operation, fake loop, goto,
prototype change or padding is used. This describes measured output, without
asserting an internal compiler-pass order or guaranteeing sharing elsewhere.

C33 source2/control2 reproduces all3,095 bytes,16 routines and62 CS data bytes
with six genuine structured contexts: the empty-parameter guard nests its
fallback check; the symbol helper has a failure-OR terminal name operation;
BYTE fallback negation is a leaf of OR; distinct member cases0/1 each perform
their real type load; languagecase1 performs its real default-text return;
and one type formal drops its register hint while name retains its hint.
Ten previously exact bodies and the header stay unchanged. Repeated terminal
operations are mutually exclusive, so source duplication adds no execution.
The actual reachable trampolines are helpercase0 05ED EB7A to0669, membercase0
08A3 EB00 to08A5, and languagecase1 0AF1 EB0E to0B01. Only the member jump has
zero displacement; a separate root-report wording erratum records this.

C32 source3 restores three real zero-return paths and distinct real size/
terminator tails; three of four functions and all92 CS bytes then match, with
one opcode still different at0216 (MOV AL versus original MOV AX). The full
1,568-byte candidate passes strict identity checks but is not a whole match.
An unsigned-short local probe and a standard register-hint local probe both
produce all six decoded object structures identical to source3. These are
bounded negative facts about this expression, not a claim that short/int or
register hints are globally ignored. Keep source3 and require new source
context evidence before further declaration/operator variants; never patch
the opcode or add a volatile/dummy operation to manufacture the word read.


### Measured C36 LONG updates and shared predicate returns (whole positive control4)

Whole CO024, 3925 bytes/11 routines, matches and strictly verifies under the unchanged BC20 profile in `work-in-progress/C36UNIT-control4`. Plain `target = target + size`, `count = count - 1`, and `*position = *position + X00A_03(type)` retain the original load/compute/store paths; the first-source compound-update forms chose shorter in-place operations. This is real arithmetic with unchanged types, variables and work, not instruction padding. All11 frames and242 field identities are exact.

For X02402, a positive child-kind scope with genuine failed-kind/class `return 0` operations and final false return reproduces the original shared true/false layout. The immediately preceding goto-to-false form threaded away two real2-byte jumps; a flat early-failure/final-true form moved the physical terminals. These are measured unit-specific control-flow facts, not permission for empty hops or a guard spelling sweep. All10 other exact bodies and H stayed identical across those last controls. The accepted native/full-executable gate remains a separate obligation from this local positive object result.

C40's actual switch-arm order matters independently of its key table. The
first whole draft emits 1,608/1,609 bytes and only three of four routines
match. Moving the existing case42, case3B and case48-to-case1 fallthrough
into their native physical order, and placing nested cases5/6 before1/2/3,
restores the original whole length but leaves a missing four-byte ES reload
and two extra short jumps. No operations or types change. A genuine failed
case36 break followed by its existing reversal work restores the selector
reload. Letting case9 fall through to the actual default recursion, instead
of duplicating those two calls and breaking, removes the two extra jumps.
Source3/control3 then matches all1,609 bytes, allfour frames and153 source
fields, including82 CS bytes; WHOLE and strict0 pass. This is a measured
source-context result, not a general rule that every conditional needs an
early break. Evidence: C40UNIT-source-proposal{2,3}, C40UNIT-control{1,2,3}
and their diagnostic reviews.

C39's best source4/control4 remains negative at4,434/4,432 bytes, with13 of14
routines exact and allframes correct. The only residual count guard uses a
JE plus short JMP instead of the original short JNE with displacement126.
An unchanged-source BCC-S capture, assembled unedited with the normal TASM
/ml /jNOSMART /m2 options, equals control4 in allsix decoded structures.
The captured compiler ASM already explicitly emits JE@@10 and JMP@12@1778;
whole-function instruction and branch-label mapping confirms their targets.
No additional assembler pass can be justified from this result, and no
internal compiler pass order is inferred. Preserve this HOLD and stop local
guard/goto/return variants absent new context evidence. Evidence:
C39UNIT-asm-stage-plan and C39UNIT-source34-object-independent-review.

C40's positive source3 still failed whole-image integration: its declarations
of the copied ten-byte object and raw-copy API pointer used long double,
which made BCC emit an unused __turboFloat external. No numerical floating
operation exists in this path; original X02316 body01AC copies all ten raw
bytes through B0F1. Source4 uses a genuine ten-byte BYTE-array storage struct
for the object and API pointer. It changes no C body, CODE/data/publics,
segments/groups or actual field identity, and removes only the unused marker.
Fresh control4 is WHOLE/strict0; an independent integration supplement
verifies every byte and the raw-storage contract. No runtime marker filter,
linker exemption or compiler option was added. Evidence: C40UNIT-source-
proposal4, C40UNIT-control4-review and C40UNIT-storage-integration-review.

C44's complete three-routine controls distinguish two genuine source contexts.
Source1's `do { read_token(); } while(kind==0x82 && --depth)` emits a loop
which is two bytes shorter than the original. Source2 assigns the real read
to a comma-expression while condition: the initial jump and decrement placement
return, but the compiler materializes the comparison as AX 0/1 before testing
it, leaving the loop eleven bytes too long. An ordinary `for(read_token();
kind==0x82; read_token()) { if(!--depth) break; }` in source4 preserves the
same dynamic reads/decrements and lets the compiler share the physical read
sequence naturally. That entire118-byte routine is literal exact. No generated
ASM was edited and no jump-only source block or artificial work was added.

The same unit's ordered `!word_state && !byte_state` in source3 restores
AL load/AH zero/OR AX where source1's `!(word_state || byte_state)` uses a
direct BYTE comparison. Both predicates read the same actual WORD/BYTE objects
in the same short-circuit order, and agree across their complete value domains.
The header, scalar widths and all aggregate layouts are unchanged. The whole
115-byte final routine becomes literal exact; its typed token assignment emits
SCOPY count19. Control4 matches all278 bytes and all36 actual source fields,
WHOLE/strict0, zero DATA/warnings. These are specific compiler observations,
not general Boolean-promotion or loop-optimization rules. Evidence packets:
C44UNIT-source-draft/source-proposal{2,3,4}, control{1,2,3,4} and their
independent complete byte/field reviews. Native PUBLIC spellings are permuted
relative to established canonical source names and must not replace them.

C39 is a positive isolated compiler-backend control after its source4 was
already semantically reviewed. Compile-via-TASM (-B) produced4434 bytes,
13/14 literal routines and an inverse short condition plus JMP at the last
near-limit count guard. Its captured, unedited ASM already contained that
form; TASM did not introduce it. Removing only-B on the same semantic C/H
restores original JNE+126 at0E78. All4432 bytes, all14 bodies/frames, all204
fields (42 foreign FAR32, one own-code FAR32,161 DSWORD), four PUBLICs and
fifteen dead JMP bytes are exact; WHOLE/strict pass. Source5 changes only
that directive. Evidence: C39UNIT-control5-direct, its complete byte review,
final-source-object/native peers and gate277. This is not a general backend
solution: C41's corresponding one-shot control8 shortens multiple original
operations and remains negative (46/54 bodies), while canonical6 is53/54.

C45 likewise supplies a bounded negative cross-check. Corrected source2 via
TASM gives4297/4295 bytes,31/33 literal routines/all frames. Accepted PATHLIB's
three explicit zero predicates change none of the six decoded structures.
One direct-backend control restores BCC_ADD_PATHS's native155-byte backedge,
but merges two actual PATH_PROCESS tails in main (-17) and a FFFF store in
0F11 (-6), leaving4272/4295. Changing only its size policy to-G, justified by
the actual standalone donor and the earlier paired C5 evidence, is broadly
negative:4282 bytes/24literal routines and no original16-byte sparse table.
Canonical2 remains held; these measurements do not authorize option sweeps
or artificial statements. Two inherited OPTLIB _AX self-assignments are absent
from the complete ordinary-C draft, but that whole unit is not yet accepted.

Whole C58 demonstrates three measured control-ownership effects, not a general
recipe for adding branches. Source1 compiles 1389/1415 bytes with ten literal
routines. Two genuine failed-exit scopes restore four original ES reloads
(source2:1405/134 fields), but leave four shortened conditional paths and a
missing case51 trampoline. Reusing the already matched ordinary-C clone's
actual early-return ownership in both recursive helpers restores the literal
1225-byte prefix. In the scanner, assigning the real node advance within
case51 before continue, while retaining the existing default/common advance,
executes that operation exactly once per continuing path. The compiler shares
the advance and preserves the original two-byte case51 jump naturally.
Source6/control6 then matches all1415 bytes, all13 frames, five near-Y pairs,
108 CS bytes and134 fields (6 FAR32,73 DSWORD,55 CSWORD), with strict0.
There are no GP uses, padding, self-assignments or additional dynamic actions.
Both the isolated TASM-backend and speed-policy probes remain negative;
source6 uses -mm -G- -O -Z -p -Y. Source1..6, controls1..6 and the independent
final/native/integration reviews preserve the positive and negative evidence.
Gate282 rebuilds all870 sources, retains the original executable and all1251
map addresses, and passes925 tests. This unit consolidates seven fragments;
it adds zero C-object coverage while removing their GP/no-op scaffolding.

C62 supplies a measured caller-transport observation while the whole source
remains unadmitted. Source1/control1 gives8731/8696 CODE,9/28 literal routines,
884 fields,26 exact allocations and all31 actual SS local producers. Its
truthful canonical declarations include X019_06(int,long,long) and a BYTE
fourth formal for X00F_11. Finite original resource/consumer audits establish
nine direct literal-DS first payloads as actual FAR strings consumed by %s,
and the one SCOPE_STATE call as a fullWORD caller transport whose callee uses
only the BYTE's low bits. Source2 changes only those scoped caller views,
two real parameter register hints and LIST_PARSE's condition-owned result
assignment; the global header and options stay byte-identical. Control2 is
8729 CODE,15/28 literal routines/3726 bytes, with all nine prior literals,
all884 semantic fields,31 SS contexts and14 aggregate helpers preserved.
All nine literal sites now emit native PUSH DS/MOV AX/PUSH AX, and the WORD
transport restores the whole232-byte member-relink routine. Both parameter
hints emit exactly the previous code after own-call rebasing: the earlier
C61 positive hint is not a general register-allocation rule. Returned LONG
payloads and ignored-format arguments were not repainted. These scoped
observations do not clear C62's remaining13 bodies or justify a global API
prototype change. Evidence: C62UNIT-source-proposal{1,2},control{1,2},closure,
and control{1,2}-review; no source admission or CODE coverage credit.

C63's source2 restores the already accepted TLAYOUT donor's explicit BYTE
bitfield result around(BYTEflags&3), keeping the same BYTE memory read and
all ten function bodies otherwise unchanged. The single change removes
four bytes from control1's widened value test and restores the whole556-byte
layout routine; seven routines/1863 bytes are then literal, with315 fields,
two six-key tables/24CS,82 balanced physical calls and12 SS sites preserved.
The unit still fails WHOLE/strict. A separate whole-file-r- only policy
probe, prompted by the old TLOOP donor's positive BP-local allocation,
is broadly negative on the new ordinary-C source:3227 bytes versus3131 for
source2, only three literal routines, and LOOP frame8 versus original6
(source2 gives4). It also changes other explicit-register source routines.
Thus the old GP donor did not predict whole-file ordinary-C register
behavior. No per-function flag mixing or duplicate SI assignment is
permitted by these observations. Evidence: C63UNIT-source-proposal{1,2,3},
control{1,2,3},the paired-emission review and finite protocol reviews;
source2 remains held at CHECK/LOOP/ENUM, with zero admission/coverage credit.

Further bounded C62/C63 controls extend the accepted C10 unary-NOT
observation without changing memory types or widths. In C62, replacing
three existing BYTE equality-to-zero conditions with logical NOT restores
two complete routines and the native widened tests (control5:8736 CODE,
17/28 literal routines). Sharing the value in STATE_6303=PARSE_FLAG2=0
restores the native MOV AL,0 followed by two separate BYTE stores, in the
original order; this is not evidence for a WORD store (control6:8734).
Logical NOT of the existing EXPR_FLAGS BYTE mask then restores the whole
453-byte X03F12 routine (control7:8739,18/28 literal routines/4767 bytes,
884 fields). Other27 bodies are unchanged by that last edit. In C63 the
same narrow mask-condition rewrite restores CHECK218, giving eight literal
routines/2081 bytes (control4:3135/3141,315 fields). The actual BYTE load,
zero extension and WORD test are inspected in each case. These are measured
condition-lowering observations, not permission to widen the memory read
or repaint global types. Both complete units still fail strict verification.

Control ownership and profile changes remain file-context dependent.
C62 source3 removes two diagnostic carriers and restores all28 allocations,
but duplicates real diagnostic tails under-G (8822 CODE/897 fields).
Changing only that whole-file policy to-G- produces8640/879, restoring two
routines while losing two others. It does not justify per-function option
mixing. C63's explicit continue immediately after the existing recovery
call emits all seven decoded OMF structures identical to source4; that
spelling family is closed. Its earlier repeated-predicate numeric check
was tautological and is explicitly disclaimed as independent validation.

C64 shows two negative representation controls. A near bank descriptor's
.segment view for the same existing WORD selector emits exactly the prior
seven OMF structures (control4 vs3). A BYTE-based 3+5/3+5 symbol-prefix
view keeps both actual flags6 reads as MOV AL,ES:[SI+6], but extracts with
AND AX,7 then zero-tests, adding two bytes at each predicate rather than
emitting native zero-extension/TEST AX,7. Neither missing ES reload returns.
Control5 is8685/8729 versus minimal control3's8681, with741 fields in both;
the equal956-byte SCOPE extent in control5 is not literal equality. Stop
both view families without new original context. The prefix layout audit
supports the logical fields and BYTE footprint, not a uniquely recovered
historical C base type. No canonical source is admitted by these controls.

C65 separates LONG truth lowering from the BYTE unary-NOT observations.
The complete ordinary source1 compiles2354/2364, with four literal routines
(684 bytes),297 fields and all six frame allocations exact. Its genuine
LONG callback test emits OR AX,DX, whereas native tests AX and DX through
two separate zero branches. Changing only that predicate to !call has no
emission effect. The callback remains LONG: the alternate provider uses a
real 32-bit multiply accumulator. A pointer-shaped result view is not
supported by this arithmetic evidence. No split helper, extra unused local,
or result-type repaint is justified by the remaining seven-byte producer.

C65 also gives a measured register-lifetime observation. Ordering the real
locals as wordsFAR/intrinsicFAR/token/name/symbol/extended restores the
native BP slots. Separate lexical register scopes restore each later loop's
SI/DI roles, but free SI during an earlier token loop, removing five native
spill/reload bytes (control2:2349). Reconstructing two real function-scope
indices, used as intrinsic parameter/ordinal and then independently
initialized keyword group/remaining count, retains those live register
reservations without extra operations. Together with !COMMENT_MODE, which
restores the original signed-BYTE MOV AL/CBW/OR AX sequence, control3
restores the entire1080-byte initializer and five literal routines/1764
bytes. Source2 also corrects a same-value chained-store order to63C0 then
6398; source1's earlier exact ordered-effect claim remains explicitly
qualified in an immutable erratum. No frozen source was edited.

One whole-file -G- to-G probe then restores the native separate cdecl
ADD SP,6 and short jump in C65's finalizer, adding two bytes. All five
protected routines remain literal: control4 is2357/2364,297 fields,
all six frames,three near-Y pairs,84 call protocols,11 keys/22CS and one
real196-byte aggregate copy. The LONG test still differs by seven bytes.
The initial policy came from accepted C31 shared-backend context, not
recovered C65 build metadata. This finite policy result closes that family;
it does not support a flag matrix or per-function mixing. C65 remains a
whole-unit HOLD with no recovery credit until strict/fresh integration.
Evidence: C65UNIT-source-proposal1..4,control1..4,root producer reviews,
independent original review and bounded source semantic supplements.

A final bounded C65 failure-edge ownership control is negative. Replacing
case2's compound condition with the two real rejection breaks preserves
one conditional LONG call and the fresh post-callback flag modification,
but still emits OR AX,DX. It adds a four-byte selector reload before the
argument push, so control5 is2361/2364 with298 fields: the seven-byte LONG
contraction remains, plus four nonnative bytes/one extra DS field. Five
protected routines stay literal. A smaller net size difference therefore
provides no producer gain; source4/control4 remains best. Close the
failure-ownership family without a new original compiler/type/storage fact.
Evidence: C65UNIT-source-proposal5,control5 and its root/worker reviews;
no partial credit or source admission.


### C67 whole-file policy and original value ownership (after checkpoint282)

The eleven-function C67 unit restores real WORD return chains, including
export results consumed by incoming callers. Canonical unsigned WORD
BE6D remains unsigned; two actual signed consumers cast through int before
LONG, matching the native CWD. It is not sound to relabel the shared API.
Positive member-body guards restore ES reuse in130/131, and declaration
order plus a zero-first member conditional restore all362 bytes of12C.
An initializer-to-first-assignment trial in134 emits all seven identical
OMF structures: it does not remove the nonnative DI parameter cache.

One whole-file -G to-G- trial restores the entire155-byte12F and keeps
seven literal routines/1620 bytes, but merges native-distinct actions in
12E. One subsequent whole-file -B trial restores all126 physical calls,
including exact per-owner identities and multiplicities, and changes only
12E (672 to693, native695). Seven literal routines and the other ten bodies
are unchanged. Best control6 is3160/3152 CODE,261 fields,11 exact frames,
five near-Y/six FAR bodies,8 CS table bytes and zero other DATA. These
observations close the policy/backend families; they do not justify a
flag matrix or per-function mixing. Both whole units remain unadmitted.
Evidence: C67UNIT-source-proposal1..6 and paired control1..6 reviews.

### C68 allocator arithmetic and scoped frontend observation

The native337-byte, five-export allocator unit has four literal routines
(194 bytes). BC20 emits an IMUL sequence for unsigned capacity*=2 under
the initial policy; capacity<<=1 restores the actual SHL without changing
the unsigned update. One whole-G speed observation restores two INC SP
instructions after the cdecl warning instead of POP CX. A faithful
separate capacity+=0x2000 statement and test removes an extra MOV AX,SI,
but still emits CMP SI,0x2000 where native ADD is followed directly by
JAE. Best plain-C control5 is341/337, five fields, five exact frames and
five genuine calls. No byte deletion or masked equality supplies clearance.

One whole-B trial moves the own FAR call's NOP from prefix to suffix and
loses a previously literal routine. One C++ -P frontend observation with
explicit extern C preserves code, fixups, segments, groups, EXT identities
and PUBLIC name-offset maps relative to C control4 (342 bytes), but the
ordered PUBLIC enumeration differs. It provides no code improvement;
both backend and frontend families close. A compiler dictionary occurrence
of _FLAGS establishes a name, not a preceding-expression arithmetic-carry
contract. No FLAGS source/control was created: reading ambient flags to
coordinate these arithmetic statements lacks the required source evidence.
Evidence: C68UNIT-controls1to4-independent-review,
source5-control5-frontend6-independent-review and
control6-flags-eligibility-independent-review. Full incoming and transitive
result-use closure supports the scoped source interfaces; it does not
supply byte identity or uniquely recover historical return declarations.


C67 source7's ordered separate zero stores and positive member-body
predicate restore all695 bytes of12E, including actual BYTE read/AND AL/
MOV AH,0/OR AX with retained ES. Eight routines/2315 bytes are now literal.
A single positive compound gate in132 then removes the redundant BX+ES
reload (seven bytes), but leaves597/native594: JE, shortened JMP and one
unreachable NOP instead of native single JNE displacement124. All other ten
bodies remain identical and every126-call owner multiset remains exact.
Source8/control8 is3155/3152,259 fields. The new dead NOP is explicit in
its review; it does not restore the dispatcher's original two dead bytes.

Unedited -S capture and default /ml assembly reproduce source8 CODE and
six decoded structures. Four external CODE address offsets differ LOC1
versus LOC5 in FIXUP records, so this is not all-seven-structure equality
or blanket offset-semantic clearance. The compiler assembly itself emits
JE @@7 / JMP @8@506: assembler multipass alone cannot combine those into
the native single conditional branch. No generated text was edited or
new assembler policy authorized. These are scoped producer facts, not a
new-source admission. Evidence: C67UNIT-control7-review,
control8-review and assembly-context8.


C67 source9's conditional WORD-value returns in dispatcher cases0/1
restore its complete142-byte interval, including the four-key8-byte CS
table, unthreaded return hops and the two genuine original dead JMP bytes.
All other ten bodies remain identical to control8. Nine routines/2457
bytes are now literal; control9 is3157/3152 with259 fields. Compiled dead
coverage is three bytes: the restored original two plus132's extra NOP.
Only132+3 and134+2 remain. This positive result comes from real selected
value ownership, with one BYTE predicate and exactly one WORD action arm;
no dummy local, unreachable break, padding or ASM was inserted. No whole
admission or new CODE credit follows from the private dispatcher match.
Evidence: C67UNIT-source-proposal9 and control9-review.


C67 source10 uses a type-only local opaque-prefix9/WORD-link view for
134's actual unconditional TYPE_POOL WORD+9 read and subsequent symbol
chain. It leaves formals, globals, H and all other ten bodies unchanged;
there is no allocated struct/pointer, stride, sizeof, hint or new action.
The default byte-packed field-base representation is eligible as a scoped
view, not a universal TYPE layout. Actual control10 has all seven decoded
object structures identical to control9, including every FIXUP record:
3157 CODE/259 fields. It does not remove the DI cache. Close this one
field-base family and retain source9; no new code credit. Evidence:
C67UNIT-source-proposal10,control10,control10-root-paired-review.


### Declared-register policy and real CODE-storage anchors (after282)

The BC20 User's Guide, chapter6 printed page173, distinguishes -rd from
-r-: -rd keeps persistent register variables only where explicitly declared;
-r- suppresses them and SI/DI preservation. -Z concerns cached copies across
indirect assignments. A single C67 whole-file -rd observation, with all S9
bodies/H/types/hints unchanged, restores its entire101-byte134. The other ten
bodies remain identical after actual placement normalization. Control11 is
3155/3152 CODE,259 fields,ten literal routines/2558 bytes,all126 stacks and
11 frames. Only132's explicit JE/JMP/unreachableNOP versus native JNE remains
(+3). This positive policy context does not establish historical flags or
license a register/flag matrix. Source11/control11/root paired review and
register-policy-independent-review retain the exact finite result.

The same primary guide, printed page177, documents far-object segment/class
names (-zE/-zF) and the CODE name (-zC). CO042's original single00 byte is
literal storage/overlay-anchor data, not a complete x86 instruction. A real
far unsigned-char object under -zECO042_TEXT -zFCODE first emits an empty
compiler TEXT segment, followed by one populated CODE far segment (attribute
104: PUBLIC/PARA, not PRIVATE; an immutable prose erratum corrects the early
control1 qualification). Explicit -zCCO042_TEXT lets the compiler unify its
CODE and far-object segment itself. Control2 emits one first CODE segment,
exact byte00,original X042_00 PUBLIC at0 and native byte/public attribute40;
zero fixups/masks/other initialized DATA,WHOLE1/strict0. No generated ASM,
object or segment-header patch was used. Source/storage/native/ordinary
integration review and a fresh image gate remain mandatory before admission.
Evidence: C70UNIT-source-proposal1/2,control1/2,native-preflight and
control1-attribute-erratum. These are C-object CODE storage bytes, not an
increase in the count of recovered executable routines.

The initial far-object output also exposed a generic checker bug: an empty
CODE declaration hid a later populated CODE segment. match.py compared the
empty segment; verify.py silently skipped the real PUBLIC, including wrong
known-address and outside-export cases. Two selection predicates now skip
zero-length CODE declarations. Five behavioral regressions reproduce four
old failures and all pass after the fix. All930 tests and a fresh870-source
full gate pass (113 seconds,33 compile),original502432-byte EXE and all1251
nonabsolute MAP addresses unchanged. No source admission followed from this
checker correction. C70UNIT-code-selection-fix holds before/after snapshots,
red replay, full-suite/full-gate logs and both MAPs.


One C67 whole-file-Tm2-only observation from unchanged S11 removes the
actual assembler-reserved NOP after its forward JMP. TASM reports two passes;
control12 is3154/3152,259 fields. All other ten bodies remain identical,
all10 literal anchors/2558 bytes and126 protocols preserved. Only132's
explicit BCC JE/JMP pair remains(+2),not the native singleJNE. Compiled
dead coverage now equals the original dispatcher2 bytes. The C7 donor
mechanism and finite eligibility are pinned; no NOSMART,register/body mixing
was added. The assembler-pass family closes; whole C67 remains unadmitted.

C69 complete51-body context exposes a target-language declarator hazard:
`extern char far *first,*second;` emits the second pointer as NEAR under
BC20 medium model. Actual native STATE1756 uses LES4B plus ES BYTE read;
control1 ignored its selector. Separate two FAR declarations restore the
real domain without casts or alias painting. Control2 changes only the
main body,all other50/all28 anchors unchanged; the old source1 interface
clearance is explicitly qualified in an immutable material erratum.

One whole-G to-G- observation on corrected C69 S2 restores all eight extra
foreign call sites in five shared-tail contexts. Control3 is7822/7789,767
fields,30 literal routines/2688 bytes (all prior28 retained). All51 frames,
44near-Y/7FAR,25 actual SS consumers,two COPY14 sites and4tables/28keys/56CS
verify. It over-shares one main near SKIP call (native3,compiled2),so total
calls242/native243 despite correct172foreign multiplicities. One extra
dead JMP in114 remains alongside the main's restored original2-byte dead
hop. Smaller aggregate size is not admission; whole G family closes.
Evidence: C69UNIT-control1/2/3-review,second-far-declarator-erratum and
source2-control2-independent-review. Root epoch rebase authenticates all41
old CODE contributions' decoded structures and PUBLIC order; raw hashes
differ only in107strictly parsed E8/E9DOS timestamp/comment checksums,
not CODE,relocations or other records.

One C69 whole-B-only observation on corrected S3 restores the missing
third main SKIP call. C4 has all243 original per-owner/endpoint identities,
all30 literal anchors/2688 bytes, all51 frames/25SS/COPY14x2/28keys56CS.
Only three bodies change: main gains12B,0F9 gains3B and114 gains1B.
Two new unreachable NOPs follow shortened forward EB7F jumps; the own
FAR call instead targets already-emitted main, encodes resolved PUSHCS/CALL
without NOP or fixup pair. C4 is7838/7789 and remains HOLD; restored call
identity, rather than aggregate size, selects it over C3. Whole G/B families
close. Evidence: C69UNIT-control4-review (673deacb manifest).

One C69 whole-Tm2-only observation on unchanged S4 removes exactly
the two specified unreachable NOPs, each after a shortened EB7F forward
jump. Independent actual decoding finds all other49 normalized bodies
unchanged and all51 instruction/target graphs equal after excluding just
those NOPs. All30 literal anchors/2688B,243 call identities,768 binding
identities/raw addends,7PUBLICs,25SS origins and4tables/28keys/56CS survive.
TASM reports two passes; C5 is7836/7789. Dead bytes fall6 to4: original
main hop2 and extra114 hop2 remain. No whole match or source credit;
assembler-pass family closes. Evidence: C69UNIT-control5-independent-review
(ee8a582a manifest), eligibility818b0e03.

C69's already accepted ordinary STAOV10B donor owns a compound farWORD
update and a separate meaningful14-byte advance/test. Replacing the full
draft's expanded RMW and expression-valued loop advance with that exact
action ownership restores all56 native bytes in whole-file S6/C6. Only
this body changes; allother50 normalized bodies equalC5. Actual31 literal
routines/2744B,243 call identities,768 field bindings,51 frames,25SS,
COPY14x2,28keys56CS and dead4 survive. C6 is7831/7789,20 routines still
nonliteral. The current accepted donor object was selected by actual
source owner and materialized independently; stale packed short identity
was rejected. No new CODE credit before whole-unit fidelity. Evidence:
C69UNIT-small-residual-source-audit and control6-review(fed062d8).

C69 STAOV102 has genuine initialization and loop-value ownership: native
zeros the two WORD traversal locals before copying LONG size to best,
and owns its next-node read as a statement followed by a plain test.
Source7 moves that real best assignment after the zeros and uses a real
advance label/plain node test, retaining all types/hints. The whole163B
body becomes literal, including native ES reload; one real STATE85C0
DS relocation is restored. Other50 bodies equalC6,all32 anchors/2907B,
243 calls,51 frames,25SS,COPY14x2,28keys56CS/dead4 preserved. C7 is
7833/7789 with769fields. Larger aggregate still yields a real body gain;
whole-unit HOLD remains. Evidence: STAOV102-ownership-diagnosis and
C69UNIT-control7-independent-review(a4ddee9d).

One C69 STAOV105 ordinary LONG-guard scope/error-status-return observation
restores only final ORAXAX after the real104 call,adding2B. Native JE+00
and earlier ES reload remain absent; nested scope alone did not restore
the reload. Other50 bodies/all32 anchors2907B,243 calls,769 bindings,
51 frames/25SS/COPY14x2/28keys56CS/dead4 remain unchanged. C8 is7835/7789
with105 still6B shorter. BestS8 retains this actual native-test gain, not
a new literal routine; the one scope/status family closes. No forced
JE/ES,emptyif or barrier follows. Evidence:C69UNIT-control8-review(e37a519c).

One C69 STAOV0F9 negative early-return guard restores the native signed
BYTE promotion exactly without carriers or type changes. C9 first13B and
selected body/epilogue equal native after+2 translation, but native JE7E
remains explicit JNE02/JMP7E. Sizes native146/C8 147/C9 148 distinguish
actual predicate gain from size. Other50 bodies/all32 anchors2907B,243
calls,769bindings,51frames/25SS/COPY14x2/28keys56CS/dead4 preserved.
No newNOP or literal routine; bestS9 retains native predicate ownership.
One BYTE early-exit observation closes, no local spelling/profile sweep.
Evidence:C69UNIT-control9-independent-review(13c1717c).

C69 reset donor S10 restores whole192B STAOV11B through seven real
valued assignment chains; current true H stays fixed. More importantly,
S1-S10 main had a semantic operand-domain defect: INPUT_STRING_PTR needs
the near address of its segment WORD, not that WORD's segment value.
Both operands have the same WORD width and symbolic DS binding, so field
identity/stack-width equality was insufficient. Canonical INSPTR and
ordinary C7UNIT use the existing unsigned WORD address transport. S11
changes only the caller to (unsigned int)&STATE_8524, producing native
MOV AX,8524/PUSH AX instead of PUSH WORD[8524]. Preceding STRING_COPY
correctly retains the segment VALUE. Actual provider/context/GETBYTE
dereferences prove the correction; old site-effect clearance is revoked
in the immutable929bca3c erratum. Corrected site only is cleared.

S12 explicit equality to zero for the real FAR-pointee BYTE restores
native LES/CMPBYTE; S13 does the same for later SEARCH_PATH[0], completing
all727B of main after owned placement normalization. S13 also prioritizes
the existing 2B entry iterator with a genuine register declaration and
chains spare/used zero writes, leaving limit separate. All844B of10D,
frame136, actual nodeDI/entrySI, and BX reload/store order become literal.
No hint or alias guarantee was assumed before the actual observation.

S14 BFD3 existing-two-consumer chains change only two CODE bytes in the
complete decoded object: SI stores become AX stores. All other structures,
ordered PUBs,769 fixups and otherDATA equalS13. Both near helper calls
precede MOVSIAX and fresh STATE85C0 bank reads/far-LHS computation. This
target ordering matters because allocation may change that bank segment;
no portable C operand-order rule is inferred. Whole164B becomes literal.

S15 removes the introduced previous-link cache in11C. Native direct WORD
test/re-read, BX/ES reuse and no SI save are reproduced; all179B are
literal. S16 reuses actual115 node formal as its mutable cursor with a
genuine register hint and direct selected-arm assignments, removing the
introduced current/ternary carrier. All72B become literal, frame0/RET2
and fullWORD return intact. Compact actual object equality preserves other
bodies/protocols; no unexecuted fresh all51 decoder is claimed. BestS16:
38 literal routines/5085B; whole7798/native7789 remains HOLD. All1922
potential native bytes remain uncredited. Evidence:C69UNIT-control10..16
review packets and their explicit semantic/metadata qualifications.

C69 S17 existing formal/cursor ownership restores all376B of116;114 keeps
its residual. S18 truthful terminal guard context restores607B of106.
S20 positive INCLUDE action ownership restores342B; it also corrects a
material hash-to-EOF rewind defect: original class1A isE7, SCAN_NEXT may
return1A, and the native EOF arm does not decrement INPUT_INDEX. The
1536 bounded actual-instruction trace review is site-specific, not whole
runtime proof. S20 comma-loop in110 was negative; S21 rolls it back only.

S22 three char FAR payload+1 expressions generate offset INCAX before
selector-memory PUSH, unlike the prior MK_FP(segment,offset+1) sequence.
Actual object windows prove16bit offset-only increment/no segment carry,
not a portable operand-order guarantee. Whole101190 and10C193 become
native literal; all other49 normalized bodies and769 binding identities
are preserved. Best43/51literal6793; whole7799/native7789 remains HOLD.
No pointer local/spill, profile change, new type or alias assumption was
used. Evidence:C69UNIT-control17/18/20/21/22 review packets; historical
source1..18 EOF-effect clearance revoked atthat site by dedicated erratum.

C69 S24 exact merge retains all threeS22 payload expressions and tests
the existing SKIP node formal lifetime, positive NEXT action and shared
exit together. ActualSKIP85→77/native75; nodeSI/depthDI and ES retention
between NEXT test/read become native. One genuine redundant STATE8524
DS reload disappears. UnsignedDEC still materializes via MOVAXDI/ORAX
thenJE versus nativeDECflags JNE/JMP; remaining+2 is not solved and no
signed repaint or operator/label/profile sweep follows. All other50 bodies
and43anchors6793 fixed; whole7791/native7789 stillHOLD. Evidence:
C69UNIT-control24-independent-review821d7518; finite65536depthzero check
and192 action partitions are not arbitrary-runtime oracle claims.

C71 storage observation (control1): a private mutable FAR aggregate with
393 unsigned WORD relative offsets and357 fixed char string fields emits
all13406 original diagnostic-resource bytes, warning-free, with no PUBLICs,
fixups or other initialized DATA. Every table entry uses offsetof (or zero);
the one interior suffix uses field160+15. A sizeof13406 check rejects
aggregate padding. Actual bytes, not sizeof alone, verify every field.
The C70 storage profile -mm -zCCO026_TEXT -zECO026_TEXT -zFCODE yields
one populated CO026_TEXT/CODE segment for this initialized static object;
the compiler retains it despite no in-file reader. No forced reference,
new export or object normalization is needed. This is one measured case,
not a general guarantee for all unused static data or profiles.

The existing BCC_MESSAGE_IMAGE address carrier names the loader descriptor,
not this loaded-image payload. Its __SEGTABLE__ bridge and getter remain
unchanged. Resource offsets preserve duplicate and interior-string sharing;
zero offsets remain loadedsegment:0, and no index clipping is introduced.
Header-only target rows express zero executable routines, while whole
segment match and strict0 verify all resource storage. C71 contributes
13406 initialized CODE-class storage bytes, zero new executable functions.
Evidence:C71UNIT resource inventory/consumer/source1/control1/native3
packets and corrected integration-plan2; plan1 inventedpublic1 rejected.

C76's whole25-function CO001 context distinguishes automatic register caching
from documented declared-only -rd. Default direct source emits23 native bodies
but caches two once-used WORD getter formals in SI. One coherent -rd model
with precisely20 additional genuine register objects (22 total) preserves all
23 native bodies and removes those two caches. Far-BYTE-base indexing then
loads ES before BX; restoring the original TB integer-offset owner under the
same whole-rd policy restores native BX-before-ES. S5/C5 matches all1331 native
bytes and81 positively bound fields/25 exports without ASM, object patches,
fake uses or per-function option mixing. The primary -r- switch instead
suppresses persistent register variables, including explicit declarations;
it was not substituted for -rd. These are measured file/context results, not
a historical-policy or universal scheduling/allocator claim. Native/source
domain qualifications and full image integration gates remain mandatory.
Evidence:C76UNIT-register-policy-eligibility,declared-model-scope-qualification,
source4/source5 peers,control4 andcontrol5 final independent review.

C77 whole CO002 uses the retained measured BC20 direct -mm -G -O -Z -p -Y profile.
The initial complete source matches21 of25 native functions, all25 offsets and
sizes. Three real returned conditional WORD call values restore their shared
value exit relay; explicit register priority for the existing symbol formal
and previous-stream local leaves real storage in its native stack home.
S2/C2 matches all1580 native bytes/25 exports/174 positively bound fields,
without extra objects, dummy uses, ASM, profile changes or per-function options.
These are measured whole-context results, not universal allocator or historical
keyword claims. Shared three-WORD Record6 retains the genuine unused third
member; normalizing only C11FIND/C13UNIT headers preserves all seven decoded
object structures of both accepted callers. Domain/ordering qualifications
and fresh full-image integration gates remain mandatory.

### C78 complete expression-dispatch owner reconstruction

The complete seven-function CO003 source uses the retained measured whole-file
-mm -G- -O -Z -p -Y -B -TjNOSMART -Tm2 context. Direct speed/size controls
isolated warning cleanup and action sharing; the emitted-assembly path restored
all68 physical calls. Branch-owned selected calls remove three used source phi
carriers while preserving original guarded reads and native frame10/frame6.
An existing lower-value initializer plus a conditional call-or-zero assignment
and exact BYTE-mask zero predicates restore its native value join and tests.
A genuine failed-value early return restores the conversion guard phase.
Explicit priority on the existing secondary-tree owner, with first ordinary,
then reproduces the native relative cache owners without dummy uses or options
mixing. S8/C8 positively materializes all2477 original bytes:2329 instructions
and148 real CS bytes/74keys,all7 functions/3PUBLICs/247bindings/68calls,
four ordinary forward FAR prefixNOPs and the real architectural_SP guard.
These are measured whole-context results, not unique historical source/flags
or universal optimizer/ranking rules. Covered alias/malformed/initialization,
LONG/BYTE and accepted INT-caller versus BYTE-provider qualifications remain;
fresh ordinary merge,full-image/entireMAP/namespace/tests gates are mandatory.
