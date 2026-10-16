/* objplace's test object: two functions in one .text (no --split_sections), a call from one to
   the other (R_ARM_THM_CALL), the address of an extern and of the file's own table in literal
   words (R_ARM_ABS32). */
extern int g_total;
static const unsigned char k_table[4] = { 1, 2, 3, 4 };

int helper(int x)
{
    return x * 3 + k_table[x & 3];
}

int caller(int a)
{
    g_total = helper(a) + 1;
    return g_total;
}
