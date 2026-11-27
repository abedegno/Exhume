// Package shelf keeps stock on shelves. It exists to give the skeleton generator's tests a
// little of everything: embedded fields, tags, an interface, named slice and map types, value
// and pointer receivers, a generic type and function, and closures.
package shelf

import (
	"fmt"
	"sync"

	"shop/cart"
)

// Stocker is anything that can report its stock.
type Stocker interface {
	Stock(sku string) (int, bool)
	Label() string
}

// Labels maps a stock code to its label.
type Labels map[string]string

// Bins is a row of bins.
type Bins []int

// Base is embedded in Shelf.
type Base struct {
	ID   int64
	Note string
}

// Shelf holds items.
type Shelf struct {
	Base
	sync.Mutex
	Items  []cart.Item `json:"items"`
	labels Labels      `json:"-"`
	Bins   Bins        `json:"bins,omitempty"`
	byName map[string]*cart.Item
	Done   chan struct{}
	OnAdd  func(cart.Item) error
}

// Pair is a key and a value.
type Pair[K comparable, V any] struct {
	Key K
	Val V
}

// NewShelf makes an empty shelf.
//
//go:noinline
func NewShelf(id int64, note string) *Shelf {
	return &Shelf{Base: Base{ID: id, Note: note}, labels: Labels{}, byName: map[string]*cart.Item{}}
}

// Stock reports how many of an item the shelf holds.
func (s *Shelf) Stock(sku string) (int, bool) {
	s.Lock()
	defer s.Unlock()
	it, ok := s.byName[sku]
	if !ok {
		return 0, false
	}
	return it.Qty, true
}

// Label names the shelf.
func (b Base) Label() string {
	return fmt.Sprintf("%d %s", b.ID, b.Note)
}

// Add puts an item on the shelf.
func (s *Shelf) Add(it cart.Item) error {
	check := func() error {
		if it.Qty <= 0 {
			return fmt.Errorf("no %s", it.Name)
		}
		return nil
	}
	if err := check(); err != nil {
		return err
	}
	if s.OnAdd != nil {
		if err := s.OnAdd(it); err != nil {
			return err
		}
	}
	s.Items = append(s.Items, it)
	s.byName[it.Name] = &s.Items[len(s.Items)-1]
	s.labels[it.Name] = it.Name
	return nil
}

// Report describes anything that holds stock.
//
//go:noinline
func Report(s Stocker) string {
	n, _ := s.Stock("tea")
	return fmt.Sprint(s.Label(), n)
}

// First returns the key of the first pair.
//
//go:noinline
func First[K comparable, V any](ps []Pair[K, V]) (K, bool) {
	var zero K
	if len(ps) == 0 {
		return zero, false
	}
	return ps[0].Key, true
}

// Count counts the pairs and labels them.
//
//go:noinline
func Count(ps []Pair[string, int], b Bins) int {
	k, _ := First(ps)
	return len(k) + len(b)
}
