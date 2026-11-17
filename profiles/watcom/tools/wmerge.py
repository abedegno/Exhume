"""Watcom merge profile entry point."""
from wcli import main as dispatch
def main(cfg,argv):
    return dispatch(cfg,argv,'merge')
