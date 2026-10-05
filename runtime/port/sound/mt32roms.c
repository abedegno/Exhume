/* Finding the user's MT-32 or CM-32L ROM images (mt32roms.h).

   A ROM is recognised by its content, through libmt32emu's own identification
   (mt32emu_identify_rom_file), so the file names do not matter: MT32_CONTROL.ROM, Munt's
   mt32_ctrl_1_07.rom and anything else work alike. Split images (the halves libmt32emu calls
   _a/_b for a control ROM and _h/_l for a PCM ROM) are passed over: only whole images make a pair.

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

/* PATH's folder: PATH itself when it is a folder, else the folder of the file it names. */
static void folder_of(const char *path, char *out, size_t n)
{
    char *s;
    snprintf(out, n, "%s", path);
    if (is_dir(out)) return;
    s = strrchr(out, '/');
#ifdef _WIN32
    { char *b = strrchr(out, '\\'); if (b && (!s || b > s)) s = b; }
#endif
    if (s) *s = 0; else snprintf(out, n, ".");
}

#ifdef AUDIO_HAVE_MT32EMU
/* the model of a whole control ROM, 0 CM-32L, 1 CM-32LN, 2 MT-32; -1 for anything else */
static int ctrl_model(const char *id)
{
    size_t k = strlen(id);
    if (k > 2 && id[k - 2] == '_' && (id[k - 1] == 'a' || id[k - 1] == 'b')) return -1;   /* a half */
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
#endif

int mt32roms_pick(const char *path, char *ctrl, char *pcm, char *ctrl_id, char *pcm_id, size_t n, char *dir)
{
#ifdef AUDIO_HAVE_MT32EMU
    char folder[1024], full[1300], best_ctrl[1300] = "", best_cid[64] = "";
    char pcm_cm[1300] = "", pcm_mt[1300] = "";
    int best_model = 9, best_ver = -2;
    DIR *d;
    struct dirent *e;
    if (!path || !*path) return 0;
    folder_of(path, folder, sizeof folder);
    if (!(d = opendir(folder))) return 0;
    while ((e = readdir(d)) != NULL) {
        mt32emu_rom_info ri;
        struct stat st;
        if (e->d_name[0] == '.') continue;
        snprintf(full, sizeof full, "%s/%s", folder, e->d_name);
        /* ROM images are 32 KB to 1 MB; anything else is not worth reading */
        if (stat(full, &st) || !S_ISREG(st.st_mode) || st.st_size < 32768 || st.st_size > 1048576) continue;
        memset(&ri, 0, sizeof ri);
        if (mt32emu_identify_rom_file(&ri, full, NULL) != MT32EMU_RC_OK) continue;
        if (ri.control_rom_id) {
            int m = ctrl_model(ri.control_rom_id), v = ctrl_version(ri.control_rom_id);
            if (m >= 0 && (m < best_model || (m == best_model && v > best_ver))) {
                best_model = m; best_ver = v;
                snprintf(best_ctrl, sizeof best_ctrl, "%s", full);
                snprintf(best_cid, sizeof best_cid, "%s", ri.control_rom_id);
            }
        }
        if (ri.pcm_rom_id && !strcmp(ri.pcm_rom_id, "pcm_cm32l")) snprintf(pcm_cm, sizeof pcm_cm, "%s", full);
        if (ri.pcm_rom_id && !strcmp(ri.pcm_rom_id, "pcm_mt32")) snprintf(pcm_mt, sizeof pcm_mt, "%s", full);
    }
    closedir(d);
    /* the best control ROM whose PCM ROM is here; failing that, the other model's */
    {
        const char *p = best_model <= 1 ? pcm_cm : pcm_mt;
        if (!*best_ctrl || !*p) {
            /* the preferred control ROM has no partner: try again for the other model's pair */
            if (best_model <= 1 && *pcm_mt) {
                d = opendir(folder);
                best_ctrl[0] = 0; best_ver = -2;
                while (d && (e = readdir(d)) != NULL) {
                    mt32emu_rom_info ri;
                    struct stat st;
                    snprintf(full, sizeof full, "%s/%s", folder, e->d_name);
                    if (e->d_name[0] == '.' || stat(full, &st) || !S_ISREG(st.st_mode)
                        || st.st_size < 32768 || st.st_size > 1048576) continue;
                    memset(&ri, 0, sizeof ri);
                    if (mt32emu_identify_rom_file(&ri, full, NULL) == MT32EMU_RC_OK && ri.control_rom_id
                        && ctrl_model(ri.control_rom_id) == 2 && ctrl_version(ri.control_rom_id) > best_ver) {
                        best_ver = ctrl_version(ri.control_rom_id);
                        snprintf(best_ctrl, sizeof best_ctrl, "%s", full);
                        snprintf(best_cid, sizeof best_cid, "%s", ri.control_rom_id);
                    }
                }
                if (d) closedir(d);
                p = pcm_mt;
            }
            if (!*best_ctrl || !*p) return 0;
        }
        if (ctrl) snprintf(ctrl, n, "%s", best_ctrl);
        if (pcm) snprintf(pcm, n, "%s", p);
        if (ctrl_id) snprintf(ctrl_id, n, "%s", best_cid);
        if (pcm_id) snprintf(pcm_id, n, "%s", p == pcm_cm ? "pcm_cm32l" : "pcm_mt32");
        if (dir) snprintf(dir, n, "%s", folder);
        return 1;
    }
#else
    (void)path; (void)ctrl; (void)pcm; (void)ctrl_id; (void)pcm_id; (void)n; (void)dir;
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
        const char *la = getenv("LOCALAPPDATA"), *ad = getenv("APPDATA");
        if (la && *la) ADD("%s/DOSBox/mt32-roms", la);
        else if (ad && *ad) ADD("%s/DOSBox/mt32-roms", ad);
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
    char ctrl[1300], pcm[1300], cid[1300], pid[1300], d[1300], dirs[16][1024];
    const char *source = NULL;
    int i, k;
    if (!mt32roms_available()) {
        fprintf(stderr, PORT_NAME ": mt32: no ROMs (built without libmt32emu)\n");
        return NULL;
    }
    if (given && *given && mt32roms_pick(given, ctrl, pcm, cid, pid, sizeof ctrl, d)) source = "given";
    else if (env && *env && mt32roms_pick(env, ctrl, pcm, cid, pid, sizeof ctrl, d)) source = "environment";
    else if (remembered && *remembered && mt32roms_pick(remembered, ctrl, pcm, cid, pid, sizeof ctrl, d)) source = "remembered";
    else {
        k = search_dirs(home, data, dirs, 16);
        for (i = 0; i < k && !source; i++)
            if (is_dir(dirs[i]) && mt32roms_pick(dirs[i], ctrl, pcm, cid, pid, sizeof ctrl, d)) source = "found";
    }
    if (!source) {
        if (given && *given) fprintf(stderr, PORT_NAME ": mt32: no ROM pair in %s (a control ROM and its PCM ROM, whole images)\n", given);
        fprintf(stderr, PORT_NAME ": mt32: no ROMs\n");
        return NULL;
    }
    snprintf(dir_out, n, "%s", d);
    fprintf(stderr, PORT_NAME ": mt32: ROMs %s %s (%s, %s)\n", source, dir_out, cid, pid);
    return source;
}
