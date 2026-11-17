"""Synthetic OMF/LE range, module, binding, and composite proofs."""
from pathlib import Path
import sys
import struct
import unittest
from copy import deepcopy
from dataclasses import asdict, replace
sys.path[:0] = [str(Path(__file__).resolve().parents[1]/"tools"), str(Path(__file__).resolve().parent)]
from le import LEImage
from omf32 import Module, Segment, Fixup, OMFError
from test_le import fixture, with_fixups
from matching import is_alignment_padding, match_function, compare_range, MatchError, validate_group_frames
from placements import verify_module, ModuleError, external_anchor_bindings, infer_bindings, infer_data_bindings, BindingError, verify_composite


class FunctionMatchTests(unittest.TestCase):
    def test_accepts_observed_486_zero_effect_alignment(self):
        self.assertTrue(is_alignment_padding(bytes.fromhex('8d442000')))
        self.assertTrue(is_alignment_padding(bytes.fromhex('8d80000000008d5200')))
        self.assertTrue(is_alignment_padding(bytes.fromhex('8d80000000008d92000000008bc0')))
        self.assertFalse(is_alignment_padding(bytes.fromhex('8d442001')))
        self.assertFalse(is_alignment_padding(bytes.fromhex('b800000000')))

    def fixture(self):
        image=LEImage.parse(with_fixups(b'\x07\x00\x01\x00\x01\x18\x00'))
        data=bytearray(image.data)
        data[0x440:0x446]=b'\xa1\x18\0\0\0\xc3'
        image=LEImage.parse(bytes(data))
        module=Module(name='test',segments=[Segment('_TEXT','CODE',6,0x69,bytearray(b'\xa1\0\0\0\0\xc3'))],
                      publics={'read_':(1,0)},externals=['_value'],groups=[('FLAT',())],
                      fixups=[Fixup(1,1,4,9,False,1,1,2,1,0)])
        return module,image

    def test_resolves_symbol_and_verifies_le_fixup(self):
        module,image=self.fixture()
        r=match_function(module,'read_',image,1,0,6,{'_value':(1,24)})
        self.assertTrue(r['match'])
        self.assertEqual(r['verified_fixups'],1)
        self.assertEqual(r['compared_bytes'],6)

    def test_accepts_explicit_frame_identical_to_target_index_only(self):
        module,image=self.fixture()
        original=module.fixups[0]
        module.fixups[0]=replace(original,frame_method=2,frame=1)
        self.assertTrue(match_function(module,'read_',image,1,0,6,
                                       {'_value':(1,24)})['match'])
        module.segments.append(Segment('_DATA','DATA',4,0xa9,bytearray(4)))
        module.externals.append('_other')
        module.fixups[0]=replace(original,frame_method=0,frame=2,target_method=0,target=2)
        self.assertTrue(match_function(module,'read_',image,1,0,6,{},
                                       {2:(1,24)})['match'])
        for fix in [replace(original,frame_method=2,frame=2),
                    replace(original,frame_method=0,frame=1),
                    replace(module.fixups[0],frame=1)]:
            module.fixups[0]=fix
            with self.subTest(frame=fix),self.assertRaisesRegex(MatchError,'frame'):
                match_function(module,'read_',image,1,0,6,{'_value':(1,24)},
                               {2:(1,24)})

    def test_wrong_symbol_target_is_not_hidden_by_mask(self):
        module,image=self.fixture()
        with self.assertRaisesRegex(MatchError,'target'):
            match_function(module,'read_',image,1,0,6,{'_value':(1,25)})

    def test_unaccounted_target_fixup_fails_even_when_bytes_match(self):
        module,image=self.fixture()
        module.fixups=[]
        module.segments[0].data[:]=b'\xa1\x18\0\0\0\xc3'
        with self.assertRaisesRegex(MatchError,'unaccounted'):
            match_function(module,'read_',image,1,0,6,{})

    def test_real_opcode_difference_is_reported(self):
        module,image=self.fixture()
        module.segments[0].data[0]=0xa3
        r=match_function(module,'read_',image,1,0,6,{'_value':(1,24)})
        self.assertFalse(r['match'])
        self.assertEqual(r['differing_offsets'],[0])

    def test_relative_call_resolved_without_loader_fixup(self):
        module,image=self.fixture()
        data=fixture();data[0x440:0x446]=b'\xe8\x13\0\0\0\xc3'
        image=LEImage.parse(bytes(data))
        module.segments[0].data[:]=b'\xe8\0\0\0\0\xc3'
        module.fixups[0]=Fixup(1,1,4,9,True,1,1,2,1,0)
        r=match_function(module,'read_',image,1,0,6,{'_value':(1,24)})
        self.assertTrue(r['match'])

    def test_matching_prefix_does_not_prove_complete_function(self):
        module,image=self.fixture()
        module.publics['next_']=(1,5)
        with self.assertRaisesRegex(MatchError,'extent'):
            match_function(module,'read_',image,1,0,1,{})

    def test_range_cannot_cross_next_public(self):
        module,image=self.fixture()
        module.publics['next_']=(1,5)
        with self.assertRaisesRegex(MatchError,'extent'):
            match_function(module,'read_',image,1,0,6,{'_value':(1,24)})

    def test_negative_addend_wraps_as_32_bit_relocation(self):
        module,image=self.fixture()
        data=fixture();data[0x440:0x446]=b'\xe8\x07\0\0\0\xc3'
        image=LEImage.parse(bytes(data))
        module.segments[0].data[:]=b'\xe8\xfc\xff\xff\xff\xc3'
        module.fixups[0]=Fixup(1,1,4,9,True,1,1,2,1,0)
        self.assertTrue(match_function(module,'read_',image,1,0,6,{'_value':(1,16)})['match'])

    def test_duplicate_fixup_rejected(self):
        module,image=self.fixture();module.fixups.append(module.fixups[0])
        with self.assertRaisesRegex(MatchError,'overlap'):
            match_function(module,'read_',image,1,0,6,{'_value':(1,24)})



class ModuleProofTests(unittest.TestCase):
    def fixture(self):
        data=bytearray(with_fixups(b'\x07\0\x01\0\x01\x08\0'))
        data[0x440:0x446]=b'\xa1\x08\0\0\0\xc3'
        data[0x448:0x44c]=b'ABCD'
        image=LEImage.parse(bytes(data))
        mod=Module(name='test',segments=[
            Segment('_TEXT','CODE',6,0x69,bytearray(b'\xa1\0\0\0\0\xc3')),
            Segment('_DATA','DATA',4,0xa9,bytearray(b'ABCD')),
            Segment('_BSS','BSS',4,0xa9,bytearray(4))],
            publics={'read_':(1,0),'_value':(2,0),'_buffer':(3,0)},
            externals=['_value'],groups=[('FLAT',())],
            fixups=[Fixup(1,1,4,9,False,1,1,2,1,0)])
        return mod,image,{1:(1,0),2:(1,8),3:(1,24)}

    def test_accounts_for_all_code_data_and_bss(self):
        mod,image,places=self.fixture()
        report=verify_module(mod,image,places,{})
        self.assertTrue(report['verified'])
        self.assertEqual(report['initialized_bytes'],10)
        self.assertEqual(report['zero_fill_bytes'],4)
        self.assertEqual(report['verified_fixups'],1)

    def test_private_symbols_still_proved_but_not_cross_module_anchors(self):
        mod,image,places=self.fixture()
        mod.local_publics={'_buffer'}
        mod.externals.append('private_reference')
        mod.local_externals={2}
        report=verify_module(mod,image,places,{'private_reference':(1,0)})
        self.assertEqual(report['local_symbols'],{'_buffer':(1,24),'private_reference':(1,0)})
        shared=external_anchor_bindings(report)
        self.assertNotIn('_buffer',shared)
        self.assertNotIn('private_reference',shared)
        self.assertEqual(shared['_value'],(1,8))
        self.assertEqual(report['zero_fill_bytes'],4)
        damaged=dict(report);damaged['local_symbols']={'_buffer':(1,25)}
        with self.assertRaisesRegex(ValueError,'scope'):
            external_anchor_bindings(damaged)
        damaged=dict(report);del damaged['local_symbols']
        with self.assertRaisesRegex(ValueError,'scope'):
            external_anchor_bindings(damaged)

    def test_changed_initialized_data_fails(self):
        mod,image,places=self.fixture();mod.segments[1].data[0]=0
        with self.assertRaises(ModuleError):verify_module(mod,image,places,{})

    def test_unplaced_nonempty_segment_fails(self):
        mod,image,places=self.fixture();del places[3]
        with self.assertRaises(ModuleError):verify_module(mod,image,places,{})

    def test_nonzero_target_bss_fails(self):
        mod,image,places=self.fixture();places[3]=(1,12)
        with self.assertRaises(ModuleError):verify_module(mod,image,places,{})

    def test_conflicting_public_binding_fails(self):
        mod,image,places=self.fixture()
        with self.assertRaises(ModuleError):verify_module(mod,image,places,{'_value':(1,9)})

    def test_overlapping_segment_placements_fail(self):
        mod,image,places=self.fixture();places[3]=(1,8)
        with self.assertRaises(ModuleError):verify_module(mod,image,places,{})

    def test_file_backed_zeros_do_not_prove_bss_ownership(self):
        mod,image,places=self.fixture()
        raw=bytearray(image.data);raw[0x44c:0x450]=bytes(4)
        image=LEImage.parse(bytes(raw));places[3]=(1,12)
        with self.assertRaisesRegex(ModuleError,'backing'):
            verify_module(mod,image,places,{})

    def test_segment_alignment_is_enforced(self):
        mod,image,places=self.fixture();places[3]=(1,21)
        with self.assertRaisesRegex(ModuleError,'alignment'):
            verify_module(mod,image,places,{})


class BindingTests(unittest.TestCase):
    def test_infers_data_placement_from_relocated_public(self):
        mod,image,_=ModuleProofTests().fixture()
        report=infer_bindings(mod,image,1,0)
        self.assertEqual(report['symbols']['_value'],(1,8))
        self.assertEqual(report['placements'],{1:(1,0),2:(1,8)})
        self.assertEqual(report['unplaced_segments'],[3])

    def test_disagreement_with_known_symbol_is_failure(self):
        mod,image,_=ModuleProofTests().fixture()
        with self.assertRaises(BindingError):
            infer_bindings(mod,image,1,0,known_symbols={'_value':(1,9)})


class CompositeTests(unittest.TestCase):
    def setUp(self):
        raw = bytearray(with_fixups(b'\x07\0\x01\0\x01\x08\0'))
        raw[0x440:0x446] = b'\xa1\x08\0\0\0\xc3'
        raw[0x448:0x44c] = b'ABCD'
        self.image = LEImage.parse(bytes(raw))
        self.modules = {
            'reader': Module(name='reader', segments=[
                Segment('_TEXT', 'CODE', 6, 0x69,
                        bytearray(b'\xa1\0\0\0\0\xc3'))],
                publics={'read_': (1, 0)}, externals=['_value'],
                groups=[('FLAT', ())],
                fixups=[Fixup(1, 1, 4, 9, False, 1, 1, 2, 1, 0)]),
            'storage': Module(name='storage', segments=[
                Segment('_DATA', 'DATA', 4, 0xa9, bytearray(b'ABCD')),
                Segment('_BSS', 'BSS', 4, 0xa9, bytearray(4))],
                publics={'_value': (1, 0), '_buffer': (2, 0)})}
        self.placements = {'reader': {1: (1, 0)},
                           'storage': {1: (1, 8), 2: (1, 24)}}
        self.expected = [(1, 0, 6, 'initialized'),
                         (1, 8, 4, 'initialized'), (1, 24, 4, 'BSS')]

    def prove(self, **kwargs):
        self.assertIsNotNone(verify_composite, 'complete composite gate missing')
        args = dict(modules=self.modules, image=self.image,
                    placements=self.placements, symbols={},
                    expected_ranges=self.expected)
        args.update(kwargs)
        return verify_composite(**args)

    def test_cross_object_pointer_uses_actual_export_and_accounts_every_segment(self):
        proof = self.prove()
        self.assertTrue(proof['verified'])
        self.assertEqual((proof['initialized_bytes'], proof['zero_fill_bytes'],
                          proof['verified_fixups']), (10, 4, 1))
        self.assertEqual(proof['publics']['_value'], (1, 8))
        self.assertEqual(set(proof['components']), {'reader', 'storage'})
        self.assertFalse(proof['full_executable_rebuilt'])

    def test_missing_extra_and_wrong_kind_obligations_reject(self):
        for expected in (self.expected[:-1],
                         self.expected + [(1, 28, 1, 'BSS')],
                         [*self.expected[:-1], (1, 24, 4, 'initialized')],
                         [(1, 0, 12, 'initialized'), self.expected[-1]]):
            with self.subTest(expected=expected), self.assertRaises(ModuleError):
                self.prove(expected_ranges=expected)

    def test_duplicate_export_rejects_before_binding_or_byte_proof(self):
        self.modules['storage'].publics['read_'] = (1, 0)
        with self.assertRaisesRegex(ModuleError, 'duplicate.*public|duplicate.*export'):
            self.prove()

    def test_conflicting_external_anchor_cannot_override_actual_export(self):
        with self.assertRaisesRegex(ModuleError, 'conflict'):
            self.prove(symbols={'_value': (1, 9)})

    def test_private_same_spelling_keeps_independent_scope(self):
        for module in self.modules.values():
            module.publics['private_scratch'] = (1, 0)
            module.local_publics.add('private_scratch')
        proof = self.prove()
        self.assertNotIn('private_scratch', proof['publics'])
        self.assertEqual(proof['components']['reader']['local_symbols'],
                         {'private_scratch': (1, 0)})
        self.assertEqual(proof['components']['storage']['local_symbols'],
                         {'private_scratch': (1, 8)})

    def test_private_export_cannot_resolve_another_objects_external(self):
        self.modules['storage'].local_publics.add('_value')
        with self.assertRaises(ModuleError):
            self.prove()

    def test_private_name_in_external_anchor_map_cannot_leak_between_objects(self):
        self.modules['storage'].local_publics.add('_value')
        with self.assertRaisesRegex(ModuleError, 'private|local'):
            self.prove(symbols={'_value': (1, 8)})

    def test_unresolved_local_external_does_not_inherit_global_binding(self):
        self.modules['reader'].local_externals.add(1)
        with self.assertRaisesRegex(ModuleError, 'local|private'):
            self.prove()

    def test_global_external_cannot_be_redirected_to_own_same_named_local(self):
        reader = self.modules['reader']
        reader.publics['_value'] = (1, 0)
        reader.local_publics.add('_value')
        # Make the illegal local binding match the pointer to expose scope
        # laundering independently of ordinary relocated-byte rejection.
        raw = bytearray(with_fixups(b'\x07\0\x01\0\x01\0\0'))
        raw[0x440:0x446] = b'\xa1\0\0\0\0\xc3'
        raw[0x448:0x44c] = b'ABCD'
        self.image = LEImage.parse(bytes(raw))
        with self.assertRaisesRegex(ModuleError, 'ambiguous|scope'):
            self.prove()

    def test_same_spelling_global_and_local_external_indices_reject(self):
        reader = self.modules['reader']
        reader.externals.append('_value')
        reader.local_externals.add(2)
        reader.publics['_value'] = (1, 0)
        reader.local_publics.add('_value')
        raw = bytearray(with_fixups(b'\x07\0\x01\0\x01\0\0'))
        raw[0x440:0x446] = b'\xa1\0\0\0\0\xc3'
        raw[0x448:0x44c] = b'ABCD'
        self.image = LEImage.parse(bytes(raw))
        with self.assertRaisesRegex(ModuleError, 'ambiguous|scope'):
            self.prove()

    def test_overlapping_producers_reject_before_proof(self):
        self.placements['storage'][1] = (1, 0)
        with self.assertRaisesRegex(ModuleError, 'overlap'):
            self.prove()

    def test_unplaced_or_extra_placement_reject(self):
        for changes in ({}, {1: (1, 8), 2: (1, 24), 99: (1, 0)}):
            with self.subTest(changes=changes), self.assertRaises(ModuleError):
                self.prove(placements={**self.placements, 'storage': changes})

    def test_numeric_coercion_and_duplicate_normalized_indices_reject(self):
        for places in ({1.5: (1, 0)}, {True: (1, 0)},
                       {1: (1, 0), '1': (1, 0)}, {1: (1, 0.5)}):
            with self.subTest(places=places), self.assertRaises(ModuleError):
                self.prove(placements={**self.placements, 'reader': places})

    def test_bad_tail_and_fixup_cannot_hide_in_another_proof(self):
        for damage in ('tail', 'pointer', 'addend'):
            modules = deepcopy(self.modules)
            if damage == 'tail':
                modules['storage'].segments[0].data[-1] ^= 1
            elif damage == 'pointer':
                modules['reader'].segments[0].data[1] = 1
            else:
                modules['reader'].fixups[0] = replace(
                    modules['reader'].fixups[0], displacement=1)
            with self.subTest(damage=damage), self.assertRaises(ModuleError):
                self.prove(modules=modules)

    def test_empty_and_mismatched_producer_sets_reject(self):
        for changes in ({'modules': {}}, {'placements': {}},
                        {'modules': {**self.modules, 'empty': Module()}}):
            with self.subTest(changes=changes), self.assertRaises(ModuleError):
                self.prove(**changes)


from synthetic_omf import rec, name, u32, records, object_bytes, linked_image

class EasyOMFTests(unittest.TestCase):
    def test_full_native_easy_and_standard_variants_resolve_identically(self):
        # Catch short Easy offsets, wrong LOC5 semantics, discarded threads,
        # redefinition mutating old uses, or a verifier relocation waiver.
        image=LEImage.parse(linked_image())
        for easy,threads in ((True,True),(True,False),(False,True),(False,False)):
            with self.subTest(easy=easy,threads=threads):
                module=Module.parse(object_bytes(easy,threads))
                self.assertEqual([s.size for s in module.segments],[0x10020,0x10020])
                self.assertEqual(module.publics,{'own_code_high':(1,0x10000),'own_data_high':(2,0x10010)})
                expected=[
                    (1,0x10000,4,9,False,1,1,0,2,0),
                    (1,0x10004,4,9,False,1,1,0,2,0x10001),
                    (1,0x10008,4,9,False,1,1,2,1,0)]
                self.assertEqual([tuple(asdict(f).values()) for f in module.fixups],expected)
                proof=verify_module(module,image,{1:(1,0),2:(2,0)},
                                    {'own_external':(2,0x10010)},group_frames={1:(2,0)})
                self.assertEqual((proof['initialized_bytes'],proof['verified_fixups']),(131136,3))
                self.assertFalse(proof['full_executable_rebuilt'])
                if easy:
                    self.assertEqual(module.format,'easy-omf386')
                    self.assertEqual(module.format_source['flags'],0x80)
                    self.assertEqual(module.format_source['payload'],b'80386')
                    self.assertEqual([s.attributes for s in module.segments],[0xa8,0xa8])
                    self.assertEqual([s.access for s in module.segments],[None,None])
                    self.assertEqual([e['raw_location'] for e in module.fixup_encodings],[5,5,5])
                if threads:
                    self.assertEqual(len(module.thread_definitions),3)
                    self.assertEqual([e['target_definition'] for e in module.fixup_encodings],[1,1,2])
                    self.assertEqual(module.thread_definitions[2]['replaces'],1)

    def test_easy_preserves_complete_original_record_metadata(self):
        # Catch lost PUBDEF group/type/comment flags/record forms during normalization.
        raw=object_bytes()
        module=Module.parse(raw)
        self.assertEqual(b''.join(r[2] for r in module.source_records),raw)
        self.assertEqual([r[0] for r in module.source_records],list(range(len(module.source_records))))

    @staticmethod
    def small(extra=(),easy=True,with_data=True):
        rs=records(easy,False)
        # Independent definition prefix, then a small data association.
        prefix=rs[:next(i for i,r in enumerate(rs) if r[0] in (0xa0,0xa1))]
        if with_data:prefix.append((0xa0 if easy else 0xa1,b'\x01'+u32(0)+bytes(16)))
        return b''.join(rec(*r) for r in prefix+list(extra)+[(0x8a,b'\0')])

    def test_marker_order_flags_payload_and_empty_header_cannot_switch_late(self):
        marker=rec(0x88,b'\x80\xaa80386');h=rec(0x80,b'\0');end=rec(0x8a,b'\0')
        for raw,error in ((marker+h+end,'order'),
                          (h+rec(0x88,b'\x80\0translator')+marker+end,'order'),
                          (h+marker+marker+end,'order'),
                          (h+h+marker+end,'order'),
                          (h+marker+h+end,'headers'),
                          (rec(0x88,b'\x80\0translator')+h+marker+end,'order')):
            with self.subTest(raw=raw),self.assertRaisesRegex(OMFError,error):Module.parse(raw)
        for flags in (0,0x40,0xc0):
            with self.subTest(flags=flags),self.assertRaisesRegex(OMFError,'unsupported.*flags'):
                Module.parse(h+rec(0x88,bytes([flags,0xaa])+b'80386')+end)
        for payload in (b'',b'8038',b'80386\0',b'80386X',b'80387'):
            with self.subTest(payload=payload),self.assertRaisesRegex(OMFError,'unsupported'):
                Module.parse(h+rec(0x88,b'\x80\xaa'+payload)+end)

    def test_easy_rejects_unmeasured_records_comments_attributes_access_locations(self):
        for kind in (0x99,0x91,0xa1,0x9d,0x8b,0xa2,0xa3,0x94,0x95,0xb4,0xb6,0xc0):
            with self.subTest(kind=kind),self.assertRaisesRegex(OMFError,'unsupported Easy OMF record'):
                Module.parse(self.small([(kind,b'')]))
        for payload in (b'\x80\xaaOS220',b'\x80\x9fmath387r',b'\x80\xfeT1234',b'\x80\xa2\1',b'\x80\x9e'):
            with self.subTest(payload=payload),self.assertRaisesRegex(OMFError,'unsupported Easy OMF comment'):
                Module.parse(self.small([(0x88,payload)]))
        for attr in (0x20,0xa9,0xaa,0x68):
            with self.subTest(attr=attr),self.assertRaisesRegex(OMFError,'SEGDEF'):
                Module.parse(self.small([(0x98,bytes([attr])+u32(32)+b'\x02\x03\x01')]))
        for suffix in (b'\0',b'\2',b'\2\0'):
            with self.subTest(suffix=suffix),self.assertRaisesRegex(OMFError,'access'):
                Module.parse(self.small([(0x98,b'\xa8'+u32(32)+b'\x02\x03\x01'+suffix)]))
        for loc in (0,1,2,3,4,6,9,11,13,15):
            with self.subTest(loc=loc),self.assertRaisesRegex(OMFError,'location'):
                Module.parse(self.small([(0x9c,bytes([0xc0|(loc<<2),0,0x54,2]))]))

    def test_standard_loc5_is_two_bytes_and_helpers_reject_it(self):
        module=Module.parse(self.small([(0x9d,b'\xd4\0\x54\2')],easy=False))
        self.assertEqual((module.fixups[0].location,module.fixups[0].width),(5,2))
        with self.assertRaisesRegex(ModuleError,'width/location'):
            verify_module(module,LEImage.parse(linked_image()),{1:(1,0),2:(2,0)},{})

    def test_all_thread_slots_namespaces_methods_and_redefinition_are_independent(self):
        # Two separate namespaces, mixed inline/thread references, P0 then P1.
        defs=b''.join(bytes([0x40|(1<<2)|slot,1,slot,2]) for slot in range(4))
        refs=b''.join(bytes([0xd4,slot*4,0x80|(slot<<4)|8|4|slot]) for slot in range(4))
        module=Module.parse(self.small([(0x9c,defs),(0x9c,refs)]))
        self.assertEqual([(f.frame_method,f.frame,f.target_method,f.target) for f in module.fixups],[(1,1,0,2)]*4)
        self.assertEqual([e['frame_definition'] for e in module.fixup_encodings],[0,2,4,6])
        self.assertEqual([e['target_definition'] for e in module.fixup_encodings],[1,3,5,7])
        for fm,datum in ((0,2),(1,1),(2,1),(4,None),(5,None)):
            for tm,targ in ((0,2),(1,1),(2,1)):
                with self.subTest(fm=fm,tm=tm):
                    definition=bytes([0x40|(fm<<2)])+(bytes([datum]) if datum else b'')+bytes([tm<<2,targ])
                    m=Module.parse(self.small([(0x9c,definition+b'\xd4\0\x88'+u32(0x10001)),(0x9c,b'\xd4\4\x8c')]))
                    self.assertEqual([(f.frame_method,f.frame,f.target_method,f.target,f.displacement) for f in m.fixups],[(fm,datum,tm,targ,0x10001),(fm,datum,tm,targ,0)])
        # Legal aliases preserve raw method/frame bits, consuming target datum.
        m=Module.parse(self.small([(0x9c,b'\x44\1\x10\2\xd4\0\xcc')]))
        self.assertEqual((m.fixups[0].frame,m.fixups[0].target),(1,2))
        self.assertEqual(m.thread_definitions[1]['encoded_method'],4)
        self.assertEqual(m.fixup_encodings[0]['raw_frame_field'],4)
        for fix in (b'\xd4\0\x9c',b'\xd4\0\x8d',b'\xd4\0\x8c'):
            with self.subTest(fix=fix),self.assertRaisesRegex(OMFError,'undefined'):
                Module.parse(self.small([(0x9c,fix)]))

    def test_thread_state_survives_records_dosseg_and_pass_boundary_but_not_modules(self):
        # Definitions before data are legal; DOSSEG breaks data, not definitions.
        rs=records(False,False);prefix=rs[:next(i for i,r in enumerate(rs) if r[0]==0xa1)]
        defs=(0x9d,b'\x44\1\0\2')
        data=(0xa1,b'\1'+u32(0)+bytes(16));fix=(0x9d,b'\xe4\0\x8c')
        dosseg=(0x88,b'\x80\x9e');separator=(0x88,b'\x40\xa2\1');timestamp=(0x88,b'\x80\xfeT1234')
        def parse(tail):return Module.parse(b''.join(rec(*r) for r in prefix+list(tail)+[(0x8a,b'\0')]))
        m=parse([defs,data,dosseg,separator,timestamp,data,fix,fix])
        self.assertEqual(len(m.fixups),2)
        self.assertEqual([e['target_definition'] for e in m.fixup_encodings],[1,1])
        with self.assertRaisesRegex(OMFError,'preceding data'):parse([defs,data,dosseg,fix])
        with self.assertRaisesRegex(OMFError,'undefined'):parse([data,fix])
        # A thread definition alone is metadata, never a fake relocation.
        m=parse([defs]);self.assertEqual(m.fixups,[])
        self.assertEqual(len(m.thread_definitions),2)

    def test_new_thread_paths_validate_indexes_datums_reserved_bits_and_truncations(self):
        for head in (0x00,0x04,0x08,0x40,0x44,0x48):
            for datum in (b'\0',b'\x7f',b'\x80',b''):
                with self.subTest(head=head,datum=datum),self.assertRaises(OMFError):
                    Module.parse(self.small([(0x9c,bytes([head])+datum)]))
        for head in (0x20,0x60,0x58,0x5c,0x0c,0x1c,0x4c):
            with self.subTest(head=head),self.assertRaisesRegex(OMFError,'thread'):
                Module.parse(self.small([(0x9c,bytes([head]))]))
        for fd,tail in ((0x10,b'\0\2'),(0x10,b'\1\0'),(0x12,b'\1\2')):
            with self.subTest(fd=fd,tail=tail),self.assertRaises(OMFError):
                Module.parse(self.small([(0x9c,b'\xd4\0'+bytes([fd])+tail+u32(0))]))
        # Every boundary of an otherwise valid four-byte displacement is tested.
        for sub in (b'\xd4',b'\xd4\0',b'\xd4\0\x10',b'\xd4\0\x10\1',
                    b'\xd4\0\x10\1\2',b'\xd4\0\x10\1\2\1',
                    b'\xd4\0\x10\1\2\1\0',b'\xd4\0\x10\1\2\1\0\0'):
            with self.subTest(sub=sub),self.assertRaisesRegex(OMFError,'truncated'):
                Module.parse(self.small([(0x9c,sub)]))
        with self.assertRaisesRegex(OMFError,'outside preceding data'):
            Module.parse(self.small([(0x9c,b'\xd4\x0d\x54\2')]))
        with self.assertRaisesRegex(OMFError,'preceding data'):
            Module.parse(self.small([(0x9c,b'\xd4\0\x54\2')],with_data=False))

    def test_two_byte_thread_indexes_refer_to_previously_defined_tables(self):
        extras=[(0x98,b'\xa8'+u32(0)+b'\2\3\1')]*126
        # Segment128 is valid; group128 and external128 are independently defined.
        extras += [(0x9a,b'\6')]*127+[(0x8c,name('extra')+b'\0')]*127
        for frame,target in ((0,0),(1,1),(2,2)):
            m=Module.parse(self.small(extras+[(0x9c,bytes([0x40|(frame<<2)])+b'\x80\x80'+bytes([target<<2])+b'\x80\x80'+b'\xd4\0\x8c')]))
            self.assertEqual((m.fixups[0].frame,m.fixups[0].target),(128,128))

    def test_record_framing_checksum_and_easy_wide_bounds_remain_strict(self):
        raw=object_bytes();damaged=bytearray(raw);damaged[8]^=1
        for data,error in ((bytes(damaged),'checksum'),(raw[:-1],'truncated'),(raw[:-5],'MODEND'),(raw+b'X','trailing')):
            with self.subTest(error=error),self.assertRaisesRegex(OMFError,error):Module.parse(data)
        # Existing zero-checksum omission convention applies without weakening nonzero validation.
        rs=records();encoded=[rec(*r) for r in rs];encoded[1]=encoded[1][:-1]+b'\0'
        self.assertEqual(Module.parse(b''.join(encoded)).format,'easy-omf386')
        for kind,payload in ((0xa0,b'\1'+u32(0x10020)+b'X'),
                             (0x90,b'\0\1'+name('outside')+u32(0x10021)+b'\0'),
                             (0x98,b'\xa8'+u32(64*1024*1024+1)+b'\2\3\1')):
            with self.subTest(kind=kind),self.assertRaises(OMFError):Module.parse(self.small([(kind,payload)]))
        for width in range(4):
            with self.subTest(width=width),self.assertRaises(OMFError):
                Module.parse(self.small([(0xa0,b'\1'+u32(1)[:width])]))

    def test_whole_gate_rejects_body_relocation_frame_and_placement_damage(self):
        image=LEImage.parse(linked_image());module=Module.parse(object_bytes())
        places={1:(1,0),2:(2,0)};symbols={'own_external':(2,0x10010)}
        def prove(m=module,p=places,s=symbols,f=None,img=image):
            return verify_module(m,img,p,s,group_frames=f)
        for frames in (None,{1:(1,0)},{1:(2,4)},{2:(2,0)}):
            with self.subTest(frames=frames),self.assertRaises(ModuleError):prove(f=frames)
        for seg,off in ((0,1),(0,0x1001f),(1,1),(1,0x1001f),(0,0x10000)):
            m=deepcopy(module);m.segments[seg].data[off]^=1
            with self.subTest(seg=seg,off=off),self.assertRaises(ModuleError):prove(m=m,f={1:(2,0)})
        for change in ({'target':1},{'displacement':1},{'relative':True}):
            m=deepcopy(module);m.fixups[0]=replace(m.fixups[0],**change)
            with self.subTest(change=change),self.assertRaises(ModuleError):prove(m=m,f={1:(2,0)})
        m=deepcopy(module);m.fixups.append(m.fixups[0])
        with self.assertRaisesRegex(ModuleError,'overlap'):prove(m=m,f={1:(2,0)})
        m=deepcopy(module);m.fixups.pop()
        with self.assertRaisesRegex(ModuleError,'unaccounted'):prove(m=m,f={1:(2,0)})
        with self.assertRaises(ModuleError):prove(p={1:(1,0)},f={1:(2,0)})
        with self.assertRaises(ModuleError):prove(s={},f={1:(2,0)})
        with self.assertRaises(ModuleError):prove(s={'own_external':(1,0x10010)},f={1:(2,0)})
        inferred=infer_bindings(module,image,1,0,known_symbols=symbols)
        self.assertEqual(inferred['placements'],places)
        self.assertEqual(inferred['symbols']['own_external'],(2,0x10010))
        with self.assertRaisesRegex(BindingError,'no relocations'):infer_data_bindings(module,{'own_data_high':(2,0x10010)})

    def test_relocation_free_easy_data_proves_without_fabricated_fixups(self):
        # A high-offset complete data source needs anchors, not dummy relocations.
        rs=[(0x80,name('own-data')),(0x88,b'\x80\xaa80386'),
            (0x96,name('')+name('DATA')),
            (0x98,b'\xa8'+u32(0x10020)+b'\2\2\1'),
            (0x90,b'\0\1'+name('own_data_high')+u32(0x10010)+b'\0')]
        body=bytearray(0x10020);body[-4:]=b'OWND'
        rs += [(0xa0,b'\1'+u32(off)+body[off:off+512]) for off in range(0,len(body),512)]
        rs += [(0x8a,b'\0')]
        module=Module.parse(b''.join(rec(*r) for r in rs))
        self.assertEqual(module.fixups,[])
        bindings=infer_data_bindings(module,{'own_data_high':(2,0x10010)})
        proof=verify_module(module,LEImage.parse(linked_image()),bindings['placements'],bindings['symbols'])
        self.assertEqual((proof['initialized_bytes'],proof['verified_fixups']),(65568,0))
        self.assertEqual(module.thread_definitions,[])

    def test_explicit_and_threaded_zero_displacement_provenance_is_distinct(self):
        m=Module.parse(self.small([(0x9c,b'\xd4\0\x54\2'),
                                  (0x9c,b'\xd4\4\x50\2'+u32(0))]))
        self.assertEqual([f.displacement for f in m.fixups],[0,0])
        self.assertEqual([e['displacement_present'] for e in m.fixup_encodings],[False,True])
        self.assertEqual([e['displacement_width'] for e in m.fixup_encodings],[0,4])
        self.assertNotEqual(m.fixup_encodings[0]['raw_bytes'],m.fixup_encodings[1]['raw_bytes'])

    def test_complete_gate_checks_loader_target_and_group_membership(self):
        module=Module.parse(object_bytes());image=LEImage.parse(linked_image())
        def prove(m=module,img=image):
            return verify_module(m,img,{1:(1,0),2:(2,0)},
                                 {'own_external':(2,0x10010)},group_frames={1:(2,0)})
        for field,delta in ((2,12),(5,1)):
            raw=bytearray(image.data);raw[image.fixup_record_start+field]^=delta
            with self.subTest(field=field),self.assertRaises(ModuleError):prove(img=LEImage.parse(bytes(raw)))
        m=deepcopy(module);m.groups=[('DGROUP',())]
        with self.assertRaisesRegex(ModuleError,'member target'):prove(m=m)

    def test_standard_inline_choices_in_threaded_module_remain_auditable(self):
        m=Module.parse(self.small([(0x9d,b'\x44\1\0\2'),
                                  (0x9d,b'\xe4\0\x54\2'),
                                  (0x9d,b'\xe4\4\x50\2'+u32(0)),
                                  (0x9d,b'\xe4\x08\x8c')],easy=False))
        self.assertEqual([e['target_thread'] for e in m.fixup_encodings],[False,False,True])
        self.assertEqual([e['displacement_present'] for e in m.fixup_encodings],[False,True,False])
        self.assertEqual([e['raw_fixdat'] for e in m.fixup_encodings],[0x54,0x50,0x8c])

    def test_standard_even_and_odd_thread_displacement_widths_stay_distinct(self):
        for kind,loc,payload,want in ((0x9c,0xc4,b'\x21\x43',0x4321),
                                     (0x9d,0xe4,u32(0x10001),0x10001)):
            with self.subTest(kind=kind):
                m=Module.parse(self.small([(kind,b'\0\2'),(kind,bytes([loc,0,0x58])+payload)],easy=False))
                self.assertEqual(m.fixups[0].displacement,want)
                self.assertEqual(m.fixup_encodings[0]['displacement_width'],len(payload))

    def test_native_threaded_self_relative_call_requires_no_loader_relocation(self):
        for easy,kind,loc in ((True,0x9c,0x94),(False,0x9d,0xa4)):
            rs=records(easy,True)
            first=next(i for i,r in enumerate(rs) if r[0]==kind)
            rs[first]=(kind,rs[first][1]+b'\1\1')  # target thread1 = own CODE
            third=next(i for i,r in enumerate(rs) if r[0]==kind and r[1].startswith(b'\x08\x01'))
            rs.insert(third+1,(kind,bytes([loc,12,0x5d])))
            module=Module.parse(b''.join(rec(*r) for r in rs))
            raw=bytearray(linked_image());raw[4096+0x1000c:4096+0x10010]=b'\xf0\xff\xfe\xff'
            image=LEImage.parse(bytes(raw))
            self.assertEqual(len(image.read_fixups()),3)
            proof=verify_module(module,image,{1:(1,0),2:(2,0)},
                                {'own_external':(2,0x10010)},group_frames={1:(2,0)})
            self.assertEqual(proof['verified_fixups'],4)
            self.assertTrue(module.fixups[3].relative)
            inferred=infer_bindings(module,image,1,0)
            self.assertEqual(inferred['placements'],{1:(1,0),2:(2,0)})

    def test_easy_public_group_index_cannot_name_an_undefined_group(self):
        with self.assertRaisesRegex(OMFError,'public group'):
            Module.parse(self.small([(0x90,b'\2\1'+name('bad_group')+u32(0)+b'\0')]))

    def test_easy_reserved_comment_flags_cannot_use_standard_ga_padding_exception(self):
        # A complete otherwise-proven module must fail before any verifier gate.
        rs=records();rs.insert(2,(0x88,b'\4\0\0'))
        with self.assertRaisesRegex(OMFError,'flags'):
            Module.parse(b''.join(rec(*r) for r in rs))
        # Preserve the existing measured standard GA filler convention.
        standard=records(False,False);standard.insert(1,(0x88,b'\4\0\0'))
        module=Module.parse(b''.join(rec(*r) for r in standard))
        proof=verify_module(module,LEImage.parse(linked_image()),{1:(1,0),2:(2,0)},
                            {'own_external':(2,0x10010)},group_frames={1:(2,0)})
        self.assertEqual((proof['initialized_bytes'],proof['verified_fixups']),(131136,3))

    def test_easy_null_theadr_name_is_a_present_header_and_complete_native_proof(self):
        # IBM2.4.1 permits the null name; presence and name content differ.
        rs=records();rs[0]=(0x80,b'\0')
        raw=b''.join(rec(*r) for r in rs);module=Module.parse(raw)
        self.assertEqual(module.name,'')
        self.assertEqual(b''.join(r[2] for r in module.source_records),raw)
        proof=verify_module(module,LEImage.parse(linked_image()),{1:(1,0),2:(2,0)},
                            {'own_external':(2,0x10010)},group_frames={1:(2,0)})
        self.assertEqual((proof['initialized_bytes'],proof['verified_fixups']),(131136,3))
        # Legacy standard behavior remains unchanged by the Easy declaration.
        with self.assertRaisesRegex(OMFError,'missing module header'):
            Module.parse(rec(0x80,b'\0')+rec(0x8a,b'\0'))


class StrictInputsTests(unittest.TestCase):
    fixture = ModuleProofTests.fixture

    def test_module_rejects_coerced_and_extra_placements(self):
        mod, image, places = self.fixture()
        for bad in ({**places, 1.5: (1, 0)}, {**places, '1': (1, 0)},
                    {**places, 99: (1, 0)}, {**places, 1: (True, 0)},
                    {**places, 1: (1, 0.0)}):
            with self.subTest(bad=bad), self.assertRaises(ModuleError):
                verify_module(mod, image, bad, {})

    def test_module_rejects_non_dict_bindings_and_bad_scope(self):
        mod, image, places = self.fixture()
        for symbols in ([], {'bad': (1, 0.5)}, {'bad': (False, 0)}):
            with self.subTest(symbols=symbols), self.assertRaises(ModuleError):
                verify_module(mod, image, places, symbols)
        mod.local_externals = {99}
        with self.assertRaises(ModuleError):
            verify_module(mod, image, places, {})

    def test_group_index_requires_canonical_integer_spelling(self):
        mod, image, places = self.fixture()
        mod.groups = [('DATA_GROUP', (2, 3))]
        for key in ('01', '+1', ' 1', '١'):
            with self.subTest(key=key), self.assertRaises(MatchError):
                validate_group_frames(mod, image, places, {key: (1, 0)})


class SyntheticFrameTests(unittest.TestCase):
    def fixture(self):
        raw = fixture()
        raw[0x440:0x446] = b'\xe8\x0b\0\0\0\xc3'
        image = LEImage.parse(bytes(raw))
        module = Module(name='caller', segments=[Segment('TEXT', 'CODE', 6, 0x69,
                          bytearray(b'\xe8\0\0\0\0\xc3'))],
                        publics={'caller': (1, 0)}, externals=['callee'],
                        groups=[('CALL_GROUP', (1,))],
                        fixups=[Fixup(1, 1, 4, 9, True, 1, 1, 2, 1, 0)])
        return module, image

    def test_group_relative_call_checks_callee_identity(self):
        module, image = self.fixture()
        proof = verify_module(module, image, {1: (1, 0)}, {'callee': (1, 16)},
                              group_frames={1: (1, 0)})
        self.assertEqual(proof['verified_fixups'], 1)
        with self.assertRaises(ModuleError):
            verify_module(module, image, {1: (1, 0)}, {'callee': (1, 17)},
                          group_frames={1: (1, 0)})
        with self.assertRaisesRegex(ModuleError, 'frame'):
            verify_module(module, image, {1: (1, 0)}, {'callee': (1, 16)})

    def test_source_code_frame_is_bounded_and_exact(self):
        module, image = self.fixture()
        module.fixups[0] = replace(module.fixups[0], frame_method=0)
        self.assertTrue(verify_module(module, image, {1: (1, 0)},
                                     {'callee': (1, 16)})['verified'])
        for change in ({'frame': 2}, {'displacement': 1}, {'width': 2},
                       {'location': 5}, {'frame_method': 3}):
            damaged = deepcopy(module)
            damaged.fixups[0] = replace(damaged.fixups[0], **change)
            with self.subTest(change=change), self.assertRaises(ModuleError):
                verify_module(damaged, image, {1: (1, 0)}, {'callee': (1, 16)})

    def test_crossing_fields_and_missing_loader_cannot_be_masked(self):
        module, image = self.fixture()
        with self.assertRaisesRegex(MatchError, 'crosses'):
            compare_range(module, 1, 2, image, 1, 2, 4, {'callee': (1, 16)},
                          group_frames={1: (1, 0)})
        module.fixups[0] = replace(module.fixups[0], relative=False)
        with self.assertRaisesRegex(ModuleError, 'target mismatch'):
            verify_module(module, image, {1: (1, 0)}, {'callee': (1, 16)},
                          group_frames={1: (1, 0)})


class LogicalOriginTests(unittest.TestCase):
    """Issuer contract only; no historical replay is inferred by these tests."""
    def fixture(self):
        from logical_origins import _issue_verified_origin, segment_identity
        image = LEImage.parse(bytes(fixture()))
        module = Module(name='data', segments=[
            Segment('DATA', 'DATA', 1, 0x69, bytearray(b'i'))],
            groups=[('DATA_GROUP', (1,))])
        context = _issue_verified_origin(image, (1, 8), segment_identity(module, 1),
                                         {'synthetic_closed_witness': True})
        return module, image, context

    def test_issued_origin_changes_only_alignment_coordinate(self):
        module, image, context = self.fixture()
        with self.assertRaisesRegex(ModuleError, 'alignment'):
            verify_module(module, image, {1: (1, 8)}, {})
        proof = verify_module(module, image, {1: (1, 8)}, {}, logical_origins=[context])
        self.assertEqual(proof['initialized_bytes'], 1)
        self.assertEqual(proof['logical_segment_origins'][0]['origin'], [1, 8])
        damaged = deepcopy(module)
        damaged.segments[0].data[0] ^= 1
        with self.assertRaisesRegex(ModuleError, 'differing bytes'):
            verify_module(damaged, image, {1: (1, 8)}, {}, logical_origins=[context])

    def test_serialized_forged_duplicate_or_wrong_target_context_rejects(self):
        module, image, context = self.fixture()
        for contexts in ({}, [dict(origin=[1, 8])], [object()], [context, context]):
            with self.subTest(contexts=contexts), self.assertRaises(ModuleError):
                verify_module(module, image, {1: (1, 8)}, {}, logical_origins=contexts)
        raw = bytearray(image.data)
        raw[-1] ^= 1
        changed = LEImage.parse(bytes(raw))
        with self.assertRaisesRegex(ModuleError, 'identity mismatch'):
            verify_module(module, changed, {1: (1, 8)}, {}, logical_origins=[context])

    def test_issued_context_cannot_be_copied_or_serialized(self):
        from copy import copy
        import pickle
        from logical_origins import LogicalSegmentOrigin
        _, _, context = self.fixture()
        for operation in (lambda: LogicalSegmentOrigin(), lambda: copy(context),
                          lambda: deepcopy(context), lambda: pickle.dumps(context)):
            with self.assertRaises(TypeError):
                operation()

    def test_segment_identity_and_contribution_range_are_checked(self):
        from logical_origins import _issue_verified_origin, segment_identity
        module, image, context = self.fixture()
        for change in ('class', 'format', 'membership'):
            damaged = deepcopy(module)
            if change == 'class': damaged.segments[0].class_name = 'CODE'
            elif change == 'format': damaged.format = 'easy-omf386'
            else: damaged.groups = [('DATA_GROUP', (1, 1))]
            with self.subTest(change=change), self.assertRaises(ValueError):
                segment_identity(damaged, 1)
        with self.assertRaises(ModuleError):
            verify_module(module, image, {1: (1, 7)}, {}, logical_origins=[context])
        for origin in ((1, -1), (True, 8), (1, 40), (1, 8.0)):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                _issue_verified_origin(image, origin, segment_identity(module, 1), {})


class StandaloneScopeTests(unittest.TestCase):
    fixture = ModuleProofTests.fixture

    def test_private_public_cannot_bind_same_named_global_external(self):
        module, image, placements = self.fixture()
        module.local_publics.add('_value')
        with self.assertRaisesRegex(ModuleError, 'scope|ambiguous'):
            verify_module(module, image, placements, {'_value': (1, 8)})

    def test_same_spelling_local_and_global_external_indices_reject(self):
        module, image, placements = self.fixture()
        module.externals.append('_value')
        module.local_externals.add(2)
        with self.assertRaisesRegex(ModuleError, 'scope|ambiguous'):
            verify_module(module, image, placements, {'_value': (1, 8)})
