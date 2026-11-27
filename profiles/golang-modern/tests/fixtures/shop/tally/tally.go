// Package tally holds only a helper that the compiler inlines at every call, so the binary has
// no function of this package.
package tally

// Add adds two numbers.
func Add(a, b int) int { return a + b }
