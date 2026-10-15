/* opts: --cpu Cortex-M3 -O0 */
typedef unsigned int uint;
typedef unsigned short ushort;
typedef unsigned char uchar;
typedef signed char sbyte;
typedef void code(void);

extern uchar g_mode;
#define DAT_20000000 g_mode
extern ushort g_count;
#define DAT_20000002 g_count
extern int g_last;
#define DAT_20000004 g_last
extern int g_table;
#define DAT_20000008 g_table

void put(int param_1);
int add3(int param_1, int param_2, int param_3);
void clear_table(void);
int pick(int param_1);
void tick(void);
int sum_to(int param_1);
int clamp(int param_1, int param_2, int param_3);
void report(int param_1);

/* draft of put at 0x08000000, 0x6 bytes (o0c) */
void put(int param_1)
{
  DAT_20000004 = param_1;
}

/* draft of add3 at 0x08000006, 0x8 bytes (o0c) */
int add3(int param_1, int param_2, int param_3)
{
  return param_1 + param_2 + param_3;
}

/* draft of clear_table at 0x0800000E, 0x14 bytes (o0c) */
void clear_table(void)
{
  int v0;
  v0 = 0;
  while (v0 < 8) {
    (&DAT_20000008)[v0] = 0;
    v0++;
  }
}

/* draft of pick at 0x08000022, 0x2A bytes (o0c) */
int pick(int param_1)
{
  switch (param_1) {
  case 0:
    return 0xa;
  case 1:
    return 0x14;
  case 2:
    return 0x23;
  case 3:
    return 0x2f;
  case 4:
    return 0x33;
  default:
    return -1;
  }
}

/* draft of tick at 0x0800004C, 0x2C bytes (o0c) */
void tick(void)
{
  if (DAT_20000000 == 2) {
    DAT_20000002 = DAT_20000002 + 1;
  }
  else {
    DAT_20000002 = 0;
  }
  if (DAT_20000002 > 0x12c) {
    DAT_20000000 = 0;
  }
}

/* draft of sum_to at 0x08000078, 0x10 bytes (o0c) */
int sum_to(int param_1)
{
  int v0;
  v0 = 0;
  while (param_1 > 0) {
    v0 = v0 + param_1;
    param_1--;
  }
  return v0;
}

/* draft of clamp at 0x08000088, 0x16 bytes (o0c) */
int clamp(int param_1, int param_2, int param_3)
{
  if (param_1 < param_2) {
    return param_2;
  }
  if (param_1 > param_3) {
    return param_3;
  }
  return param_1;
}

/* draft of report at 0x0800009E, 0x3A bytes (o0c) */
void report(int param_1)
{
  int v2;
  v2 = 0;
  while (v2 < param_1) {
    put(pick(v2) + (&DAT_20000008)[v2 & 7]);
    v2++;
  }
}
