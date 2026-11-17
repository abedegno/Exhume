"""Original synthetic format fixtures; no compiler or target bytes."""
import struct

def rec(k,p):
    b=bytes([k])+struct.pack('<H',len(p)+1)+p
    return b+bytes([-sum(b)&255])
def name(s):
    b=s.encode('ascii');return bytes([len(b)])+b
def u32(n):return struct.pack('<I',n)

def records(easy=True,threads=True):
    # Completely populated CODE and DATA; all-zero artificial payload except
    # independently chosen sentinel words. They are parser/gate witnesses.
    kinds=(0x98,0x90,0xa0,0x9c) if easy else (0x99,0x91,0xa1,0x9d)
    seg,pub,data,fix=kinds
    size=0x10020
    chunks=[(0x80,name('synthetic-fixture'))]
    if easy:chunks.append((0x88,b'\x80\xaa80386'))
    chunks += [(0x88,b'\x80\0Handcrafted OMF fixture'),
               (0x96,b''.join(name(s) for s in ('','CODE','CODE','DATA','DATA','DGROUP'))),
               (seg,bytes([0xa8 if easy else 0xa9])+u32(size)+b'\x02\x03\x01'),
               (seg,bytes([0xa8 if easy else 0xa9])+u32(size)+b'\x04\x05\x01'),
               (0x9a,b'\x06\xff\x02'),(0x8c,name('own_external')+b'\0'),
               (pub,b'\x00\x01'+name('own_code_high')+u32(0x10000)+b'\0'),
               (pub,b'\x01\x02'+name('own_data_high')+u32(0x10010)+b'\0')]
    if threads:chunks.append((fix,b'\x44\x01\x00\x02'))
    payloads=[bytearray(size),bytearray(size)]
    payloads[0][-4:]=b'OWNC';payloads[1][-4:]=b'OWND'
    for number,payload in enumerate(payloads,1):
        for off in range(0,size,512):
            chunks.append((data,bytes([number])+u32(off)+payload[off:off+512]))
            if number==1 and off==0x10000:
                loc=0xd4 if easy else 0xe4
                if threads:
                    chunks += [(fix,bytes([loc,0,0x8c])),
                               (fix,bytes([loc,4,0x88])+u32(0x10001)),
                               (fix,b'\x08\x01'+bytes([loc,8,0x8c]))]
                else:
                    chunks += [(fix,bytes([loc,0,0x14,1,2])),
                               (fix,bytes([loc,4,0x10,1,2])+u32(0x10001)),
                               (fix,bytes([loc,8,0x16,1,1]))]
    chunks.append((0x8a,b'\0'))
    return chunks

def object_bytes(easy=True,threads=True):
    return b''.join(rec(*r) for r in records(easy,threads))


def linked_image():
    """Own independent LE fixture; no parser AST or historical output used.

    Two 17-page objects. CODE high references resolve to DATA0, DATA0x10001,
    and DATA0x10010; three loader relocations occupy logical page16.
    """
    page_size=4096
    count=34
    obj=0xc4; pages=obj+48; fp=pages+count*4; fr=fp+(count+1)*4
    fix=b''.join(b'\x07\x10'+struct.pack('<hBI',off,2,target)
                 for off,target in ((0,0),(4,0x10001),(8,0x10010)))
    raw=bytearray(4096+count*page_size)
    raw[:4]=b'LE\0\0'
    for offset,value in {20:count,24:1,28:0,32:2,36:0x10020,
                         40:page_size,44:page_size,48:(count+1)*4+len(fix),
                         64:obj,68:2,72:pages,104:fp,108:fr,128:4096}.items():
        struct.pack_into('<I',raw,offset,value)
    struct.pack_into('<6I',raw,obj,0x10020,0x10000,0x2005,1,17,0)
    struct.pack_into('<6I',raw,obj+24,0x10020,0x30000,0x2003,18,17,0)
    for n in range(count):
        raw[pages+4*n:pages+4*n+4]=(n+1).to_bytes(3,'big')+b'\0'
    for n in range(count+1):
        struct.pack_into('<I',raw,fp+4*n,0 if n<=16 else len(fix))
    raw[fr:fr+len(fix)]=fix
    for offset,target in ((0,0),(4,0x10001),(8,0x10010)):
        struct.pack_into('<I',raw,4096+0x10000+offset,target)
    raw[4096+0x1001c:4096+0x10020]=b'OWNC'
    raw[4096+17*page_size+0x1001c:4096+17*page_size+0x10020]=b'OWND'
    return bytes(raw)
