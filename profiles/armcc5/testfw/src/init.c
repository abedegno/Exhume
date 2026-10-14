/* one initialised array on its own: a candidate that leaves it uninitialised must not match */
unsigned char g_init[16] = { 9, 9, 9, 9 };
unsigned char *init(void) { return g_init; }
