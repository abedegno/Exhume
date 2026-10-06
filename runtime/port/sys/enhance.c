/* The enhancement registry (enhance.h). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "enhance.h"
#include "portgame.h"                 /* PORT_NAME */
#ifndef PORT_NAME
#define PORT_NAME "port"
#endif

int port_config_get(const char *home, const char *key, char *out, size_t outsz);
int port_config_set(const char *home, const char *key, const char *value);

uint32_t enhance_on;
static const struct enhance_flag *tab;
static int ntab;
static const char *game_name = "";
static const char *const kind_name[] = { "presentation", "timing", "gameplay" };

void enhance_init(const struct enhance_flag *table, int n, const char *game)
{
    tab = table;
    ntab = n > 32 ? 32 : n;
    game_name = game ? game : "";
}

int enhance_index(const char *name)
{
    int i;
    for (i = 0; i < ntab; i++)
        if (!strcmp(tab[i].name, name)) return i;
    return -1;
}

const char *enhance_names(uint32_t mask, char *out, size_t n)
{
    size_t k = 0;
    int i;
    if (n) out[0] = 0;
    for (i = 0; i < ntab; i++)
        if ((mask >> i) & 1u && k < n)
            k += (size_t)snprintf(out + k, n - k, "%s%s", k ? "," : "", tab[i].name);
    return out;
}

uint32_t enhance_kind_mask(int kind)
{
    uint32_t m = 0;
    int i;
    for (i = 0; i < ntab; i++)
        if (tab[i].kind == kind && !tab[i].only) m |= 1u << i;
    return m;
}

static void valid_names(void)
{
    int i, first = 1;
    fprintf(stderr, PORT_NAME ": enhance: the enhancements are");
    for (i = 0; i < ntab; i++)
        if (!tab[i].only) { fprintf(stderr, "%s %s", first ? "" : ",", tab[i].name); first = 0; }
    fprintf(stderr, "%s (--enhance list)\n", first ? " none yet" : "");
}

int enhance_parse(const char *list, uint32_t *mask, int on, int strict)
{
    uint32_t m = *mask;
    const char *p = list ? list : "";
    while (*p) {
        char name[64];
        size_t k = 0;
        int i;
        while (*p == ',' || *p == ' ') p++;
        while (*p && *p != ',') {
            if (k + 1 < sizeof name) name[k++] = *p;
            p++;
        }
        while (k && name[k - 1] == ' ') k--;
        name[k] = 0;
        if (!k) continue;
        i = enhance_index(name);
        if (i < 0 || tab[i].only) {
            if (i >= 0) fprintf(stderr, PORT_NAME ": enhance: %s is %s only\n", name, tab[i].only);
            else fprintf(stderr, PORT_NAME ": enhance: no enhancement called %s%s\n", name, strict ? "" : " (ignored)");
            if (strict) {
                if (i < 0) valid_names();
                return -1;
            }
            continue;
        }
        if (on) m |= 1u << i;
        else m &= ~(1u << i);
    }
    *mask = m;
    return 0;
}

void enhance_list(FILE *f)
{
    int i;
    fprintf(f, "Enhancements for %s, each off unless turned on (--enhance NAME[,NAME]):\n", game_name);
    for (i = 0; i < ntab; i++) {
        if (tab[i].only) continue;
        fprintf(f, "  %-16s %-12s %s%s%s%s\n", tab[i].name, kind_name[tab[i].kind], tab[i].about,
                tab[i].source ? " (from " : "", tab[i].source ? tab[i].source : "", tab[i].source ? ")" : "");
    }
}

int enhance_load(const char *home, uint32_t *mask)
{
    char v[512];
    if (port_config_get(home, "enhance", v, sizeof v) != 0) return 0;
    return enhance_parse(v, mask, 1, 0);
}

int enhance_save(const char *home, uint32_t mask)
{
    char v[512];
    return port_config_set(home, "enhance", enhance_names(mask, v, sizeof v));
}

/* Format 5 has, right after its 12-byte header, one chunk of stream 9: the byte 9, a length
   word and the names comma-separated (runtime/replay/replay.c). */
int enhance_from_recording(const char *path, uint32_t *mask, int *carries)
{
    unsigned char h[15];
    char names[512];
    unsigned len;
    FILE *f = fopen(path, "rb");
    *mask = 0;
    *carries = 0;
    if (!f) return 0;                   /* the replay itself says it cannot open it */
    if (fread(h, 1, 12, f) != 12 || h[4] != 5) {
        fclose(f);
        return 0;
    }
    *carries = 1;
    if (fread(h + 12, 1, 3, f) != 3 || h[12] != 9 || (len = h[13] | (unsigned)h[14] << 8) >= sizeof names
        || fread(names, 1, len, f) != len) {
        fclose(f);
        fprintf(stderr, PORT_NAME ": enhance: %s is a format 5 recording without its enhancements\n", path);
        return -1;
    }
    fclose(f);
    names[len] = 0;
    if (enhance_parse(names, mask, 1, 1) < 0) {
        fprintf(stderr, PORT_NAME ": enhance: %s was recorded with an enhancement this build does not have (%s)\n",
                path, names);
        return -1;
    }
    return 0;
}

void enhance_log(void)
{
    char b[512];
    if (!enhance_on) return;
    fprintf(stderr, PORT_NAME ": enhance: %s (not as DOS%s)\n", enhance_names(enhance_on, b, sizeof b),
            enhance_on & enhance_kind_mask(ENH_GAMEPLAY) ? ", gameplay changed" : "");
}
