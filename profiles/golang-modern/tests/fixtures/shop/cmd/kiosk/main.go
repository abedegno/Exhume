// Command kiosk stocks a shelf and prints what it holds.
package main

import (
	"fmt"

	"shop/cart"
	"shop/shelf"
)

func main() {
	sh := shelf.NewShelf(7, "front")
	for _, it := range []cart.Item{{Name: "tea", Pence: 250, Qty: 2}, {Name: "jam", Pence: 99, Qty: 3}} {
		if err := sh.Add(it); err != nil {
			fmt.Println(err)
		}
	}
	var st shelf.Stocker = sh
	n, ok := st.Stock("tea")
	fmt.Println(st.Label(), n, ok, sh.Items[0], cart.Total(sh.Items), sh.Bins)
	fmt.Println(shelf.Count([]shelf.Pair[string, int]{{Key: "a", Val: 1}}, shelf.Bins{1, 2}))
	fmt.Println(shelf.Labels{"a": "b"}, shelf.Report(sh))
	cart.Tick()
	v, err := cart.Parse("12")
	fmt.Println(cart.Square(v), err, cart.Label(v), cart.Bounds(v), cart.Ops[0](v))
	fmt.Println(shelf.Summary(sh))
	fmt.Println(cart.Rounded(v))
}
