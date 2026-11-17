"""Extract verified TASM 3.1 files from preserved Borland C++ 3.1 media."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ARCHIVE_SHA256='76e155a7fb7eb0590930ecd513644c977497a020466a272943776fbf683d0f27'
SOURCE_URL='https://winworldpc.com/download/f7396558-9ba2-11e9-ab10-fa163e9022f0'
MEDIA_FOLDER='Borland CPP 3.1 and Application Frameworks (1992) (3.5-1.44mb)'
PARTS=[('Disk07.img','TASM.CA1','3dde1cd8b2eb5eb147866bc5faad0603b36b2855f222dbc4462b1a14a36aceb4'),
       ('Disk09.img','TASM.CA2','faeed31698d9dafb224e4b777716b0878a40d4ce136c546ee1a14723d832fa83')]
TOOLS={'TASM.EXE':'80534cd6a1e2c2b4376ae3f21954c6dbfa77a6998d5e0bf8b8bee6153f3174ea',
       'TASMX.EXE':'421decd9cde792c49cc35c096c93e092df98188e96fc43a58e00ee48a21b2724'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(archive,destination,members):
    subprocess.run(['7z','e','-y','-o'+str(destination),str(archive),*members],
                   check=True,capture_output=True)


def prepare(archive: Path,destination: Path) -> dict:
    """Publish only the two validated assembler executables; never run setup."""
    archive=archive.resolve();destination=destination.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    if digest(archive)!=ARCHIVE_SHA256:
        raise ValueError('Borland archive identity mismatch')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tasm-media-',dir=destination.parent) as directory:
        stage=Path(directory);media=stage/'media';media.mkdir()
        extract(archive,media,[MEDIA_FOLDER+'/'+disk for disk,_,_ in PARTS])
        parts=stage/'parts';parts.mkdir();payload=bytearray()
        for disk,part,expected in PARTS:
            extract(media/disk,parts,[part])
            if digest(parts/part)!=expected:
                raise ValueError('TASM package part identity mismatch')
            # Each Borland split-archive part has a five-byte package header.
            payload.extend((parts/part).read_bytes()[5:])
        package=stage/'TASM.ZIP';package.write_bytes(payload)
        home=stage/'home';home.mkdir()
        extract(package,home,list(TOOLS))
        entries={}
        for name,expected in TOOLS.items():
            if digest(home/name)!=expected:
                raise ValueError('TASM executable identity mismatch')
            entries[name]={'sha256':expected,'bytes':(home/name).stat().st_size}
        if destination.exists():
            raise FileExistsError(destination)
        home.rename(destination)
    return {'version':'Turbo Assembler 3.1','source_url':SOURCE_URL,
            'archive_sha256':ARCHIVE_SHA256,
            'provider_archive_sha1':'c130504ebdba28d41ba95fc70a46e99a787d6efa',
            'method':'Extract Disk07/Disk09 FAT12 images, verify TASM.CA1/CA2, remove each five-byte package header, concatenate ZIP and extract validated executables. No installer executed.',
            'tools':entries}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    report=prepare(args.archive,args.destination)
    args.manifest.parent.mkdir(parents=True,exist_ok=True)
    args.manifest.write_text(json.dumps(report,indent=2)+'\n')
    print('Verified Turbo Assembler 3.1 extracted.')
