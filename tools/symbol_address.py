"""Typed original-image addresses for 16-bit MZ/FBOV verification.

OVL indices identify original FBOV segment-table entries, never x86 paragraphs.
This module resolves physical locations only; accepted-entry/caller identity and
zero-addend checks belong to the caller's relocation/binding proof.
"""
import re
import struct

_HEX=re.compile(r'[0-9A-Fa-f]{1,4}\Z')


def _word(value):
    if type(value) is not int or not 0<=value<=0xffff:
        raise ValueError('address component must be a 16-bit unsigned integer')
    return value


def parse(text):
    """Parse DS:off, paragraph:off, or OVL:original_index:off."""
    if not isinstance(text,str):
        raise ValueError('address must be text')
    parts=text.split(':')
    if len(parts)==2 and parts[0].upper()=='DS':
        domain='DS';numbers=parts[1:]
    elif len(parts)==3 and parts[0].upper()=='OVL':
        domain='OVL';numbers=parts[1:]
    elif len(parts)==2:
        domain='FAR';numbers=parts
    else:
        raise ValueError('unknown address domain or field count')
    if not all(_HEX.fullmatch(n) for n in numbers):
        raise ValueError('address fields must contain one to four hexadecimal digits')
    return (domain,*(int(n,16) for n in numbers))


def format_address(value):
    """Return the unique uppercase spelling of a typed address tuple."""
    if type(value) is not tuple or not value:
        raise ValueError('address must be a typed tuple')
    domain=value[0]
    count={'DS':2,'FAR':3,'OVL':3}.get(domain) if isinstance(domain,str) else None
    if count is None or len(value)!=count:
        raise ValueError('unknown address domain or field count')
    numbers=[_word(n) for n in value[1:]]
    prefix='' if domain=='FAR' else domain+':'
    return prefix+':'.join(f'{n:04X}' for n in numbers)


def symbol_maps(symbols):
    """Separate named data, resident FAR and overlay-body address maps."""
    maps={'DS':{},'FAR':{},'OVL':{}}
    for name,text in symbols.items():
        address=parse(text)
        key=address[1] if address[0]=='DS' else address[1:]
        target=maps[address[0]]
        if key in target and target[key]!=name:
            raise ValueError(f'{name} and {target[key]} share {format_address(address)}')
        target[key]=name
    return maps['DS'],maps['FAR'],maps['OVL']


def resident_reference(text):
    """Address for a resident-only executor; an FBOV index is never a segment."""
    address=parse(text)
    if address[0]=='OVL':
        raise ValueError('overlay body execution requires an overlay loader')
    return ('DS',address[1]) if address[0]=='DS' else address[1:]


def overlay_location(cfg,index,off,extent=1):
    """Return (original stub paragraph, absolute original body file offset).

    Resolve only a positive, nonwrapping span inside one original flags=3 FBOV
    overlay. No symbol lookup, addend arithmetic or current linker state is used.
    cfg must expose the authoritative original MZ image as cfg.exe.
    """
    _word(index);_word(off)
    if type(extent) is not int or not 1<=extent<=0x10000 or off+extent>0x10000:
        raise ValueError('overlay extent must be positive and fit the 16-bit offset space')
    image=cfg.exe
    if len(image)<28 or image[:2]!=b'MZ':
        raise ValueError('invalid original MZ header')
    last,pages,relocs,header_paras=struct.unpack_from('<4H',image,2)
    header=header_paras*16
    end=(pages-1)*512+last if last else pages*512
    reloc_table=struct.unpack_from('<H',image,0x18)[0]
    if (pages==0 or last>=512 or header<28 or header>end or end>len(image) or
            (relocs and (reloc_table<28 or reloc_table+relocs*4>header))):
        raise ValueError('invalid original MZ bounds')
    if end+16>len(image) or image[end:end+4]!=b'FBOV':
        raise ValueError('missing or truncated original FBOV header')
    table,count=struct.unpack_from('<II',image,end+8)
    if table<header or table+count*8>end or index>=count:
        raise ValueError('invalid original FBOV segment table or index')
    paragraph,maximum,flags,minimum=struct.unpack_from('<4H',image,table+index*8)
    stub=header+paragraph*16
    if flags!=3 or stub<header or stub+16>end:
        raise ValueError('invalid original overlay stub bounds or flags')
    signature,_,file_offset,code_size,fixup_size,_,_=struct.unpack_from('<2sHI4H',image,stub)
    body=end+16+file_offset
    if (signature!=b'\xcd?' or code_size==0 or fixup_size%2 or
            body<end+16 or body+code_size+fixup_size>len(image) or
            off+extent>code_size):
        raise ValueError('invalid original overlay header, body bounds or requested span')
    return paragraph,body+off
