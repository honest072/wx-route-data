"""ADS-B 항적(readsb trace_full) → 노선별 대표 경로 추출.
사용: python3 -I proc.py <trace_dir> [<trace_dir> ...] > routes.json   (MODE=c 이면 합의 경로 w)
"""
import sys, os, gzip, json, math, glob, statistics

AP = {
 'ICN':(37.4602,126.4407),'GMP':(37.5583,126.7906),'CJU':(33.5113,126.4930),'PUS':(35.1795,128.9382),
 'CJJ':(36.7166,127.4991),'TAE':(35.8941,128.6586),'MWX':(34.9914,126.3828),
 'NRT':(35.7720,140.3929),'KIX':(34.4347,135.2440),'FUK':(33.5859,130.4511),'OKA':(26.1958,127.6459),
 'CTS':(42.7752,141.6923),'NGO':(34.8584,136.8054),'MYJ':(33.8272,132.6997),
 'TPE':(25.0777,121.2328),'HKG':(22.3080,113.9185),'MFM':(22.1496,113.5916),'PVG':(31.1443,121.8083),
 'BKK':(13.6900,100.7501),'DAD':(16.0439,108.1992),'HAN':(21.2212,105.8072),'SGN':(10.8188,106.6520),
 'CXR':(11.9982,109.2194),'PQC':(10.1698,103.9931),'CEB':(10.3075,123.9794),'MNL':(14.5086,121.0194),
 'CRK':(15.1859,120.5600),'BKI':(5.9372,116.0510),'GUM':(13.4834,144.7960),'SPN':(15.1190,145.7290),
 'UBN':(47.6467,106.8197),'VTE':(17.9883,102.5633)}
PAIRS = ['GMP-CJU','PUS-CJU','CJJ-CJU','TAE-CJU','MWX-CJU','ICN-NRT','ICN-KIX','ICN-NGO','ICN-FUK','ICN-MYJ',
 'ICN-CTS','ICN-OKA','PUS-NRT','PUS-KIX','PUS-FUK','ICN-TPE','ICN-HKG','ICN-MFM','ICN-PVG','ICN-BKK','ICN-DAD',
 'ICN-HAN','ICN-SGN','ICN-CXR','ICN-PQC','ICN-VTE','ICN-MNL','ICN-CRK','ICN-CEB','ICN-BKI','PUS-BKK','PUS-DAD',
 'ICN-GUM','ICN-SPN','PUS-GUM','ICN-UBN']
PSET = set(PAIRS)

def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2-la1)/2)**2 + math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 2*3440.065*math.asin(min(1, math.sqrt(h)))

def nearest_ap(p, lim):
    best = min(AP, key=lambda k: nm(p, AP[k]))
    return best if nm(p, AP[best]) <= lim else None

def load(fp):
    raw = open(fp, 'rb').read()
    try: raw = gzip.decompress(raw)
    except OSError: pass
    return json.loads(raw)

def segments(js):
    t0 = js.get('timestamp', 0); tr = js.get('trace') or []
    segs, cur, last_t = [], [], None
    for row in tr:
        t = t0 + row[0]; lat, lon, alt = row[1], row[2], row[3]
        if lat is None or lon is None: continue
        ground = alt == 'ground'
        a = 0 if ground else (alt if isinstance(alt, (int, float)) else None)
        det = row[8] if len(row) > 8 and isinstance(row[8], dict) else None
        cs = (det.get('flight') or '').strip() if det else ''
        if last_t is not None and cur:
            g = t - last_t; pa = cur[-1]['a'] or 0
            if g > 4*3600 or (g > 1800 and (pa < 12000 or (a is not None and a < 12000))):
                segs.append(cur); cur = []
        if ground:
            if cur and len(cur) > 5 and max(p['a'] or 0 for p in cur) > 5000:
                cur.append({'t': t, 'lat': lat, 'lon': lon, 'a': 0, 'cs': cs}); segs.append(cur); cur = []
            last_t = t; continue
        cur.append({'t': t, 'lat': lat, 'lon': lon, 'a': a, 'cs': cs}); last_t = t
    if cur: segs.append(cur)
    return segs

def classify(seg):
    if len(seg) < 20: return None
    s, e = (seg[0]['lat'], seg[0]['lon']), (seg[-1]['lat'], seg[-1]['lon'])
    o, d = nearest_ap(s, 80), nearest_ap(e, 80)
    if not o or not d or o == d: return None
    key = f'{o}-{d}'; rev = False
    if key not in PSET:
        key = f'{d}-{o}'; rev = True
        if key not in PSET: return None
    # 이륙 직후 호출부호가 바뀌는 경우(직전 편명이 남아 있음) — 구간에서 가장 많이 나온 호출부호 사용
    _c = {}
    for p in seg:
        if p['cs']: _c[p['cs']] = _c.get(p['cs'], 0) + 1
    cs = max(_c, key=_c.get) if _c else ''
    # 커버리지: 최대 공백(NM) 계산
    gap = max(nm((seg[i]['lat'], seg[i]['lon']), (seg[i+1]['lat'], seg[i+1]['lon'])) for i in range(len(seg)-1))
    alts = [p['a'] for p in seg if p['a']]
    return {'key': key, 'rev': rev, 'cs': cs, 'gap': gap, 'maxalt': max(alts) if alts else 0,
            'dur': (seg[-1]['t'] - seg[0]['t'])/60, 'pts': [(p['lat'], p['lon'], p['a'] or 0) for p in seg]}

def resample(pts, n=60):
    cum = [0]
    for i in range(1, len(pts)): cum.append(cum[-1] + nm(pts[i-1], pts[i]))
    tot = cum[-1] or 1; out = []; j = 0
    for k in range(n+1):
        want = tot*k/n
        while j < len(cum)-2 and cum[j+1] < want: j += 1
        f = (want-cum[j])/((cum[j+1]-cum[j]) or 1)
        out.append((pts[j][0]+(pts[j+1][0]-pts[j][0])*f, pts[j][1]+(pts[j+1][1]-pts[j][1])*f))
    return out, tot

def main(dirs):
    flights = []
    for d in dirs:
        for fp in glob.glob(os.path.join(d, '**', 'trace_full_*'), recursive=True):
            try: js = load(fp)
            except Exception: continue
            for seg in segments(js):
                c = classify(seg)
                if c: c['reg'] = js.get('r', ''); c['type'] = js.get('t', ''); flights.append(c)
    out = {}
    for key in PAIRS:
        fl = [f for f in flights if f['key'] == key]
        if not fl: continue
        tracks = []
        for f in fl:
            pts = f['pts'][::-1] if f['rev'] else f['pts']
            rs, tot = resample(pts)
            tracks.append({**f, 'rs': rs, 'tot': tot})
        # 공백이 작은 항적 우선
        good = [t for t in tracks if t['gap'] < 150] or tracks
        # medoid: 다른 항적과 평균 거리 최소
        def dsum(a): return sum(statistics.mean(nm(p, q) for p, q in zip(a['rs'], b['rs'])) for b in good)
        med = min(good, key=dsum)
        spread = statistics.mean(statistics.mean(nm(p, q) for p, q in zip(med['rs'], b['rs'])) for b in good)
        fls = sorted(round(t['maxalt']/1000)*10 for t in good if t['maxalt'] > 8000)
        out[key] = {'n': len(tracks), 'nGood': len(good), 'jja': sum(1 for t in tracks if t['cs'].startswith('JJA')),
                    'medCs': med['cs'], 'medReg': med['reg'], 'medDir': 'rev' if med['rev'] else 'fwd',
                    'gap': round(med['gap']), 'spreadNM': round(spread, 1), 'distNM': round(med['tot']),
                    'fl': [fls[0], fls[-1]] if fls else None, 'flMed': fls[len(fls)//2] if fls else None,
                    'durMed': round(statistics.median(t['dur'] for t in good)),
                    'track': [(round(a, 3), round(b, 3)) for a, b in med['rs']]}
    json.dump(out, sys.stdout, ensure_ascii=False)
    print(f'\nflights={len(flights)} routes={len(out)}', file=sys.stderr)


# ---------------- 합의(consensus) 경로: 실제 관측점만 사용 ----------------
R_NM = 3440.065
def _brg(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2-lo1)*math.cos(la2); x = math.cos(la1)*math.sin(la2)-math.sin(la1)*math.cos(la2)*math.cos(lo2-lo1)
    return math.atan2(y, x)
def _dest(p, brg, d):
    la1, lo1 = map(math.radians, p); dr = d/R_NM
    la2 = math.asin(math.sin(la1)*math.cos(dr)+math.cos(la1)*math.sin(dr)*math.cos(brg))
    lo2 = lo1+math.atan2(math.sin(brg)*math.sin(dr)*math.cos(la1), math.cos(dr)-math.sin(la1)*math.sin(la2))
    return (math.degrees(la2), math.degrees(lo2))
def _atxt(A, B, P):
    d13 = nm(A, P)/R_NM; t13 = _brg(A, P); t12 = _brg(A, B)
    xt = math.asin(max(-1, min(1, math.sin(d13)*math.sin(t13-t12))))
    at = math.acos(max(-1, min(1, math.cos(d13)/max(1e-9, math.cos(xt)))))
    if math.cos(t13-t12) < 0: at = -at
    return at*R_NM, xt*R_NM

def consensus(key, tracks, nb=40):
    o, d = key.split('-'); A, B = AP[o], AP[d]; L = nm(A, B); brg0 = _brg(A, B)
    bins = [[] for _ in range(nb)]; hits = [set() for _ in range(nb)]
    for ti, t in enumerate(tracks):
        pts = t['pts'][::-1] if t['rev'] else t['pts']
        for lat, lon, alt in pts:
            at, xt = _atxt(A, B, (lat, lon))
            f = at/L
            if f <= 0 or f >= 1: continue
            k = min(nb-1, int(f*nb)); bins[k].append(xt); hits[k].add(ti)
    xs = [statistics.median(b) if len(h) >= 1 else None for b, h in zip(bins, hits)]
    known = [i for i, v in enumerate(xs) if v is not None]
    if not known: return None
    full = []
    for i in range(nb):
        if xs[i] is not None: full.append(xs[i]); continue
        lo = max([k for k in known if k < i], default=None); hi = min([k for k in known if k > i], default=None)
        if lo is None and hi is None: full.append(0)
        elif lo is None: full.append(xs[hi]*(i+.5)/(hi+.5))
        elif hi is None: full.append(xs[lo]*(nb-i-.5)/(nb-lo-.5))
        else: full.append(xs[lo]+(xs[hi]-xs[lo])*(i-lo)/(hi-lo))
    wp = []
    for i in range(nb):
        f = (i+.5)/nb; on = _dest(A, brg0, f*L)
        # 경로 위 지점에서의 진행방위 기준 수직 오프셋
        b2 = _brg(on, B)
        p = _dest(on, b2+math.pi/2, full[i])
        wp.append((round(p[0], 3), round(p[1], 3)))
    cov = sum(1 for h in hits if len(h) >= 1)/nb
    cov2 = sum(1 for h in hits if len(h) >= 2)/nb
    return wp, cov, cov2

def main2(dirs):
    flights = []
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
            try: js = load(fp)
            except Exception: continue
            for seg in segments(js):
                c = classify(seg)
                if c: c['reg'] = js.get('r', ''); flights.append(c)
    out = {}
    for key in PAIRS:
        fl = [f for f in flights if f['key'] == key]
        if not fl: continue
        r = consensus(key, fl)
        if not r: continue
        wp, cov, cov2 = r
        fls = sorted(round(t['maxalt']/1000)*10 for t in fl if t['maxalt'] > 8000)
        q = lambda p: fls[min(len(fls)-1, int(p*len(fls)))] if fls else None
        out[key] = {'n': len(fl), 'jja': sum(1 for t in fl if t['cs'].startswith('JJA')),
                    'cov': round(cov, 2), 'cov2': round(cov2, 2),
                    'fl': [q(.2), q(.8)] if fls else None, 'flMed': q(.5), 'w': wp}
    json.dump(out, sys.stdout, ensure_ascii=False)
    print(f'\nflights={len(flights)} routes={len(out)}', file=sys.stderr)

if __name__ == '__main__':
    (main2 if os.environ.get("MODE")=="c" else main)(sys.argv[1:])
