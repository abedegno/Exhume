# UW2's named constants: where the names came from (step 3)

UW2Decomp commit 165ed97 replaced 1,251 hex literals (about 1,780 uses) with names, each only where evidence says what the number means. The general rule and its traps are in skills/readability-pass; these are the UW2-specific scripts that produced the biggest group, the 448 item ids in `items.h`, from the game's own strings. They read your copy of the game and write their outputs beside themselves; nothing they produce is committed here.

- `strpak.py STRINGS.PAK strings.json`: decodes UW's string file (a Huffman tree of 4-byte nodes, then blocks of bit-packed strings ending in `|`) into JSON, block by block. Block 4 holds the item names.
- `genitems.py`: names each item id after its block 4 string (`ITEM_` plus the name in capitals, the article and plural forms after `_` and `&` dropped), with the id appended where two items share a name. Writes `itemnames.json`.
- `writeitems.py`: writes `items_table.h`, the `#define`s in groups by object class with the game's string as each comment, applying a few hand corrections (names the string block leaves ambiguous, such as the paired trigger ids).

The other constants came from documents and code, not scripts: the object classes from the FM Towns build's major-class enum, field masks and list sizes from the object format in UW-Formats, string block numbers, fonts and palettes from the loaders, tile types and terrain classes from the map format, skills and quest bytes from the player record. Each group in UW2Decomp's headers says where its names and values come from.
