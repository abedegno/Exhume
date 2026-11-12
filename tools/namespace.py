"""Pure namespace preflight extracted from verify.update; private until reviewed."""
from typing import NamedTuple
from symbol_address import format_address


class NamespaceResult(NamedTuple):
    known: tuple
    labels: tuple
    problems: tuple
    missing_address: str | None = None


def validate_namespace(current, references, publics):
    """Validate immutable snapshots; return immutable merged names and diagnostics.

    current: (name,address_text,label) tuples from one load_symbols snapshot.
    references: (name,address_tuple,field_offset) tuples after ordinary inference.
    publics: (name,address_tuple) tuples after placement.
    No disk access, callback, configuration lookup or symbol write occurs here.
    """
    known = {name: value for name, value, _ in current}
    labels = {name: label for name, _, label in current}
    syms = {name: (value, where) for name, value, where in references}
    problems = []
    for name, (value, _) in syms.items():
        address = format_address(value)
        if name in known and known[name] != address:
            problems.append(f'{name}: symbols.tsv has {known[name]}, this file gives {address}')
        known[name] = address
    for name, value in publics:
        address = format_address(value)
        if name in known and known[name] != address:
            problems.append(f'{name}: symbols.tsv has {known[name]}, but this file defines it at {address}')
        if name not in syms:
            syms[name] = (value, None)
        known[name] = address
    byaddr = {}
    for name, address in known.items():
        byaddr.setdefault(address, []).append(name)
    for name, (value, _) in syms.items():
        address = format_address(value)
        # The legacy updater raises KeyError here if a conflicting PUBLIC
        # replaced this extern's only address. Retain its partial diagnostics
        # and explicit failure so the wrapper can reproduce that behavior.
        if address not in byaddr:
            return NamespaceResult(tuple(known.items()), tuple(labels.items()),
                                   tuple(problems), address)
        others = [other for other in byaddr[address] if other != name]
        if others:
            problems.append(f'{name} is at {address}, which symbols.tsv already calls {", ".join(others)}')
    return NamespaceResult(tuple(known.items()), tuple(labels.items()), tuple(problems))
