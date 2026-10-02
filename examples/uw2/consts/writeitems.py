import json
names = {int(k): v for k, v in json.load(open('itemnames.json')).items()}
b = json.load(open('strings.json'))['4']
fix = {0x2F: 'ITEM_SWAMP_BOOTS', 0x1A5: 'ITEM_RELEASE_TRIGGER_1A5', 0x1B5: 'ITEM_RELEASE_TRIGGER_1B5',
       0x1A1: 'ITEM_PICKUP_TRIGGER_1A1', 0x1B1: 'ITEM_PICKUP_TRIGGER_1B1',
       0x184: 'ITEM_SPECIAL_EFFECT_TRAP', 0x186: 'ITEM_SPELL_TRAP',
       0x16E: 'ITEM_TMAP_C', 0x16F: 'ITEM_TMAP_S'}
names.update(fix)
groups = [
 (0x000, 0x00F, 'Weapons (class 0x00), hack_class_data\'s Weapons table'),
 (0x010, 0x01F, 'Missiles and missile weapons (class 0x01), the Missile table'),
 (0x020, 0x03F, 'Armour and clothing (classes 0x02 and 0x03), the Armor table'),
 (0x040, 0x07F, 'Creatures (major class 1); 0x7F is the player'),
 (0x080, 0x08F, 'Containers (class 0x08), misc_class_data\'s Containers table'),
 (0x090, 0x09F, 'Light sources (0x90-0x97, the Lights table) and wands (0x98-0x9F)'),
 (0x0A0, 0x0AF, 'Treasure (class 0x0A)'),
 (0x0B0, 0x0BF, 'Food and drink (class 0x0B), the Food table'),
 (0x0C0, 0x0DF, 'Scenery and junk (classes 0x0C and 0x0D)'),
 (0x0E0, 0x0FF, 'Potions and runestones (classes 0x0E and 0x0F)'),
 (0x100, 0x10F, 'Keys, the lockpick and the lock (class 0x10)'),
 (0x110, 0x11F, 'Unique and quest items (class 0x11)'),
 (0x120, 0x12F, 'Magic and useful items (class 0x12)'),
 (0x130, 0x13F, 'Books, scrolls and maps (class 0x13)'),
 (0x140, 0x14F, 'Doors (class 0x14); 0x148-0x14F are the open versions of 0x140-0x147'),
 (0x150, 0x16F, 'Furniture and other 3D objects (classes 0x15 and 0x16)'),
 (0x170, 0x17F, 'Buttons, switches, levers and pull chains (class 0x17); +8 is the other state'),
 (0x180, 0x19F, 'Traps (classes 0x18 and 0x19)'),
 (0x1A0, 0x1BF, 'Triggers (classes 0x1A and 0x1B)'),
 (0x1C0, 0x1CF, 'Animated objects (major class 7)'),
]
out = []
for lo, hi, title in groups:
    out.append('')
    out.append('/* %s */' % title)
    for i in range(lo, hi + 1):
        if i in names:
            s = b[i].split('&')[0].replace('_', ' ', 1) if '_' in b[i] else b[i].split('&')[0]
            out.append('#define %-32s 0x%03X  /* %s */' % (names[i], i, s))
open('items_table.h', 'w').write('\n'.join(out) + '\n')
print(len(names))
