/* portgame.h: UW2's side of runtime/port/port.h (UW2Decomp keeps these in src/port/port.h)
   and of the sound library's audio.c.
   The program's name in its messages; DGROUP's paragraph in UW2.EXE, which the pseudo-registers
   _DS and _SS start with, and where Borland's _ctype table sits in DGROUP; the termination
   chain exit runs, which seg021's init hooked; and the far data blocks of UW2.EXE that no
   source defines yet (mem/fardata.c), whose initial bytes are read from the user's own UW2.EXE
   at start-up, never shipped. The load segment and the far heap are the runtime's defaults. */
#define PORT_NAME "uw2port"
#define PORT_DGROUP_PARA 0x65E9u
#define PORT_CTYPE_AT 0x1BF6
void seg021_exit_chain(void);                   /* sys/sysentry.c */
#define PORT_EXIT_CHAIN() seg021_exit_chain()
/* the black box (sys/blackbox.c): stage/ leaves out the port's settings file */
#define PORT_BLACKBOX 1
#define BLACKBOX_SKIP "uw2port.cfg"
/* the sound library's environment variables (sound/ail.c, sound/audio.c) */
#define AIL_SNDCHECK_ENV "UW2PORT_SNDCHECK"     /* list each sound read that differs from DOS's */
#define AUDIO_ROMS_ENV "UW2PORT_MT32_ROMS"      /* the user's MT-32 or CM-32L ROMs */

extern unsigned char seg_370D[];           /* seg003's data, the graphics library's */
extern unsigned char seg052_519C[];        /* seg004's data, the 3D renderer's */
extern unsigned char dseg062_62a6[];       /* seg021's data (FD71) */
#define SEG_370D_SIZE     0x5E76
#define SEG052_519C_SIZE  0xE4D6
#define DSEG062_62A6_SIZE 0x0C40
