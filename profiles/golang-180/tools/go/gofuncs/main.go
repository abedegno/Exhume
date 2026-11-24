// gofuncs: list functions from an ELF Go binary's pclntab (works on stripped binaries).
// usage: gofuncs BINARY  -> TSV: entry end size file startline endline name
package main

import (
	"debug/elf"
	"debug/gosym"
	"fmt"
	"os"
)

func main() {
	f, err := elf.Open(os.Args[1])
	if err != nil {
		panic(err)
	}
	pcln := f.Section(".gopclntab")
	text := f.Section(".text")
	data, err := pcln.Data()
	if err != nil {
		panic(err)
	}
	lt := gosym.NewLineTable(data, text.Addr)
	tab, err := gosym.NewTable(nil, lt)
	if err != nil {
		panic(err)
	}
	for _, fn := range tab.Funcs {
		file, l0, _ := tab.PCToLine(fn.Entry)
		minL, maxL := l0, l0
		for pc := fn.Entry; pc < fn.End; pc++ {
			if _, l, _ := tab.PCToLine(pc); l > 0 {
				if l < minL {
					minL = l
				}
				if l > maxL {
					maxL = l
				}
			}
		}
		fmt.Printf("%#x\t%#x\t%d\t%s\t%d\t%d\t%s\n", fn.Entry, fn.End, fn.End-fn.Entry, file, minL, maxL, fn.Name)
	}
}
