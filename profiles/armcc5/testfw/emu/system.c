/* A small whole-system firmware for the emulator tests (tests/test_emu_*.py): clocks, SysTick,
   a timer, a UART that answers lines, flash programming, the device information block, a GPIO
   output and input, and a register device on a bit-banged I2C bus. It links with the test
   firmware's crc.c (crc16), so the same function runs in a whole system and alone. */
#define REG(a) (*(volatile unsigned int *)(a))
#define RCC_CR      REG(0x40021000)
#define RCC_CFGR    REG(0x40021004)
#define RCC_APB2ENR REG(0x40021018)
#define RCC_APB1ENR REG(0x4002101C)
#define ST_CTRL     REG(0xE000E010)
#define ST_LOAD     REG(0xE000E014)
#define NVIC_ISER0  REG(0xE000E100)
#define NVIC_ISER1  REG(0xE000E104)
#define U1_SR       REG(0x40013800)
#define U1_DR       REG(0x40013804)
#define U1_BRR      REG(0x40013808)
#define U1_CR1      REG(0x4001380C)
#define TIM2_CR1    REG(0x40000000)
#define TIM2_DIER   REG(0x4000000C)
#define TIM2_SR     REG(0x40000010)
#define TIM2_PSC    REG(0x40000028)
#define TIM2_ARR    REG(0x4000002C)
#define FL_KEYR     REG(0x40022004)
#define FL_SR       REG(0x4002200C)
#define FL_CR       REG(0x40022010)
#define FL_AR       REG(0x40022014)
#define GPIOA_IDR   REG(0x40010808)
#define GPIOA_BSRR  REG(0x40010810)
#define GPIOB_BSRR  REG(0x40010C10)
#define GPIOB_IDR   REG(0x40010C08)
#define FLASH_KB    (*(volatile unsigned short *)0x1FFFF7E0)
#define UID0        REG(0x1FFFF7E8)
#define SAVE_ADDR   0x08007C00u          /* the last 1 KB page of a 32 KB part */

extern unsigned short crc16(const unsigned char *p, unsigned int n);

volatile unsigned int g_ticks;          /* SysTick, each millisecond */
volatile unsigned int g_tim_updates;    /* TIM2, each millisecond */
unsigned short g_flash_kb;
unsigned int g_uid0;
unsigned short g_saved;                 /* read back from the flash after programming it */
unsigned char g_sensor[2];              /* two registers of the I2C device */
unsigned char g_button;                 /* PA0 */
unsigned char g_line[32];
volatile unsigned char g_line_len;
volatile unsigned char g_line_ready;
unsigned int g_lines;

/* ---- a bit-banged I2C bus on PB6 (SCL) and PB7 (SDA): the emulator stubs these four ---- */
static void bus_delay(void) { volatile int i; for (i = 0; i < 10; i++) ; }
static void scl(int v) { GPIOB_BSRR = v ? (1u << 6) : (1u << 22); bus_delay(); }
static void sda(int v) { GPIOB_BSRR = v ? (1u << 7) : (1u << 23); bus_delay(); }

void i2c_start(void) { sda(1); scl(1); sda(0); scl(0); }
void i2c_stop(void) { sda(0); scl(1); sda(1); }
int i2c_write(unsigned char b)
{
    int i, ack;
    for (i = 7; i >= 0; i--) { sda((b >> i) & 1); scl(1); scl(0); }
    sda(1); scl(1); ack = !(GPIOB_IDR & (1u << 7)); scl(0);
    return ack;
}
unsigned char i2c_read(int ack)
{
    int i; unsigned char b = 0;
    sda(1);
    for (i = 0; i < 8; i++) { scl(1); b = (unsigned char)((b << 1) | ((GPIOB_IDR >> 7) & 1)); scl(0); }
    sda(!ack); scl(1); scl(0);
    return b;
}

static int sensor_read(unsigned char reg, unsigned char *out, int n)
{
    int i;
    i2c_start();
    if (!i2c_write(0x2A << 1)) { i2c_stop(); return 0; }
    i2c_write(reg);
    i2c_start();
    i2c_write((0x2A << 1) | 1);
    for (i = 0; i < n; i++) out[i] = i2c_read(i < n - 1);
    i2c_stop();
    return 1;
}

static void sensor_write(unsigned char reg, unsigned char v)
{
    i2c_start(); i2c_write(0x2A << 1); i2c_write(reg); i2c_write(v); i2c_stop();
}

/* ---- clocks, timers, UART ---- */
static void clock_init(void)
{
    RCC_CR |= 1u << 16;                          /* HSE on */
    while (!(RCC_CR & (1u << 17))) ;
    RCC_CFGR |= 7u << 18;                        /* PLL x9 */
    RCC_CR |= 1u << 24;                          /* PLL on */
    while (!(RCC_CR & (1u << 25))) ;
    RCC_CFGR = (RCC_CFGR & ~3u) | 2u;            /* switch to the PLL */
    while ((RCC_CFGR & 0xC) != 0x8) ;
    RCC_APB2ENR |= (1u << 14) | (1u << 2) | (1u << 3);
    RCC_APB1ENR |= 1u;
}

void SysTick_Handler(void) { g_ticks++; }

void TIM2_IRQHandler(void)
{
    if (TIM2_SR & 1) { TIM2_SR = ~1u; g_tim_updates++; }
}

void USART1_IRQHandler(void)
{
    if (U1_SR & 0x20) {
        unsigned char c = (unsigned char)U1_DR;
        if (g_line_ready) return;
        if (c == '\r') g_line_ready = 1;
        else if (g_line_len < sizeof g_line) g_line[g_line_len++] = c;
    }
}

static void send(unsigned char c) { while (!(U1_SR & 0x80)) ; U1_DR = c; }

/* a line's answer: the line in capitals, then its CRC-16 (low byte first) */
static void answer(void)
{
    unsigned int i; unsigned short crc;
    for (i = 0; i < g_line_len; i++) {
        unsigned char c = g_line[i];
        if (c >= 'a' && c <= 'z') g_line[i] = (unsigned char)(c - 32);
        send(g_line[i]);
    }
    crc = crc16(g_line, g_line_len);
    send((unsigned char)crc); send((unsigned char)(crc >> 8));
    g_lines++;
    g_line_len = 0; g_line_ready = 0;
}

static void flash_save(unsigned short v)
{
    FL_KEYR = 0x45670123; FL_KEYR = 0xCDEF89AB;
    FL_CR = 2; FL_AR = SAVE_ADDR; FL_CR = 0x42;  /* erase the page */
    while (FL_SR & 1) ;
    FL_CR = 1;                                   /* program */
    *(volatile unsigned short *)SAVE_ADDR = v;
    while (FL_SR & 1) ;
    FL_CR = 0x80;                                /* lock */
    g_saved = *(volatile unsigned short *)SAVE_ADDR;
}

int main(void)
{
    unsigned int last = 0;
    clock_init();
    ST_LOAD = 72000 - 1; ST_CTRL = 7;            /* 1 ms at 72 MHz */
    TIM2_PSC = 7199; TIM2_ARR = 9; TIM2_DIER = 1; TIM2_CR1 = 1; NVIC_ISER0 = 1u << 28;
    U1_BRR = 0x271; U1_CR1 = (1u << 13) | (1u << 5) | (1u << 3) | (1u << 2);
    NVIC_ISER1 = 1u << (37 - 32);
    g_flash_kb = FLASH_KB;
    g_uid0 = UID0;
    flash_save(0x1234);
    sensor_write(3, 0x77);
    sensor_read(2, g_sensor, 2);
    for (;;) {
        if (g_line_ready) answer();
        if (g_ticks - last >= 100) {             /* every 100 ms: blink PA5, sample PA0 */
            last = g_ticks;
            GPIOA_BSRR = (g_ticks / 100) & 1 ? (1u << 5) : (1u << 21);
            g_button = (unsigned char)(GPIOA_IDR & 1);
        }
    }
}
