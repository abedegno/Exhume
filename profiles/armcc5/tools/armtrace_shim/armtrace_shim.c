/* armtrace_shim.so: an LD_PRELOAD library for the Linux armcc (a 32-bit x86 program) that writes
   a few bytes into the compiler process's own memory when the process starts, before main.
   Nothing on disk is touched; the next compile without the library is the stock compiler.

   ARMTRACE_PATCH="ADDR:OLD:NEW[,ADDR:OLD:NEW...]", each field hex (ADDR without 0x). Each patch
   is written only when the bytes at ADDR are OLD (so a patch meant for one build does nothing
   to another): NEW must be no longer than OLD. If any check fails, nothing is written and the
   library says so on stderr. armtrace.py builds the variable from armtrace.local.toml.

   Built with no C library, so that only a 32-bit compiler is needed (no 32-bit libc headers):
       cc -m32 -O2 -fPIC -shared -nostdlib -o armtrace_shim.so armtrace_shim.c
   ARMTRACE_HOOK="ADDR:OLD" and ARMTRACE_LEVELS="DEFAULT[,SITE=LEVEL...]" (hex addresses, decimal
   levels) are the other form: the five bytes at ADDR (checked against OLD as above) become a jump
   to this library's own getter, which answers DEFAULT, or LEVEL when it was called from the call
   whose return address is SITE. ADDR is a point where the stack holds the return address into
   the getter's caller (the getter's entry, or a tail jump to it). So one part of the trace can be
   asked for at a higher level than the rest.

   ARMTRACE_ONLY="SITE:K" (with the hook) keeps the getter at 0 except from the K-th call (counted
   from 1) from SITE until the next call from SITE: with SITE the call that starts a graph
   colouring, only the K-th colouring is traced. ARMTRACE_SURVEY=1 (with the hook) writes each
   call site's return address to stderr (with --errors, that file) the first time it calls, as `armtrace_shim: site HEX`.

   glibc calls a library's constructors with (argc, argv, envp). */

static long sys3(long n, long a, long b, long c)
{
    long r;
    __asm__ volatile ("push %%ebx; mov %2, %%ebx; int $0x80; pop %%ebx"
                      : "=a"(r) : "0"(n), "r"(a), "c"(b), "d"(c) : "memory");
    return r;
}

#define SYS_write 4
#define SYS_mprotect 125
#define PAGE 4096UL

static void say(const char *s)
{
    long n = 0;
    while (s[n]) n++;
    sys3(SYS_write, 2, (long)s, n);
}

static int hexv(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* hex digits at *p up to a ':' / ',' / end: a number (bytes == 0) or a byte string */
static const char *field(const char *p, unsigned long *num, unsigned char *buf, int *len)
{
    unsigned long v = 0;
    int n = 0, half = 0, h;
    while (*p && *p != ':' && *p != ',' && *p != '=') {
        h = hexv(*p++);
        if (h < 0) return 0;
        v = v * 16 + h;
        if (buf) {
            if (half) { if (n >= 16) return 0; buf[n++] = (unsigned char)(((v >> 4) & 0xF) << 4 | h); v = 0; }
            half ^= 1;
        }
    }
    if (buf && half) return 0;
    if (num) *num = v;
    if (len) *len = n;
    return p;
}

struct patch { unsigned long addr; unsigned char old[16], new_[16]; int nold, nnew; };

#define NSITES 64
static unsigned long site_addr[NSITES];
static int site_level[NSITES], nsites, default_level;
static unsigned long only_site, only_k, only_n, seen[256];
static int survey, nseen;

static void say(const char *s);

static void say_site(unsigned long a)
{
    char b[] = "armtrace_shim: site 00000000\n";
    int i;
    for (i = 0; i < 8; i++) b[20 + i] = "0123456789abcdef"[(a >> (28 - 4 * i)) & 15];
    say(b);
}

__attribute__((used, noinline)) int armtrace_getter(void)
{
    unsigned long ra = (unsigned long)__builtin_return_address(0);
    int i;
    if (survey) {
        for (i = 0; i < nseen && seen[i] != ra; i++) ;
        if (i == nseen && nseen < 256) { seen[nseen++] = ra; say_site(ra); }
    }
    if (only_site) {
        if (ra == only_site) only_n++;
        if (only_n != only_k) return 0;
    }
    for (i = 0; i < nsites; i++)
        if (site_addr[i] == ra) return site_level[i];
    return default_level;
}

static const char *find_env(char **envp, const char *k)
{
    int i;
    for (; envp && *envp; envp++) {
        for (i = 0; k[i] && (*envp)[i] == k[i]; i++) ;
        if (!k[i]) return *envp + i;
    }
    return 0;
}

static const char *decimal(const char *p, int *v)
{
    int n = 0, d = 0;
    while (*p >= '0' && *p <= '9') { n = n * 10 + (*p++ - '0'); d++; }
    *v = n;
    return d ? p : 0;
}

__attribute__((constructor)) static void armtrace_shim(int argc, char **argv, char **envp)
{
    const char *v = find_env(envp, "ARMTRACE_PATCH="), *h = find_env(envp, "ARMTRACE_HOOK="),
               *lv = find_env(envp, "ARMTRACE_LEVELS=");
    struct patch ps[8];
    int np = 0, i, j;
    (void)argc; (void)argv;
    if (h && *h) {                       /* the hook: a jump to armtrace_getter, as one more patch */
        struct patch *p = &ps[np];
        long rel;
        h = field(h, &p->addr, 0, 0);
        if (!h || *h++ != ':') goto bad;
        h = field(h, 0, p->old, &p->nold);
        if (!h || *h || p->nold < 5) goto bad;
        rel = (long)armtrace_getter - (long)(p->addr + 5);
        p->new_[0] = 0xe9;
        for (i = 0; i < 4; i++) p->new_[1 + i] = (unsigned char)(rel >> (8 * i));
        p->nnew = 5;
        np++;
        if (!lv || !(lv = decimal(lv, &default_level))) goto bad;
        while (*lv == ',') {
            if (nsites == NSITES) goto bad;
            lv = field(lv + 1, &site_addr[nsites], 0, 0);
            if (!lv || *lv++ != '=' || !(lv = decimal(lv, &site_level[nsites]))) goto bad;
            nsites++;
        }
        if (*lv) goto bad;
        if ((lv = find_env(envp, "ARMTRACE_ONLY=")) && *lv) {
            lv = field(lv, &only_site, 0, 0);
            if (!lv || *lv++ != ':') goto bad;
            if (!(lv = decimal(lv, &i)) || *lv || i < 1) goto bad;
            only_k = (unsigned long)i;
        }
        survey = (lv = find_env(envp, "ARMTRACE_SURVEY=")) && *lv == '1';
    }
    if (!v) v = "";
    while (*v) {
        struct patch *p = &ps[np];
        if (np == 8) { say("armtrace_shim: too many patches\n"); return; }
        v = field(v, &p->addr, 0, 0);
        if (!v || *v++ != ':') goto bad;
        v = field(v, 0, p->old, &p->nold);
        if (!v || *v++ != ':') goto bad;
        v = field(v, 0, p->new_, &p->nnew);
        if (!v || p->nnew == 0 || p->nnew > p->nold) goto bad;
        np++;
        if (*v == ',') v++;
        else if (*v) goto bad;
    }
    for (i = 0; i < np; i++)
        for (j = 0; j < ps[i].nold; j++)
            if (((volatile unsigned char *)ps[i].addr)[j] != ps[i].old[j]) {
                say("armtrace_shim: the bytes at a patch address are not the expected ones (another build?); nothing written\n");
                return;
            }
    for (i = 0; i < np; i++) {
        unsigned long a = ps[i].addr & ~(PAGE - 1), e = (ps[i].addr + ps[i].nnew + PAGE - 1) & ~(PAGE - 1);
        if (sys3(SYS_mprotect, a, e - a, 7) != 0) { say("armtrace_shim: mprotect failed\n"); return; }
        for (j = 0; j < ps[i].nnew; j++) ((volatile unsigned char *)ps[i].addr)[j] = ps[i].new_[j];
        sys3(SYS_mprotect, a, e - a, 5);
    }
    return;
bad:
    say("armtrace_shim: ARMTRACE_PATCH is not ADDR:OLD:NEW[,...] in hex\n");
}
