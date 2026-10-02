import json, re, collections
b = json.load(open('strings.json'))['4']
def nm(s):
    s = s.split('&')[0]
    if '_' in s: s = s.split('_', 1)[1]
    s = re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').upper()
    return s
names = {i: nm(b[i]) for i in range(512) if b[i]}
cnt = collections.Counter(names.values())
out = {}
for i, n in names.items():
    out[i] = 'ITEM_' + n + ('_%X' % i if cnt[n] > 1 else '')
json.dump(out, open('itemnames.json', 'w'))
for i in sorted(out): print('%03X %-40s %s' % (i, out[i], b[i]))
