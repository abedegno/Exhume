/* blackbox.c: replaces nothing. Every player's session is recorded, so that a crash, or
   anything else the port does differently from DOS, can be replayed exactly (Exhume's
   docs/port.md, "The black box"; first written for UW2Decomp's port). Each session gets a
   folder, recordings/YYYYMMDD-HHMMSS in the home directory, holding RECORD.OUT, the inputs as
   runtime/replay/replay.c records them, and stage/, a copy of the files the game itself keeps in
   the home directory (the saved games, the game's configuration) as they were when the session
   began, which is what a replay starts from:

       python3 tools/replay.py port recordings/S/RECORD.OUT OUT --stage recordings/S/stage

   No state dumps are written and the stop key (RP_STOP_SCAN) does not end the recording. A
   fault in the port writes the recording's buffered streams before the program ends
   (crash.c), and so does the game's exit (borland.c's bc_exit). The newest BLACKBOX_KEEP
   sessions are kept.

   The project's portgame.h turns it on and names what is the game's:
     PORT_BLACKBOX       defined: crash.c and borland.c call port_blackbox_close (link this file)
     BLACKBOX_KEEP       the sessions kept, this one included (5)
     BLACKBOX_STAGE      what stage/ copies of the home directory, as string literals separated
                         by commas: paths in it, with '/' between names, matched without case;
                         a folder named is copied whole, and a path under a folder copies only
                         that of the folder. They are the files a replay reads back (UW1 and
                         UW2: "DATA", whole, for UW.CFG, the sound cards, and any file the player
                         put there that the port reads ahead of the game's own, such as a
                         translated STRINGS.PAK; and "SAVE0" to "SAVE4", the game in progress
                         and the four saved games). Nothing else is copied, and a symbolic link
                         is never followed (is_link).

   stage/ is an allow-list, not a copy of the home directory less some names, because the home
   directory holds files that are not the player's to send: the game copied out of GOG's CD
   image (gog-cd/, sys/gamedir.c), MT-32 ROMs (roms/, mt32-roms/), a game folder the player put
   there for the search to find, and the port's own settings and recordings. Underworld
   Exhumed's UW1 1.0.1 and UW2 1.2.2 copied all of it but the settings file and the recordings
   into every session's stage/. The game writes nothing else in the home directory that a
   replay needs: its other files there are scratch (UW1's a.tmp to h.tmp, made and removed at
   start) or output (screenshots, uwpicNNN.gif), and a game file it changes in place (UW1's
   CUTS\csNNN.n00, copied into the home directory at its first write, borland.c) is written
   again, the same way, by the replay before it is read.

   The recording's name is a host path, which replay.c opens as it is (borland.c's
   bc_open_host). The black box of Underworld Exhumed's UW1 1.0.0 and UW2 1.2.1 gave it a DOS
   path, RECORDINGS\YYYYMMDD-HHMMSS\RECORD.OUT, which the game's file names cut to 8.3: every
   session of a day went to RECORDIN/YYYYMMDD/RECORD.OUT, each overwriting the last, never
   pruned, and copied into each later session's stage/. An old RECORDIN/ is left where it is,
   the player's own files, and is not staged.

   The port's main calls port_blackbox_start(home) before the game starts, for a player's run
   only: one with a window, neither recording nor replaying a test, and with an option to turn
   it off (UW2: --no-recording); runtime/README.md has the wiring. */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include "port.h"
#ifdef _WIN32
#include <direct.h>
#define mkdir(p, m) _mkdir(p)
/* kernel32's, declared here rather than through windows.h, which names things the port's headers
   name too (gogreg.c's note) */
__declspec(dllimport) unsigned long __stdcall GetFileAttributesA(const char *path);
#define BB_NO_FILE 0xFFFFFFFFul         /* INVALID_FILE_ATTRIBUTES */
#define BB_REPARSE 0x400ul              /* FILE_ATTRIBUTE_REPARSE_POINT */
#else
#include <unistd.h>
#endif

#ifndef BLACKBOX_KEEP
#define BLACKBOX_KEEP 5
#endif

extern int16_t rp_request;
extern int16_t rp_blackbox;
extern const char *rp_blackbox_name;
void rp_blackbox_close(int crashed);

#ifdef BLACKBOX_SKIP
#error "BLACKBOX_SKIP was replaced by BLACKBOX_STAGE, an allow-list of what stage/ copies (the header's note)"
#endif
#ifndef BLACKBOX_STAGE
#ifdef PORT_BLACKBOX
#error "portgame.h names the home directory's entries the black box stages (BLACKBOX_STAGE)"
#endif
#define BLACKBOX_STAGE ""               /* no black box: nothing is staged (a path is never empty) */
#endif

static const char *const stage_names[] = { BLACKBOX_STAGE };

static int is_dir(const char *p)
{
    struct stat st;
    return stat(p, &st) == 0 && S_ISDIR(st.st_mode);
}

/* a symbolic link (on Windows any reparse point: a link or a junction), which stage/ and the
   pruning never follow: a link can lead out of the home directory, to what is not the player's
   to send or the black box's to delete, or back up the tree, round for ever */
static int is_link(const char *p)
{
#ifdef _WIN32
    unsigned long a = GetFileAttributesA(p);
    return a != BB_NO_FILE && (a & BB_REPARSE);
#else
    struct stat st;
    return lstat(p, &st) == 0 && S_ISLNK(st.st_mode);
#endif
}

static int copy_file(const char *from, const char *to)
{
    unsigned char buf[65536];
    size_t n;
    FILE *in = fopen(from, "rb"), *out;
    if (!in) return 1;
    if (!(out = fopen(to, "wb"))) { fclose(in); return 1; }
    while ((n = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, n, out);
    fclose(in);
    return fclose(out) != 0;
}

/* What stage/ does with the home directory's entry at path (relative, '/' between names):
   2 copies it whole (a stage name is the path), 1 goes into it for what is named under it (a
   stage name starts with the path and '/'), 0 leaves it out. Names match without case. */
static int staged(const char *path)
{
    size_t i, n = strlen(path);
    int how = 0;
    for (i = 0; i < sizeof stage_names / sizeof *stage_names; i++) {
        const char *s = stage_names[i];
        if (!strcasecmp(s, path)) return 2;
        if (strlen(s) > n && s[n] == '/' && !strncasecmp(s, path, n)) how = 1;
    }
    return how;
}

/* from's files and folders into to, which is made: all of them when rel is NULL, else those the
   stage names allow, rel being from's path in the home directory ("" for the home directory) */
static int copy_tree(const char *from, const char *to, const char *rel)
{
    DIR *d = opendir(from);
    struct dirent *e;
    char a[1200], b[1200], r[1200];
    int bad = 0, how = 2;
    if (!d) return 1;
    mkdir(to, 0755);
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        if (rel) {
            snprintf(r, sizeof r, "%s%s%s", rel, *rel ? "/" : "", e->d_name);
            if (!(how = staged(r))) continue;
        }
        snprintf(a, sizeof a, "%s/%s", from, e->d_name);
        snprintf(b, sizeof b, "%s/%s", to, e->d_name);
        if (is_link(a)) continue;       /* never followed: is_link */
        if (is_dir(a)) bad |= copy_tree(a, b, how == 2 ? NULL : r);
        else if (how == 2) bad |= copy_file(a, b);
    }
    closedir(d);
    return bad;
}

/* removes the folder p and everything in it; p is always a session folder under recordings/. A
   link is removed, never what it leads to. */
static void remove_tree(const char *p)
{
    DIR *d;
    struct dirent *e;
    char a[1200];
    if (is_link(p)) {
        if (remove(p) != 0) rmdir(p);   /* Windows removes a link to a folder as a folder */
        return;
    }
    if (!(d = opendir(p))) return;
    while ((e = readdir(d)) != NULL) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        snprintf(a, sizeof a, "%s/%s", p, e->d_name);
        if (is_dir(a) || is_link(a)) remove_tree(a);
        else remove(a);
    }
    closedir(d);
    rmdir(p);
}

static int by_name(const void *x, const void *y)
{
    return strcmp(*(char *const *)x, *(char *const *)y);
}

/* all but the newest BLACKBOX_KEEP - 1 sessions go, to leave room for this one; session
   folders are named by their time, so the oldest sort first */
static void prune(const char *dir)
{
    DIR *d = opendir(dir);
    struct dirent *e;
    char *names[256], path[1200];
    int n = 0, i;
    if (!d) return;
    while ((e = readdir(d)) != NULL && n < 256)
        if (strlen(e->d_name) == 15 && e->d_name[8] == '-') names[n++] = strdup(e->d_name);
    closedir(d);
    qsort(names, (size_t)n, sizeof *names, by_name);
    for (i = 0; i < n; i++) {
        if (i < n - (BLACKBOX_KEEP - 1)) {
            snprintf(path, sizeof path, "%s/%s", dir, names[i]);
            remove_tree(path);
        }
        free(names[i]);
    }
}

/* Called by the port's main before the game starts, for a player's run. home is the port's
   home directory, the one the game's DOS paths resolve in. Returns 0 when the session is
   being recorded. */
int port_blackbox_start(const char *home)
{
    static char record[1300];
    char dir[1100], session[1200], stage[1300];
    time_t t = time(NULL);
    struct tm *tm = localtime(&t);
    char stamp[16];
    strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", tm);
    snprintf(dir, sizeof dir, "%s/recordings", home);
    mkdir(dir, 0755);
    prune(dir);
    snprintf(session, sizeof session, "%s/%s", dir, stamp);
    if (mkdir(session, 0755) != 0 && !is_dir(session)) return 1;
    snprintf(stage, sizeof stage, "%s/stage", session);
    if (copy_tree(home, stage, "")) fprintf(stderr, PORT_NAME ": the session's starting files were not all copied\n");
    /* a host path, which replay.c opens as it is: not a DOS one, which the game's 8.3 file
       names would cut to RECORDIN\YYYYMMDD (the header's note) */
    snprintf(record, sizeof record, "%s/RECORD.OUT", session);
    rp_blackbox_name = record;
    rp_blackbox = 1;
    rp_request = 1;                     /* RP_RECORD */
    fprintf(stderr, PORT_NAME ": recording this session to %s\n", record);
    return 0;
}

/* The fault handler's, exit's and the closed window's part: the recording's buffered streams
   written (a --record session's too: the port's main calls this when plat_run returns, so
   that a recording whose game was still running when the window closed is whole). */
void port_blackbox_close(int crashed)
{
    rp_blackbox_close(crashed);
}
