// gocmp: disassemble / compare Go functions between ELF amd64 binaries using pclntab.
//
//	gocmp dis BIN REGEX        print normalised asm with file:line
//	gocmp cmp ORIG NEW REGEX   compare functions present in both; report MATCH / DIFF
//	gocmp ref BIN REGEX        function bytes and per-instruction lines, for object-level scoring
//	flags: -v show side-by-side diff for mismatches
package main

import (
	"bufio"
	"debug/elf"
	"debug/gosym"
	"encoding/hex"
	"flag"
	"fmt"
	"os"
	"path"
	"regexp"
	"sort"
	"strings"

	"golang.org/x/arch/x86/x86asm"
)

type Bin struct {
	f      *elf.File
	tab    *gosym.Table
	text   []byte
	taddr  uint64
	lo, hi uint64 // address range considered "data/code addresses" for masking
	byName map[string]*gosym.Func
}

func load(p string) *Bin {
	f, err := elf.Open(p)
	if err != nil {
		panic(err)
	}
	ts := f.Section(".text")
	td, _ := ts.Data()
	pd, _ := f.Section(".gopclntab").Data()
	tab, err := gosym.NewTable(nil, gosym.NewLineTable(pd, ts.Addr))
	if err != nil {
		panic(err)
	}
	b := &Bin{f: f, tab: tab, text: td, taddr: ts.Addr, byName: map[string]*gosym.Func{}}
	b.lo = ^uint64(0)
	for _, s := range f.Sections {
		if s.Addr == 0 {
			continue
		}
		if s.Addr < b.lo {
			b.lo = s.Addr
		}
		if s.Addr+s.Size > b.hi {
			b.hi = s.Addr + s.Size
		}
	}
	for i := range tab.Funcs {
		fn := &tab.Funcs[i]
		b.byName[fn.Name] = fn
	}
	return b
}

type Line struct{ Asm, Src string }

func (b *Bin) dis(fn *gosym.Func) []Line {
	var out []Line
	code := b.text[fn.Entry-b.taddr : fn.End-b.taddr]
	for off := 0; off < len(code); {
		pc := fn.Entry + uint64(off)
		inst, err := x86asm.Decode(code[off:], 64)
		if err != nil {
			out = append(out, Line{fmt.Sprintf("?? %02x", code[off]), ""})
			off++
			continue
		}
		next := pc + uint64(inst.Len)
		var args []string
		for _, a := range inst.Args {
			if a == nil {
				break
			}
			args = append(args, b.arg(a, next, fn))
		}
		file, line, _ := b.tab.PCToLine(pc)
		s := strings.ToLower(inst.Op.String())
		if inst.DataSize != 0 && len(args) > 0 {
			s += fmt.Sprintf("/%d", inst.DataSize)
		}
		if len(inst.Prefix) > 0 && inst.Prefix[0]&0xff == 0xf3 && inst.Op != x86asm.MOVSS && inst.Op != x86asm.MOVSD_XMM {
			s = "rep " + s
		}
		out = append(out, Line{s + " " + strings.Join(args, ", "), fmt.Sprintf("%s:%d", path.Base(file), line)})
		off += inst.Len
	}
	return out
}

func (b *Bin) sym(addr uint64, fn *gosym.Func) string {
	if addr >= fn.Entry && addr < fn.End {
		return fmt.Sprintf("<+%#x>", addr-fn.Entry)
	}
	if f := b.tab.PCToFunc(addr); f != nil {
		if addr == f.Entry {
			return "<" + f.Name + ">"
		}
		return fmt.Sprintf("<%s+%#x>", f.Name, addr-f.Entry)
	}
	// static funcval: 8-byte word at addr pointing at a function entry
	for _, sec := range b.f.Sections {
		if sec.Type == elf.SHT_PROGBITS && addr >= sec.Addr && addr+8 <= sec.Addr+sec.Size && sec.Name != ".text" {
			d, err := sec.Data()
			if err == nil {
				o := addr - sec.Addr
				v := uint64(d[o]) | uint64(d[o+1])<<8 | uint64(d[o+2])<<16 | uint64(d[o+3])<<24 | uint64(d[o+4])<<32 | uint64(d[o+5])<<40 | uint64(d[o+6])<<48 | uint64(d[o+7])<<56
				if f := b.tab.PCToFunc(v); f != nil && f.Entry == v {
					return "<&" + f.Name + ">"
				}
			}
		}
	}
	if showAddr {
		if sec := b.f.Section(".rodata"); sec != nil && addr >= sec.Addr && addr < sec.Addr+sec.Size {
			d, _ := sec.Data()
			o := addr - sec.Addr
			e := o + 48
			if e > uint64(len(d)) {
				e = uint64(len(d))
			}
			return fmt.Sprintf("<DATA@%#x %q>", addr, d[o:e])
		}
		return fmt.Sprintf("<DATA@%#x>", addr)
	}
	return "<DATA>"
}

var showAddr bool

func (b *Bin) arg(a x86asm.Arg, next uint64, fn *gosym.Func) string {
	switch a := a.(type) {
	case x86asm.Rel:
		return b.sym(uint64(int64(next)+int64(a)), fn)
	case x86asm.Mem:
		if a.Base == x86asm.RIP {
			return "[" + b.sym(uint64(int64(next)+a.Disp), fn) + "]"
		}
		if a.Base == 0 && a.Index == 0 && uint64(a.Disp) >= b.lo && uint64(a.Disp) < b.hi {
			return "[" + b.sym(uint64(a.Disp), fn) + "]"
		}
		s := ""
		if a.Segment != 0 {
			s = a.Segment.String() + ":"
		}
		s += "["
		parts := []string{}
		if a.Base != 0 {
			parts = append(parts, a.Base.String())
		}
		if a.Index != 0 {
			parts = append(parts, fmt.Sprintf("%s*%d", a.Index, a.Scale))
		}
		if a.Disp != 0 || len(parts) == 0 {
			parts = append(parts, fmt.Sprintf("%#x", a.Disp))
		}
		return s + strings.Join(parts, "+") + "]"
	case x86asm.Imm:
		if uint64(a) >= b.lo && uint64(a) < b.hi {
			return b.sym(uint64(a), fn)
		}
		return fmt.Sprintf("$%#x", int64(a))
	default:
		return a.String()
	}
}

// masked is one instruction with every register replaced by its class and no branch or call
// target: two instructions with the same masked form differ only in register choice.
func masked(code []byte) string {
	inst, err := x86asm.Decode(code, 64)
	if err != nil {
		return "??"
	}
	reg := func(r x86asm.Reg) string {
		switch {
		case r == 0:
			return ""
		case r == x86asm.RSP || r == x86asm.ESP || r == x86asm.RIP:
			return r.String()
		case r >= x86asm.X0 && r <= x86asm.X15:
			return "x"
		case r >= x86asm.ES && r <= x86asm.GS:
			return r.String()
		}
		return "r"
	}
	out := []string{inst.Op.String(), fmt.Sprint(inst.DataSize)}
	for _, a := range inst.Args {
		if a == nil {
			break
		}
		switch a := a.(type) {
		case x86asm.Reg:
			out = append(out, reg(a))
		case x86asm.Mem:
			out = append(out, fmt.Sprintf("[%s%s+%s*%d+%#x]", reg(a.Segment), reg(a.Base), reg(a.Index), a.Scale, a.Disp))
		case x86asm.Imm:
			out = append(out, fmt.Sprintf("$%#x", int64(a)))
		default:
			out = append(out, "rel")
		}
	}
	return strings.Join(out, ",")
}

func main() {
	verbose := flag.Bool("v", false, "show diffs")
	firstWindow := flag.Int("first", 0, "with -v, show only N lines of context around the first mismatching line (0 = all)")
	flag.BoolVar(&showAddr, "addr", false, "show data addresses")
	flag.Parse()
	a := flag.Args()
	switch a[0] {
	case "dis":
		b := load(a[1])
		re := regexp.MustCompile(a[2])
		for _, fn := range b.tab.Funcs {
			if !re.MatchString(fn.Name) {
				continue
			}
			fmt.Printf("\n== %s  (%d bytes)\n", fn.Name, fn.End-fn.Entry)
			for _, l := range b.dis(&fn) {
				fmt.Printf("  %-18s %s\n", l.Src, l.Asm)
			}
		}
	case "mask":
		// gocmp mask: hex instructions on stdin, one per line; prints each with its registers masked
		sc := bufio.NewScanner(os.Stdin)
		sc.Buffer(make([]byte, 1<<20), 1<<20)
		for sc.Scan() {
			code, err := hex.DecodeString(strings.TrimSpace(sc.Text()))
			if err != nil || len(code) == 0 {
				fmt.Println("??")
				continue
			}
			fmt.Println(masked(code))
		}
	case "ref":
		// gocmp ref BIN REGEX: per function `FUNC name size hex`, then `I offset length line` per
		// instruction, for scoring a compiled object (go tool objdump of a package archive)
		// against the image without linking.
		b := load(a[1])
		re := regexp.MustCompile(a[2])
		for _, fn := range b.tab.Funcs {
			if !re.MatchString(fn.Name) {
				continue
			}
			code := b.text[fn.Entry-b.taddr : fn.End-b.taddr]
			fmt.Printf("FUNC %s %d %x\n", fn.Name, len(code), code)
			for off := 0; off < len(code); {
				n := 1
				if inst, err := x86asm.Decode(code[off:], 64); err == nil {
					n = inst.Len
				}
				_, line, _ := b.tab.PCToLine(fn.Entry + uint64(off))
				fmt.Printf("I %d %d %d %s\n", off, n, line, masked(code[off:off+n]))
				off += n
			}
		}
	case "str":
		// gocmp str BIN ADDR LEN
		b := load(a[1])
		var addr, n uint64
		fmt.Sscanf(a[2], "0x%x", &addr)
		fmt.Sscanf(a[3], "%d", &n)
		for _, sec := range b.f.Sections {
			if addr >= sec.Addr && addr+n <= sec.Addr+sec.Size {
				d, _ := sec.Data()
				fmt.Printf("%q\n", d[addr-sec.Addr:addr-sec.Addr+n])
			}
		}
	case "raw":
		o, n := load(a[1]), load(a[2])
		re := regexp.MustCompile(a[3])
		tot, rel, bad := 0, 0, 0
		for _, of := range o.tab.Funcs {
			if !re.MatchString(of.Name) {
				continue
			}
			nf := n.byName[of.Name]
			if nf == nil {
				fmt.Println("MISSING", of.Name)
				bad++
				continue
			}
			x := o.text[of.Entry-o.taddr : of.End-o.taddr]
			y := n.text[nf.Entry-n.taddr : nf.End-n.taddr]
			if len(x) != len(y) {
				fmt.Println("SIZE", of.Name)
				bad++
				continue
			}
			mask := make([]bool, len(x))
			for off := 0; off < len(x); {
				in, err := x86asm.Decode(x[off:], 64)
				if err != nil {
					off++
					continue
				}
				if in.PCRel > 0 {
					for i := 0; i < in.PCRel; i++ {
						mask[off+in.PCRelOff+i] = true
					}
				}
				off += in.Len
			}
			for i := range x {
				tot++
				if x[i] != y[i] {
					if mask[i] {
						rel++
					} else {
						bad++
						fmt.Printf("UNMASKED %s +%#x: %02x vs %02x\n", of.Name, i, x[i], y[i])
					}
				}
			}
		}
		fmt.Printf("%d bytes; %d differ inside PC-relative displacement fields; %d differ elsewhere\n", tot, rel, bad)
	case "cmp":
		o, n := load(a[1]), load(a[2])
		re := regexp.MustCompile(a[3])
		var names []string
		for _, fn := range o.tab.Funcs {
			if re.MatchString(fn.Name) {
				names = append(names, fn.Name)
			}
		}
		sort.Strings(names)
		ok := 0
		for _, name := range names {
			of := o.byName[name]
			nf := n.byName[name]
			if nf == nil {
				fmt.Printf("MISSING  %s\n", name)
				continue
			}
			ol, nl := o.dis(of), n.dis(nf)
			codeSame, lineSame := len(ol) == len(nl), len(ol) == len(nl)
			for i := 0; codeSame && i < len(ol); i++ {
				if ol[i].Asm != nl[i].Asm {
					codeSame = false
				}
				if ol[i].Src != nl[i].Src {
					lineSame = false
				}
			}
			st := "MATCH   "
			switch {
			case codeSame && lineSame:
				ok++
			case codeSame:
				st = "LINES   "
			default:
				st = "DIFF    "
			}
			fmt.Printf("%s %s  (%d vs %d bytes)\n", st, name, of.End-of.Entry, nf.End-nf.Entry)
			if *verbose && st != "MATCH   " {
				m := len(ol)
				if len(nl) > m {
					m = len(nl)
				}
				start, end := 0, m
				if *firstWindow > 0 {
					firstDiff := -1
					for i := 0; i < m; i++ {
						var x, y Line
						if i < len(ol) {
							x = ol[i]
						}
						if i < len(nl) {
							y = nl[i]
						}
						if x != y {
							firstDiff = i
							break
						}
					}
					if firstDiff >= 0 {
						start = firstDiff - 5
						if start < 0 {
							start = 0
						}
						end = firstDiff + *firstWindow
						if end > m {
							end = m
						}
					}
				}
				if start > 0 {
					fmt.Printf("   ... (%d matching lines omitted)\n", start)
				}
				for i := start; i < end; i++ {
					var x, y Line
					if i < len(ol) {
						x = ol[i]
					}
					if i < len(nl) {
						y = nl[i]
					}
					mark := " "
					if x != y {
						mark = "*"
					}
					fmt.Printf("   %s %-16s %-44s | %-16s %s\n", mark, x.Src, x.Asm, y.Src, y.Asm)
				}
				if end < m {
					fmt.Printf("   ... (%d more lines omitted)\n", m-end)
				}
			}
		}
		fmt.Printf("\n%d/%d functions match (code + line table)\n", ok, len(names))
	}
}
