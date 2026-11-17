"""Process-issued PUBLIC DATA origin evidence, without filesystem access.

Only validated replay producers call the private issuer. Serialized reports
are receipts, never inputs granting authority. This is an internal correctness
contract, not protection against arbitrary Python replacing private code.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import NamedTuple
from weakref import WeakKeyDictionary


@dataclass(eq=False, frozen=True, init=False)
class LogicalSegmentOrigin:
    __slots__ = ('__weakref__',)

    def __init__(self):
        raise TypeError('logical origins are issued only by validated replay')

    def __copy__(self):
        raise TypeError('issued contexts cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('issued contexts cannot be copied')

    def __reduce_ex__(self, protocol):
        raise TypeError('issued contexts cannot be serialized')


class _Payload(NamedTuple):
    target_sha256: str
    object_number: int
    origin: int
    identity: tuple
    evidence_sha256: str


_ISSUED = WeakKeyDictionary()
SCOPE = ('Candidate combined PUBLIC DATA layout only; not complete historical '
         'link order, executable identity or source contribution credit.')


def segment_identity(module, number):
    """Exact raw names and one named membership; alignment is per contribution."""
    segment = module._segment(number)
    if (module.format != 'standard-omf' or segment.class_name != 'DATA' or
            segment.access is not None or (segment.attributes & 31) != 9):
        raise ValueError('logical origin requires measured standard use32 PUBLIC2 DATA')
    memberships = [(name, members) for name, members in module.groups if number in members]
    if (len(memberships) != 1 or not memberships[0][0] or
            memberships[0][1].count(number) != 1 or
            sum(name == memberships[0][0] for name,_ in module.groups) != 1):
        raise ValueError('logical origin requires unambiguous named group membership')
    return (segment.name, segment.class_name, memberships[0][0], 'use32', 'PUBLIC2')


def _issue_verified_origin(image, origin, identity, evidence):
    """Private boundary: caller has replayed an independent closed witness."""
    if (not isinstance(origin, tuple) or len(origin) != 2 or
            any(type(value) is not int for value in origin)):
        raise ValueError('invalid logical origin integers')
    obj, start = origin
    if start < 0 or start >= image._object(obj).size:
        raise ValueError('logical origin outside target object')
    if (not isinstance(identity, tuple) or len(identity) != 5 or
            any(type(value) is not str or not value for value in identity) or
            identity[1] != 'DATA' or identity[3:] != ('use32', 'PUBLIC2')):
        raise ValueError('invalid logical segment identity')
    encoded = json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False)
    payload = _Payload(hashlib.sha256(image.data).hexdigest(), obj, start, identity,
                       hashlib.sha256(encoded.encode()).hexdigest())
    context = object.__new__(LogicalSegmentOrigin)
    _ISSUED[context] = payload
    return context


def bind_logical_origins(module, image, placements, contexts):
    """Return matched segment origins and auditable receipts; no file reads."""
    if contexts is None:
        return {}, []
    if not isinstance(contexts, (tuple, list)):
        raise ValueError('logical origins require a sequence of issued contexts')
    if not contexts:
        return {}, []
    target_sha = hashlib.sha256(image.data).hexdigest()
    origins, receipts, seen = {}, [], set()
    for context in contexts:
        if type(context) is not LogicalSegmentOrigin or context not in _ISSUED:
            raise ValueError('unissued logical origin context')
        if context in seen:
            raise ValueError('duplicate logical origin context')
        seen.add(context)
        payload = _ISSUED[context]
        if target_sha != payload.target_sha256:
            raise ValueError('logical origin target identity mismatch')
        matches = []
        for number, segment in enumerate(module.segments, 1):
            if not segment.size:
                continue
            try:
                identity = segment_identity(module, number)
            except ValueError:
                continue
            if identity == payload.identity:
                matches.append(number)
        if len(matches) != 1:
            raise ValueError('extraneous or ambiguous logical origin segment identity')
        number = matches[0]
        if number in origins:
            raise ValueError('duplicate logical origin for segment')
        if any(key == number and type(key) is not int for key in placements):
            raise ValueError('logical origin requires exact segment index integers')
        position = placements.get(number)
        if (not isinstance(position, (tuple,list)) or len(position) != 2 or
                any(type(value) is not int for value in position)):
            raise ValueError('logical origin requires exact placement integers')
        obj, start = position
        if obj != payload.object_number or not 0 <= payload.origin <= start < image._object(obj).size:
            raise ValueError('logical origin outside contribution object/range')
        origins[number] = payload.origin
        receipts.append(dict(source_segment=number, origin=[obj,payload.origin],
            segment_identity=list(payload.identity), target_sha256=payload.target_sha256,
            evidence_sha256=payload.evidence_sha256, scope=SCOPE))
    return origins, receipts
