/* runtime.c: the part of Arm's C library (MicroLIB or the standard one) a firmware's C reaches
   without naming it, for the open-toolchain build (openbuild.py). Plain and slow on purpose:
   the build is for testing behaviour, not speed. A firmware that calls more of the library
   (sprintf, float helpers) adds its own file through [openbuild] extra, or links compiler-rt's
   builtins through [openbuild] libs. 64-bit division and shifts are in runtime.S. */
typedef unsigned int size_t;
typedef unsigned long long u64;
typedef long long s64;

void *memset(void *d, int c, size_t n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
void *memcpy(void *d, const void *s, size_t n)
{
    unsigned char *p = d; const unsigned char *q = s;
    while (n--) *p++ = *q++;
    return d;
}
void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *p = d; const unsigned char *q = s;
    if (p < q) while (n--) *p++ = *q++;
    else { p += n; q += n; while (n--) *--p = *--q; }
    return d;
}
int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *p = a, *q = b;
    for (; n; n--, p++, q++) if (*p != *q) return *p - *q;
    return 0;
}
size_t strlen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }
int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) a++, b++;
    return (unsigned char)*a - (unsigned char)*b;
}
int strncmp(const char *a, const char *b, size_t n)
{
    for (; n; n--, a++, b++) if (*a != *b || !*a) return (unsigned char)*a - (unsigned char)*b;
    return 0;
}
char *strcpy(char *d, const char *s) { char *r = d; while ((*d++ = *s++) != 0) ; return r; }

/* the run-time ABI's memory helpers (note __aeabi_memset's argument order) */
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset8(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }

u64 __udivmoddi4(u64 n, u64 d, u64 *rem)
{
    u64 q = 0, r = 0; int i;
    for (i = 63; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1);
        if (r >= d) { r -= d; q |= 1ULL << i; }
    }
    if (rem) *rem = r;
    return q;
}

s64 __divmoddi4(s64 n, s64 d, s64 *rem)
{
    int nn = n < 0, nd = d < 0; u64 r, q;
    q = __udivmoddi4(nn ? -(u64)n : (u64)n, nd ? -(u64)d : (u64)d, &r);
    if (rem) *rem = nn ? -(s64)r : (s64)r;
    return nn != nd ? -(s64)q : (s64)q;
}
