# A decompilation project's build driver, over Exhume's tools/gate.py. Copy it to the
# project's root beside its exhume.toml; the gate's logic and the project's facts (link
# commands, known differing bytes, boot steps) are in exhume.toml's [gate] section.
#
#   make / make game   modding build, prints the EXE's path
#   make exact         exact link, compared with your original EXE
#   make check         the full gate; recompiles only sources changed since they last passed
#   make check-all     the full gate, recompiling every source
#   make fast          compile, match and verify the changed sources only (not a proof)
#   make boot          boot the modding build and screenshot it
#   make hooks         install the git pre-push hook that runs make check

EXHUME ?= $(HOME)/Exhume
PY := $(if $(wildcard $(EXHUME)/.venv/bin/python),$(EXHUME)/.venv/bin/python,python3)
GATE := $(PY) $(EXHUME)/tools/gate.py --config exhume.toml

.PHONY: game exact check check-all fast boot hooks repocheck help
.DEFAULT_GOAL := game

game:
	@$(GATE) game

exact:
	@$(GATE) exact

check:
	@$(GATE) check

check-all:
	@$(GATE) check --all

fast:
	@$(GATE) check --fast

boot:
	@$(GATE) boot

hooks:
	@sh $(EXHUME)/tools/install-hooks.sh . "make check"

repocheck:
	@$(PY) $(EXHUME)/tools/repocheck.py

help:
	@sed -n '1,/^$$/p' Makefile
