/* crash.c: replaces nothing. A host fault (a bad pointer the port has not caught) prints the
   game thread's call stack before the program ends, so the port's first runs say where they
   stopped, and the black box (blackbox.c, with PORT_BLACKBOX) writes out the session's
   recording, so that a replay of it runs into the fault. Under Emscripten (the web build) there
   are no signals to catch and no backtrace(): a trap in the WebAssembly ends the program with
   the JavaScript engine's own report, and port_backtrace says it has none.

   The game runs on a thread of its own (the platform layer's game thread), and its faults are the
   ones that matter. POSIX signal handlers are the process's, but the Windows C runtime keeps
   those of SIGSEGV and SIGILL per thread: installed by main alone, they never saw a fault on the
   game's thread, and the recordings of Underworld Exhumed's UW1 1.0.1 crashes on Windows were
   cut short (no MISC chunk, the streams' last chunks lost, the call count 0; issue 8). So
   port_crash_handlers is called on the game's thread too (plat_sdl3.c's game_thread), and on
   Windows it also sets a top-level exception filter, which is the process's and sees an
   exception no thread handled, whichever thread it was on (and then hands it to the filter set
   before, the C runtime's). The report is written once, however many of these see the fault, and
   a second thread that faults waits for it (report). */
#ifndef __EMSCRIPTEN__
#undef _POSIX_C_SOURCE
#define _DARWIN_C_SOURCE
#include <signal.h>
#include "portgame.h"
#ifndef PORT_NAME
#define PORT_NAME "port"
#endif
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#ifdef _WIN32
/* Windows has no backtrace(): a fault is reported without the call stack. */
#include <io.h>
#include <windows.h>
#define backtrace(frames, n) ((void)(frames), (void)(n), 0)
#define backtrace_symbols_fd(frames, n, fd) \
    ((void)(frames), (void)(n), (void)!write(fd, "(no call stack on this host)\n", 29))
#else
#include <execinfo.h>
#include <unistd.h>
#endif

#ifdef PORT_BLACKBOX
void port_blackbox_close(int crashed);  /* blackbox.c */
#endif

/* The report, once: the call stack and the black box's recording. More than one thread can fault
   at once (or Windows' filter and a thread's handler see the same fault), so the first to claim it
   writes it, atomically, and any other thread waits until it is written: had it gone on and ended
   the program (on_fault's raise, the C runtime's filter), the recording would be cut short. A
   fault inside the report itself, on the thread writing it, returns at once, since that thread
   will never finish it. The wait is bounded (about 30 s), so a report that hangs cannot hold the
   program open for ever. */
static atomic_int claimed, written;
static _Thread_local int writing;

static void pause_ms(int ms)
{
#ifdef _WIN32
    Sleep((DWORD)ms);
#else
    struct timespec t = { 0, ms * 1000000L };
    nanosleep(&t, NULL);
#endif
}

static void report(void)
{
    void *frames[64];
    int n, waited;
    static const char msg[] = PORT_NAME ": host fault; the call stack:\n";
    if (writing || atomic_load(&written)) return;
    if (atomic_exchange(&claimed, 1)) {
        for (waited = 0; !atomic_load(&written) && waited < 30000; waited += 10) pause_ms(10);
        return;
    }
    writing = 1;
    n = backtrace(frames, 64);
    if (write(2, msg, sizeof msg - 1) < 0) { }
    backtrace_symbols_fd(frames, n, 2);
#ifdef PORT_BLACKBOX
    port_blackbox_close(1);             /* the session's recording, for a replay of the fault */
#endif
    atomic_store(&written, 1);
}

static void on_fault(int sig)
{
    report();
    signal(sig, SIG_DFL);
    raise(sig);
}

#ifdef _WIN32
static LPTOP_LEVEL_EXCEPTION_FILTER prev_filter;

/* An exception no thread handled, on any thread: the faults on_fault takes, reported, then the
   filter set before (the C runtime's, which ends the program). Not an integer divide by zero:
   that is DOS's divide overflow, which the project's sys/int0trap.c takes (its SIGFPE handler)
   and turns into the game's INT 0, as DOS does; it is not a host fault. */
static LONG WINAPI on_exception(EXCEPTION_POINTERS *x)
{
    switch (x->ExceptionRecord->ExceptionCode) {
    case EXCEPTION_ACCESS_VIOLATION: case EXCEPTION_IN_PAGE_ERROR: case EXCEPTION_ILLEGAL_INSTRUCTION:
    case EXCEPTION_PRIV_INSTRUCTION: case EXCEPTION_STACK_OVERFLOW: case EXCEPTION_DATATYPE_MISALIGNMENT:
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
        report();
        break;
    default:
        break;
    }
    return prev_filter ? prev_filter(x) : EXCEPTION_CONTINUE_SEARCH;
}
#endif

/* Called by main and again by the game's thread as it starts (plat_sdl3.c): the signal handlers
   on this thread (on Windows they are the thread's own) and, once, Windows' top-level filter */
void port_crash_handlers(void)
{
    signal(SIGSEGV, on_fault);
#ifdef SIGBUS
    signal(SIGBUS, on_fault);
#endif
    signal(SIGILL, on_fault);
#ifdef _WIN32
    {
        static int filtered;
        if (!filtered) {
            filtered = 1;
            prev_filter = SetUnhandledExceptionFilter(on_exception);
        }
    }
#endif
}

/* The call stack, for port_halt when tracing. */
void port_backtrace(void)
{
    void *frames[32];
    int n = backtrace(frames, 32);
    backtrace_symbols_fd(frames, n, 2);
}

#else   /* __EMSCRIPTEN__ */
#include <unistd.h>

void port_crash_handlers(void) {}

void port_backtrace(void)
{
    static const char msg[] = "(no call stack on this host)\n";
    if (write(2, msg, sizeof msg - 1) < 0) { }
}
#endif
