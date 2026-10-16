"""Shared set-up for the ti-c2000 tests: the profile's tools and Exhume's on the path."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
FIXTURES = os.path.join(HERE, 'fixtures')
for p in (TOOLS, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
