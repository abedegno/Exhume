HEADER = """Combat: where a blow lands, what it hits, whether it hits, the damage it does, the
sounds of a miss, the player's weapon swing and charge, missiles striking, critters
attacking and the experience for a kill. The whole of DOS resident segment seg024_24E9,
in original order.

What it does in the game: one melee blow is a few globals filled in by the attacker's
side and then do_attack. The player's side is player_attack, called every frame from the
input code while a swing key or the right mouse button is held: it charges the blow
(play_pow), works out the weapon (GetPlayerWeapon, DoPlayerWeapon) and on release swings,
or for a bow or sling hands over to player_fire in MISSILE.C. A critter's side is
critter_attack, called by the AI (AI.C). do_attack finds the target in front of the
attacker (resolve_attack), rolls to hit (frp_check, a skill_check in SKILLCHK.C against
the defender's Creature defence) and either rolls damage (do_damage, which hands it to
damage_item in DAMAGE.C) or plays the miss (do_miss). missile_thwack is the same damage
path for a missile that has struck (MISSILE.C, PHYSICS.C), and player_killed_a gives the
experience for a kill.

Data owned: the attack globals shared by these steps (fromwho attacker, hitobj target,
towhere and swing, askill attack skill, damage, power, hitloc, hitangle, criti), the
player's swing state (pQatt, attackKey, play_pow) and the special weapon (specweap).
Tables: swing_kind, swing_keys, hitz_tab and the special weapon bonuses.
Function and global names are the originals from the FM Towns symbol table where it has
them.
Name: descriptive (melee and missile combat: resolve_attack, player_attack)."""

RETAG = [
("/* The player's own critter record. No FM Towns name: static there. */",
 "/* name: the player's own critter record. No FM Towns name: static there. */"),
("""/* Initialised data, DS:356 onwards. The statics have no FM Towns names; the names here
   are descriptive. */""",
 """/* Initialised data, DS:356 onwards. */
/* name: the statics have no FM Towns names; the names here are descriptive. */"""),
("""/* Uninitialised data, DS:24B0 onwards. Turbo C lays _BSS out in an order set by the names
   (some hash), not by declaration, so the static names""",
 """/* Uninitialised data, DS:24B0 onwards. */
/* match: Turbo C lays _BSS out in an order set by the names
   (some hash), not by declaration, so the static names"""),
("/* DOS only: no FM Towns counterpart between critter_attack and player_killed_a. */",
 """/* name: DOS only: no FM Towns counterpart between critter_attack and player_killed_a.
   It cancels a player blow that is still queued (pQatt > 0) and resets the weapon and
   power displays. */"""),
]

BEFORE = [
("int far pickloc(", """Picks the hit location (0..3) from the defender's bottom and top (dz, dtop) and the
attack's (az, atop): a blow whose middle is below the defender's bottom gives 2, above its
top 3, otherwise a weighted random choice favouring 2 or 3 by which half the blow lands in
and then 0 over 1. The location indexes the Creature armour values and cmbModTH; the
player's armour slot hit is (hitloc + 1) & 3. missile_thwack adds 4 for a missile."""),
("int far set_hitobj(", """From the objects ObjectCheck found in the blow's way (oCollisions[c->first ..
c->first + c->count]), picks the one nearest the attacker, skipping traps and the
attacker itself; the player's blows also skip allied mobiles unless that one is the only
candidate left. Sets targx, targy to its map square and returns its oCollisions index, or
-1."""),
("void far find_wall_coll(", """A blow that met a wall: steps along the heading in 1/16-square steps for dist + 1
steps until the terrain check reports a wall, and puts a short-lived ITEM_FLASH animation
object there (the spark). For the player it also sets hit_wall and plays the wall hit
sound, effect 7 with a weapon, 8 bare handed."""),
("unsigned char far resolve_attack(", """Where the attacker's blow goes: a motion probe of radius wsize + 1, at a height set by
the swing (towhere / 3 picks the low, middle or high third of the attacker; the player's
pitch shifts it), wsize + 3 fine units ahead along the attacker's heading. Returns 1 with
hitobj and hitloc set when it meets an object; on a wall it makes the spark
(find_wall_coll) and returns 0."""),
("char far is_sharp(", """True for an edged or pointed weapon: a MAJOR_HACK object of minor class 0 or 1 (the
weapons, missiles and launchers, items 0..0x1F) other than items 7..9 (the cudgel and the
two after it) and the sling. Used for the poison weapon bonus and the player's swing
sounds."""),
("int far frp_check(", """The to-hit roll. Returns 0 for a hit and 1 or 2 for a miss (1 - the skill_check
result). A non-critter defender is always hit; the player striking a door may wear his
weapon (a chance of 2 * (door id & 7) in 12). Against a critter: the attack skill plus
hitangle is checked against the critter's defence, less the player's protection at that
location (cmbModTH) when the player is the target. With poison weapon active, a sharp
weapon against a creature that bleeds adds damage * (skills[9] + 30) / 40. A critical
(result 2) sets criti and multiplies the damage by 1 or 2 at even odds; if the player is
the one hit, the screen flashes and the armour at that location takes wear. A bad miss
(result -1) by the player against a critter not marked passive wears his weapon."""),
("void far do_damage(", """Rolls and applies a hit's damage. damage is turned into dice, (damage / 6)d6 plus
1d(damage % 6), with a minimum of 2; the roll is scaled by power / 128 (the charge) and
hitangle is added, so blows from the side and behind do more. A critter's armour at the
hit location is subtracted (a location of 0xFF uses location 0; powerful critters'
armour counts 5/3); easy mode halves damage to the player. The result, ddone, goes to
damage_item with the damage type bits in type. The rest is feedback: the hit sound,
the critter's health shown in the view frame when the player is the attacker, blood for
critters that bleed (twice on a player's critical), and a dust effect otherwise."""),
("char far do_miss(", """The sound of a miss. hit 0 is a swing at nothing (effect 10, unless it met a wall).
Otherwise the blow was blocked: effect 7 for weapon kind 1 (the player's blunt weapons), or kind 2
(edged) against armour kind 1 (metal), else effect 8. For the player as target the armour
is the item in the slot for the hit location, and leather counts as not metal. Always
returns 0."""),
("void far compute_hitangle(", """hitangle: how far round from the defender's front the attacker stands, in eighths of
a turn, 0 (face to face) to 4 (from behind). It is added to the attack skill and to the
damage."""),
("char far do_attack(", """One melee blow from fromwho, after the caller has set wsize, towhere, power, damage and
askill. Critters do not hit their own side. A miss still calls damage_item with 0 damage
(so the target notices it, inferred). Returns 1 for a hit."""),
("int far check_ammo(", """Looks for the ammunition of missile weapon class weapon (Missile[].ammo, an offset
from FIRST_MISSILE) in the inventory. Returns its slot, or -1 after printing 'Sorry, you
have no <ammunition>.'."""),
("int far GetPlayerWeapon(", """The weapon in the player's weapon hand (slot 8 - lefty) and its data record: a Missile
record for a bow or sling (player_weapon 3; returns 0, or -1 with player_weapon 4 when
there is no ammunition), else a Weapons record (player_weapon 2 edged, 1 blunt), else
Weapons[15], the bare fist (player_weapon 0). Sets wsize, the weapon's reach, from
ComObjData radius. Returns 1 for melee."""),
("void far DoPlayerWeapon(", """Sets up the player's blow. Attack skill: half the attack skill plus the weapon's skill
(fist for anything outside barehand..mace) plus Valor plus dexterity / 7, plus 7 on easy.
Damage: the weapon's damage byte for the swing's kind (slash, bash or stab, by swing_kind)
plus strength / 9; bare handed, 2/5 of the barehand skill plus strength / 6 plus 4
(strength is attr[0] of the player's Creature record). A weapon enchantment of major
class 0xC adds 2 * effect + 1 damage (effects 0..3) or 2 * effect - 7 to hit (4..7);
effects 8 and up are special weapons, specweap = effect - 7, whose powers player_attack
applies after a hit."""),
("void far missile_finish(", """Ends a bow or sling shot: resets the swing state, the power bar and the weapon frame,
and gives the mouse cursor back."""),
("void far player_attack(", """The player's melee and missile attack, called each frame with swing, the ninth of the
3D view clicked (1..9; 0 for none). pQatt < 0 is a swing in
progress, its value -1 - swing kind. The weapon animation frame (wframe) drives it: while
the button or key is held the power bar (play_pow, up by the weapon's wd[4] every 16 ticks
to at most 100) charges; at the strike frame the power becomes wd[3] plus the charged share
of wd[5] - wd[3], the blow is struck, and on a hit by a special weapon (on a critical, or
every hit for specweap 6) its power fires: 1 heals the player by the damage done, 2
repels undead, 3 a fireball at the target, 4 holds the target, 5 and 6 open a door. A bow
or sling instead switches the cursor to aim and fires on release (player_fire). Making a
blow sets the player's noise (10 charging, 15 striking), which critters hear."""),
("void far missile_thwack(", """A missile (or anything thrown) has hit def: fills in the attack globals as for a
blow, full power and no angle bonus, a hit location from the heights plus 4, and the
missile's damage dmg, then do_damage with damage type bits type."""),
("char far critter_attack(", """A critter's blow. damage and to-hit come from its Creature record's attack type
(attacks[type].damage plus strength / 5; attacks[type].chance plus half its equip
value); a powerful critter adds 7..12 to hit and 4..15 damage. charge is the power.
A hit on the player by a poisonous attack (poison > 0) poisons him at that level when
rand() % (poison + 6) beats twice the player's armour at that location (Creature[63],
the player's record) and the player does not resist poison (check_res, type 0x10).
Returns 1 for a hit."""),
("void far player_killed_a(", """Experience for killing a critter: exp * 4 + 2d(exp), from its Creature record, times
1.5..3 for a powerful one, through player_get_exp (which halves it again, SKILLCHK.C).
Also starts music theme 6."""),
]

TRAIL = [
("game_sprint(0xCF);", "'Sorry, you have no '"),
("game_sprint(0x60);", "'.'"),
]
