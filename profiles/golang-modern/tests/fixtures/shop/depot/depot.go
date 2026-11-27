// Package depot gives the skeleton generator's tests a generic type that a struct field
// instantiates, a generic method that the compiler inlines at every call, and a method with a
// map parameter whose signature the method table keeps.
package depot

import "shop/cart"

// Queue is a first-in first-out queue.
type Queue[T any] struct {
	items []T
}

// PushBack adds v at the back. The compiler inlines it at every call.
func (q *Queue[T]) PushBack(v T) { q.items = append(q.items, v) }

// Len counts the queued values.
//
//go:noinline
func (q *Queue[T]) Len() int { return len(q.items) }

// Restocker is anything that can take a delivery.
type Restocker interface {
	Restock(counts map[string]int64) int
}

// Depot holds orders.
type Depot struct {
	Name    string
	Orders  *Queue[string]
	Crates  *Queue[[]cart.Item]
	Pending int
}

// Deliver hands a delivery to anything that can take one.
//
//go:noinline
func Deliver(r Restocker, counts map[string]int64) int {
	return r.Restock(counts)
}

// NewDepot makes a depot with empty queues.
//
//go:noinline
func NewDepot(name string) *Depot {
	return &Depot{Name: name, Orders: &Queue[string]{}, Crates: &Queue[[]cart.Item]{}}
}

// Restock adds a delivery and reports how many orders are queued.
func (d *Depot) Restock(counts map[string]int64) int {
	for name := range counts {
		d.Orders.PushBack(name)
	}
	d.Crates.PushBack(nil)
	d.Pending += len(counts)
	return d.Orders.Len() + d.Crates.Len()
}
