// Package cart adds up a shopping cart.
package cart

// Item is one line of a cart.
type Item struct {
	Name  string
	Pence int
	Qty   int
}

// price is small enough for the compiler to inline into Total.
func price(i Item) int {
	return i.Pence * i.Qty
}

// Total is the sum of the prices of the items.
//
//go:noinline
func Total(items []Item) int {
	total := 0
	for _, it := range items {
		total += price(it)
	}
	return total
}
