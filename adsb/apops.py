"""공항별 착륙 시도 결과 실측(GH Rev.11): ADS-B 항적에서 복행·홀딩(선회 대기)·회항을 찾고, 그때 METAR(돌풍·윈드시어)와 짝지음
사용: python3 -I apops.py d1115 d1116 ...   (run.sh가 호출)
입력: 같은 폴더의 trace 폴더들, wx/metar/*.json(wxlog 브랜치 보관분), prev/apev.json(이전 주 누적)
출력: apops.json(앱 표시용 요약) · apev.json(편별 사건 누적, 다음 주 합산용)
판정(보수적으로 — 애매하면 사건으로 세지 않음)
 · 복행: 목적 공항 8NM 안에서 1,500ft 아래까지 내려온 뒤, 15NM 안에서 다시 1,200ft 이상 올라가고 이후 착륙
 · 홀딩: 착륙 전 60분 안, 공항 70NM 안·4,000~20,000ft에서 15분 동안 누적 선회 330° 이상(관제 지연 유도 포함)
 · 회항: 다른 취항 공항 12NM 안·3,000ft 아래까지 접근했다가 다시 2,000ft 이상 올라가 다른 곳에 착륙
"""
import sys, os, glob, json, math, re, datetime as dt, urllib.request, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
from proc import nm, AP

ICAO = {'ICN': 'RKSI', 'GMP': 'RKSS', 'CJU': 'RKPC', 'PUS': 'RKPK', 'CJJ': 'RKTU', 'TAE': 'RKTN', 'MWX': 'RKJB', 'NRT': 'RJAA',
        'KIX': 'RJBB', 'FUK': 'RJFF', 'OKA': 'ROAH', 'CTS': 'RJCC', 'NGO': 'RJGG', 'MYJ': 'RJOM', 'TPE': 'RCTP', 'HKG': 'VHHH',
        'MFM': 'VMMC', 'PVG': 'ZSPD', 'BKK': 'VTBS', 'DAD': 'VVDN', 'HAN': 'VVNB', 'SGN': 'VVTS', 'CXR': 'VVCR', 'PQC': 'VVPQ',
        'CEB': 'RPVM', 'MNL': 'RPLL', 'CRK': 'RPLC', 'BKI': 'WBKK', 'GUM': 'PGUM', 'SPN': 'PGSN', 'UBN': 'ZMCK', 'VTE': 'VLVT'}
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP = 180


def brg(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2); x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x))


def near_ap(p, lim):
    k = min(AP, key=lambda c: nm(p, AP[c]))
    return k if nm(p, AP[k]) <= lim else None


def analyse(seg):
    """한 비행 구간 → (도착 공항, 착륙 시각, 사건 목록[(공항, 종류, 시각)])"""
    P = [(p['t'], p['lat'], p['lon'], p['a'] or 0) for p in seg if p['lat'] is not None]
    if len(P) < 30: return None
    end = (P[-1][1], P[-1][2]); d = near_ap(end, 12)
    if not d or P[-1][3] > 2500: return None
    ev = []
    D = [nm((p[1], p[2]), AP[d]) for p in P]
    # 복행
    mi = None; far = max([i for i in range(len(P)) if D[i] > 30] or [0])
    for i, p in enumerate(P):
        if i < far: continue
        if D[i] < 8 and p[3] < 1500:
            if mi is None or p[3] < P[mi][3]: mi = i
        if mi is not None and i > mi and D[i] < 15 and p[3] > P[mi][3] + 1200 and P[-1][0] - p[0] > 240:
            ev.append((d, 'ga', p[0])); break
    # 홀딩(선회 대기)
    tl = P[-1][0]; Q = [(p[0], brg((P[i - 1][1], P[i - 1][2]), (p[1], p[2]))) for i, p in enumerate(P) if i and tl - p[0] <= 3600 and D[i] < 70 and 4000 <= p[3] <= 20000 and nm((P[i - 1][1], P[i - 1][2]), (p[1], p[2])) > 0.3]
    turns = []
    for i in range(1, len(Q)):
        dh = (Q[i][1] - Q[i - 1][1] + 540) % 360 - 180
        if Q[i][0] - Q[i - 1][0] < 120: turns.append((Q[i][0], dh))
    j = 0; s = 0.0
    for i in range(len(turns)):
        s += turns[i][1]
        while turns[i][0] - turns[j][0] > 900: s -= turns[j][1]; j += 1
        if abs(s) >= 330: ev.append((d, 'hold', turns[i][0])); break
    # 회항: 다른 취항 공항에 낮게 접근했다가 다른 곳에 착륙
    for x, ll in AP.items():
        if x == d or x not in ICAO: continue
        best = None
        for i, p in enumerate(P[:-20]):
            dd = nm((p[1], p[2]), ll)
            if dd < 12 and p[3] < 3000 and (best is None or p[3] < P[best][3]): best = i
        if best is not None:
            i = best; prev = max(q[3] for q in P[max(0, i - 60):i + 1]); after = max(q[3] for q in P[i:])
            # 위에서 내려와 접근하다가(이륙 직후 제외) 다시 2,000ft 이상 올라가 다른 곳으로
            if prev > P[i][3] + 3000 and after > P[i][3] + 2000 and nm((P[0][1], P[0][2]), ll) > 15: ev.append((x, 'div', P[i][0]))
    return d, tl, ev


def metar_index():
    idx = {}
    for fp in glob.glob(os.path.join(HERE, 'wx', 'metar', '*.json')):
        try:
            for m in json.load(open(fp, encoding='utf-8')):
                ic, t, raw = m.get('icaoId'), m.get('obsTime'), m.get('rawOb') or ''
                if ic and t: idx.setdefault(ic, []).append((int(t), raw))
        except Exception: pass
    return idx


def iem_fill(idx, need, t0, t1):
    """보관분이 없는 기간은 아이오와 주립대 공개 METAR 아카이브(IEM)로 보충 — 실패하면 건너뜀"""
    a, b = dt.datetime.utcfromtimestamp(t0 - 7200), dt.datetime.utcfromtimestamp(t1 + 3600)
    for ic in sorted(need):
        have = [t for t, _ in idx.get(ic, []) if t0 - 7200 <= t <= t1]
        if len(have) > (t1 - t0) / 3600 * 0.6: continue
        u = ('https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=%s&data=metar&year1=%d&month1=%d&day1=%d&year2=%d&month2=%d&day2=%d'
             '&tz=Etc/UTC&format=onlycomma&latlon=no&missing=empty&trace=empty&report_type=3&report_type=4') % (ic, a.year, a.month, a.day, b.year, b.month, b.day)
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': '7C-Turbulence/1.0'}), timeout=90) as r:
                txt = r.read().decode('utf-8', 'ignore')
            n = 0
            for line in txt.splitlines()[1:]:
                p = line.split(',', 2)
                if len(p) < 3: continue
                try: t = int(dt.datetime.strptime(p[1], '%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc).timestamp())
                except Exception: continue
                idx.setdefault(ic, []).append((t, p[2])); n += 1
            print('IEM', ic, n, file=sys.stderr); time.sleep(1)
        except Exception as e:
            print('IEM 실패', ic, e, file=sys.stderr)


def wx_at(idx, ic, t):
    L = [x for x in idx.get(ic, []) if t - 5400 <= x[0] <= t + 600]
    if not L: return None
    t0, raw = max(L, key=lambda x: x[0])
    g = 0
    for w in re.finditer(r'\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)\b', raw.split(' RMK')[0]):
        s = int(w.group(2)); gg = int(w.group(3)) if w.group(3) else 0
        if w.group(4) == 'MPS': s, gg = round(s * 1.944), round(gg * 1.944)
        g = max(g, s, gg)
    return {'g': g, 'ws': bool(re.search(r'\bWS\s+(R\d|ALL|RWY)', raw)), 'raw': raw[:120]}


def binof(g):
    return '<25' if g < 25 else '25-34' if g < 35 else '35-44' if g < 45 else '45+'


def main(dirs):
    rec = {}; seen = set()
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
            try: js = proc.load(fp)
            except Exception: continue
            for seg in proc.segments(js):
                r = analyse(seg)
                if not r: continue
                d, tl, ev = r
                sid = f"{js.get('icao', os.path.basename(fp))}@{int(tl) // 600}"
                if sid in seen: continue
                seen.add(sid)
                # 착륙 시도 단위: 실제 착륙(d) + 회항 사건의 원래 목적지
                rec[sid + ':' + d] = {'ap': ICAO.get(d), 't': int(tl), 'ga': any(e[1] == 'ga' and e[0] == d for e in ev), 'hold': any(e[1] == 'hold' and e[0] == d for e in ev), 'div': False}
                for x, k, t in ev:
                    if k == 'div' and x in ICAO: rec[sid + ':' + x] = {'ap': ICAO[x], 't': int(t), 'ga': False, 'hold': False, 'div': True}
    old = {}
    try: old = json.load(open(os.path.join(HERE, 'prev', 'apev.json'), encoding='utf-8'))
    except Exception: pass
    old.update({k: v for k, v in rec.items() if v['ap']})
    cut = time.time() - KEEP * 86400
    ev = {k: v for k, v in old.items() if v.get('t', 0) >= cut and v.get('ap')}
    idx = metar_index()
    if ev:
        need = {v['ap'] for v in ev.values() if 'wx' not in v}
        ts = [v['t'] for v in ev.values() if 'wx' not in v]
        if ts: iem_fill(idx, need, min(ts), max(ts))
    for v in ev.values():
        if 'wx' not in v or v['wx'] is None: v['wx'] = wx_at(idx, v['ap'], v['t'])
    out = {}
    for v in ev.values():
        A = out.setdefault(v['ap'], {'arr': 0, 'ga': 0, 'hold': 0, 'div': 0, 'bins': {}, 'ws': [0, 0, 0, 0], 'nowx': 0})
        f = [1, int(v['ga']), int(v['hold']), int(v['div'])]
        for i, k in enumerate(['arr', 'ga', 'hold', 'div']): A[k] += f[i]
        w = v.get('wx')
        if not w: A['nowx'] += 1; continue
        B = A['bins'].setdefault(binof(w['g']), [0, 0, 0, 0])
        for i in range(4): B[i] += f[i]
        if w['ws']:
            for i in range(4): A['ws'][i] += f[i]
    days = len({dt.datetime.utcfromtimestamp(v['t']).strftime('%Y%m%d') for v in ev.values()})
    J = {'at': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), 'days': days, 'n': len(ev), 'ap': out,
         'note': '공개 ADS-B 항적(adsb.lol, ODbL)에서 자동 판정 · 복행/홀딩/회항은 보수적 기준 · METAR는 AWC 보관분·IEM 아카이브'}
    json.dump(J, open(os.path.join(HERE, 'apops.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    json.dump(ev, open(os.path.join(HERE, 'apev.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    tot = {k: sum(A[k] for A in out.values()) for k in ('arr', 'ga', 'hold', 'div')}
    print(f'apops: 기간 {days}일 · 착륙 시도 {tot["arr"]} · 복행 {tot["ga"]} · 홀딩 {tot["hold"]} · 회항 {tot["div"]}', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1:])
