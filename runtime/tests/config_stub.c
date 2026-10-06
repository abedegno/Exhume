/* port_config_get and port_config_set for enhance_test.c, over one in-memory value */
#include <stdio.h>
#include <string.h>
static char val[256];
static int have;
int port_config_get(const char *home, const char *key, char *out, size_t n)
{
    (void)home; (void)key;
    if (!have) return -1;
    snprintf(out, n, "%s", val);
    return 0;
}
int port_config_set(const char *home, const char *key, const char *v)
{
    (void)home; (void)key;
    snprintf(val, sizeof val, "%s", v);
    have = 1;
    return 0;
}
