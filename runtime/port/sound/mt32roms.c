/* Finding the user's MT-32 or CM-32L ROM images (mt32roms.h).

   A ROM is recognised by its content, through libmt32emu's own identification
   (mt32emu_identify_rom_file), so the file names do not matter: MT32_CONTROL.ROM, Munt's
   mt32_ctrl_1_07.rom and anything else work alike. Split images, the halves libmt32emu calls
   _a/_b for a control ROM and _l/_h for a PCM ROM, are joined when both halves are there
   (mt32emu_merge_and_add_rom_files); the CM-32L PCM's _h joins the whole MT-32 PCM, which is its
   low half. A half without its partner is passed over, and a whole image is taken over halves.

   The pair: a CM-32L control ROM goes with the CM-32L PCM ROM (the CM-32LN's control ROM too),
   an MT-32 control ROM with the MT-32 PCM ROM. A CM-32L pair is preferred over a CM-32LN pair
   over an MT-32 pair, and within a model the newest numbered control ROM.

   The search, after what the user gave: the port's home (roms/, mt32-roms/), the game's folder,
   the program's folder (where DOSBox-X looks), then the folders DOSBox Staging searches by default
   on each system (its src/midi/mt32.cpp and src/misc/cross.cpp): Windows %LOCALAPPDATA%\DOSBox\
   mt32-roms and C:\mt32-rom-data; macOS ~/Library/Preferences/DOSBox/mt32-roms,
   ~/Library/Audio/Sounds/MT32-Roms, /usr/local/share/mt32-rom-data and /usr/share/mt32-rom-data;
   elsewhere $XDG_DATA_HOME/dosbox/mt32-roms, $XDG_DATA_HOME/mt32-rom-data, each of $XDG_DATA_DIRS'
   mt32-rom-data and $XDG_CONFIG_HOME/dosbox/mt32-roms. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <dirent.h>
#include "mt32roms.h"
#include "portgame.h"
#include "plat.h"
#ifndef PORT_NAME
#define PORT_NAME "port"
#endif
#ifdef AUDIO_HAVE_MT32EMU
#include <mt32emu/c_interface/c_interface.h>
#endif

int mt32roms_available(void)
{
#ifdef AUDIO_HAVE_MT32EMU
    return 1;
#else
    return 0;
#endif
}

static int is_dir(const char *p)
{
    struct stat st;
    return p && *p && stat(p, &st) == 0 && S_ISDIR(st.st_mode);
}

static int is_file(const char *p)
{
    struct stat st;
    return p && *p && stat(p, &st) == 0 && S_ISREG(st.st_mode);
}

/* PATH's folder: PATH itself when it is a folder, the folder of the file it names when it is a
   file. A path to nothing stays as it is, so that a mistyped one finds no ROMs rather than its
   parent folder's. */
static void folder_of(const char *path, char *out, size_t n)
{
    char *s;
    snprintf(out, n, "%s", path);
    if (is_dir(out) || !is_file(out)) return;
    s = strrchr(out, '/');
#ifdef _WIN32
    { char *b = strrchr(out, '\\'); if (b && (!s || b > s)) s = b; }
#endif
    if (s) *s = 0; else snprintf(out, n, ".");
}

#ifdef AUDIO_HAVE_MT32EMU
/* the model of a control ROM's identifier, 0 CM-32L, 1 CM-32LN, 2 MT-32; -1 for anything else */
static int ctrl_model(const char *id)
{
    if (!strncmp(id, "ctrl_cm32ln_", 12)) return 1;
    if (!strncmp(id, "ctrl_cm32l_", 11)) return 0;
    if (!strncmp(id, "ctrl_mt32_", 10)) return 2;
    return -1;
}

/* a numbered version as a number (1_07 -> 107, 2_04 -> 204); others (bluer) rank below */
static int ctrl_version(const char *id)
{
    const char *v = strrchr(id, '_');
    int major, minor;
    if (v && v - id > 2 && sscanf(v - 1, "%d_%d", &major, &minor) == 2) return major * 100 + minor;
    return -1;
}

/* ID with the half's suffix (_a, _b, _h, _l) taken off into base, and the suffix's letter, or 0 */
static char half_of(const char *id, char *base, size_t n)
{
    size_t k = strlen(id);
    if (k < 3 || id[k - 2] != '_' || !strchr("abhl", id[k - 1])) return 0;
    snprintf(base, n, "%.*s", (int)(k - 2), id);
    return id[k - 1];
}

struct romfile { char path[1300]; char id[32]; };

/* the files of a scan whose identifier is ID, or NULL */
static const struct romfile *find_id(const struct romfile *f, int nf, const char *id)
{
    int i;
    for (i = 0; i < nf; i++) if (!strcmp(f[i].id, id)) return &f[i];
    return NULL;
}

/* an image from one file or two halves, into part1 and part2 (part2 "" for a whole file) */
static void take(char part1[1300], char part2[1300], const struct romfile *a, const struct romfile *b)
{
    snprintf(part1, 1300, "%s", a->path);
    snprintf(part2, 1300, "%s", b ? b->path : "");
}
#endif

int mt32roms_pick(const char *path, struct mt32roms_set *out)
{
#ifdef AUDIO_HAVE_MT32EMU
    char folder[1024], full[1300], base[32], other[40];
    struct romfile *f;
    struct mt32roms_set best;
    int nf = 0, i, best_model = 9, best_ver = -2, best_whole = 0, ok = 0;
    char cm[2][1300] = { "", "" }, mt[2][1300] = { "", "" };
    const struct romfile *x;
    DIR *d;
    struct dirent *e;
    if (!path || !*path) return 0;
    folder_of(path, folder, sizeof folder);
    if (!(d = opendir(folder))) return 0;
    if (!(f = calloc(MT32ROMS_MAX_FILES, sizeof *f))) { closedir(d); return 0; }
    while ((e = readdir(d)) != NULL && nf < MT32ROMS_MAX_FILES) {
        mt32emu_rom_info ri;
        struct stat st;
        if (e->d_name[0] == '.') continue;
        snprintf(full, sizeof full, "%s/%s", folder, e->d_name);
        /* ROM images and their halves are 32 KB to 1 MB; anything else is not worth reading */
        if (stat(full, &st) || !S_ISREG(st.st_mode) || st.st_size < 32768 || st.st_size > 1048576) continue;
        memset(&ri, 0, sizeof ri);
        if (mt32emu_identify_rom_file(&ri, full, NULL) != MT32EMU_RC_OK) continue;
        if (!ri.control_rom_id && !ri.pcm_rom_id) continue;
        snprintf(f[nf].path, sizeof f[nf].path, "%s", full);
        snprintf(f[nf].id, sizeof f[nf].id, "%s", ri.control_rom_id ? ri.control_rom_id : ri.pcm_rom_id);
        nf++;
    }
    closedir(d);
    memset(&best, 0, sizeof best);
    /* the control ROMs: whole files, and _a halves whose _b is here too; the best model, then the
       newest version, then a whole file over joined halves */
    for (i = 0; i < nf; i++) {
        const struct romfile *b = NULL;
        char h = half_of(f[i].id, base, sizeof base);
        int m, v, whole = !h;
        if (strncmp(f[i].id, "ctrl_", 5)) continue;
        if (h) {
            if (h != 'a') continue;
            snprintf(other, sizeof other, "%s_b", base);
            if (!(b = find_id(f, nf, other))) continue;
        } else snprintf(base, sizeof base, "%s", f[i].id);
        m = ctrl_model(base); v = ctrl_version(base);
        if (m < 0) continue;
        if (m < best_model || (m == best_model && (v > best_ver || (v == best_ver && whole && !best_whole)))) {
            best_model = m; best_ver = v; best_whole = whole;
            take(best.ctrl[0], best.ctrl[1], &f[i], b);
            snprintf(best.ctrl_id, sizeof best.ctrl_id, "%s", base);
        }
    }
    /* the PCM ROMs: the MT-32's whole, or its _l and _h; the CM-32L's whole, or its _h with the
       MT-32's whole PCM, which is the same as the CM-32L's low half */
    if ((x = find_id(f, nf, "pcm_mt32")) != NULL) take(mt[0], mt[1], x, NULL);
    else if ((x = find_id(f, nf, "pcm_mt32_l")) != NULL && find_id(f, nf, "pcm_mt32_h"))
        take(mt[0], mt[1], x, find_id(f, nf, "pcm_mt32_h"));
    if ((x = find_id(f, nf, "pcm_cm32l")) != NULL) take(cm[0], cm[1], x, NULL);
    else if ((x = find_id(f, nf, "pcm_mt32")) != NULL && find_id(f, nf, "pcm_cm32l_h"))
        take(cm[0], cm[1], x, find_id(f, nf, "pcm_cm32l_h"));
    if (best_model <= 1 && !*cm[0] && *mt[0]) {
        /* a CM-32L or CM-32LN control ROM with no PCM ROM to go with it: the best MT-32 pair instead */
        best_model = 9; best_ver = -2; best_whole = 0;
        for (i = 0; i < nf; i++) {
            const struct romfile *b = NULL;
            char h = half_of(f[i].id, base, sizeof base);
            int v, whole = !h;
            if (strncmp(f[i].id, "ctrl_mt32_", 10)) continue;
            if (h) {
                if (h != 'a') continue;
                snprintf(other, sizeof other, "%s_b", base);
                if (!(b = find_id(f, nf, other))) continue;
            } else snprintf(base, sizeof base, "%s", f[i].id);
            v = ctrl_version(base);
            if (best_model == 9 || v > best_ver || (v == best_ver && whole && !best_whole)) {
                best_model = 2; best_ver = v; best_whole = whole;
                take(best.ctrl[0], best.ctrl[1], &f[i], b);
                snprintf(best.ctrl_id, sizeof best.ctrl_id, "%s", base);
            }
        }
    }
    if (best_model <= 1 && *cm[0]) {
        snprintf(best.pcm[0], 1300, "%s", cm[0]); snprintf(best.pcm[1], 1300, "%s", cm[1]);
        snprintf(best.pcm_id, sizeof best.pcm_id, "pcm_cm32l"); ok = 1;
    } else if (best_model == 2 && *mt[0]) {
        snprintf(best.pcm[0], 1300, "%s", mt[0]); snprintf(best.pcm[1], 1300, "%s", mt[1]);
        snprintf(best.pcm_id, sizeof best.pcm_id, "pcm_mt32"); ok = 1;
    }
    free(f);
    if (!ok) return 0;
    snprintf(best.dir, sizeof best.dir, "%s", folder);
    if (out) *out = best;
    return 1;
#else
    (void)path; (void)out;
    return 0;
#endif
}

int mt32roms_add(void *context, const struct mt32roms_set *set)
{
#ifdef AUDIO_HAVE_MT32EMU
    mt32emu_context mt = (mt32emu_context)context;
    int n = 0, i;
    for (i = 0; i < 2; i++) {
        const char (*p)[1300] = i ? set->pcm : set->ctrl;
        if (p[1][0] ? mt32emu_merge_and_add_rom_files(mt, p[0], p[1]) > 0 : mt32emu_add_rom_file(mt, p[0]) > 0) n++;
    }
    return n;
#else
    (void)context; (void)set;
    return 0;
#endif
}

/* the search's folders, in order, into list (count returned) */
static int search_dirs(const char *home, const char *data, char list[][1024], int max)
{
    int k = 0;
    const char *h = getenv("HOME"), *base = plat_base_dir();
#define ADD(...) do { if (k < max) { snprintf(list[k], sizeof list[k], __VA_ARGS__); k++; } } while (0)
    if (home && *home) { ADD("%s/roms", home); ADD("%s/mt32-roms", home); }
    if (data && *data) ADD("%s", data);
    if (base && *base) ADD("%s", base);
#if defined(_WIN32)
    {
        const char *la = getenv("LOCALAPPDATA");
        if (la && *la) ADD("%s/DOSBox/mt32-roms", la);
        ADD("C:/mt32-rom-data");
    }
#elif defined(__APPLE__)
    if (h && *h) { ADD("%s/Library/Preferences/DOSBox/mt32-roms", h); ADD("%s/Library/Audio/Sounds/MT32-Roms", h); }
    ADD("/usr/local/share/mt32-rom-data");
    ADD("/usr/share/mt32-rom-data");
#else
    {
        const char *xd = getenv("XDG_DATA_HOME"), *xc = getenv("XDG_CONFIG_HOME"), *dirs = getenv("XDG_DATA_DIRS");
        char buf[1024];
        if (xd && *xd) { ADD("%s/dosbox/mt32-roms", xd); ADD("%s/mt32-rom-data", xd); }
        else if (h && *h) { ADD("%s/.local/share/dosbox/mt32-roms", h); ADD("%s/.local/share/mt32-rom-data", h); }
        snprintf(buf, sizeof buf, "%s", dirs && *dirs ? dirs : "/usr/local/share:/usr/share");
        for (char *s = strtok(buf, ":"); s; s = strtok(NULL, ":")) ADD("%s/mt32-rom-data", s);
        if (xc && *xc) ADD("%s/dosbox/mt32-roms", xc);
        else if (h && *h) ADD("%s/.config/dosbox/mt32-roms", h);
    }
#endif
#undef ADD
    return k;
}

const char *mt32roms_locate(const char *given, const char *env, const char *remembered, const char *home,
                            const char *data, char *dir_out, size_t n)
{
    struct mt32roms_set set;
    char dirs[16][1024];
    const char *source = NULL;
    int i, k;
    if (!mt32roms_available()) {
        fprintf(stderr, PORT_NAME ": mt32: no ROMs (built without libmt32emu)\n");
        return NULL;
    }
    if (remembered && *remembered && !is_dir(remembered) && !is_file(remembered))
        fprintf(stderr, PORT_NAME ": mt32: the remembered ROM folder %s is gone\n", remembered);
    if (given && *given && mt32roms_pick(given, &set)) source = "given";
    else if (env && *env && mt32roms_pick(env, &set)) source = "environment";
    else if (remembered && *remembered && mt32roms_pick(remembered, &set)) source = "remembered";
    else {
        k = search_dirs(home, data, dirs, 16);
        for (i = 0; i < k && !source; i++)
            if (is_dir(dirs[i]) && mt32roms_pick(dirs[i], &set)) source = "found";
    }
    if (!source) {
        if (given && *given) fprintf(stderr, PORT_NAME ": mt32: no ROM pair in %s (a control ROM and its PCM ROM, whole or in halves)\n", given);
        fprintf(stderr, PORT_NAME ": mt32: no ROMs\n");
        return NULL;
    }
    snprintf(dir_out, n, "%s", set.dir);
    fprintf(stderr, PORT_NAME ": mt32: ROMs %s %s (%s, %s)%s\n", source, dir_out, set.ctrl_id, set.pcm_id,
            set.ctrl[1][0] || set.pcm[1][0] ? ", joined from halves" : "");
    return source;
}
