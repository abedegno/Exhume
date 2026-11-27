package cart

// The functions in this file are inlined at every call, so the binary has no function of this
// file: the skeleton must create it.

// roundUp rounds up to a multiple of ten.
func roundUp(n int) int {
	return (n + 9) / 10 * 10
}

// counter has no runtime type information, so its method cannot be given a receiver.
type counter int

func (c counter) twice() int {
	if c < 0 {
		return 0
	}
	return int(c) * 2
}
