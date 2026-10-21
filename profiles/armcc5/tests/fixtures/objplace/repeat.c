/* objplace's second test object: two .data sections of one name (armcc keeps the file's
   scalars apart from a table of pointers to its functions), and narrow tail calls to a global
   function (R_ARM_THM_JUMP11). */
int state = 1;
static int count = 2;

void bump(void)
{
    state++;
}

void step(int x)
{
    if (x) bump();
    else count++;
}

void maybe(int x)
{
    if (x) bump();
}

typedef struct { void (*a)(void); void (*b)(int); int n; } ops_t;
ops_t ops = { bump, step, 3 };
