/* port_config_get and port_config_set for the host tests (enhance_test.c, settings_test.c):
   an in-memory key/value store of up to 32 pairs; HOME is ignored */
#include <stdio.h>
#include <string.h>
#define STUB_MAX 32
static struct { char key[64]; char val[256]; } store[STUB_MAX];
static int count;
int port_config_get(const char *home, const char *key, char *out, size_t n)
{
    int i;
    (void)home;
    for (i = 0; i < count; i++)
        if (!strcmp(store[i].key, key)) {
            snprintf(out, n, "%s", store[i].val);
            return 0;
        }
    return -1;
}
int port_config_set(const char *home, const char *key, const char *v)
{
    int i;
    (void)home;
    for (i = 0; i < count; i++)
        if (!strcmp(store[i].key, key)) break;
    if (i == count) {
        if (count == STUB_MAX) return -1;
        count++;
        snprintf(store[i].key, sizeof store[i].key, "%s", key);
    }
    snprintf(store[i].val, sizeof store[i].val, "%s", v);
    return 0;
}
