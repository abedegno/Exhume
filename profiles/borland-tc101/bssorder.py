"""Predict where Turbo C++ 1.01 puts a file's uninitialised file-scope variables in _BSS,
and the order it lists a file's publics.

    python3 profiles/borland-tc101/bssorder.py NAME [NAME ...]   names in emitted order, with keys

Turbo C emits _BSS variables in ascending order of key(name) (no leading underscore, the
name cut to 32 characters), equal keys keeping definition order; anything wider than a
byte goes at an even offset. It lists a file's publics in descending key order, equal keys
in reverse order of first sight (a prototype counts). TLINK numbers overlay stub entries
from the last public listed, so an overlay's stub order constrains its function names.
Found from probe compiles on UW2's seg015; it predicted seg010's and ovr137's layouts too.
Use it to choose names for statics that have no original name, instead of probe compiles."""
import sys

def key(name):
    b = name.encode()[:32]; n = len(b)
    second = b[1] if n > 1 else 0
    penult = b[n - 2] if n > 1 else 0
    return (b[0] + 256 * second + 8 * penult + 64 * n) & 1023

if __name__ == '__main__':
    for i, n in sorted(enumerate(sys.argv[1:]), key=lambda t: (key(t[1]), t[0])):
        print(f'{key(n):4}  {n}')
