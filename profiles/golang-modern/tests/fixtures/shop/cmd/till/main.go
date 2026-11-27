// Command till prints the total of a fixed cart.
package main

import (
	"fmt"

	"shop/cart"
)

func main() {
	items := []cart.Item{{Name: "tea", Pence: 250, Qty: 2}, {Name: "jam", Pence: 99, Qty: 3}}
	fmt.Println(cart.Total(items))
}
