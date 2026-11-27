package cart

import (
	"errors"
	"strconv"

	"shop/tally"
)

// The functions in this file have results of known shapes, for the tests of the result
// recovery. Each is called from cmd/kiosk and its results are used there, except Triple.

var ticks int

// Tick has no results.
//
//go:noinline
func Tick() {
	ticks++
}

// Square returns one integer.
//
//go:noinline
func Square(n int) int {
	return n*n + 1
}

// Parse returns an integer and an error.
//
//go:noinline
func Parse(s string) (int, error) {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0, errors.New("not a number: " + s)
	}
	return n, nil
}

// Label returns a string.
//
//go:noinline
func Label(n int) string {
	if n > 1 {
		return "many"
	}
	return "one"
}

// Span is returned in two registers.
type Span struct {
	Lo, Hi int
}

// Bounds returns a struct in registers.
//
//go:noinline
func Bounds(n int) Span {
	return Span{Lo: n, Hi: n + 10}
}

// Triple is only called through a function value, so it has no direct call site.
//
//go:noinline
func Triple(n int) int {
	return 3 * n
}

// Ops holds Triple as a value.
var Ops = []func(int) int{Triple}

// Rounded uses helpers that are inlined at every call, one of them from a package of its own.
//
//go:noinline
func Rounded(n int) int {
	return tally.Add(roundUp(n), counter(n).twice())
}

// Lo ends on the line above Hi, so that line holds code in the binary.
//
//go:noinline
func Lo(n int) int { return n - 1 }
func Hi(n int) int { return Square(Lo(n)) + Square(n) } // too large to inline
