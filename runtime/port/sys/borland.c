/* borland.c: replaces Borland's C library where the host's differs or has no such function
   (Exhume's docs/port.md, "The portability layer"; first written for UW2Decomp's port): the bc_ functions compat.h renames the game's calls
   to, the handle I/O extras (filelength, tell, eof), the directory search, the DOS calls the C
   makes itself (intdosx, geninterrupt, getvect, setvect, getdfree), port I/O, the string
   extras (itoa, ltoa, strupr ...), the _ctype table, rand and srand, time and clock, and exit.

   This file includes no game header and not compat.h, so the names it uses are the host's.
   Every path the game names goes through the platform layer's mapping (plat_resolve), so the
   game sees its own directory: the user's data root, with the port's home directory over it.

   Borland's handles open in text mode unless O_BINARY is given (_fmode is O_TEXT): reads drop
   the CR of each CR LF and stop at Ctrl-Z, writes put a CR before each LF. */
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#ifdef _WIN32
#include <direct.h>
#define mkdir(path, mode) _mkdir(path)  /* Windows's mkdir takes no mode */
/* The host's handles are always binary: Borland's text mode is bc_read's and bc_write's, and
   the C library's own (CR LF, and Ctrl-Z as the end) would apply it a second time. */
#define HOST_BINARY O_BINARY
#else
#define HOST_BINARY 0
#endif
#include "port.h"
#include "plat.h"

/* Borland's open flags (compat.h) */
#define B_RDONLY 0x0001
#define B_WRONLY 0x0002
#define B_RDWR   0x0004
#define B_CREAT  0x0100
#define B_TRUNC  0x0200
#define B_EXCL   0x0400
#define B_APPEND 0x0800
#define B_TEXT   0x4000
#define B_BINARY 0x8000

#define MAXFD 256
static unsigned char fd_text[MAXFD];
static unsigned char fd_eof[MAXFD];
static char *fd_cow[MAXFD];             /* the DOS path of a file to copy on its first write */
static unsigned char fd_wrote[MAXFD];   /* a handle written to since it was opened */

/* The game's changes to its files (writes as they reach the file, a written file's close,
   removals, renames, new folders), counted for the web build, which keeps the home directory
   in the browser's storage and copies it there once a second when this has moved
   (platform/sdl3/plat_sdl3.c). Counted on the game's thread, read on the backend's. Only the
   game's file layer (these bc_ calls) is counted: the port's own host stdio (the settings file,
   the black box's starting copy) is not, and port_config_set asks for its own copy. */
static unsigned files_changed;
static void changed(void) { __atomic_add_fetch(&files_changed, 1u, __ATOMIC_RELAXED); }
unsigned port_files_changed(void) { return __atomic_load_n(&files_changed, __ATOMIC_RELAXED); }

/* Write-behind for files opened write-only (the web build): under Emscripten every file call
   from the game's thread is proxied to the main thread, some 1.5 ms each under Node.js, and the
   replays' state dumps (runtime/replay/replay.c, STATE.OUT) write a word at a time: UW1's talk
   session made 379470 writes, 530 s of its 580. A write-only file's writes are kept in a buffer
   of 64 KB and written when it fills, before anything else is done with the handle (lseek, tell,
   filelength, eof, fstat, close) and at exit. Nothing can read a write-only handle, so
   only a crash loses what is held. */
#if defined(__EMSCRIPTEN__) && !defined(PORT_WRITE_BEHIND)
#define PORT_WRITE_BEHIND 1
#endif
#ifdef PORT_WRITE_BEHIND
#define WB_SIZE 0x10000u
static unsigned char *wb_buf[MAXFD];
static unsigned wb_len[MAXFD];

static int wb_flush(int fd)
{
    unsigned done = 0;
    ssize_t r;
    if (fd < 0 || fd >= MAXFD || !wb_buf[fd]) return 0;
    while (done < wb_len[fd]) {
        r = write(fd, wb_buf[fd] + done, wb_len[fd] - done);
        if (r <= 0) { wb_len[fd] = 0; return -1; }
        done += (unsigned)r;
    }
    if (done) changed();
    wb_len[fd] = 0;
    return 0;
}

static void wb_flush_all(void)
{
    int fd;
    for (fd = 0; fd < MAXFD; fd++) wb_flush(fd);
}

static void wb_start(int fd)
{
    static int registered;
    if (!registered) { registered = 1; atexit(wb_flush_all); }
    if (!wb_buf[fd]) wb_buf[fd] = malloc(WB_SIZE);
    wb_len[fd] = 0;
}

static void wb_stop(int fd)
{
    if (fd < 0 || fd >= MAXFD || !wb_buf[fd]) return;
    wb_flush(fd);
    free(wb_buf[fd]);
    wb_buf[fd] = NULL;
}

/* write(), through the buffer when the handle has one */
static ssize_t raw_write(int fd, const void *buf, size_t n)
{
    if (fd < 0 || fd >= MAXFD || !wb_buf[fd]) return write(fd, buf, n);
    if (wb_len[fd] + n > WB_SIZE) {
        if (wb_flush(fd)) return -1;
        if (n > WB_SIZE) { ssize_t w = write(fd, buf, n); if (w > 0) changed(); return w; }
    }
    memcpy(wb_buf[fd] + wb_len[fd], buf, n);
    wb_len[fd] += (unsigned)n;
    return (ssize_t)n;
}
#define WB_FLUSH(fd) wb_flush(fd)
void port_flush_writes(void) { wb_flush_all(); }
#else
void port_flush_writes(void) {}
#define raw_write write
#define WB_FLUSH(fd) 0
#endif

/* The register file of Turbo C's pseudo-registers (compat.h). */
union port_reg16 { uint16_t x; struct { uint8_t l, h; } b; };
union port_reg16 port_ax, port_bx, port_cx, port_dx;
uint16_t port_si, port_di, port_bp, port_sp = 0x1000, port_cs, port_ds = PORT_DGROUP_PARA + PORT_LOAD_SEG,
         port_es, port_ss = PORT_DGROUP_PARA + PORT_LOAD_SEG, port_flags;

/* Borland's open flags (B_RDWR and the rest) as the host's */
static int host_flags(int access)
{
    int fl = ((access & B_RDWR) ? O_RDWR : (access & B_WRONLY) ? O_WRONLY : O_RDONLY) | HOST_BINARY;
    if (access & B_CREAT) fl |= O_CREAT;
    if (access & B_TRUNC) fl |= O_TRUNC;
    if (access & B_EXCL) fl |= O_EXCL;
    if (access & B_APPEND) fl |= O_APPEND;
    return fl;
}

/* Opens the host file host as handle I/O on it expects (text mode, write-behind); path is the
   name the caller gave, for the log. */
static int open_host(const char *path, const char *host, int access, int fl)
{
    int fd = open(host, fl, 0644);
    /* a host path (bc_open_host) is its own host file: named once */
    if (path == host) port_log("open(\"%s\", %04X) = %d\n", host, access, fd);
    else port_log("open(\"%s\", %04X) -> %s = %d\n", path, access, host, fd);
    if (fd >= 0 && fd < MAXFD) {
        fd_text[fd] = !(access & B_BINARY);
        fd_eof[fd] = 0;
        fd_wrote[fd] = (fl & (O_RDWR | O_WRONLY)) && (fl & (O_CREAT | O_TRUNC));    /* a new or emptied file */
        free(fd_cow[fd]);
        fd_cow[fd] = NULL;
#ifdef PORT_WRITE_BEHIND
        wb_stop(fd);                    /* a handle number used before */
        if ((fl & (O_RDWR | O_WRONLY)) == O_WRONLY) wb_start(fd);
#endif
    }
    return fd;
}

/* bc_open for a host path, not a DOS one: the port's own files that are not the game's, whose
   names DOS could not hold (the black box's recordings/YYYYMMDD-HHMMSS/RECORD.OUT, which the
   game's 8.3 names would cut to RECORDIN/YYYYMMDD; replay.c opens it so). The mode, given with
   B_CREAT, is ignored, as bc_open ignores it. */
int bc_open_host(const char *host, int access, ...)
{
    return open_host(host, host, access, host_flags(access));
}

int bc_open(const char *path, int access, ...)
{
    char host[1024];
    struct stat st;
    int fl, fd, how;
    if (access & B_CREAT) {
        va_list ap;
        va_start(ap, access);
        (void)va_arg(ap, int);
        va_end(ap);
    }
    fl = host_flags(access);
    how = (access & (B_CREAT | B_TRUNC)) == (B_CREAT | B_TRUNC) ? PLAT_CREATE
        : (fl & (O_RDWR | O_WRONLY)) || (access & B_CREAT) ? PLAT_WRITE : PLAT_READ;
    /* A file opened to change but only read so far stays in the data root: it is copied into
       the home directory at its first write (cow_write). The game opens its archives
       read-write (ARC.C) and mostly only reads them. */
    if (how == PLAT_WRITE && !(access & B_CREAT) && (fl & O_RDWR)) {
        if (plat_resolve(path, PLAT_READ, host, sizeof host) == 0
            && strncmp(host, plat_home(), strlen(plat_home())) != 0 && stat(host, &st) == 0) {
            fd = open(host, O_RDONLY | HOST_BINARY);
            port_log("open(\"%s\", %04X) -> %s = %d, copied on its first write\n", path, access, host, fd);
            if (fd >= 0 && fd < MAXFD) {
                fd_text[fd] = !(access & B_BINARY);
                fd_eof[fd] = 0;
                free(fd_cow[fd]);
                fd_cow[fd] = strdup(path);
            }
            return fd;
        }
    }
    if (plat_resolve(path, how, host, sizeof host)) { errno = ENOENT; return -1; }
    return open_host(path, host, access, fl);
}

/* The first write to a file still read from the data root: copy it into the home directory
   and put the copy in place of the handle, at the same position. */
static int cow_write(int fd)
{
    char host[1024];
    off_t pos;
    int nfd;
    if (fd < 0 || fd >= MAXFD || !fd_cow[fd]) return 0;
    pos = lseek(fd, 0, SEEK_CUR);
    if (plat_resolve(fd_cow[fd], PLAT_WRITE, host, sizeof host)) return -1;
    nfd = open(host, O_RDWR | HOST_BINARY);
    if (nfd < 0) return -1;
    lseek(nfd, pos, SEEK_SET);
    dup2(nfd, fd);
    close(nfd);
    port_log("copied %s into the home directory for writing\n", fd_cow[fd]);
    free(fd_cow[fd]);
    fd_cow[fd] = NULL;
    return 0;
}

int bc_creat(const char *path, int mode)
{
    (void)mode;
    return bc_open(path, B_RDWR | B_CREAT | B_TRUNC);
}

int bc_read(int fd, void *buf, unsigned n)
{
    unsigned char *b = buf;
    ssize_t got;
    unsigned i, o;
    if (fd < 0 || fd >= MAXFD || !fd_text[fd]) {
        got = read(fd, buf, n);
        if (fd >= 0 && fd < MAXFD && got == 0) fd_eof[fd] = 1;
        return (int)got;
    }
    if (fd_eof[fd]) return 0;
    for (;;) {
        got = read(fd, buf, n);
        if (got <= 0) { fd_eof[fd] = got == 0; return (int)got; }
        for (i = o = 0; i < (unsigned)got; i++) {
            if (b[i] == 0x1A) {
                lseek(fd, (off_t)i - got, SEEK_CUR);
                fd_eof[fd] = 1;
                break;
            }
            if (b[i] == '\r' && i + 1 < (unsigned)got && b[i + 1] == '\n') continue;
            if (b[i] == '\r' && i + 1 == (unsigned)got) {
                unsigned char c;
                if (read(fd, &c, 1) == 1) {
                    lseek(fd, -1, SEEK_CUR);
                    if (c == '\n') continue;
                }
            }
            b[o++] = b[i];
        }
        if (o || fd_eof[fd]) return (int)o;
    }
}

static int write_text(int fd, const void *buf, unsigned n)
{
    const unsigned char *b = buf;
    unsigned i, start = 0;
    if (cow_write(fd)) return -1;
    if (fd < 0 || fd >= MAXFD || !fd_text[fd]) return (int)raw_write(fd, buf, n);
    for (i = 0; i < n; i++) {
        if (b[i] == '\n') {
            if (i > start && raw_write(fd, b + start, i - start) != (ssize_t)(i - start)) return -1;
            if (raw_write(fd, "\r\n", 2) != 2) return -1;
            start = i + 1;
        }
    }
    if (n > start && raw_write(fd, b + start, n - start) != (ssize_t)(n - start)) return -1;
    return (int)n;
}

int bc_write(int fd, const void *buf, unsigned n)
{
    int r = write_text(fd, buf, n);
    if (r > 0 && fd >= 0 && fd < MAXFD) fd_wrote[fd] = 1;
#ifdef PORT_WRITE_BEHIND
    if (r > 0 && fd >= 0 && fd < MAXFD && wb_buf[fd]) return r;    /* not in the file yet: counted when it is */
#endif
    if (r > 0) changed();
    return r;
}

int bc_close(int fd)
{
    int r = 0, wrote = 0;
#ifdef PORT_WRITE_BEHIND
    r = wb_flush(fd);
    wb_stop(fd);
#endif
    if (fd >= 0 && fd < MAXFD) { free(fd_cow[fd]); fd_cow[fd] = NULL; wrote = fd_wrote[fd]; fd_wrote[fd] = 0; }
    r = close(fd) || r ? -1 : 0;
    if (wrote) changed();               /* after the flush and the close: the file is whole */
    return r;
}

long bc_lseek(int fd, long off, int whence)
{
    if (fd >= 0 && fd < MAXFD) fd_eof[fd] = 0;
    if (WB_FLUSH(fd)) return -1L;
    return (long)lseek(fd, (off_t)off, whence);
}

long filelength(int fd)
{
    struct stat st;
    if (WB_FLUSH(fd)) return -1L;
    return fstat(fd, &st) ? -1L : (long)st.st_size;
}

long tell(int fd) { if (WB_FLUSH(fd)) return -1L; return (long)lseek(fd, 0, SEEK_CUR); }

int eof(int fd)
{
    struct stat st;
    if (WB_FLUSH(fd)) return -1;
    if (fd >= 0 && fd < MAXFD && fd_eof[fd]) return 1;
    if (fstat(fd, &st)) return -1;
    return lseek(fd, 0, SEEK_CUR) >= st.st_size;
}

int setmode(int fd, int mode)
{
    int old;
    if (fd < 0 || fd >= MAXFD) return -1;
    old = fd_text[fd] ? B_TEXT : B_BINARY;
    fd_text[fd] = (mode & B_TEXT) != 0;
    return old;
}

int bc_access(const char *path, int mode)
{
    char host[1024];
    if (plat_resolve(path, PLAT_READ, host, sizeof host)) return -1;
    (void)mode;
    return access(host, F_OK);
}

int bc_unlink(const char *path)
{
    int r = plat_remove(path);
    if (r == 0) changed();
    return r;
}

int bc_rename(const char *from, const char *to)
{
    int r = plat_rename(from, to);
    if (r == 0) changed();
    return r;
}

int bc_mkdir(const char *path)
{
    char host[1024];
    if (plat_resolve(path, PLAT_CREATE, host, sizeof host)) return -1;
    if (mkdir(host, 0755)) return -1;
    changed();
    return 0;
}

/* The DOS current directory: the port keeps the game in its own directory (the merged tree's
   root), as the game never changes it except to go back there on exit. */
int bc_chdir(const char *path)
{
    port_log("chdir(\"%s\")\n", path);
    return 0;
}

int getcurdir(int drive, char *dir)
{
    (void)drive;
    dir[0] = 0;
    return 0;
}

int bc_stat(const char *path, struct stat *st)
{
    char host[1024];
    if (plat_resolve(path, PLAT_READ, host, sizeof host)) return -1;
    return stat(host, st);
}

int bc_fstat(int fd, struct stat *st) { if (WB_FLUSH(fd)) return -1; return fstat(fd, st); }

FILE *bc_fopen(const char *path, const char *mode)
{
    char host[1024], m[8];
    int i, k = 0, how = PLAT_READ;
    FILE *fp;
    for (i = 0; mode[i] && k < 6; i++)
        if (mode[i] != 't') m[k++] = mode[i];
    m[k] = 0;
#ifdef _WIN32
    if (!strchr(m, 'b') && k < 7) { m[k++] = 'b'; m[k] = 0; }  /* as on a POSIX host: no translation */
#endif
    if (strchr(mode, 'w') || strchr(mode, 'a')) how = PLAT_CREATE;
    else if (strchr(mode, '+')) how = PLAT_WRITE;
    if (plat_resolve(path, how, host, sizeof host)) return NULL;
    port_log("fopen(\"%s\", \"%s\") -> %s\n", path, mode, host);
    fp = fopen(host, m);
    /* The stream's reads, writes and seeks are the port's, on its handle (below); the host's
       stdio only opens and closes it, so it should hold no buffer. */
    if (fp) setvbuf(fp, NULL, _IONBF, 0);
    return fp;
}

/* A stream's I/O, done on its handle with no buffer, so that the stream and its handle are
   always at the same place. The game reads a stream's handle directly too: LOADGR.C calls
   fseek(grfp, 0L, 1) and then reads fileno(grfp), which works with Borland's library because
   its fseek empties the buffer and moves the handle to the stream's position. No host C
   library promises that. macOS's kept its buffer; Microsoft's UCRT, even for a stream set to
   _IONBF, reads two bytes ahead into a small buffer, and its fseek to a place inside that
   buffer moves only the buffer pointer, so the handle was a byte ahead and every .GR file's
   offset table was read one byte late (error D004 on Windows). Every stream is binary, as the
   port's streams always were: Borland's text mode is not applied to them. */
size_t bc_fread(void *buf, size_t size, size_t n, FILE *fp)
{
    size_t want = size * n, got = 0;
    ssize_t r;
    if (!want) return 0;
    while (got < want && (r = read(fileno(fp), (char *)buf + got, (unsigned)(want - got))) > 0) got += (size_t)r;
    return got / size;
}

size_t bc_fwrite(const void *buf, size_t size, size_t n, FILE *fp)
{
    size_t want = size * n, put = 0;
    ssize_t r;
    if (!want) return 0;
    while (put < want && (r = write(fileno(fp), (const char *)buf + put, (unsigned)(want - put))) > 0) put += (size_t)r;
    if (put) changed();
    return put / size;
}

int bc_fseek(FILE *fp, long off, int whence)
{
    return lseek(fileno(fp), (off_t)off, whence) == (off_t)-1 ? -1 : 0;
}

long bc_ftell(FILE *fp)
{
    return (long)lseek(fileno(fp), 0, SEEK_CUR);
}

int bc_fgetc(FILE *fp)
{
    unsigned char c;
    return read(fileno(fp), &c, 1) == 1 ? c : EOF;
}

char *bc_fgets(char *s, int n, FILE *fp)
{
    int i = 0, c = 0;
    while (i < n - 1 && (c = bc_fgetc(fp)) != EOF) {
        s[i++] = (char)c;
        if (c == '\n') break;
    }
    if (i == 0 && n > 1) return NULL;
    s[i] = 0;
    return s;
}

/* sscanf and scanf with Borland's integer widths (compat.h): the format is rewritten so that
   an integer conversion with no length stores a short and one with l an int (32 bits on
   every host the port builds for); h, hh, assignment suppression, %%, scansets and the
   other conversions are left as they are. */
static void borland_scan_format(const char *fmt, char *out, size_t n)
{
    size_t o = 0;
    while (*fmt && o + 3 < n) {
        if (*fmt != '%') { out[o++] = *fmt++; continue; }
        out[o++] = *fmt++;
        if (*fmt == '%') { out[o++] = *fmt++; continue; }
        if (*fmt == '*') out[o++] = *fmt++;
        while (*fmt >= '0' && *fmt <= '9' && o + 3 < n) out[o++] = *fmt++;
        if (*fmt == 'N' || *fmt == 'F') fmt++;  /* Borland's near and far: nothing on the host */
        if (*fmt == 'h' && fmt[1] == 'h') { out[o++] = *fmt++; out[o++] = *fmt++; }
        else if (*fmt == 'h' || *fmt == 'L') out[o++] = *fmt++;
        else if (*fmt == 'l') {
            fmt++;                              /* Borland's long is the host's int */
            if (!strchr("diouxXn", *fmt)) out[o++] = 'l';
        } else if (*fmt && strchr("diouxXn", *fmt)) out[o++] = 'h';
        if (*fmt == '[') {
            out[o++] = *fmt++;
            if (*fmt == '^' && o + 3 < n) out[o++] = *fmt++;
            if (*fmt == ']' && o + 3 < n) out[o++] = *fmt++;
            while (*fmt && *fmt != ']' && o + 3 < n) out[o++] = *fmt++;
        }
        if (*fmt && o + 3 < n) out[o++] = *fmt++;
    }
    out[o] = 0;
}

int bc_sscanf(const char *s, const char *fmt, ...)
{
    char f[512];
    va_list ap;
    int r;
    borland_scan_format(fmt, f, sizeof f);
    va_start(ap, fmt);
    r = vsscanf(s, f, ap);
    va_end(ap);
    return r;
}

int bc_scanf(const char *fmt, ...)
{
    char f[512];
    va_list ap;
    int r;
    borland_scan_format(fmt, f, sizeof f);
    va_start(ap, fmt);
    r = vscanf(f, ap);
    va_end(ap);
    return r;
}

/* Borland's rand: a 32-bit LCG, multiplier 015A4E35h, seed 1, the high word's low 15 bits. */
static uint32_t rand_seed = 1;
int bc_rand(void)
{
    rand_seed = rand_seed * 0x015A4E35u + 1;
    return (int)((rand_seed >> 16) & 0x7FFF);
}

/* srand: the seed's low word, high word 0, as Borland's srand stores it (a host unsigned is
   32 bits, and srand((unsigned)time(NULL)) passes them all). */
void bc_srand(unsigned seed) { rand_seed = (uint16_t)seed; }

/* The seed, for the state dump (runtime/replay/replay.c). */
uint32_t port_rand_seed(void) { return rand_seed; }

/* Sets the whole 32-bit seed, which srand cannot (it sets the low word only): for tools that
   run a routine from a given generator state (tools/vectors.py). */
void port_set_rand_seed(uint32_t seed) { rand_seed = seed; }

long bc_time(long *t)
{
    long now = (long)time(NULL);
    if (t) *t = now;
    return now;
}

/* clock() counts BIOS ticks, 18.2 a second, from the start of the program. */
long bc_clock(void) { return (long)pit_bios_ticks(); }

/* exit: the C library's exit, then the termination chain the program hooked (PORT_EXIT_CHAIN,
   portgame.h; UW2: the chain seg021's init hooked, whose shutdown prints the message at cPerror). */
void bc_exit(int status)
{
    fflush(stdout);
#ifdef PORT_BLACKBOX
    port_blackbox_close(0);
#endif
#ifdef PORT_EXIT_CHAIN
    PORT_EXIT_CHAIN();
#endif
    fflush(stdout);
    fflush(stderr);
    plat_game_exit(status);
}

/* The string extras. */
static char *utoa_base(unsigned long v, char *buf, int radix, int neg)
{
    char tmp[40];
    int n = 0, i = 0;
    if (radix < 2 || radix > 36) { buf[0] = 0; return buf; }
    do { int d = (int)(v % (unsigned)radix); tmp[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10); v /= (unsigned)radix; } while (v);
    if (neg) buf[i++] = '-';
    while (n) buf[i++] = tmp[--n];
    buf[i] = 0;
    return buf;
}

/* itoa takes a 16-bit int as Borland's did: -1 in radix 16 is ffff, in radix 10 -1. */
char *itoa(int value, char *buf, int radix)
{
    int16_t v = (int16_t)value;
    if (radix == 10 && v < 0) return utoa_base((unsigned long)(-(long)v), buf, radix, 1);
    return utoa_base((uint16_t)v, buf, radix, 0);
}

char *ltoa(long value, char *buf, int radix)
{
    int32_t v = (int32_t)value;
    if (radix == 10 && v < 0) return utoa_base((unsigned long)(-(int64_t)v), buf, radix, 1);
    return utoa_base((uint32_t)v, buf, radix, 0);
}

char *ultoa(unsigned long value, char *buf, int radix)
{
    return utoa_base((uint32_t)value, buf, radix, 0);
}

char *strupr(char *s)
{
    char *p;
    for (p = s; *p; p++) if (*p >= 'a' && *p <= 'z') *p -= 32;
    return s;
}

char *strlwr(char *s)
{
    char *p;
    for (p = s; *p; p++) if (*p >= 'A' && *p <= 'Z') *p += 32;
    return s;
}

int stricmp(const char *a, const char *b) { return strcasecmp(a, b); }
int strnicmp(const char *a, const char *b, size_t n) { return strncasecmp(a, b, n); }

/* Borland's _ctype, 257 entries (EOF first), as the EXE's DGROUP has it at DS:PORT_CTYPE_AT
   (UW2: 1BF6h). */
unsigned char _ctype[257];

void port_dos_int0(void);
typedef void (*isr_fn)(void);
static isr_fn vectors[256];

void borland_init(void)
{
#ifdef PORT_CTYPE_AT
    memcpy(_ctype, port_dgroup_image + PORT_CTYPE_AT, sizeof _ctype);
#endif
    vectors[0] = port_dos_int0;         /* DOS's own: "Divide overflow" */
}

/* Interrupt vectors: a table only. The game sets int 0 (int0_trap) and reads it back. */
isr_fn getvect(int n) { return vectors[n & 0xFF]; }

void setvect(int n, isr_fn f)
{
    vectors[n & 0xFF] = f;
    if ((n & 0xFF) == 0) int0_install();
}

isr_fn port_vector(int n) { return vectors[n & 0xFF]; }

/* DOS calls the C makes itself through intdosx: read (3Fh) and write (40h) into a far
   buffer (UW2: MISCUTIL.C), with DS:DX through the paragraph map. */
struct WORDREGS { unsigned short ax, bx, cx, dx, si, di, cflag, flags; };
union REGS { struct WORDREGS x; };
struct SREGS { unsigned short es, cs, ss, ds; };
void *port_mk_fp(unsigned seg, unsigned off);

int intdosx(union REGS *in, union REGS *out, struct SREGS *s)
{
    unsigned ah = in->x.ax >> 8;
    int r;
    *out = *in;
    out->x.cflag = 0;
    if (ah == 0x3F || ah == 0x40) {
        void *buf = port_mk_fp(s->ds, in->x.dx);
        if (!buf && in->x.cx) { out->x.cflag = 1; out->x.ax = 6; return out->x.ax; }
        if (port_ems_has(buf) && ((unsigned long)in->x.dx + in->x.cx > 0x10000UL || port_ems_runs_past(buf, in->x.cx))) {
            /* no replayed session does this: a transfer into the frame that wraps its segment's
               offset, or runs past the frame's end by its linear address (DS inside the frame,
               DX + CX short of 10000h). The frame mapped twice would wrap it to the frame's
               start, the frame mapped once (the web build) would not, and what DOS itself does
               there has not been measured. Mapped once, the transfer would run on past the
               frame's 64 KB into whatever follows it (on the web, the heap): stop instead. */
#ifdef PORT_FRAME_SINGLE
            port_fatal("intdosx: DOS function %02Xh (%s) at %04X:%04X for %u bytes runs past the EMS frame's end,"
                       " which the frame mapped once cannot hold",
                       ah, ah == 0x3F ? "read" : "write", s->ds, in->x.dx, in->x.cx);
#else
            port_log("intdosx: DOS function %02Xh at %04X:%04X for %u bytes runs past the EMS frame's end\n",
                     ah, s->ds, in->x.dx, in->x.cx);
#endif
        }
        r = ah == 0x3F ? bc_read(in->x.bx, buf, in->x.cx) : bc_write(in->x.bx, buf, in->x.cx);
        if (r < 0) { out->x.cflag = 1; out->x.ax = 5; }
        else out->x.ax = (unsigned short)r;
        return out->x.ax;
    }
    port_log("intdosx: DOS function %02Xh not emulated\n", ah);
    out->x.cflag = 1;
    out->x.ax = 1;
    return 1;
}

int intdos(union REGS *in, union REGS *out)
{
    struct SREGS s = { 0, 0, 0, (unsigned short)port_ds };
    return intdosx(in, out, &s);
}

/* int86: int 21h through intdos; int 1, 2 (the NMI), 3 and 4 return at once, as DOS's default
   handler for them, an iret, does (UW1: MISCUTIL.C's dbg_break raises int 2); anything else is
   logged and returns with the registers as they were */
int int86(int n, union REGS *in, union REGS *out)
{
    if (n == 0x21) return intdos(in, out);
    *out = *in;
    if (n < 1 || n > 4) port_log("int86(%02Xh) not emulated (AX %04X)\n", n, in->x.ax);
    return out->x.ax;
}

/* delay: Borland's calibrated busy wait, here the host's sleep */
void delay(unsigned ms)
{
    plat_sleep_ns((uint64_t)(ms & 0xFFFF) * 1000000u);
}

/* sound and nosound: the PC speaker, as Borland's library drives it (PIT channel 2 at
   1193181 / hz, by the divisor 1234DDh / hz it computes, and port 61h's speaker bits), a
   square wave in the mixer (sound/audio.c) */
void audio_speaker(unsigned hz, uint64_t t_us);
uint64_t ail_now_us(void);
void sound(unsigned hz)
{
    unsigned div;
    hz &= 0xFFFF;
    if (hz <= 18) return;               /* Borland's: no divisor fits 16 bits */
    div = 0x1234DDu / hz;
    audio_speaker(1193181u / div, ail_now_us());
}

void nosound(void)
{
    audio_speaker(0, ail_now_us());
}

void bc_geninterrupt(int n)
{
    port_log("geninterrupt(%02Xh) not emulated (AX %04X)\n", n, port_ax.x);
}

struct dfree { unsigned df_avail, df_total, df_bsec, df_sclus; };
/* getdfree: plenty of room, in DOS's units (clusters of 8 sectors of 512 bytes, at most 65535
   of them, as DOS reported a large drive). */
void getdfree(unsigned char drive, struct dfree *df)
{
    (void)drive;
    df->df_avail = 0x7FFF;
    df->df_total = 0xFFFF;
    df->df_bsec = 512;
    df->df_sclus = 8;
}

void outportb(unsigned port, unsigned char v) { port_outb(port & 0xFFFF, v); }
unsigned char inportb(unsigned port) { return port_inb(port & 0xFFFF); }
void outport(unsigned port, unsigned v) { port_outb(port & 0xFFFF, v & 0xFF); port_outb((port + 1) & 0xFFFF, (v >> 8) & 0xFF); }
unsigned inport(unsigned port) { return port_inb(port & 0xFFFF) | (unsigned)port_inb((port + 1) & 0xFFFF) << 8; }

void port_outb(unsigned port, uint8_t v)
{
    if (port >= 0x3C0 && port <= 0x3DF) { vga_outb(port, v); return; }
    port_log("out %03Xh, %02Xh ignored\n", port, v);
}

uint8_t port_inb(unsigned port)
{
    if (port >= 0x3C0 && port <= 0x3DF) return vga_inb(port);
    port_log("in %03Xh ignored\n", port);
    return 0xFF;
}

/* Directory search: findfirst and findnext over the merged tree, with DOS wildcards. The
   search state lives in ff_reserved: an index into a table of results. The record is the
   game's view of it (include/dir.h), byte for byte: packed, a 32-bit size. Before
   Milestone 5 this copy was laid out naturally, so the game read ff_name six bytes early
   and copied files with empty names: no save could be written. */
#pragma pack(push, 1)
struct ffblk {
    char ff_reserved[21];
    char ff_attrib;
    unsigned short ff_ftime;
    unsigned short ff_fdate;
    int ff_fsize;
    char ff_name[13];
};
#pragma pack(pop)
_Static_assert(sizeof(struct ffblk) == 43, "Borland's ffblk is 43 bytes");

struct found { char name[13]; int isdir; long size, mtime; };
struct search { struct found *f; int n, cap, next, attrib; char pat[13]; int used; };
#define MAXSEARCH 8
static struct search searches[MAXSEARCH];

static int wild(const char *pat, const char *name)
{
    /* DOS 8.3 matching: name and extension apart, '*' to the end of each part, '?' any. */
    char pn[9], pe[4], nn[9], ne[4];
    const char *d;
    int i;
    #define SPLIT(s, n, e) do { d = strchr(s, '.'); \
        for (i = 0; i < 8 && s[i] && s + i != d; i++) n[i] = (char)toupper((unsigned char)s[i]); n[i] = 0; \
        e[0] = 0; if (d) { for (i = 0; i < 3 && d[1 + i]; i++) e[i] = (char)toupper((unsigned char)d[1 + i]); e[i] = 0; } } while (0)
    SPLIT(pat, pn, pe);
    SPLIT(name, nn, ne);
    for (i = 0; i < 8; i++) {
        if (pn[i] == '*') break;
        if (!pn[i] && !nn[i]) break;
        if (pn[i] == '?' ) { if (!nn[i]) continue; continue; }
        if (pn[i] != nn[i]) return 0;
    }
    for (i = 0; i < 3; i++) {
        if (pe[i] == '*') break;
        if (!pe[i] && !ne[i]) break;
        if (pe[i] == '?') continue;
        if (pe[i] != ne[i]) return 0;
    }
    return 1;
}

static void collect(const char *name, int isdir, long size, long mtime, void *ctx)
{
    struct search *s = ctx;
    if (strlen(name) > 12 || !wild(s->pat, name)) return;
    if (isdir && !(s->attrib & 0x10)) return;
    if (s->n == s->cap) {
        s->cap = s->cap ? s->cap * 2 : 32;
        s->f = realloc(s->f, sizeof *s->f * (size_t)s->cap);
    }
    strcpy(s->f[s->n].name, name);
    strupr(s->f[s->n].name);
    s->f[s->n].isdir = isdir;
    s->f[s->n].size = size;
    s->f[s->n].mtime = mtime;
    s->n++;
}

int findnext(struct ffblk *ff)
{
    int id = (unsigned char)ff->ff_reserved[0];
    struct search *s;
    struct found *f;
    struct tm *tm;
    time_t t;
    if (id >= MAXSEARCH || !searches[id].used) return -1;
    s = &searches[id];
    if (s->next >= s->n) { errno = ENOENT; return -1; }
    f = &s->f[s->next++];
    strcpy(ff->ff_name, f->name);
    ff->ff_attrib = f->isdir ? 0x10 : 0x20;
    ff->ff_fsize = (int)f->size;
    t = (time_t)f->mtime;
    tm = localtime(&t);
    ff->ff_ftime = (unsigned short)(tm->tm_hour << 11 | tm->tm_min << 5 | tm->tm_sec / 2);
    ff->ff_fdate = (unsigned short)((tm->tm_year - 80) << 9 | (tm->tm_mon + 1) << 5 | tm->tm_mday);
    return 0;
}

int findfirst(const char *path, struct ffblk *ff, int attrib)
{
    static int rr;
    char dir[256];
    const char *slash = strrchr(path, '\\');
    struct search *s;
    int id = rr;
    rr = (rr + 1) % MAXSEARCH;
    s = &searches[id];
    s->n = 0;
    s->next = 0;
    s->used = 1;
    s->attrib = attrib;
    if (slash) {
        size_t l = (size_t)(slash - path);
        if (l >= sizeof dir) l = sizeof dir - 1;
        memcpy(dir, path, l);
        dir[l] = 0;
        snprintf(s->pat, sizeof s->pat, "%s", slash + 1);
    } else {
        dir[0] = 0;
        snprintf(s->pat, sizeof s->pat, "%s", path);
    }
    plat_listdir(dir, collect, s);
    ff->ff_reserved[0] = (char)id;
    return findnext(ff);
}

/* The rest of <dos.h> the game names but the boot path does not reach. */
void disable(void) {}
void enable(void) {}
