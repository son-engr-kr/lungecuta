import struct, sys
def s15(b): return struct.unpack('>i', b)[0] / 65536.0
for path in sys.argv[1:]:
    d = open(path, 'rb').read()
    n = struct.unpack('>I', d[128:132])[0]
    tags = {}
    for i in range(n):
        sig, off, size = struct.unpack('>4sII', d[132 + 12*i: 144 + 12*i])
        tags[sig.decode('latin1')] = (off, size)
    print('==', path.split('/')[-1], 'version', d[8:12].hex(), 'class', d[12:16], 'tags', sorted(tags))
    for t in ('desc',):
        off, size = tags[t]; print(' desc raw:', d[off:off+size][:120])
    for t in ('rTRC', 'gTRC', 'bTRC'):
        if t not in tags: continue
        off, size = tags[t]
        typ = d[off:off+4]
        if typ == b'para':
            ft = struct.unpack('>H', d[off+8:off+10])[0]
            nparams = {0:1,1:3,2:4,3:5,4:7}[ft]
            ps = [s15(d[off+12+4*k: off+16+4*k]) for k in range(nparams)]
            print(' ', t, 'para type', ft, 'params g,a,b,c,d,e,f =', [round(p, 6) for p in ps])
        elif typ == b'curv':
            cnt = struct.unpack('>I', d[off+8:off+12])[0]
            if cnt == 1:
                print(' ', t, 'curv gamma', struct.unpack('>H', d[off+12:off+14])[0] / 256.0)
            else:
                print(' ', t, 'curv table', cnt)
    for t in ('rXYZ','gXYZ','bXYZ','wtpt','chad'):
        if t in tags:
            off, size = tags[t]
            vals = [s15(d[off+8+4*k: off+12+4*k]) for k in range((size-8)//4)]
            print(' ', t, [round(v, 5) for v in vals])
    if 'cicp' in tags:
        off, size = tags['cicp']; print('  cicp', list(d[off+8:off+12]))
