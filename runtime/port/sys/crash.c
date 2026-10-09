/* crash.c: replaces nothing. A host fault (a bad pointer the port has not caught) prints the
   game thread's call stack before the program ends, so the port's first runs say where they
   stopped, and the black box (blackbox.c, with PORT_BLACKBOX) writes out the session's
   recording, so that a replay of it runs into the fault. Under Emscripten (the web build) there
   are no signals to catch and no backtrace(): a trap in the WebAssembly ends the program with
   the JavaScript engine's own report, and port_backtrace says it has none. */
#ifndef __EMSCRIPTEN__
#undef _POSIX_C_SOURCE
#define _DARWIN_C_SOURCE
#include <signal.h>
#include "portgame.h"
#ifndef PORT_NAME
#define PORT_NAME "port"
#endif
#include <stdio.h>
#include <stdlib.h>
#ifdef _WIN32
/* Windows has no backtrace(): a fault is reported without the call stack. */
#include <io.h>
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

static void on_fault(int sig)
{
    void *frames[64];
    int n = backtrace(frames, 64);
    static const char msg[] = PORT_NAME ": host fault; the call stack:\n";
    if (write(2, msg, sizeof msg - 1) < 0) { }
    backtrace_symbols_fd(frames, n, 2);
#ifdef PORT_BLACKBOX
    port_blackbox_close(1);             /* the session's recording, for a replay of the fault */
#endif
    signal(sig, SIG_DFL);
    raise(sig);
}

void port_crash_handlers(void)
{
    signal(SIGSEGV, on_fault);
#ifdef SIGBUS
    signal(SIGBUS, on_fault);
#endif
    signal(SIGILL, on_fault);
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
