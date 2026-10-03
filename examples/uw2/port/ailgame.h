/* ailgame.h: UW2's for runtime/port/sound/ail.c: the portability layer first, then UW2's own
   declarations of the AIL API and its structs (src/include/sound.h: struct DrvrDesc, struct
   SoundBuff, the AIL_ functions as SOUND.C and CUTS.C call them), so the C API is checked
   against the game's view of it; and the FM drivers' time-variant effects for yamaha.c
   (runtime/port/sound/yamaha.h, struct AilFmExt): uw2_tvfx, which UW2Decomp defines in its
   own src/port/sound/tvfx.c (exhume.toml's [sound] extensions). */
#include "compat.h"
#include "sound.h"
#define AIL_FM_EXT uw2_tvfx
