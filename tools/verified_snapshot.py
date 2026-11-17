"""Validated immutable byte snapshots and guarded atomic JSON receipts.

Capture before parsing or copying. A receipt's expected hashes come from those
snapshots, never a second read made after validation. Not a Python sandbox.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib,json,os,tempfile

@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    raw: bytes
    sha256: str
    def json(self):
        return json.loads(self.raw)
    def check(self):
        if self.path.is_symlink() or self.path.read_bytes()!=self.raw:
            raise ValueError(f'validated file changed: {self.path}')

def capture(path, expected_sha256=None):
    path=Path(path).absolute()
    if path.is_symlink():raise ValueError(f'symlink file: {path}')
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest!=expected_sha256:
        raise ValueError(f'file identity mismatch: {path}')
    return FileSnapshot(path,raw,digest)

def check(snapshots):
    for item in snapshots:item.check()

def capture_tree(root):
    root=Path(root).resolve();result={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError(f'symlink in validated tree: {path}')
        if path.is_file():result[path.relative_to(root).as_posix()]=capture(path)
    return result

def check_tree(root,snapshots):
    current=capture_tree(root)
    if set(current)!=set(snapshots):raise ValueError('validated tree file set changed')
    check(snapshots.values())

def copy_verified(snapshot,destination):
    snapshot.check();destination=Path(destination)
    if destination.absolute()==snapshot.path or destination.is_symlink():raise ValueError('unsafe snapshot destination')
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(snapshot.raw)
    result=capture(destination,snapshot.sha256);snapshot.check();return result

def publish_json(path,value,snapshots=()):
    """Publish complete JSON, retaining pins before and after atomic replacement.

    If validation fails after replacement, remove the new receipt; it must not
    look successful. An existing receipt is untouched when preparation fails.
    """
    path=Path(path);snapshots=tuple(snapshots);raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode();check(snapshots)
    if path.is_symlink():raise ValueError('symlink receipt')
    if any(path.resolve()==pin.path.resolve()for pin in snapshots):
        raise ValueError('receipt destination overlaps a validated input')
    path.parent.mkdir(parents=True,exist_ok=True);fd,temp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent);published=False
    try:
        with os.fdopen(fd,'wb')as out:out.write(raw)
        check(snapshots);os.replace(temp,path);published=True
        result=capture(path,hashlib.sha256(raw).hexdigest());check(snapshots);result.check();return result
    except Exception:
        if published:path.unlink(missing_ok=True)
        raise
    finally:
        Path(temp).unlink(missing_ok=True)
