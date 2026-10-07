/* plat_sdl3.c: replaces nothing in the DOS build. The SDL3 backend of the platform API
   (plat.h): the window and its scaling, the event loop, key and pointer events, lifecycle
   events, the high-resolution counter, threads, the audio stream, the window's icon, and the
   message box and folder picker a program shows before its window opens. The only file of the
   port that includes SDL.

   The game runs on its own thread (plat_run); this file's loop runs on the main thread, as SDL
   needs on macOS and iOS. Each pass it reads the emulated VGA's picture through the scanout
   hook, converts it through the DAC's palette and presents it, so the screen updates however
   the game spends its time, as a CRT did. */
#include <SDL3/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "plat.h"
#include "ui/settings.h"
#if defined(PLAT_ICON) && !defined(__APPLE__)
#include PLAT_ICON
#endif

void port_pause(int on);               /* the runtime's sys/pit.c */

static const PlatHooks *hooks;
static PlatConfig live;                /* the options as they stand: plat_set_display and plat_set_mouse_lock change it */
static SDL_Window *g_win;
static int locked, captured, cursor_hidden;   /* the pointer lock option's capture, mouse-look's, the host's cursor hidden */
static unsigned buttons;                       /* the buttons the game saw go down */
static float last_x, last_y;                   /* where the game's pointer last was */
static int was_open, rel_restore, scaled_up;   /* scaled_up: the window was doubled for the screen (scale 1) */
static uint8_t held[256];              /* the keys the game saw go down (a set-1 code, +128 after E0) */
static SDL_AtomicInt game_done;
static SDL_AtomicInt capture_req;      /* plat_pointer_capture's request: 0 none, 1 capture, 2 release */

void plat_pointer_capture(int on)
{
    SDL_SetAtomicInt(&capture_req, on ? 1 : 2);
}
static int game_status;

/* SDL scan codes (USB HID usages) to PC set-1 make codes. 0 means no key; a value with bit 8
   set is sent after an E0 prefix. Print Screen and Pause have sequences of their own (key()). */
#define E0(c) (0x100 | (c))
static const uint16_t set1[SDL_SCANCODE_COUNT] = {
    [SDL_SCANCODE_ESCAPE] = 0x01,
    [SDL_SCANCODE_1] = 0x02, [SDL_SCANCODE_2] = 0x03, [SDL_SCANCODE_3] = 0x04, [SDL_SCANCODE_4] = 0x05,
    [SDL_SCANCODE_5] = 0x06, [SDL_SCANCODE_6] = 0x07, [SDL_SCANCODE_7] = 0x08, [SDL_SCANCODE_8] = 0x09,
    [SDL_SCANCODE_9] = 0x0A, [SDL_SCANCODE_0] = 0x0B, [SDL_SCANCODE_MINUS] = 0x0C,
    [SDL_SCANCODE_EQUALS] = 0x0D, [SDL_SCANCODE_BACKSPACE] = 0x0E, [SDL_SCANCODE_TAB] = 0x0F,
    [SDL_SCANCODE_Q] = 0x10, [SDL_SCANCODE_W] = 0x11, [SDL_SCANCODE_E] = 0x12, [SDL_SCANCODE_R] = 0x13,
    [SDL_SCANCODE_T] = 0x14, [SDL_SCANCODE_Y] = 0x15, [SDL_SCANCODE_U] = 0x16, [SDL_SCANCODE_I] = 0x17,
    [SDL_SCANCODE_O] = 0x18, [SDL_SCANCODE_P] = 0x19, [SDL_SCANCODE_LEFTBRACKET] = 0x1A,
    [SDL_SCANCODE_RIGHTBRACKET] = 0x1B, [SDL_SCANCODE_RETURN] = 0x1C, [SDL_SCANCODE_LCTRL] = 0x1D,
    [SDL_SCANCODE_A] = 0x1E, [SDL_SCANCODE_S] = 0x1F, [SDL_SCANCODE_D] = 0x20, [SDL_SCANCODE_F] = 0x21,
    [SDL_SCANCODE_G] = 0x22, [SDL_SCANCODE_H] = 0x23, [SDL_SCANCODE_J] = 0x24, [SDL_SCANCODE_K] = 0x25,
    [SDL_SCANCODE_L] = 0x26, [SDL_SCANCODE_SEMICOLON] = 0x27, [SDL_SCANCODE_APOSTROPHE] = 0x28,
    [SDL_SCANCODE_GRAVE] = 0x29, [SDL_SCANCODE_LSHIFT] = 0x2A, [SDL_SCANCODE_BACKSLASH] = 0x2B,
    [SDL_SCANCODE_NONUSHASH] = 0x2B,
    [SDL_SCANCODE_Z] = 0x2C, [SDL_SCANCODE_X] = 0x2D, [SDL_SCANCODE_C] = 0x2E, [SDL_SCANCODE_V] = 0x2F,
    [SDL_SCANCODE_B] = 0x30, [SDL_SCANCODE_N] = 0x31, [SDL_SCANCODE_M] = 0x32, [SDL_SCANCODE_COMMA] = 0x33,
    [SDL_SCANCODE_PERIOD] = 0x34, [SDL_SCANCODE_SLASH] = 0x35, [SDL_SCANCODE_RSHIFT] = 0x36,
    [SDL_SCANCODE_KP_MULTIPLY] = 0x37, [SDL_SCANCODE_LALT] = 0x38, [SDL_SCANCODE_SPACE] = 0x39,
    [SDL_SCANCODE_CAPSLOCK] = 0x3A,
    [SDL_SCANCODE_F1] = 0x3B, [SDL_SCANCODE_F2] = 0x3C, [SDL_SCANCODE_F3] = 0x3D, [SDL_SCANCODE_F4] = 0x3E,
    [SDL_SCANCODE_F5] = 0x3F, [SDL_SCANCODE_F6] = 0x40, [SDL_SCANCODE_F7] = 0x41, [SDL_SCANCODE_F8] = 0x42,
    [SDL_SCANCODE_F9] = 0x43, [SDL_SCANCODE_F10] = 0x44, [SDL_SCANCODE_NUMLOCKCLEAR] = 0x45,
    [SDL_SCANCODE_SCROLLLOCK] = 0x46,
    [SDL_SCANCODE_KP_7] = 0x47, [SDL_SCANCODE_KP_8] = 0x48, [SDL_SCANCODE_KP_9] = 0x49,
    [SDL_SCANCODE_KP_MINUS] = 0x4A, [SDL_SCANCODE_KP_4] = 0x4B, [SDL_SCANCODE_KP_5] = 0x4C,
    [SDL_SCANCODE_KP_6] = 0x4D, [SDL_SCANCODE_KP_PLUS] = 0x4E, [SDL_SCANCODE_KP_1] = 0x4F,
    [SDL_SCANCODE_KP_2] = 0x50, [SDL_SCANCODE_KP_3] = 0x51, [SDL_SCANCODE_KP_0] = 0x52,
    [SDL_SCANCODE_KP_PERIOD] = 0x53, [SDL_SCANCODE_NONUSBACKSLASH] = 0x56,
    [SDL_SCANCODE_F11] = 0x57, [SDL_SCANCODE_F12] = 0x58,
    [SDL_SCANCODE_KP_ENTER] = E0(0x1C), [SDL_SCANCODE_RCTRL] = E0(0x1D),
    [SDL_SCANCODE_KP_DIVIDE] = E0(0x35), [SDL_SCANCODE_RALT] = E0(0x38),
    [SDL_SCANCODE_HOME] = E0(0x47), [SDL_SCANCODE_UP] = E0(0x48), [SDL_SCANCODE_PAGEUP] = E0(0x49),
    [SDL_SCANCODE_LEFT] = E0(0x4B), [SDL_SCANCODE_RIGHT] = E0(0x4D), [SDL_SCANCODE_END] = E0(0x4F),
    [SDL_SCANCODE_DOWN] = E0(0x50), [SDL_SCANCODE_PAGEDOWN] = E0(0x51), [SDL_SCANCODE_INSERT] = E0(0x52),
    [SDL_SCANCODE_DELETE] = E0(0x53), [SDL_SCANCODE_LGUI] = E0(0x5B), [SDL_SCANCODE_RGUI] = E0(0x5C),
    [SDL_SCANCODE_APPLICATION] = E0(0x5D),
};

static void key(SDL_Scancode sc, int down)
{
    uint16_t c;
    if (!hooks->key) return;
    if (sc == SDL_SCANCODE_PRINTSCREEN) {
        static const uint8_t make[] = { 0xE0, 0x2A, 0xE0, 0x37 }, brk[] = { 0xE0, 0xB7, 0xE0, 0xAA };
        const uint8_t *s = down ? make : brk;
        int i;
        for (i = 0; i < 4; i++) hooks->key(s[i]);
        return;
    }
    if (sc == SDL_SCANCODE_PAUSE) {
        static const uint8_t pause[] = { 0xE1, 0x1D, 0x45, 0xE1, 0x9D, 0xC5 };
        int i;
        if (down) for (i = 0; i < 6; i++) hooks->key(pause[i]);
        return;
    }
    if ((unsigned)sc >= SDL_SCANCODE_COUNT || !(c = set1[sc])) return;
    held[(c & 0x7F) | ((c & 0x100) >> 1)] = down != 0;
    if (c & 0x100) hooks->key(0xE0);
    hooks->key((uint8_t)((c & 0x7F) | (down ? 0 : 0x80)));
}

/* ---- the settings screen's keys (ui/settings.h) ---- */

static int set_key_of(SDL_Scancode sc)
{
    switch (sc) {
    case SDL_SCANCODE_UP: return SET_KEY_UP;
    case SDL_SCANCODE_DOWN: return SET_KEY_DOWN;
    case SDL_SCANCODE_LEFT: return SET_KEY_LEFT;
    case SDL_SCANCODE_RIGHT: return SET_KEY_RIGHT;
    case SDL_SCANCODE_TAB: return SET_KEY_TAB;
    case SDL_SCANCODE_RETURN: case SDL_SCANCODE_KP_ENTER: case SDL_SCANCODE_SPACE: return SET_KEY_ENTER;
    case SDL_SCANCODE_ESCAPE: return SET_KEY_ESC;
    default: return -1;
    }
}

/* The screen opened or closed since the last look (by F11, Escape or a click): the game's clock
   stops or goes on, and a closing screen lets go of every key the game saw down before it (their
   releases went to the screen) and gives back a pointer capture it took. */
static void sync_open(void)
{
    int o = settings_open(), i;
    if (o == was_open) return;
    was_open = o;
    if (o) {
        port_pause(1);
        if (g_win && (locked || captured)) {
            SDL_SetWindowRelativeMouseMode(g_win, false);
            rel_restore = 1;
        }
        if (g_win && cursor_hidden) { SDL_ShowCursor(); cursor_hidden = 0; }
        /* the layer is 640 wide: at scale 1 its text is too small to read in a 320 wide picture,
           so the window is doubled while the screen is open */
        if (g_win && live.scale < 2 && !(SDL_GetWindowFlags(g_win) & SDL_WINDOW_FULLSCREEN)) {
            SDL_SetWindowSize(g_win, 640, live.aspect ? 480 : 400);
            scaled_up = 1;
        }
    } else {
        port_pause(0);
        if (g_win && scaled_up) {
            scaled_up = 0;
            if (!(SDL_GetWindowFlags(g_win) & SDL_WINDOW_FULLSCREEN))
                SDL_SetWindowSize(g_win, 320 * live.scale, (live.aspect ? 240 : 200) * live.scale);
        }
        if (g_win && rel_restore) SDL_SetWindowRelativeMouseMode(g_win, true);
        rel_restore = 0;
        /* a button held when it opened was let go over the screen: the game gets the release */
        for (i = 0; i < 3; i++)
            if ((buttons & (1u << i)) && hooks->pointer) {
                PlatPointer p;
                memset(&p, 0, sizeof p);
                buttons &= ~(1u << i);
                p.type = PLAT_POINTER_UP;
                p.x = last_x; p.y = last_y;
                p.button = 1u << i;
                p.buttons = buttons;
                p.absolute = !locked && !captured;
                hooks->pointer(&p);
            }
        for (i = 0; i < 256; i++)
            if (held[i] && hooks->key) {
                if (i >= 128) hooks->key(0xE0);
                hooks->key((uint8_t)((i & 0x7F) | 0x80));
                held[i] = 0;
            }
    }
}

/* One key event of the window: F11 opens and closes the settings screen, which has every other
   key while it is open; else the game's. */
static void handle_key(SDL_Scancode sc, int down, int repeat)
{
    if (sc == SDL_SCANCODE_F11) {
        if (down && !repeat) {
            if (settings_open()) settings_key(SET_KEY_F11);
            else settings_show(1);
            sync_open();
        }
        return;
    }
    if (settings_open()) {
        int k = set_key_of(sc);
        if (down && k >= 0) settings_key(k);
        sync_open();
        return;
    }
    if (!repeat) key(sc, down);
}

static int game_thread(void *p)
{
    void **a = p;
    int (*game)(void *) = (int (*)(void *))a[0];
    int st = game(a[1]);
    game_status = st;
    SDL_SetAtomicInt(&game_done, 1);
    return st;
}

void plat_game_exit(int status)
{
    game_status = status;
    SDL_SetAtomicInt(&game_done, 1);
    for (;;) SDL_Delay(1000);
}

void plat_game_park(void)
{
    for (;;) SDL_Delay(1000);
}

uint64_t plat_counter(void) { return SDL_GetPerformanceCounter(); }
uint64_t plat_counter_hz(void) { return SDL_GetPerformanceFrequency(); }
void plat_sleep_ns(uint64_t ns) { SDL_DelayPrecise(ns); }

int plat_thread_start(const char *name, int (*fn)(void *), void *arg)
{
    SDL_Thread *t = SDL_CreateThread(fn, name, arg);
    if (!t) return -1;
    SDL_DetachThread(t);
    return 0;
}

/* The settings screen's folder picker: the dialog's answer waits here for the loop to hand it on. */
static SDL_AtomicInt pick_state;       /* 0 none, 1 a folder in pick_path, 2 cancelled */
static char pick_path[1024];

static void SDLCALL pick_cb(void *ud, const char *const *list, int filter)
{
    (void)ud; (void)filter;
    if (list && list[0] && strlen(list[0]) < sizeof pick_path) {
        strcpy(pick_path, list[0]);
        SDL_SetAtomicInt(&pick_state, 1);
    } else
        SDL_SetAtomicInt(&pick_state, 2);
}

static int start_pick(void)
{
    if (!g_win) return -1;
    SDL_SetAtomicInt(&pick_state, 0);
    SDL_ShowOpenFolderDialog(pick_cb, NULL, g_win, NULL, false);
    return 0;
}

void plat_key_byte(uint8_t b)
{
    static int e0;
    int c, sc;
    if (b == 0xE0) { e0 = 1; return; }
    c = (b & 0x7F) | (e0 ? 0x100 : 0);
    e0 = 0;
    for (sc = 0; sc < SDL_SCANCODE_COUNT; sc++)
        if (set1[sc] == c) break;
    if (sc == SDL_SCANCODE_COUNT || !hooks) {
        if (hooks && hooks->key) {          /* no such key of the window's: as it came */
            if (c & 0x100) hooks->key(0xE0);
            hooks->key(b);
        }
        return;
    }
    handle_key((SDL_Scancode)sc, !(b & 0x80), 0);
}

void plat_pointer_event(const PlatPointer *ev)
{
    PlatPointer p;
    if (!hooks || !hooks->pointer) return;
    p = *ev;
    if (settings_open()) {
        settings_pointer((int)(p.x * SET_W / 320), (int)(p.y * SET_H / 200),
                         p.type != PLAT_POINTER_UP && (p.buttons & PLAT_BUTTON_LEFT));
        sync_open();
        return;
    }
    if (p.type == PLAT_POINTER_DOWN) buttons |= p.button;
    else if (p.type == PLAT_POINTER_UP) buttons &= ~p.button;
    p.buttons = buttons;
    if (p.absolute) { last_x = p.x; last_y = p.y; }
    hooks->pointer(&p);
}

void plat_set_display(int fullscreen, int scale, int aspect, int integer_scale)
{
    live.fullscreen = fullscreen;
    live.scale = scale > 0 ? scale : 1;
    live.aspect = aspect;
    live.integer_scale = integer_scale;
    if (!g_win) return;
    SDL_SetWindowFullscreen(g_win, fullscreen != 0);
    if (!fullscreen) SDL_SetWindowSize(g_win, 320 * live.scale, (aspect ? 240 : 200) * live.scale);
}

static void mouse_title(SDL_Window *w, const PlatConfig *cfg, int locked);

void plat_set_mouse_lock(int on)
{
    live.mouse_lock = on != 0;
    if (!g_win) return;
    if (!on && locked) {
        SDL_SetWindowRelativeMouseMode(g_win, false);
        locked = 0;
        /* a screen open now would give the capture back on closing, with no lock to release it by:
           only mouse-look's own capture may still be wanted then */
        if (!captured) rel_restore = 0;
    }
    if (on) {
        SDL_SetHint(SDL_HINT_MOUSE_RELATIVE_SYSTEM_SCALE, "1");
        mouse_title(g_win, &live, locked);
    } else
        SDL_SetWindowTitle(g_win, live.title ? live.title : PLAT_TITLE);
}

static void (*audio_fill)(int16_t *, int);

static void SDLCALL audio_cb(void *ud, SDL_AudioStream *s, int additional, int total)
{
    int16_t buf[2048];
    (void)ud; (void)total;
    while (additional > 0) {
        int frames = additional / 4;
        if (frames > 512) frames = 512;
        if (frames <= 0) frames = 1;
        audio_fill(buf, frames);
        SDL_PutAudioStreamData(s, buf, frames * 4);
        additional -= frames * 4;
    }
}

int plat_audio_open(int rate, void (*fill)(int16_t *, int))
{
    SDL_AudioSpec spec;
    SDL_AudioStream *s;
    if (!SDL_WasInit(SDL_INIT_AUDIO) && !SDL_InitSubSystem(SDL_INIT_AUDIO)) return -1;
    spec.format = SDL_AUDIO_S16;
    spec.channels = 2;
    spec.freq = rate;
    audio_fill = fill;
    s = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, audio_cb, NULL);
    if (!s) return -1;
    SDL_ResumeAudioStreamDevice(s);
    return 0;
}

/* The scaled picture's place in the render output: the 4:3 shape when aspect is on, the
   largest that fits, by whole multiples when integer_scale is on, centred. */
static SDL_FRect place(SDL_Renderer *r, const PlatConfig *cfg, int w, int h)
{
    int ow, oh;
    float lw = (float)w, lh = cfg->aspect ? (float)w * 3.0f / 4.0f : (float)h, s;
    SDL_FRect d;
    SDL_GetCurrentRenderOutputSize(r, &ow, &oh);
    s = (float)ow / lw < (float)oh / lh ? (float)ow / lw : (float)oh / lh;
    if (cfg->integer_scale && s >= 1.0f) s = (float)(int)s;
    d.w = lw * s; d.h = lh * s;
    d.x = ((float)ow - d.w) / 2; d.y = ((float)oh - d.h) / 2;
    return d;
}

/* The window's icon, the project's PLAT_ICON (plat.h). Not on macOS, where the .app's own
   icon is the Dock's and SDL would put this small one in its place. */
static void set_icon(SDL_Window *w)
{
#if defined(PLAT_ICON) && !defined(__APPLE__)
    SDL_Surface *s = SDL_CreateSurfaceFrom(PLAT_ICON_W, PLAT_ICON_H, SDL_PIXELFORMAT_RGBA32,
                                           (void *)plat_icon_rgba, PLAT_ICON_W * 4);
    if (s) {
        SDL_SetWindowIcon(w, s);
        SDL_DestroySurface(s);
    }
#else
    (void)w;
#endif
}

/* With the pointer lock option, the window's title says how to capture or release it. */
static void mouse_title(SDL_Window *w, const PlatConfig *cfg, int locked)
{
    char t[256];
    snprintf(t, sizeof t, "%s - %s", cfg->title ? cfg->title : PLAT_TITLE,
             locked ? "Ctrl+F10 releases the mouse" : "click to capture the mouse");
    SDL_SetWindowTitle(w, t);
}

int plat_run(const PlatConfig *cfg0, const PlatHooks *h, int (*game)(void *), void *arg)
{
    static uint8_t pix[640 * 480];
    static uint32_t rgb[640 * 480];
    uint8_t pal[768];
    uint32_t lut[256];
    SDL_Window *win = NULL;
    SDL_Renderer *ren = NULL;
    SDL_Texture *tex = NULL, *ltex = NULL;
    static uint32_t layer[SET_W * SET_H];
    const PlatConfig *cfg = &live;
    int tw = 0, th = 0, w = 320, hgt = 200, quit = 0, shot = 0, i, scale = cfg0->scale > 0 ? cfg0->scale : 3;
    unsigned swallow = 0;
    int hidden_win = 0, vsync = 0, left_down = 0;
    Uint64 frame_ns = 0, last_present = 0, pace_from = 0;
    unsigned presents = 0, paced = 0, hidden_passes = 0;
    void *targ[2];
    SDL_FRect dst = { 0, 0, 0, 0 };
    Uint64 start;
    SDL_Event e;
    SDL_Thread *gt;

    hooks = h;
    live = *cfg0;
    settings_pick_folder = start_pick;
    if (cfg->hidden) SDL_SetHint(SDL_HINT_VIDEO_DRIVER, "offscreen");
    /* a captured pointer moves the game's cursor as fast as it moved the host's */
    if (cfg->mouse_lock) SDL_SetHint(SDL_HINT_MOUSE_RELATIVE_SYSTEM_SCALE, "1");
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS)) {
        fprintf(stderr, PLAT_NAME ": SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    if (!cfg->hidden) {
        win = SDL_CreateWindow(cfg->title ? cfg->title : PLAT_TITLE, 320 * scale,
                               (cfg->aspect ? 240 : 200) * scale, SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY | (cfg->fullscreen ? SDL_WINDOW_FULLSCREEN : 0));
        if (win) ren = SDL_CreateRenderer(win, NULL);
        if (!win || !ren) {
            fprintf(stderr, PLAT_NAME ": no window: %s\n", SDL_GetError());
            return 1;
        }
        g_win = win;
        ltex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_ABGR8888, SDL_TEXTUREACCESS_STREAMING, SET_W, SET_H);
        if (ltex) {
            SDL_SetTextureScaleMode(ltex, SDL_SCALEMODE_LINEAR);
            SDL_SetTextureBlendMode(ltex, SDL_BLENDMODE_BLEND);
        }
        vsync = SDL_SetRenderVSync(ren, 1);
        {
            /* the display's frame time, which the loop below never presents faster than */
            const SDL_DisplayMode *m = SDL_GetCurrentDisplayMode(SDL_GetDisplayForWindow(win));
            float hz = m && m->refresh_rate > 0 ? m->refresh_rate : 60.0f;
            frame_ns = (Uint64)(1e9f / hz);
            fprintf(stderr, PLAT_NAME ": renderer %s, display %.0f Hz, vsync %s\n", SDL_GetRendererName(ren), hz,
                    vsync ? "requested" : "refused");
        }
        set_icon(win);
        if (cfg->mouse_lock) mouse_title(win, cfg, 0);
    }
    targ[0] = (void *)game;
    targ[1] = arg;
    start = SDL_GetTicks();
    {
        /* the game's thread gets a large stack: host frames are bigger than DOS's */
        SDL_PropertiesID props = SDL_CreateProperties();
        SDL_SetPointerProperty(props, SDL_PROP_THREAD_CREATE_ENTRY_FUNCTION_POINTER, (void *)game_thread);
        SDL_SetStringProperty(props, SDL_PROP_THREAD_CREATE_NAME_STRING, "game");
        SDL_SetPointerProperty(props, SDL_PROP_THREAD_CREATE_USERDATA_POINTER, targ);
        SDL_SetNumberProperty(props, SDL_PROP_THREAD_CREATE_STACKSIZE_NUMBER, 16 << 20);
        gt = SDL_CreateThreadWithProperties(props);
        SDL_DestroyProperties(props);
    }
    if (!gt) {
        fprintf(stderr, PLAT_NAME ": no game thread: %s\n", SDL_GetError());
        return 1;
    }
    while (!quit) {
        {
            int c = SDL_SetAtomicInt(&capture_req, 0);
            if (c && win && !locked) {      /* the pointer lock's own capture stands */
                captured = c == 1;
                SDL_SetWindowRelativeMouseMode(win, captured);
            }
        }
        while (SDL_PollEvent(&e)) {
            PlatPointer p;
            float x, y;
            memset(&p, 0, sizeof p);
            switch (e.type) {
            case SDL_EVENT_QUIT:
                if (hooks->lifecycle) hooks->lifecycle(PLAT_QUIT_REQUEST);
                quit = 1;
                break;
            case SDL_EVENT_DROP_FILE:
                if (hooks->drop && e.drop.data) hooks->drop(e.drop.data);
                break;
            case SDL_EVENT_KEY_DOWN:
            case SDL_EVENT_KEY_UP:
                if (cfg->mouse_lock && e.key.scancode == SDL_SCANCODE_F10 && (e.key.mod & SDL_KMOD_CTRL)) {
                    /* Ctrl+F10 releases a captured pointer, as in DOSBox; the game never sees it */
                    if (e.type == SDL_EVENT_KEY_DOWN && locked) {
                        SDL_SetWindowRelativeMouseMode(win, false);
                        locked = 0;
                        mouse_title(win, cfg, 0);
                    }
                    break;
                }
                handle_key(e.key.scancode, e.type == SDL_EVENT_KEY_DOWN, e.key.repeat);
                break;
            case SDL_EVENT_MOUSE_MOTION:
            case SDL_EVENT_MOUSE_BUTTON_DOWN:
            case SDL_EVENT_MOUSE_BUTTON_UP:
                if (e.motion.which == SDL_TOUCH_MOUSEID || !hooks->pointer || !ren || dst.w <= 0) break;
                if (settings_open()) {
                    /* the screen has the pointer: its position in the layer's pixels */
                    if (e.type == SDL_EVENT_MOUSE_BUTTON_DOWN || e.type == SDL_EVENT_MOUSE_BUTTON_UP) {
                        if (e.button.button == SDL_BUTTON_LEFT) left_down = e.type == SDL_EVENT_MOUSE_BUTTON_DOWN;
                        SDL_RenderCoordinatesFromWindow(ren, e.button.x, e.button.y, &x, &y);
                    } else
                        SDL_RenderCoordinatesFromWindow(ren, e.motion.x, e.motion.y, &x, &y);
                    settings_pointer((int)((x - dst.x) * SET_W / dst.w), (int)((y - dst.y) * SET_H / dst.h), left_down);
                    sync_open();
                    break;
                }
                if (cfg->mouse_lock && !locked) {
                    /* the pointer lock option: a click captures the pointer and goes no further,
                       nor does its release; the pointer moves nothing until then */
                    if (e.type == SDL_EVENT_MOUSE_BUTTON_DOWN && SDL_SetWindowRelativeMouseMode(win, true)) {
                        locked = 1;
                        swallow |= 1u << e.button.button;
                        mouse_title(win, cfg, 1);
                    }
                    break;
                }
                if (e.type == SDL_EVENT_MOUSE_BUTTON_UP && (swallow & (1u << e.button.button))) {
                    swallow &= ~(1u << e.button.button);
                    break;
                }
                if (e.type == SDL_EVENT_MOUSE_MOTION) {
                    SDL_RenderCoordinatesFromWindow(ren, e.motion.x, e.motion.y, &x, &y);
                    p.type = PLAT_POINTER_MOVE;
                    {
                        /* window points to render pixels to screen pixels */
                        float dens = win ? SDL_GetWindowPixelDensity(win) : 1.0f;
                        p.dx = e.motion.xrel * dens * (float)w / dst.w;
                        p.dy = e.motion.yrel * dens * (float)hgt / dst.h;
                    }
                } else {
                    unsigned b = e.button.button == SDL_BUTTON_LEFT ? PLAT_BUTTON_LEFT
                               : e.button.button == SDL_BUTTON_RIGHT ? PLAT_BUTTON_RIGHT : PLAT_BUTTON_MIDDLE;
                    SDL_RenderCoordinatesFromWindow(ren, e.button.x, e.button.y, &x, &y);
                    p.type = e.type == SDL_EVENT_MOUSE_BUTTON_DOWN ? PLAT_POINTER_DOWN : PLAT_POINTER_UP;
                    p.button = b;
                    buttons = p.type == PLAT_POINTER_DOWN ? buttons | b : buttons & ~b;
                }
                p.x = (x - dst.x) * (float)w / dst.w;
                p.y = (y - dst.y) * (float)hgt / dst.h;
                p.buttons = buttons;
                p.absolute = !locked && !captured;
                last_x = p.x; last_y = p.y;
                if (!locked && !captured) {
                    /* the game's cursor stands in for the host's over the picture */
                    int over = x >= dst.x && x < dst.x + dst.w && y >= dst.y && y < dst.y + dst.h;
                    if (over != cursor_hidden) {
                        if (over) SDL_HideCursor(); else SDL_ShowCursor();
                        cursor_hidden = over;
                    }
                }
                hooks->pointer(&p);
                break;
            case SDL_EVENT_WINDOW_MOUSE_LEAVE:
                if (cursor_hidden) { SDL_ShowCursor(); cursor_hidden = 0; }
                break;
            case SDL_EVENT_FINGER_DOWN:
            case SDL_EVENT_FINGER_UP:
            case SDL_EVENT_FINGER_MOTION: {
                int ow, oh;
                if (!hooks->pointer || !ren || dst.w <= 0) break;
                SDL_GetCurrentRenderOutputSize(ren, &ow, &oh);
                x = e.tfinger.x * (float)ow; y = e.tfinger.y * (float)oh;
                if (settings_open()) {
                    settings_pointer((int)((x - dst.x) * SET_W / dst.w), (int)((y - dst.y) * SET_H / dst.h),
                                     e.type != SDL_EVENT_FINGER_UP);
                    sync_open();
                    break;
                }
                p.id = (int)(e.tfinger.fingerID & 0x7FFFFFFF) + 1;
                p.type = e.type == SDL_EVENT_FINGER_DOWN ? PLAT_POINTER_DOWN
                       : e.type == SDL_EVENT_FINGER_UP ? PLAT_POINTER_UP : PLAT_POINTER_MOVE;
                p.button = PLAT_BUTTON_LEFT;
                p.buttons = p.type == PLAT_POINTER_UP ? 0 : PLAT_BUTTON_LEFT;
                p.x = (x - dst.x) * (float)w / dst.w;
                p.y = (y - dst.y) * (float)hgt / dst.h;
                p.dx = e.tfinger.dx * (float)ow * (float)w / dst.w;
                p.dy = e.tfinger.dy * (float)oh * (float)hgt / dst.h;
                p.absolute = 1;
                hooks->pointer(&p);
                break;
            }
            case SDL_EVENT_WINDOW_OCCLUDED:
                hidden_win = 1;
                break;
            case SDL_EVENT_WINDOW_EXPOSED:
                hidden_win = 0;
                break;
            case SDL_EVENT_WILL_ENTER_BACKGROUND:
            case SDL_EVENT_WINDOW_MINIMIZED:
                if (e.type == SDL_EVENT_WINDOW_MINIMIZED) hidden_win = 1;
                if (hooks->lifecycle) hooks->lifecycle(PLAT_SUSPEND);
                break;
            case SDL_EVENT_DID_ENTER_FOREGROUND:
            case SDL_EVENT_WINDOW_RESTORED:
                if (e.type == SDL_EVENT_WINDOW_RESTORED) hidden_win = 0;
                if (hooks->lifecycle) hooks->lifecycle(PLAT_RESUME);
                break;
            default:
                break;
            }
        }
        if (hooks->tick) hooks->tick();     /* an input script's events (inscript.c) */
        sync_open();
        {
            int ps = SDL_GetAtomicInt(&pick_state);
            if (ps) {                       /* the folder picker answered (or was cancelled) */
                SDL_SetAtomicInt(&pick_state, 0);
                settings_folder_chosen(ps == 1 ? pick_path : NULL);
            }
        }
        hooks->scanout(pix, &w, &hgt, pal);
        if (!shot && cfg->screenshot_after_ms > 0 && SDL_GetTicks() - start >= (Uint64)cfg->screenshot_after_ms) {
            shot = 1;
            if (plat_write_png(cfg->screenshot_path ? cfg->screenshot_path : PLAT_NAME ".png", pix, w, hgt, pal) == 0)
                fprintf(stderr, PLAT_NAME ": wrote %s (%dx%d) at %lu ms\n", cfg->screenshot_path ? cfg->screenshot_path : PLAT_NAME ".png",
                        w, hgt, (unsigned long)(SDL_GetTicks() - start));
        }
        if (cfg->exit_after_ms > 0 && SDL_GetTicks() - start >= (Uint64)cfg->exit_after_ms) quit = 1;
        if (SDL_GetAtomicInt(&game_done)) quit = 1;
        if (ren && hidden_win) {
            /* minimised or covered: nothing to draw, and presenting would not wait */
            SDL_Delay(16);
            hidden_passes++;
        } else if (ren) {
            if (w != tw || hgt != th) {
                if (tex) SDL_DestroyTexture(tex);
                tex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_XRGB8888, SDL_TEXTUREACCESS_STREAMING, w, hgt);
                SDL_SetTextureScaleMode(tex, SDL_SCALEMODE_NEAREST);
                tw = w; th = hgt;
            }
            for (i = 0; i < 256; i++) {
                const uint8_t *c = pal + 3 * i;
                lut[i] = (uint32_t)((c[0] << 2) | (c[0] >> 4)) << 16 | (uint32_t)((c[1] << 2) | (c[1] >> 4)) << 8
                       | (uint32_t)((c[2] << 2) | (c[2] >> 4));
            }
            for (i = 0; i < w * hgt; i++) rgb[i] = lut[pix[i]];
            SDL_UpdateTexture(tex, NULL, rgb, w * 4);
            dst = place(ren, cfg, w, hgt);
            SDL_SetRenderDrawColor(ren, 0, 0, 0, 255);
            SDL_RenderClear(ren);
            SDL_RenderTexture(ren, tex, NULL, &dst);
            if (ltex && settings_draw(layer)) {
                /* the settings screen: the picture dimmed, the layer over it */
                SDL_SetRenderDrawBlendMode(ren, SDL_BLENDMODE_BLEND);
                SDL_SetRenderDrawColor(ren, 0, 0, 0, 160);
                SDL_RenderFillRect(ren, &dst);
                SDL_UpdateTexture(ltex, NULL, layer, SET_W * 4);
                SDL_RenderTexture(ren, ltex, NULL, &dst);
            }
            if (shot == 1 && cfg->window_shot_path) {
                /* the window's contents as scaled, for checking the presentation */
                SDL_Surface *s = SDL_RenderReadPixels(ren, NULL), *c = s ? SDL_ConvertSurface(s, SDL_PIXELFORMAT_RGB24) : NULL;
                if (c) {
                    uint8_t *rgbp = malloc((size_t)c->w * (size_t)c->h * 3);
                    int yy;
                    for (yy = 0; rgbp && yy < c->h; yy++)
                        memcpy(rgbp + (size_t)yy * (size_t)c->w * 3, (uint8_t *)c->pixels + (size_t)yy * (size_t)c->pitch, (size_t)c->w * 3);
                    if (rgbp && plat_write_png_rgb(cfg->window_shot_path, rgbp, c->w, c->h) == 0)
                        fprintf(stderr, PLAT_NAME ": wrote %s (%dx%d, the window)\n", cfg->window_shot_path, c->w, c->h);
                    free(rgbp);
                }
                if (c) SDL_DestroySurface(c);
                if (s) SDL_DestroySurface(s);
                shot = 2;
            }
            SDL_RenderPresent(ren);
            {
                /* vsync is only a request: a driver may ignore it, and on Windows a present to a
                   window in the background can return at once. Unpaced, this loop then uploads and
                   presents as fast as it can, which can stall the desktop's compositor and with it
                   the pointer in every program. So it never presents faster than the display
                   refreshes: when a present came back in under half a frame time, it waits out the
                   rest. Where vsync holds, a present takes about a frame and it never waits. */
                Uint64 now = SDL_GetTicksNS();
                if (last_present && now - last_present < frame_ns / 2) {
                    SDL_DelayPrecise(frame_ns - (now - last_present));
                    paced++;
                    now = SDL_GetTicksNS();
                }
                last_present = now;
                if (!pace_from) pace_from = now;
                if (++presents == 600) {
                    /* once, for bug reports: whether the display's vsync held */
                    fprintf(stderr, PLAT_NAME ": %u presents in %.1f s, %u paced (vsync %s)\n", presents,
                            (double)(now - pace_from) / 1e9, paced, paced > presents / 4 ? "not honoured" : "holds");
                }
            }
        } else {
            SDL_Delay(10);
        }
    }
    if (ren) fprintf(stderr, PLAT_NAME ": %u presents, %u paced, %u passes with the window hidden\n", presents, paced, hidden_passes);
    if (tex) SDL_DestroyTexture(tex);
    if (ltex) SDL_DestroyTexture(ltex);
    if (ren) SDL_DestroyRenderer(ren);
    if (win) SDL_DestroyWindow(win);
    SDL_Quit();
    return game_status;
}

/* Dialogs (plat.h): SDL's message box and folder picker, before plat_run. */
void plat_message(int error, const char *title, const char *text)
{
    fprintf(stderr, PLAT_NAME ": %s\n%s\n", title, text);
    SDL_ShowSimpleMessageBox(error ? SDL_MESSAGEBOX_ERROR : SDL_MESSAGEBOX_INFORMATION, title, text, NULL);
}

static struct { SDL_AtomicInt done; char path[1024]; int ok; } picked;

static void SDLCALL folder_cb(void *ud, const char * const *list, int filter)
{
    (void)ud; (void)filter;
    picked.ok = 0;
    if (list && list[0] && strlen(list[0]) < sizeof picked.path) {
        strcpy(picked.path, list[0]);
        picked.ok = 1;
    } else if (!list) {
        fprintf(stderr, PLAT_NAME ": no folder dialog: %s\n", SDL_GetError());
    }
    SDL_SetAtomicInt(&picked.done, 1);
}

int plat_choose_folder(const char *title, const char *text, char *out, size_t outsz)
{
    const SDL_MessageBoxButtonData buttons[] = {
        { SDL_MESSAGEBOX_BUTTON_ESCAPEKEY_DEFAULT, 0, "Quit" },
        { SDL_MESSAGEBOX_BUTTON_RETURNKEY_DEFAULT, 1, "Choose folder..." },
    };
    SDL_MessageBoxData box;
    int id = 0;
    fprintf(stderr, PLAT_NAME ": %s\n%s\n", title, text);
    memset(&box, 0, sizeof box);
    box.flags = SDL_MESSAGEBOX_INFORMATION;
    box.title = title;
    box.message = text;
    box.numbuttons = 2;
    box.buttons = buttons;
    if (!SDL_ShowMessageBox(&box, &id) || id != 1) return -1;
    if (!SDL_Init(SDL_INIT_VIDEO)) return -1;
    SDL_SetAtomicInt(&picked.done, 0);
    SDL_ShowOpenFolderDialog(folder_cb, NULL, NULL, NULL, false);
    while (!SDL_GetAtomicInt(&picked.done)) {
        SDL_Event e;
        SDL_WaitEventTimeout(&e, 50);
    }
    SDL_QuitSubSystem(SDL_INIT_VIDEO);
    if (!picked.ok || strlen(picked.path) >= outsz) return -1;
    strcpy(out, picked.path);
    return 0;
}

const char *plat_base_dir(void)
{
    return SDL_GetBasePath();
}
