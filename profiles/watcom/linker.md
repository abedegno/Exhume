# OMF32 to LE: link evidence and limits

This profile's object and image tools support a measured OMF32/LE subset.
LE is the supported executable format; LX support is not implied. The parser
and placement verifier report unsupported records or fixup forms explicitly.
See their tests and code for the current bounds rather than assuming every
Watcom object or linker output is supported.

Object verification must compare complete declared code, constants, initialized
data, BSS extents and symbolic fixups. Masked opcodes alone cannot establish
the identity of a callee. Local publics and local externals remain scoped to
their module; equal private names in different objects are not shared anchors.
Group/frame semantics must be proved as well as target addresses.

WLINK invocation, response files, library members and their ordering, extender
binding, vendor prefixes and full executable identity belong to the project.
The reusable runner supplies DOS batch execution, not an invented universal
link command. Record exact executable hashes and build options in the project
manifest. A matching placed module, a rebuilt owned-source portion and an exact
full-file target match are separate results. Supplied libraries or executable
prefixes remain dependencies; parsing or linking them adds no recovered-source
credit.

Historical compiler libraries may include metadata records beyond the measured
subset. A rejected library member is an unsupported semantic case until
separately implemented and tested; silently skipping records is not proof.
