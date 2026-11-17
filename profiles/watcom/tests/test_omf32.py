from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from omf32 import Module, OMFError


def record(kind, payload):
    head = bytes([kind])+struct.pack('<H',len(payload)+1)+payload
    return head+bytes([-sum(head)&255])


def fixture():
    return b''.join([
        record(0x80,b'\x04test'),
        record(0x96,b'\x00\x05_TEXT\x04CODE\x04FLAT'),
        record(0x99,b'\x69'+struct.pack('<I',0x10008)+b'\x02\x03\x01'),
        record(0x9a,b'\x04'),
        record(0x8c,b'\x06other_\0'),
        record(0xa1,b'\x01'+struct.pack('<I',0x10000)+b'\xe8\0\0\0\0\xc3'),
        record(0x9d,b'\xa4\x01\x16\x01\x01'),
        record(0x91,b'\x01\x01\x05test_'+struct.pack('<I',0x10000)+b'\0'),
        record(0x8a,b'\0'),
    ])


class OMFTests(unittest.TestCase):
    def test_dosseg_retains_flags_and_preserves_other_metadata(self):
        rich=fixture()[:-5]+b''.join(record(kind,payload) for kind,payload in [
            (0x88,b'\x80\0translator'),(0x88,b'\x80\x9fmath387r'),
            (0x88,b'\x80\xfeO\x01'),
            (0x88,b'\x80\xfdS\x01'+struct.pack('<II',0,8)),
            (0x95,b'\x01\x01'+struct.pack('<HI',42,7)),
            (0xb7,b'\x01\x01\x07private'+struct.pack('<I',7)+b'\0'),
            (0xb4,b'\x06hidden\0')])+record(0x8a,b'\0')
        for source in (record(0x80,b'\x01x')+record(0x8a,b'\0'), fixture(),rich):
            baseline=Module.parse(source)
            self.assertEqual(baseline.dosseg_directives,[])
            # Insert before data, preserving existing comments and all other fields.
            offset=3+int.from_bytes(source[1:3],'little')
            for flags in (0,0x40,0x80,0xc0):
                with self.subTest(flags=flags,source_size=len(source)):
                    module=Module.parse(source[:offset]+record(0x88,bytes([flags,0x9e]))+source[offset:])
                    self.assertEqual(module.dosseg_directives,[flags])
                    self.assertEqual(module.comments,[(0x9e,b'')]+baseline.comments)
                    unchanged=vars(module).copy()
                    unchanged['dosseg_directives']=[]
                    unchanged['comments']=baseline.comments
                    self.assertEqual(unchanged,vars(baseline))

    def test_dosseg_retains_repeated_occurrences_without_collapsing_flags(self):
        data=record(0x80,b'\x01x')
        data+=record(0x88,b'\x40\x9e')+record(0x88,b'\x80\0translator')
        data+=record(0x88,b'\xc0\x9e')+record(0x8a,b'\0')
        module=Module.parse(data)
        self.assertEqual(module.dosseg_directives,[0x40,0xc0])
        self.assertEqual(module.comments,[(0x9e,b''),(0,b'translator'),(0x9e,b'')])

    def test_dosseg_rejects_every_reserved_flag_combination(self):
        for high in (0,0x40,0x80,0xc0):
            for reserved in range(1,64):
                with self.subTest(flags=high|reserved),self.assertRaisesRegex(OMFError,'flags'):
                    Module.parse(record(0x80,b'\x01x')+
                                 record(0x88,bytes([high|reserved,0x9e]))+record(0x8a,b'\0'))

    def test_dosseg_requires_exactly_empty_commentary(self):
        for extra in (b'\0',b'\0\0',b'X',b'\x01',b'\x01X'):
            with self.subTest(extra=extra),self.assertRaisesRegex(OMFError,'DOSSEG'):
                Module.parse(record(0x80,b'\x01x')+record(0x88,b'\x80\x9e'+extra)+record(0x8a,b'\0'))

    def test_dosseg_requires_header_and_pass_one_and_precedes_modend(self):
        header,end,dosseg=record(0x80,b'\x01x'),record(0x8a,b'\0'),record(0x88,b'\x80\x9e')
        for data,error in ((dosseg+header+end,'header'),
                           (header+record(0x88,b'\x40\xa2\x01')+dosseg+end,'pass'),
                           (header+end+dosseg,'trailing')):
            with self.subTest(error=error),self.assertRaisesRegex(OMFError,error):
                Module.parse(data)

    def test_dosseg_breaks_backward_fixup_association_but_allows_completed_data(self):
        data=fixture()
        offset=data.index(record(0x9d,b'\xa4\x01\x16\x01\x01'))
        dosseg=record(0x88,b'\x80\x9e')
        with self.assertRaisesRegex(OMFError,'preceding data'):
            Module.parse(data[:offset]+dosseg+data[offset:])
        module=Module.parse(data[:-5]+dosseg+data[-5:])
        self.assertEqual(module.fixups,Module.parse(data).fixups)
        self.assertEqual(module.dosseg_directives,[0x80])
        no_fixup=Module.parse(data[:offset]+dosseg+record(0x8a,b'\0'))
        self.assertEqual(no_fixup.segments,Module.parse(data).segments)
        self.assertEqual(no_fixup.fixups,[])
        # A fresh LEDATA restores a valid association after the directive.
        start=data.index(record(0xa1,b'\x01'+struct.pack('<I',0x10000)+b'\xe8\0\0\0\0\xc3'))
        self.assertEqual(Module.parse(data[:offset]+dosseg+data[start:]).fixups,Module.parse(data).fixups)

    def test_dosseg_keeps_record_validation_and_unknown_class_rejection(self):
        header,end=record(0x80,b'\x01x'),record(0x8a,b'\0')
        comment=record(0x88,b'\x80\x9e')
        broken=comment[:-1]+bytes([comment[-1]^1])
        for body,error in ((record(0x88,b''),'truncated'),(record(0x88,b'\x80'),'truncated'),
                           (broken,'checksum'),(comment[:-1],'truncated'),
                           (record(0x88,b'\x80\x9d'),'class'),(record(0x88,b'\x80\xa0'),'class'),
                           (record(0x9e,b''),'record')):
            with self.subTest(body=body),self.assertRaisesRegex(OMFError,error):
                Module.parse(header+body+(b'' if body==comment[:-1] else end))
        self.assertEqual(Module.parse(header+comment[:-1]+b'\0'+end).dosseg_directives,[0x80])

    def test_unrelated_translator_ordering_is_unchanged(self):
        translator=record(0x88,b'\x80\0translator')
        self.assertEqual(Module.parse(translator+fixture()).comments,[(0,b'translator')])
        data=fixture();offset=data.index(record(0x9d,b'\xa4\x01\x16\x01\x01'))
        self.assertEqual(Module.parse(data[:offset]+translator+data[offset:]).fixups,
                         Module.parse(data).fixups)

    def test_retains_watcom_debug_extension_and_line_numbers(self):
        data=fixture()[:-5]
        data+=record(0x88,b'\x80\xaaOS220')+record(0x88,b'\x80\xfeD\x01\x03C')
        data+=record(0x96,b'\x09$$SYMBOLS\x06DEBSYM')
        data+=record(0x99,b'\x20'+struct.pack('<I',3)+b'\x05\x06\x01\x02')
        data+=record(0xa1,b'\x02'+bytes(4)+b'ABC')
        data+=record(0x95,b'\x01\x01'+struct.pack('<HI',42,0x10000))
        data+=record(0x94,b'\0\x01'+struct.pack('<HH',43,7))
        module=Module.parse(data+record(0x8a,b'\0'))
        self.assertEqual(module.segments[1].access,2)
        self.assertEqual(module.segments[1].data,b'ABC')
        self.assertEqual(module.line_numbers,[(1,1,42,0x10000),(0,1,43,7)])
        for bad in [b'\0\0'+struct.pack('<HI',1,0),
                    b'\x02\x01'+struct.pack('<HI',1,0),
                    b'\0\x01'+struct.pack('<HI',1,0x10009),
                    b'\0\x01\x01']:
            with self.subTest(payload=bad),self.assertRaises(OMFError):
                Module.parse(fixture()[:-5]+record(0x95,bad)+record(0x8a,b'\0'))

    def test_rejects_unrecognized_segment_access_extension(self):
        for attr,extra in [(0x69,b'\x02'),(0x20,b'\x03'),(0x20,b'\x02\0')]:
            data=record(0x80,b'\x01x')+record(0x96,b'\0\x05_TEXT\x04CODE')
            data+=record(0x99,bytes([attr])+struct.pack('<I',8)+b'\x02\x03\x01'+extra)
            with self.subTest(attr=attr,extra=extra),self.assertRaises(OMFError):
                Module.parse(data+record(0x8a,b'\0'))

    def test_preserves_measured_tasm_metadata_and_pass_boundary(self):
        comments=[b'\0\0\x1cTurbo Assembler  Version 3.1',b'\x40\xa1',b'\x40\xa2\x01']
        data=fixture()
        public=record(0x91,b'\x01\x01\x05test_'+struct.pack('<I',0x10000)+b'\0')
        data=data.replace(public,b'')
        # Insert metadata immediately before its LEDATA record.
        offset=data.index(b'\xa1\x0c\0')
        data=data[:offset]+public+b''.join(record(0x88,p) for p in comments)+data[offset:]
        module=Module.parse(data)
        self.assertEqual(module.comments,[(p[1],p[2:]) for p in comments])
        self.assertEqual(module.publics['test_'],(1,0x10000))

    def test_rejects_invalid_tasm_format_or_pass_metadata(self):
        for p in [b'\x40\xa1X',b'\x40\xa2',b'\x40\xa2\x02',b'\x40\xa2\x01X']:
            with self.subTest(payload=p),self.assertRaises(OMFError):
                Module.parse(record(0x80,b'\x01x')+record(0x88,p)+record(0x8a,b'\0'))
        # Definitions after pass separation would be invisible to linker pass 1.
        with self.assertRaisesRegex(OMFError,'pass'):
            Module.parse(record(0x80,b'\x01x')+record(0x88,b'\x40\xa2\x01')+
                         record(0x96,b'\x01x')+record(0x8a,b'\0'))

    def test_preserves_library_timestamp_before_and_after_pass_boundary(self):
        baseline=Module.parse(fixture())
        for flags in (0x80,0xc0):
            for boundary in (b'',record(0x88,b'\x40\xa2\x01')):
                payload=b'T'+struct.pack('<I',0x315b0598)
                data=fixture()[:-5]+boundary+record(0x88,bytes([flags,0xfe])+payload)
                module=Module.parse(data+record(0x8a,b'\0'))
                self.assertIn((0xfe,payload),module.comments)
                self.assertEqual(module.segments,baseline.segments)
                self.assertEqual(module.publics,baseline.publics)
                self.assertEqual(module.fixups,baseline.fixups)
                self.assertEqual(module.externals,baseline.externals)

    def test_timestamp_does_not_allow_other_records_after_pass_boundary(self):
        prefix=fixture()[:-5]+record(0x88,b'\x40\xa2\x01')
        for payload in (b'\x80\xfeT',b'\x80\xfeT123',b'\x80\xfeT12345',
                        b'\x80\xfeU1234',b'\x80\0translator',b'\x40\xa2\x01'):
            with self.subTest(payload=payload),self.assertRaises(OMFError):
                Module.parse(prefix+record(0x88,payload)+record(0x8a,b'\0'))
        for timestamp in (b'',b'1',b'123',b'12345'):
            with self.subTest(timestamp=timestamp),self.assertRaises(OMFError):
                Module.parse(fixture()[:-5]+record(0x88,b'\x80\xfeT'+timestamp)+
                             record(0x8a,b'\0'))
        with self.assertRaises(OMFError):
            Module.parse(prefix+record(0x88,b'\x04\xfeT1234')+record(0x8a,b'\0'))

    def test_32_bit_offsets_and_self_relative_external_fixup(self):
        mod=Module.parse(fixture())
        self.assertEqual(mod.segments[0].size,0x10008)
        self.assertEqual(mod.segments[0].data[0x10000:0x10006],b'\xe8\0\0\0\0\xc3')
        self.assertEqual(mod.publics['test_'],(1,0x10000))
        f=mod.fixups[0]
        self.assertEqual((f.segment,f.offset,f.width,f.relative),(1,0x10001,4,True))
        self.assertEqual((f.target_method,f.target,f.displacement),(2,1,0))
        self.assertEqual(mod.externals[f.target-1],'other_')

    def test_preserves_local_symbol_scope_for_16_and_32_bit_records(self):
        for kind,width in [(0xb6,2),(0xb7,4)]:
            data=fixture()[:-5]
            data+=record(kind,b'\x01\x01\x07private'+(7).to_bytes(width,'little')+b'\0')
            data+=record(0xb4,b'\x06hidden\0')
            data+=record(0xb5,b'\x07hidden2\0')
            m=Module.parse(data+record(0x8a,b'\0'))
            self.assertEqual(m.publics['private'],(1,7))
            self.assertEqual(m.local_publics,{'private'})
            self.assertEqual(m.local_externals,{2,3})
            self.assertNotIn('test_',m.local_publics)

    def test_checksum_failure(self):
        data=bytearray(fixture()); data[5]^=1
        with self.assertRaisesRegex(OMFError,'checksum'):
            Module.parse(bytes(data))

    def test_truncated_record(self):
        with self.assertRaisesRegex(OMFError,'truncated'):
            Module.parse(fixture()[:-1])

    def test_missing_module_end(self):
        with self.assertRaisesRegex(OMFError,'MODEND'):
            Module.parse(fixture()[:-5])

    def test_data_cannot_escape_segment(self):
        data=record(0x80,b'\x01x')+record(0x96,b'\x00\x01x')
        data+=record(0x99,b'\x69\x01\0\0\0\x02\x02\x01')
        data+=record(0xa1,b'\x01\0\0\0\0AB')+record(0x8a,b'\0')
        with self.assertRaisesRegex(OMFError,'segment'):
            Module.parse(data)

    def test_unknown_record_rejected(self):
        data=record(0x80,b'\x01x')+record(0xc0,b'\x01')+record(0x8a,b'\0')
        with self.assertRaisesRegex(OMFError,'unsupported'):
            Module.parse(data)

    def test_semantic_comments_are_not_silently_discarded(self):
        for payload in [b'',b'\0',b'\x80\xa8\x01\x02',b'\x80\xa0\x01',b'\x80\xa6',b'\x80\xfeX']:
            with self.subTest(payload=payload):
                data=record(0x80,b'\x01x')+record(0x88,payload)+record(0x8a,b'\0')
                with self.assertRaises(OMFError):
                    Module.parse(data)

    def test_preserves_default_libraries_and_call_optimization_directive(self):
        data=record(0x80,b'\x01x')
        data+=record(0x96,b'\x00\x01x')
        data+=record(0x99,b'\x69\x01\0\0\0\x02\x02\x01')
        data+=record(0x88,b'\x80\x9fmath387r')
        data+=record(0x88,b'\x80\xfeO\x01')+record(0x8a,b'\0')
        mod=Module.parse(data)
        self.assertEqual(mod.default_libraries,['math387r'])
        self.assertEqual(mod.far_call_optimization_segments,[1])

    def test_expands_nested_32_bit_iterated_data(self):
        data=record(0x80,b'\x01x')+record(0x96,b'\x00\x01x')
        data+=record(0x99,b'\x69\x08\0\0\0\x02\x02\x01')
        blocks=struct.pack('<IH',2,1)+struct.pack('<IH',2,0)+b'\x02AB'
        data+=record(0xa3,b'\x01'+bytes(4)+blocks)+record(0x8a,b'\0')
        self.assertEqual(Module.parse(data).segments[0].data,b'ABABABAB')

    def test_iterated_data_expansion_cannot_escape_segment(self):
        data=record(0x80,b'\x01x')+record(0x96,b'\x00\x01x')
        data+=record(0x99,b'\x69\x08\0\0\0\x02\x02\x01')
        blocks=struct.pack('<IH',0xffffffff,0)+b'\x01A'
        data+=record(0xa3,b'\x01'+bytes(4)+blocks)+record(0x8a,b'\0')
        with self.assertRaisesRegex(OMFError,'segment'):
            Module.parse(data)

    def test_zero_checksum_means_checksum_omitted(self):
        data=record(0x80,b'\x01x')
        comment=record(0x88,b'\x80\xe9')
        data+=comment[:-1]+b'\0'+record(0x8a,b'\0')
        self.assertEqual(Module.parse(data).name,'x')

    def test_preserves_disassembler_code_data_range(self):
        data=record(0x80,b'\x01x')+record(0x96,b'\x00\x01x')
        data+=record(0x99,b'\x69\x08\0\0\0\x02\x02\x01')
        data+=record(0x88,b'\x80\xfdS\x01'+struct.pack('<II',0,8))+record(0x8a,b'\0')
        self.assertEqual(Module.parse(data).code_data_ranges,[(1,0,8)])

    def test_accepts_only_measured_zero_filled_watcom_padding_comments(self):
        padding=record(0x88,b'\x04\0'+bytes(30))
        data=record(0x80,b'\x01x')+padding+record(0x8a,b'\0')
        self.assertEqual(Module.parse(data).comments,[(0,bytes(30))])
        bad=record(0x80,b'\x01x')+record(0x88,b'\x04\0\x01')+record(0x8a,b'\0')
        with self.assertRaises(OMFError):
            Module.parse(bad)
