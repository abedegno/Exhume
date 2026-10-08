/* The settings screen (settings.h). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "settings.h"
#include "sys/enhance.h"

int port_config_get(const char *home, const char *key, char *out, size_t outsz);
int port_config_set(const char *home, const char *key, const char *value);
extern const unsigned char settings_font[256][16];   /* font.c */

int (*settings_pick_folder)(void);

#define MAX_ROWS 64
#define PATH_MAX_LEN 256
#define GLYPH_W 8
#define GLYPH_H 16

/* The games' panel colours as 0xRRGGBB, packed to 0xAABBGGRR by rgb() */
#define C_BACK   0x2A2318u
#define C_BORDER 0x6B5636u
#define C_TEXT   0xE8D9B5u
#define C_SELECT 0xF2C66Du    /* the title and the selection */
#define C_DIM    0xB9A77Fu

/* Layout, in layer pixels */
#define TITLE_X 16
#define TITLE_Y 10
#define TAB_Y 36
#define TAB_W 120
#define TAB_H 24
#define ROW_Y 72
#define ROW_H 20
#define VISIBLE 12
#define LABEL_X 24
#define VALUE_X 248
#define NOTE_X 496
#define SLIDER_W 160
#define HELP_Y 316
#define HELP_LINES 3
#define HELP_CHARS 72                   /* a line of help: clear of the scroll marker at x 612 */
#define FOOT_Y 368

/* macOS gives F11 to the desktop (Show Desktop) unless that shortcut is turned off, so the backend
   takes Cmd+, there too, and that is the key the footer names */
#ifdef __APPLE__
#define FOOTER "Cmd+, opens this at any time \xC2\xB7 Esc closes"
#else
#define FOOTER "F11 opens this at any time \xC2\xB7 Esc closes"
#endif

static const char *const tab_name[SET_TABS] = { "Sound", "Controls", "Display", "Enhancements", "Game" };

static const char *home_dir = "";
static const char *title_text = "";
static struct setting rows[MAX_ROWS];     /* the table, then the enhancement rows */
static char help[MAX_ROWS][200];          /* an enhancement's about line and its credit ("" for none) */
static const char *mark[MAX_ROWS];        /* the note column's text when not changed: "changes play" */
static int enh_bit[MAX_ROWS];             /* the enhancement flag of a row, else -1 */
static int value[MAX_ROWS];
static char path[MAX_ROWS][PATH_MAX_LEN]; /* SET_FOLDER rows' text */
static char changed[MAX_ROWS];            /* a restart row whose value now differs from the one the run started with */
static int start_value[MAX_ROWS];         /* each row's value when the run started */
static char start_path[MAX_ROWS][PATH_MAX_LEN];   /* and a folder row's text */
static int nrows;
static uint32_t pending_enh;
static int cur_tab;
static int sel = -1;                      /* index within the tab; -1 is the tab bar */
static int top;                           /* first visible index within the tab */
static int shown;
static int pick_row = -1;                 /* the folder row waiting for the picker */
static int drag_row = -1;                 /* the slider being dragged */
static int was_down;
static char notice[96];                   /* a line under the rows: why a folder was refused */

static uint32_t rgb(uint32_t c)
{
    return 0xFF000000u | (c & 0xFFu) << 16 | (c & 0xFF00u) | (c >> 16 & 0xFFu);
}

/* ---- the model ---- */

static int count_names(const struct setting *s)
{
    int n = 0;
    while (s->names && s->names[n]) n++;
    return n;
}

static int clamp(int v, int lo, int hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

static int parse_int(const char *text, int *out)
{
    char *end;
    long v = strtol(text, &end, 10);
    if (end == text || *end) return 0;
    *out = (int)v;
    return 1;
}

static int load_value(int r)
{
    const struct setting *s = &rows[r];
    char text[PATH_MAX_LEN];
    int v, i;
    if (enh_bit[r] >= 0) return (int)(pending_enh >> enh_bit[r] & 1u);
    if (s->get) {
        v = s->get();
        switch (s->kind) {
        case SET_CYCLE: return v >= 0 && v < count_names(s) ? v : s->def;
        case SET_BOOL:  return v == 0 || v == 1 ? v : s->def;
        default:        return clamp(v, s->lo, s->hi);
        }
    }
    if (!s->key || port_config_get(home_dir, s->key, text, sizeof text) != 0) return s->def;
    switch (s->kind) {
    case SET_CYCLE:
        if (s->stored) {
            for (i = 0; s->stored[i] && i < count_names(s); i++)
                if (!strcmp(s->stored[i], text)) return i;
            return s->def;
        }
        return parse_int(text, &v) && v >= 0 && v < count_names(s) ? v : s->def;
    case SET_BOOL:
        return parse_int(text, &v) && (v == 0 || v == 1) ? v : s->def;
    case SET_SLIDER:
        return parse_int(text, &v) ? clamp(v, s->lo, s->hi) : s->def;
    default:
        snprintf(path[r], sizeof path[r], "%s", text);
        return s->def;
    }
}

static void save(int r)
{
    const struct setting *s = &rows[r];
    char text[PATH_MAX_LEN];
    if (enh_bit[r] >= 0) {
        enhance_save(home_dir, pending_enh);
        return;
    }
    if (s->put) {
        s->put(value[r]);
        return;
    }
    if (!s->key) return;
    switch (s->kind) {
    case SET_CYCLE:
        if (s->stored) snprintf(text, sizeof text, "%s", s->stored[value[r]]);
        else snprintf(text, sizeof text, "%d", value[r]);
        break;
    case SET_FOLDER:
        snprintf(text, sizeof text, "%s", path[r]);
        break;
    default:
        snprintf(text, sizeof text, "%d", value[r]);
    }
    port_config_set(home_dir, s->key, text);
}

/* After a row's value is set: write it, tell the game, note a restart. */
static void commit(int r)
{
    if (enh_bit[r] >= 0) {
        if (value[r]) pending_enh |= 1u << enh_bit[r];
        else pending_enh &= ~(1u << enh_bit[r]);
    }
    save(r);
    if (rows[r].apply) rows[r].apply(value[r]);
    if (rows[r].restart) changed[r] = value[r] != start_value[r] || strcmp(path[r], start_path[r]) != 0;
}

static void change(int r, int dir)
{
    struct setting *s = &rows[r];
    int n, step;
    switch (s->kind) {
    case SET_CYCLE:
        n = count_names(s);
        if (n < 1) return;
        value[r] = ((value[r] + dir) % n + n) % n;
        break;
    case SET_BOOL:
        value[r] = !value[r];
        break;
    case SET_SLIDER:
        step = s->step < 1 ? 1 : s->step;
        n = clamp(value[r] + dir * step, s->lo, s->hi);
        if (n == value[r]) return;          /* at an end already: nothing to write or apply */
        value[r] = n;
        break;
    default:                            /* SET_FOLDER: the answer comes through settings_folder_chosen */
        if (getenv("PORT_FOLDER_ANSWER")) {     /* a test's answer in place of the dialog */
            pick_row = r;
            settings_folder_chosen(getenv("PORT_FOLDER_ANSWER"));
        } else if (settings_pick_folder) {
            pick_row = r;
            settings_pick_folder();
        }
        return;
    }
    commit(r);
}

void settings_folder_chosen(const char *p)
{
    int r = pick_row;
    pick_row = -1;
    if (r < 0 || !p) return;
    if (rows[r].check && rows[r].check(p) != 0) {
        snprintf(notice, sizeof notice, "%s", rows[r].refuse ? rows[r].refuse : "That folder is not right for this");
        fprintf(stderr, "settings: %s refused: %s (%s)\n", rows[r].label, p, notice);
        return;
    }
    notice[0] = 0;
    fprintf(stderr, "settings: %s accepted: %s\n", rows[r].label, p);
    snprintf(path[r], sizeof path[r], "%s", p);
    commit(r);
}

void settings_init(const char *home, const struct setting *table, int n, const char *title)
{
    int i;
    home_dir = home ? home : "";
    title_text = title ? title : "";
    nrows = 0;
    pending_enh = 0;
    memset(changed, 0, sizeof changed);
    memset(path, 0, sizeof path);
    for (i = 0; i < n && nrows < MAX_ROWS; i++) {
        rows[nrows] = table[i];
        help[nrows][0] = 0;
        mark[nrows] = NULL;
        enh_bit[nrows++] = -1;
    }
    enhance_load(home_dir, &pending_enh);
    for (i = 0; i < enhance_count() && nrows < MAX_ROWS; i++) {
        const struct enhance_flag *f = enhance_flag(i);
        if (f->only) continue;
        memset(&rows[nrows], 0, sizeof rows[nrows]);
        rows[nrows].tab = SET_TAB_ENHANCE;
        rows[nrows].label = f->name;
        rows[nrows].kind = SET_BOOL;
        rows[nrows].restart = 1;
        if (f->source) snprintf(help[nrows], sizeof help[nrows], "%s (from %s)", f->about, f->source);
        else snprintf(help[nrows], sizeof help[nrows], "%s", f->about);
        mark[nrows] = f->kind == ENH_GAMEPLAY ? "changes play" : NULL;
        enh_bit[nrows++] = i;
    }
    for (i = 0; i < nrows; i++) value[i] = load_value(i);
    memcpy(start_value, value, sizeof start_value);
    memcpy(start_path, path, sizeof start_path);
    notice[0] = 0;
    cur_tab = SET_TAB_SOUND;
    sel = 0;
    top = 0;
    shown = 0;
    pick_row = drag_row = -1;
    was_down = 0;
}

int settings_open(void) { return shown; }
void settings_show(int on) { shown = on != 0; drag_row = -1; }
int settings_value(int row) { return row >= 0 && row < nrows ? value[row] : 0; }
void settings_set_value(int row, int v)
{
    if (row >= 0 && row < nrows) value[row] = v;
}

const char *settings_note(int row)
{
    if (row < 0 || row >= nrows) return NULL;
    return changed[row] ? "Restart to apply" : mark[row];
}

const char *settings_help(int row)
{
    return row >= 0 && row < nrows && help[row][0] ? help[row] : NULL;
}

/* ---- the tabs and their rows ---- */

static int tab_count(int tab)
{
    int i, n = 0;
    for (i = 0; i < nrows; i++) n += rows[i].tab == tab;
    return n;
}

/* The K-th row of TAB, as an index into rows, or -1. */
static int tab_row(int tab, int k)
{
    int i;
    for (i = 0; i < nrows; i++)
        if (rows[i].tab == tab && k-- == 0) return i;
    return -1;
}

static void scroll_to_sel(void)
{
    if (sel < 0) return;
    if (sel < top) top = sel;
    if (sel >= top + VISIBLE) top = sel - VISIBLE + 1;
}

/* To tab T; on its tab bar (sel -1) when BAR, else on its first row. Left and Right on the bar
   keep to the bar, so that Right, Right walks the tabs instead of changing the first row found. */
static void set_tab(int t, int bar)
{
    cur_tab = (t % SET_TABS + SET_TABS) % SET_TABS;
    sel = !bar && tab_count(cur_tab) ? 0 : -1;
    top = 0;
}

/* ---- input ---- */

void settings_key(int key)
{
    int n = tab_count(cur_tab);
    int r = sel >= 0 ? tab_row(cur_tab, sel) : -1;
    if (!shown) return;
    notice[0] = 0;
    switch (key) {
    case SET_KEY_UP:
        sel = sel < 0 ? n - 1 : sel - 1;      /* the tab bar is one stop in the wrap */
        break;
    case SET_KEY_DOWN:
        sel = sel + 1 >= n ? -1 : sel + 1;
        break;
    case SET_KEY_LEFT:
        if (sel < 0) set_tab(cur_tab - 1, 1);
        else change(r, -1);
        break;
    case SET_KEY_RIGHT:
        if (sel < 0) set_tab(cur_tab + 1, 1);
        else change(r, 1);
        break;
    case SET_KEY_TAB:
        set_tab(cur_tab + 1, 0);
        break;
    case SET_KEY_ENTER:
        if (r >= 0) change(r, 1);
        break;
    case SET_KEY_ESC:
    case SET_KEY_F11:
        shown = 0;
        break;
    }
    scroll_to_sel();
}

static void set_slider_from_x(int r, int x)
{
    const struct setting *s = &rows[r];
    int step = s->step < 1 ? 1 : s->step;
    int span = s->hi - s->lo;
    int v = s->lo + ((x - VALUE_X) * span + SLIDER_W / 2) / SLIDER_W;
    v = s->lo + (v - s->lo + step / 2) / step * step;
    v = clamp(v, s->lo, s->hi);
    if (v == value[r]) return;              /* a drag within one value: nothing to write or apply */
    value[r] = v;
    commit(r);
}

void settings_pointer(int x, int y, int down)
{
    int k, r;
    int press = down && !was_down;
    was_down = down;
    if (!shown) return;
    if (press) notice[0] = 0;
    if (!down) {
        drag_row = -1;
        return;
    }
    if (!press) {
        if (drag_row >= 0) set_slider_from_x(drag_row, x);
        return;
    }
    if (y >= TAB_Y && y < TAB_Y + TAB_H && x >= TITLE_X && x < TITLE_X + TAB_W * SET_TABS) {
        set_tab((x - TITLE_X) / TAB_W, 0);
        return;
    }
    if (y < ROW_Y || y >= ROW_Y + ROW_H * VISIBLE) return;
    k = top + (y - ROW_Y) / ROW_H;
    r = tab_row(cur_tab, k);
    if (r < 0) return;
    sel = k;
    if (rows[r].kind == SET_SLIDER) {
        if (x >= VALUE_X - 4 && x < VALUE_X + SLIDER_W + 4) {
            drag_row = r;
            set_slider_from_x(r, x);
        }
    } else {
        change(r, 1);
    }
}

/* ---- drawing ---- */

static void fill(uint32_t *px, int x, int y, int w, int h, uint32_t c)
{
    int i, j;
    for (j = y; j < y + h; j++)
        for (i = x; i < x + w; i++)
            if (i >= 0 && i < SET_W && j >= 0 && j < SET_H) px[j * SET_W + i] = c;
}

static void glyph(uint32_t *px, int x, int y, unsigned char ch, uint32_t c)
{
    int i, j;
    for (j = 0; j < GLYPH_H; j++)
        for (i = 0; i < GLYPH_W; i++)
            if (settings_font[ch][j] >> (7 - i) & 1 && x + i >= 0 && x + i < SET_W && y + j >= 0 && y + j < SET_H)
                px[(y + j) * SET_W + x + i] = c;
}

/* Draws S (at most MAX characters when MAX >= 0) and returns the pixels used. A UTF-8 middle dot
   is drawn as a raised full stop; the font has no other glyphs outside ASCII. */
static int text(uint32_t *px, int x, int y, uint32_t c, const char *s, int max)
{
    int x0 = x;
    for (; *s && max != 0; s++, max--) {
        unsigned char ch = (unsigned char)*s;
        if (ch == 0xC2 && (unsigned char)s[1] == 0xB7) {
            glyph(px, x, y - 5, '.', c);
            s++;
        } else {
            glyph(px, x, y, ch, c);
        }
        x += GLYPH_W;
    }
    return x - x0;
}

/* The help line, wrapped at spaces into HELP_LINES lines of HELP_CHARS. */
static void help_text(uint32_t *px, const char *s)
{
    int line, len, cut;
    for (line = 0; line < HELP_LINES && *s; line++) {
        len = (int)strlen(s);
        cut = len;
        if (len > HELP_CHARS) {
            for (cut = HELP_CHARS; cut > 0 && s[cut] != ' '; cut--) {}
            if (cut == 0) cut = HELP_CHARS;
        }
        text(px, LABEL_X, HELP_Y + line * GLYPH_H, rgb(C_DIM), s, cut);
        s += cut;
        while (*s == ' ') s++;
    }
}

static void value_text(const struct setting *s, int r, char *out, size_t n)
{
    size_t len;
    switch (s->kind) {
    case SET_CYCLE:
        snprintf(out, n, "< %s >", value[r] < count_names(s) ? s->names[value[r]] : "?");
        break;
    case SET_BOOL:
        snprintf(out, n, "%s", value[r] ? "[x]" : "[ ]");
        break;
    case SET_SLIDER:
        snprintf(out, n, "%d", value[r]);
        break;
    default:
        if (s->show) {                      /* the row's own word for its folder ("found", "not found") */
            snprintf(out, n, "%s", s->show(path[r]));
            break;
        }
        len = strlen(path[r]);
        snprintf(out, n, "%s", len > 30 ? path[r] + len - 30 : path[r]);
    }
}

int settings_draw(uint32_t *px)
{
    char buf[PATH_MAX_LEN];
    int t, k, r, y, w, n;
    if (!shown) return 0;
    fill(px, 0, 0, SET_W, SET_H, rgb(C_BACK));
    fill(px, 0, 0, SET_W, 2, rgb(C_BORDER));
    fill(px, 0, SET_H - 2, SET_W, 2, rgb(C_BORDER));
    fill(px, 0, 0, 2, SET_H, rgb(C_BORDER));
    fill(px, SET_W - 2, 0, 2, SET_H, rgb(C_BORDER));
    text(px, TITLE_X, TITLE_Y, rgb(C_SELECT), title_text, -1);

    for (t = 0; t < SET_TABS; t++) {
        int x = TITLE_X + t * TAB_W;
        int len = (int)strlen(tab_name[t]) * GLYPH_W;
        if (t == cur_tab && sel < 0) {
            fill(px, x, TAB_Y, TAB_W - 4, TAB_H, rgb(C_SELECT));
            text(px, x + (TAB_W - 4 - len) / 2, TAB_Y + 4, rgb(C_BACK), tab_name[t], -1);
        } else {
            text(px, x + (TAB_W - 4 - len) / 2, TAB_Y + 4, rgb(t == cur_tab ? C_SELECT : C_DIM), tab_name[t], -1);
            if (t == cur_tab) fill(px, x, TAB_Y + TAB_H - 2, TAB_W - 4, 2, rgb(C_SELECT));
        }
    }

    n = tab_count(cur_tab);
    for (k = top; k < n && k < top + VISIBLE; k++) {
        const struct setting *s;
        uint32_t fg;
        r = tab_row(cur_tab, k);
        s = &rows[r];
        y = ROW_Y + (k - top) * ROW_H;
        if (k == sel) {
            fill(px, 12, y, SET_W - 24, ROW_H - 2, rgb(C_SELECT));
            fg = rgb(C_BACK);
        } else {
            fg = rgb(C_TEXT);
        }
        w = text(px, LABEL_X, y + 1, fg, s->label, -1);
        for (w += LABEL_X + GLYPH_W; w < VALUE_X - GLYPH_W; w += GLYPH_W)
            text(px, w, y + 1, k == sel ? fg : rgb(C_DIM), ".", -1);
        if (s->kind == SET_SLIDER) {
            int span = s->hi - s->lo, filled = span > 0 ? (value[r] - s->lo) * SLIDER_W / span : 0;
            if (filled > SLIDER_W - 2) filled = SLIDER_W - 2;
            fill(px, VALUE_X, y + 6, SLIDER_W, 6, k == sel ? rgb(C_BACK) : rgb(C_DIM));
            fill(px, VALUE_X + 1, y + 7, SLIDER_W - 2, 4, k == sel ? rgb(C_SELECT) : rgb(C_BACK));
            fill(px, VALUE_X + 1, y + 7, filled, 4, fg);
        }
        value_text(s, r, buf, sizeof buf);
        text(px, s->kind == SET_SLIDER ? VALUE_X + SLIDER_W + GLYPH_W : VALUE_X, y + 1, fg, buf, -1);
        if (settings_note(r)) text(px, NOTE_X, y + 1, fg == rgb(C_BACK) ? fg : rgb(C_DIM), settings_note(r), 18);
        if (k == sel && help[r][0]) help_text(px, help[r]);
    }
    if (top > 0) text(px, SET_W - 28, ROW_Y - 18, rgb(C_DIM), "^", -1);
    if (top + VISIBLE < n) text(px, SET_W - 28, ROW_Y + ROW_H * VISIBLE, rgb(C_DIM), "v", -1);
    if (notice[0]) text(px, LABEL_X, FOOT_Y - 24, rgb(C_SELECT), notice, 76);
    text(px, LABEL_X, FOOT_Y, rgb(C_DIM), FOOTER, -1);
    return 1;
}
