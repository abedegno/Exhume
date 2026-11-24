// golift: Go 1.8 ELF .typelink type extractor and compact line-grouped lifter.
//
//   golift types BIN REGEX   extract exact Go structs, tags, interfaces, and method signatures from .typelink/.rodata
//   golift lift  BIN REGEX   emit a compact, line-grouped, string/type-resolved brief per function
//
// Designed for Go 1.8 (linux/amd64, .gopclntab + .typelink + .rodata) to slash LLM token usage.
package main

import (
	"debug/elf"
	"debug/gosym"
	"encoding/binary"
	"fmt"
	"os"
	"path"
	"regexp"
	"sort"
	"strings"
	"unicode"

	"golang.org/x/arch/x86/x86asm"
)

const kindMask = (1 << 5) - 1

const (
	kindBool = 1 + iota
	kindInt
	kindInt8
	kindInt16
	kindInt32
	kindInt64
	kindUint
	kindUint8
	kindUint16
	kindUint32
	kindUint64
	kindUintptr
	kindFloat32
	kindFloat64
	kindComplex64
	kindComplex128
	kindArray
	kindChan
	kindFunc
	kindInterface
	kindMap
	kindPtr
	kindSlice
	kindString
	kindStruct
	kindUnsafePointer
)

const (
	tflagUncommon  = 1 << 0
	tflagExtraStar = 1 << 1
	tflagNamed     = 1 << 2
)

type Bin struct {
	f          *elf.File
	tab        *gosym.Table
	text       []byte
	taddr      uint64
	rodata     []byte
	roaddr     uint64
	typelink   []byte
	lo, hi     uint64
	byName     map[string]*gosym.Func
	typeByAddr map[uint64]*GoType
}

type GoField struct {
	Name   string
	Type   string
	Tag    string
	Offset uint64
	Anon   bool
}

type GoMethod struct {
	Name string
	Sig  string
	TFN  string
}

type GoType struct {
	Addr     uint64
	Kind     uint8
	TFlag    uint8
	Size     uint64
	Name     string
	PkgPath  string
	Fields   []GoField
	Methods  []GoMethod
	IMethods []GoMethod
	Elem     uint64
	Key      uint64
	Len      uint64
	ChanDir  uint64
	FuncIn   []uint64
	FuncOut  []uint64
	Variadic bool
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
	rs := f.Section(".rodata")
	var rd []byte
	var raddr uint64
	if rs != nil {
		rd, _ = rs.Data()
		raddr = rs.Addr
	}
	var tl []byte
	if tls := f.Section(".typelink"); tls != nil {
		tl, _ = tls.Data()
	}
	b := &Bin{
		f:          f,
		tab:        tab,
		text:       td,
		taddr:      ts.Addr,
		rodata:     rd,
		roaddr:     raddr,
		typelink:   tl,
		byName:     map[string]*gosym.Func{},
		typeByAddr: map[uint64]*GoType{},
	}
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
	b.parseAllTypes()
	return b
}

func (b *Bin) readBytes(addr uint64, n uint64) ([]byte, bool) {
	if n == 0 || addr < b.roaddr || n > uint64(len(b.rodata)) {
		return nil, false
	}
	o := addr - b.roaddr
	if o > uint64(len(b.rodata))-n {
		return nil, false
	}
	return b.rodata[o : o+n], true
}

func (b *Bin) u64(addr uint64) (uint64, bool) {
	bs, ok := b.readBytes(addr, 8)
	if !ok {
		return 0, false
	}
	return binary.LittleEndian.Uint64(bs), true
}

func (b *Bin) u32(addr uint64) (uint32, bool) {
	bs, ok := b.readBytes(addr, 4)
	if !ok {
		return 0, false
	}
	return binary.LittleEndian.Uint32(bs), true
}

func (b *Bin) u16(addr uint64) (uint16, bool) {
	bs, ok := b.readBytes(addr, 2)
	if !ok {
		return 0, false
	}
	return binary.LittleEndian.Uint16(bs), true
}

// Go 1.8 reflect.name decoding
func (b *Bin) readNameAt(addr uint64) (name, tag, pkgPath string, ok bool) {
	if addr == 0 {
		return "", "", "", false
	}
	hdr, good := b.readBytes(addr, 3)
	if !good {
		return "", "", "", false
	}
	flags := hdr[0]
	nlen := uint64(hdr[1])<<8 | uint64(hdr[2])
	if nlen > 4096 {
		return "", "", "", false
	}
	nb, good := b.readBytes(addr+3, nlen)
	if !good {
		return "", "", "", false
	}
	name = string(nb)
	pos := addr + 3 + nlen
	if flags&(1<<1) != 0 { // has tag
		th, good := b.readBytes(pos, 2)
		if good {
			tlen := uint64(th[0])<<8 | uint64(th[1])
			if tlen <= 4096 {
				if tb, good := b.readBytes(pos+2, tlen); good {
					tag = string(tb)
				}
				pos += 2 + tlen
			}
		}
	}
	if flags&(1<<2) != 0 { // has pkgPath
		if off, good := b.u32(pos); good && off != 0 {
			p, _, _, _ := b.readNameAt(b.roaddr + uint64(int32(off)))
			pkgPath = p
		}
	}
	return name, tag, pkgPath, true
}

func (b *Bin) readNameOff(off int32) (name, tag, pkgPath string) {
	if off <= 0 {
		return "", "", ""
	}
	n, t, p, _ := b.readNameAt(b.roaddr + uint64(off))
	return n, t, p
}

func (b *Bin) parseType(addr uint64) *GoType {
	if addr == 0 {
		return nil
	}
	if t, ok := b.typeByAddr[addr]; ok {
		return t
	}
	raw, ok := b.readBytes(addr, 48)
	if !ok {
		return nil
	}
	size := binary.LittleEndian.Uint64(raw[0:8])
	tflag := raw[20]
	kind := raw[23] & kindMask
	strOff := int32(binary.LittleEndian.Uint32(raw[40:44]))
	name, _, pkg := b.readNameOff(strOff)
	if tflag&tflagExtraStar != 0 && strings.HasPrefix(name, "*") {
		name = name[1:]
	}
	gt := &GoType{
		Addr:    addr,
		Kind:    kind,
		TFlag:   tflag,
		Size:    size,
		Name:    name,
		PkgPath: pkg,
	}
	b.typeByAddr[addr] = gt

	// Compute uncommonType offset after kind-specific Go 1.8 type struct
	uncommonOff := uint64(48)
	switch kind {
	case kindArray:
		uncommonOff = 72 // rtype(48) + elem(8) + slice(8) + len(8)
		gt.Elem, _ = b.u64(addr + 48)
		gt.Len, _ = b.u64(addr + 64)
	case kindChan:
		uncommonOff = 64 // rtype(48) + elem(8) + dir(8)
		gt.Elem, _ = b.u64(addr + 48)
		gt.ChanDir, _ = b.u64(addr + 56)
	case kindFunc:
		inC, _ := b.u16(addr + 48)
		outRaw, _ := b.u16(addr + 50)
		gt.Variadic = (outRaw & (1 << 15)) != 0
		outC := outRaw & 0x7fff
		uncommonOff = 56
		paramBase := addr + 56
		if tflag&tflagUncommon != 0 {
			paramBase += 16
		}
		if inC <= 64 && outC <= 64 {
			for i := uint16(0); i < inC; i++ {
				if p, ok := b.u64(paramBase + uint64(i)*8); ok {
					gt.FuncIn = append(gt.FuncIn, p)
					b.parseType(p)
				}
			}
			for i := uint16(0); i < outC; i++ {
				if p, ok := b.u64(paramBase + uint64(inC+i)*8); ok {
					gt.FuncOut = append(gt.FuncOut, p)
					b.parseType(p)
				}
			}
		}
	case kindInterface:
		uncommonOff = 80 // rtype(48) + pkgPath(8) + methods slice(24)
		if pp, ok := b.u64(addr + 48); ok && pp != 0 {
			p, _, _, _ := b.readNameAt(pp)
			if p != "" {
				gt.PkgPath = p
			}
		}
		mptr, ok1 := b.u64(addr + 56)
		mlen, ok2 := b.u64(addr + 64)
		if ok1 && ok2 && mlen <= 512 {
			for i := uint64(0); i < mlen; i++ {
				no, okA := b.u32(mptr + i*8)
				to, okB := b.u32(mptr + i*8 + 4)
				if !okA || !okB {
					break
				}
				mn, _, _ := b.readNameOff(int32(no))
				mt := b.parseType(b.roaddr + uint64(int32(to)))
				gt.IMethods = append(gt.IMethods, GoMethod{Name: mn, Sig: b.formatFuncSig(mt)})
			}
		}
	case kindMap:
		uncommonOff = 88 // rtype(48) + key(8) + elem(8) + bucket(8) + hmap(8) + keysize(1)+indirectkey(1)+valuesize(1)+indirectvalue(1)+bucketsize(2)+reflexivekey(1)+needkeyupdate(1)
		gt.Key, _ = b.u64(addr + 48)
		gt.Elem, _ = b.u64(addr + 56)
	case kindPtr:
		uncommonOff = 56 // rtype(48) + elem(8)
		gt.Elem, _ = b.u64(addr + 48)
	case kindSlice:
		uncommonOff = 56 // rtype(48) + elem(8)
		gt.Elem, _ = b.u64(addr + 48)
	case kindStruct:
		uncommonOff = 80 // rtype(48) + pkgPath(8) + fields slice(24)
		if pp, ok := b.u64(addr + 48); ok && pp != 0 {
			p, _, _, _ := b.readNameAt(pp)
			if p != "" {
				gt.PkgPath = p
			}
		}
		fptr, ok1 := b.u64(addr + 56)
		flen, ok2 := b.u64(addr + 64)
		if ok1 && ok2 && flen <= 1024 {
			for i := uint64(0); i < flen; i++ {
				base := fptr + i*24
				nptr, okA := b.u64(base)
				tptr, okB := b.u64(base + 8)
				foff, okC := b.u64(base + 16)
				if !okA || !okB || !okC {
					break
				}
				fn, tag, _, _ := b.readNameAt(nptr)
				ft := b.parseType(tptr)
				ftName := "<?>"
				if ft != nil {
					ftName = ft.Name
				}
				gt.Fields = append(gt.Fields, GoField{
					Name:   fn,
					Type:   ftName,
					Tag:    tag,
					Offset: foff,
					Anon:   fn == "",
				})
			}
		}
	}

	if tflag&tflagUncommon != 0 {
		uaddr := addr + uncommonOff
		if rawU, ok := b.readBytes(uaddr, 16); ok {
			pkgOff := int32(binary.LittleEndian.Uint32(rawU[0:4]))
			mcount := binary.LittleEndian.Uint16(rawU[4:6])
			moff := binary.LittleEndian.Uint32(rawU[8:12])
			if pkgOff > 0 {
				p, _, _ := b.readNameOff(pkgOff)
				if p != "" {
					gt.PkgPath = p
				}
			}
			if mcount > 0 && mcount <= 512 && moff > 0 && moff < 1<<20 {
				mbase := uaddr + uint64(moff)
				for i := uint16(0); i < mcount; i++ {
					mb, ok := b.readBytes(mbase+uint64(i)*16, 16)
					if !ok {
						break
					}
					no := int32(binary.LittleEndian.Uint32(mb[0:4]))
					to := int32(binary.LittleEndian.Uint32(mb[4:8]))
					mn, _, _ := b.readNameOff(no)
					sig := ""
					if to > 0 {
						mt := b.parseType(b.roaddr + uint64(to))
						sig = b.formatFuncSig(mt)
					}
					gt.Methods = append(gt.Methods, GoMethod{
						Name: mn,
						Sig:  sig,
					})
				}
			}
		}
	}

	if gt.Elem != 0 {
		b.parseType(gt.Elem)
	}
	if gt.Key != 0 {
		b.parseType(gt.Key)
	}
	return gt
}

func (b *Bin) formatFuncSig(ft *GoType) string {
	if ft == nil || ft.Kind != kindFunc {
		return "(?)"
	}
	var ins []string
	for i, p := range ft.FuncIn {
		t := b.typeByAddr[p]
		tn := "?"
		if t != nil {
			tn = t.Name
			if ft.Variadic && i == len(ft.FuncIn)-1 && strings.HasPrefix(tn, "[]") {
				tn = "..." + tn[2:]
			}
		}
		ins = append(ins, tn)
	}
	var outs []string
	for _, p := range ft.FuncOut {
		t := b.typeByAddr[p]
		tn := "?"
		if t != nil {
			tn = t.Name
		}
		outs = append(outs, tn)
	}
	s := "(" + strings.Join(ins, ", ") + ")"
	if len(outs) == 1 {
		s += " " + outs[0]
	} else if len(outs) > 1 {
		s += " (" + strings.Join(outs, ", ") + ")"
	}
	return s
}

func (b *Bin) parseAllTypes() {
	for i := 0; i+4 <= len(b.typelink); i += 4 {
		off := int32(binary.LittleEndian.Uint32(b.typelink[i : i+4]))
		if off <= 0 {
			continue
		}
		b.parseType(b.roaddr + uint64(off))
	}
}

var kindNames = map[uint8]string{
	kindBool: "bool", kindInt: "int", kindInt8: "int8", kindInt16: "int16", kindInt32: "int32", kindInt64: "int64",
	kindUint: "uint", kindUint8: "uint8", kindUint16: "uint16", kindUint32: "uint32", kindUint64: "uint64",
	kindUintptr: "uintptr", kindFloat32: "float32", kindFloat64: "float64", kindComplex64: "complex64",
	kindComplex128: "complex128", kindString: "string", kindUnsafePointer: "unsafe.Pointer",
}

func (b *Bin) formatTypeDecl(t *GoType) string {
	var sb strings.Builder
	fullName := t.Name
	if t.PkgPath != "" {
		sb.WriteString(fmt.Sprintf("// pkg: %s  size: %d bytes  addr: %#x\n", t.PkgPath, t.Size, t.Addr))
	} else {
		sb.WriteString(fmt.Sprintf("// size: %d bytes  addr: %#x\n", t.Size, t.Addr))
	}
	switch t.Kind {
	case kindStruct:
		sb.WriteString(fmt.Sprintf("type %s struct {\n", fullName))
		for _, f := range t.Fields {
			tag := ""
			if f.Tag != "" {
				tag = fmt.Sprintf(" `%s`", f.Tag)
			}
			if f.Anon {
				sb.WriteString(fmt.Sprintf("\t%s%s // +0x%x\n", f.Type, tag, f.Offset))
			} else {
				sb.WriteString(fmt.Sprintf("\t%-24s %s%s // +0x%x\n", f.Name, f.Type, tag, f.Offset))
			}
		}
		sb.WriteString("}\n")
	case kindInterface:
		sb.WriteString(fmt.Sprintf("type %s interface {\n", fullName))
		for _, m := range t.IMethods {
			sb.WriteString(fmt.Sprintf("\t%s%s\n", m.Name, m.Sig))
		}
		sb.WriteString("}\n")
	case kindSlice:
		elem := "?"
		if e := b.typeByAddr[t.Elem]; e != nil {
			elem = e.Name
		}
		sb.WriteString(fmt.Sprintf("type %s []%s\n", fullName, elem))
	case kindMap:
		k, v := "?", "?"
		if kt := b.typeByAddr[t.Key]; kt != nil {
			k = kt.Name
		}
		if vt := b.typeByAddr[t.Elem]; vt != nil {
			v = vt.Name
		}
		sb.WriteString(fmt.Sprintf("type %s map[%s]%s\n", fullName, k, v))
	case kindPtr:
		sb.WriteString(fmt.Sprintf("// pointer type %s\n", fullName))
	default:
		if kn, ok := kindNames[t.Kind]; ok {
			sb.WriteString(fmt.Sprintf("type %s %s\n", fullName, kn))
		} else {
			sb.WriteString(fmt.Sprintf("type %s /* kind=%d */\n", fullName, t.Kind))
		}
	}
	if len(t.Methods) > 0 {
		for _, m := range t.Methods {
			sb.WriteString(fmt.Sprintf("// method (%s) %s%s\n", fullName, m.Name, m.Sig))
		}
	}
	return sb.String()
}

// ---- Compact line-grouped lifter ----------------------------------------------------------------

func isPrintableUTF8(bs []byte) bool {
	if len(bs) == 0 {
		return false
	}
	for _, r := range string(bs) {
		if r == unicode.ReplacementChar {
			return false
		}
		if r < 0x20 && r != '\n' && r != '\r' && r != '\t' {
			return false
		}
	}
	return true
}

func (b *Bin) resolveSym(addr uint64, fn *gosym.Func) (label string, dataAddr uint64) {
	if addr >= fn.Entry && addr < fn.End {
		return fmt.Sprintf("<+%#x>", addr-fn.Entry), 0
	}
	if f := b.tab.PCToFunc(addr); f != nil {
		if addr == f.Entry {
			return "<" + f.Name + ">", 0
		}
		return fmt.Sprintf("<%s+%#x>", f.Name, addr-f.Entry), 0
	}
	// Static funcval in .rodata
	if v, ok := b.u64(addr); ok {
		if f := b.tab.PCToFunc(v); f != nil && f.Entry == v {
			return "<&" + f.Name + ">", 0
		}
	}
	// Known runtime._type in .rodata
	if t, ok := b.typeByAddr[addr]; ok {
		return fmt.Sprintf("<TYPE:%s>", t.Name), 0
	}
	return fmt.Sprintf("<DATA@%#x>", addr), addr
}

func (b *Bin) argStr(a x86asm.Arg, next uint64, fn *gosym.Func) (string, uint64) {
	switch a := a.(type) {
	case x86asm.Rel:
		s, d := b.resolveSym(uint64(int64(next)+int64(a)), fn)
		return s, d
	case x86asm.Mem:
		if a.Base == x86asm.RIP {
			s, d := b.resolveSym(uint64(int64(next)+a.Disp), fn)
			return "[" + s + "]", d
		}
		if a.Base == 0 && a.Index == 0 && uint64(a.Disp) >= b.lo && uint64(a.Disp) < b.hi {
			s, d := b.resolveSym(uint64(a.Disp), fn)
			return "[" + s + "]", d
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
		return s + strings.Join(parts, "+") + "]", 0
	case x86asm.Imm:
		if uint64(a) >= b.lo && uint64(a) < b.hi {
			s, d := b.resolveSym(uint64(a), fn)
			return s, d
		}
		return fmt.Sprintf("$%#x", int64(a)), 0
	default:
		return a.String(), 0
	}
}

type decodedInsn struct {
	off      int
	line     int
	file     string
	op       string
	args     []string
	rawOp    x86asm.Op
	dataAddr uint64
	immVal   int64
	hasImm   bool
}

func (b *Bin) liftFunc(fn *gosym.Func) string {
	code := b.text[fn.Entry-b.taddr : fn.End-b.taddr]
	var insns []decodedInsn
	for off := 0; off < len(code); {
		pc := fn.Entry + uint64(off)
		inst, err := x86asm.Decode(code[off:], 64)
		if err != nil {
			off++
			continue
		}
		next := pc + uint64(inst.Len)
		var args []string
		var daddr uint64
		var immVal int64
		hasImm := false
		for _, a := range inst.Args {
			if a == nil {
				break
			}
			as, da := b.argStr(a, next, fn)
			args = append(args, as)
			if da != 0 {
				daddr = da
			}
			if im, ok := a.(x86asm.Imm); ok {
				immVal = int64(im)
				hasImm = true
			}
		}
		file, line, _ := b.tab.PCToLine(pc)
		op := strings.ToLower(inst.Op.String())
		insns = append(insns, decodedInsn{
			off:      off,
			line:     line,
			file:     path.Base(file),
			op:       op,
			args:     args,
			rawOp:    inst.Op,
			dataAddr: daddr,
			immVal:   immVal,
			hasImm:   hasImm,
		})
		off += inst.Len
	}

	// Trim trailing INT3 padding and runtime.morestack_noctxt tail thunk
	endIdx := len(insns)
	for endIdx > 0 && insns[endIdx-1].rawOp == x86asm.INT && len(insns[endIdx-1].args) == 1 && insns[endIdx-1].args[0] == "$0x3" {
		endIdx--
	}
	if endIdx >= 2 && insns[endIdx-1].rawOp == x86asm.JMP && len(insns[endIdx-1].args) == 1 && insns[endIdx-1].args[0] == "<+0x0>" &&
		insns[endIdx-2].rawOp == x86asm.CALL && len(insns[endIdx-2].args) == 1 && strings.Contains(insns[endIdx-2].args[0], "runtime.morestack") {
		endIdx -= 2
	}
	insns = insns[:endIdx]

	// Trim standard stack-split prologue (FS TLS check + JBE to morestack)
	startIdx := 0
	if len(insns) >= 3 && strings.Contains(strings.Join(insns[0].args, ","), "FS:") &&
		insns[1].rawOp == x86asm.CMP && insns[2].rawOp == x86asm.JBE {
		startIdx = 3
	}
	insns = insns[startIdx:]

	// Heuristic string resolution: when LEA reg, [<DATA@addr>] is followed within 3 insns by MOV ..., $len (1..256)
	strHint := map[int]string{}
	for i := 0; i < len(insns); i++ {
		if insns[i].rawOp == x86asm.LEA && insns[i].dataAddr != 0 {
			addr := insns[i].dataAddr
			for j := i + 1; j < len(insns) && j <= i+4; j++ {
				if insns[j].rawOp == x86asm.MOV && insns[j].hasImm && insns[j].immVal > 0 && insns[j].immVal <= 256 {
					if bs, ok := b.readBytes(addr, uint64(insns[j].immVal)); ok && isPrintableUTF8(bs) {
						strHint[i] = fmt.Sprintf("STR:%q", string(bs))
						break
					}
				}
			}
			// Fallback: check if .rodata holds a Go string header (ptr, len) at addr (e.g. static string var)
			if _, ok := strHint[i]; !ok {
				if bs, ok := b.readBytes(addr, 32); ok && isPrintableUTF8(bs[:8]) {
					strHint[i] = fmt.Sprintf("RODATA:%q...", string(bs[:16]))
				}
			}
		}
	}

	var sb strings.Builder
	file, startLine, _ := b.tab.PCToLine(fn.Entry)
	minL, maxL := startLine, startLine
	for _, in := range insns {
		if in.line > 0 {
			if in.line < minL {
				minL = in.line
			}
			if in.line > maxL {
				maxL = in.line
			}
		}
	}
	sb.WriteString(fmt.Sprintf("\n== %s  (%s:%d-%d, %d bytes) ==\n", fn.Name, path.Base(file), minL, maxL, fn.End-fn.Entry))
	curLine := -1
	for i, in := range insns {
		if in.line != curLine {
			curLine = in.line
			sb.WriteString(fmt.Sprintf("  L%d:\n", curLine))
		}
		comment := ""
		if h, ok := strHint[i]; ok {
			comment = "   ; " + h
		}
		sb.WriteString(fmt.Sprintf("    +%03x  %-7s %s%s\n", in.off, in.op, strings.Join(in.args, ", "), comment))
	}
	return sb.String()
}

func main() {
	if len(os.Args) < 4 {
		fmt.Fprintf(os.Stderr, "usage:\n  golift types BIN REGEX\n  golift lift  BIN REGEX\n")
		os.Exit(2)
	}
	cmd, binPath, pattern := os.Args[1], os.Args[2], os.Args[3]
	b := load(binPath)
	re := regexp.MustCompile(pattern)

	switch cmd {
	case "types":
		var matched []*GoType
		for _, t := range b.typeByAddr {
			named := (t.TFlag & tflagNamed) != 0
			if !named && len(t.Methods) == 0 {
				continue
			}
			if strings.HasPrefix(t.Name, "struct {") {
				continue
			}
			if re.MatchString(t.PkgPath) || re.MatchString(t.Name) {
				matched = append(matched, t)
			}
		}
		sort.Slice(matched, func(i, j int) bool {
			if matched[i].PkgPath != matched[j].PkgPath {
				return matched[i].PkgPath < matched[j].PkgPath
			}
			return matched[i].Name < matched[j].Name
		})
		for _, t := range matched {
			fmt.Println(b.formatTypeDecl(t))
		}
	case "lift":
		// First dump matching types for the package prefix if any
		var fns []*gosym.Func
		for i := range b.tab.Funcs {
			fn := &b.tab.Funcs[i]
			if re.MatchString(fn.Name) {
				fns = append(fns, fn)
			}
		}
		for _, fn := range fns {
			fmt.Print(b.liftFunc(fn))
		}
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n", cmd)
		os.Exit(2)
	}
}
