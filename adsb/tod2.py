"""ADS-B 항적 → 비행 단계 시각 (노선·방향별 + 공항 단위 묶음)
사용: python3 -I tod2.py d1025 d1026 ...  →  pfx2.json (앱 PFX 형식), pfa2.json (공항 묶음), 요약 출력
- 노선 값: 737 우선, 5편 미만이면 전 기종 포함
- 공항 묶음: 상승(10,000ft 통과·순항 진입)은 출발 공항별, 강하(TOD·강하 10,000ft)는 도착 공항별로 모든 노선을 합침
  순항 진입·TOD는 순항고도 영향을 빼기 위해 FL350 기준으로 정규화(상승 1,500ft/분, 강하 1,200ft/분 — 앱 보정식과 동일)
"""
import sys, os, glob, json, statistics, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
from proc import nm, AP
T737 = {'B738', 'B38M', 'B737', 'B739', 'B39M', 'B37M'}
R = collections.defaultdict(lambda: collections.defaultdict(list))   # 노선
A = collections.defaultdict(lambda: collections.defaultdict(list))   # 공항
seen = set(); nseg = 0
for dd in sys.argv[1:]:
    for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
        try: js = proc.load(fp)
        except Exception: continue
        typ = js.get('t', ''); grp = '737' if typ in T737 else 'all'
        for seg in proc.segments(js):
            c = proc.classify(seg)
            if not c: continue
            o, d = (c['key'].split('-')[::-1] if c['rev'] else c['key'].split('-'))
            key = f'{o}-{d}'
            P = [(p['t'], p['lat'], p['lon'], p['a'] or 0) for p in seg]
            sid = (js.get('icao', fp), round(P[0][0] / 600))
            if sid in seen: continue
            seen.add(sid); nseg += 1
            mx = max(a for *_, a in P)
            if mx < 12000: continue
            land = next((i for i in range(len(P) - 1, -1, -1) if P[i][3] <= 2000 and nm(P[i][1:3], AP[d]) <= 15), None)
            if land is not None:
                tl = P[land][0]
                tod = next((i for i in range(land, -1, -1) if P[i][3] >= mx - 1000), None)
                ten = next((i for i in range(land, -1, -1) if P[i][3] >= 10000), None)
                if tod is not None and ten is not None and tod < ten < land and tod + 1 < len(P) and P[tod + 1][0] - P[tod][0] < 300:
                    tm, em = (tl - P[tod][0]) / 60, (tl - P[ten][0]) / 60
                    R[key]['tod' + grp].append(tm); R[key]['ten' + grp].append(em); R[key]['fl' + grp].append(mx)
                    A['arr:' + d]['tod35' + grp].append(tm - (mx - 35000) / 1200); A['arr:' + d]['ten' + grp].append(em)
            st = next((i for i in range(len(P)) if P[i][3] <= 2000 and nm(P[i][1:3], AP[o]) <= 15), None)
            if st is not None:
                t0 = P[st][0]
                up10 = next((i for i in range(st, len(P)) if P[i][3] >= 10000), None)
                toc = next((i for i in range(st, len(P)) if P[i][3] >= mx - 1000), None)
                if up10 and toc and P[up10][0] - t0 < 1800 and (up10 == st or P[up10][0] - P[up10 - 1][0] < 240):
                    cm = (P[up10][0] - t0) / 60
                    R[key]['c10' + grp].append(cm); A['dep:' + o]['c10' + grp].append(cm)
                    if P[toc][0] - P[toc - 1][0] < 300:
                        tc = (P[toc][0] - t0) / 60
                        R[key]['toc' + grp].append(tc); A['dep:' + o]['toc35' + grp].append(tc - (mx - 35000) / 1500)

import math
def q(v):
    v = sorted(v); n = len(v)
    if not n: return None
    h = 1.96 * math.sqrt(n) / 2; j = max(0, int(math.floor(n / 2 - h)) - 1); k = min(n - 1, int(math.ceil(n / 2 + h)) - 1)
    return (n, round(statistics.median(v), 1), round(v[n // 4], 1), round(v[(3 * n) // 4], 1), round(v[j], 1), round(v[k], 1))
LIM = {'c10': (1.5, 15), 'toc': (5, 45), 'toc35': (5, 45), 'tod': (10, 70), 'tod35': (10, 70), 'ten': (5, 30)}
def pick(d, m, need=5):
    lo, hi = LIM.get(m, (-1e9, 1e9)); f = lambda L: [x for x in L if lo <= x <= hi]
    v = f(d.get(m + '737', []))
    if len(v) < need: v = v + f(d.get(m + 'all', []))
    return q(v)
pfx = {}
for key, d in sorted(R.items()):
    o = {}
    t = pick(d, 'tod'); e = pick(d, 'ten'); c = pick(d, 'c10'); k = pick(d, 'toc')
    if t: o.update(tod=t[1], p25=t[2], p75=t[3], n=t[0], tci=[t[4], t[5]])
    if e: o.update(ten=e[1], e25=e[2], e75=e[3], eci=[e[4], e[5]])
    if c: o.update(c10=c[1], c25=c[2], c75=c[3], cn=c[0], cci=[c[4], c[5]])
    if k: o.update(toc=k[1], kn=k[0], kci=[k[4], k[5]])
    if o: pfx[key] = o
pfa = {}
for key, d in sorted(A.items()):
    o = {}
    if key.startswith('dep:'):
        c = pick(d, 'c10'); k = pick(d, 'toc35')
        if c: o.update(c10=c[1], c25=c[2], c75=c[3], cn=c[0], cci=[c[4], c[5]])
        if k: o.update(toc35=k[1], kn=k[0], kci=[k[4], k[5]])
    else:
        t = pick(d, 'tod35'); e = pick(d, 'ten')
        if t: o.update(tod35=t[1], p25=round(t[2] - t[1], 1), p75=round(t[3] - t[1], 1), n=t[0], tci=[round(t[4] - t[1], 1), round(t[5] - t[1], 1)])
        if e: o.update(ten=e[1], e25=e[2], e75=e[3], eci=[e[4], e[5]])
    if o: pfa[key] = o
json.dump(pfx, open('pfx2.json', 'w'), ensure_ascii=False); json.dump(pfa, open('pfa2.json', 'w'), ensure_ascii=False)
print('segments', nseg, 'routes', len(pfx), 'airports', len(pfa))
for k, o in pfx.items(): print(f"{k:8} c10 {o.get('c10','-')}(n{o.get('cn',0)}) toc {o.get('toc','-')}(n{o.get('kn',0)}) TOD {o.get('tod','-')}(n{o.get('n',0)}) ten {o.get('ten','-')}")
for k, o in pfa.items(): print(k, o)
