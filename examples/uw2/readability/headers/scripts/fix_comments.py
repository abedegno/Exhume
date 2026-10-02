# Comments that described a local struct, and also said something about the file: put back
def ed(f, a, b):
    p = 'base/' + f; s = open(p, encoding='latin1').read()
    assert s.count(a) == 1, (f, a); s = s.replace(a, b); open(p, 'w', encoding='latin1').write(s)
ed('SEG034.C', "/* opts: -mm -1 -G -O -Y -d */\n\n", "/* opts: -mm -1 -G -O -Y -d */\n/* FM Towns identifies the formerly anonymous helpers by their matching\n   positions and operations: sort_setup, sort_obj, build_sort, set_sds,\n   set_osum and clear_objsort. */\n\n")
ed('SEG043.C', "extern struct FontInfo far *cur_font;                 /* DS:21CC */",
   "/* DS:21CC, _cur_font in symbols.tsv (provisional); only its line height is used here. */\nextern struct FontInfo far *cur_font;")
ed('OVR136.C', "extern struct buttongroup *current_buttongroup;",
   "/* The five groups and current_buttongroup are another file's data (DS:012F..0266). */\nextern struct buttongroup *current_buttongroup;")
ed('OVR142.C', "    ComObjData[127].resist = 0;", "    ComObjData[127].resist = 0;         /* item 127 is the player's own object type */")
