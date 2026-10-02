# Hand edits before the Player conversion: views that the field mapping cannot express.
import re
def ed(f, pairs, rx=False):
    p='base/'+f; s=open(p,encoding='latin1').read()
    for a,b,n in pairs:
        c=s.count(a); assert c==n, (f,a,c); s=s.replace(a,b)
    open(p,'w',encoding='latin1').write(s)
# four files declared quests[2] at 0x96, which is quests[12] of the array at 0x66
for f in ('OVR135.C','OVR157.C','OVR149.C','PLAYER.C'):
    p='base/'+f; s=open(p,encoding='latin1').read()
    s=re.sub(r'player->quests\[(\d+)\]', lambda m: 'player->quests[%d]' % (int(m.group(1)) + 12), s)
    open(p,'w',encoding='latin1').write(s)
ed('OVR110.C', [('seq = player->qbert;','seq = (int *)(player->vars + 100);',1)])
ed('OVR135.C', [('player->fatigue[i]','(&player->fatigue)[i]',2)])
