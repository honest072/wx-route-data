"""7C Turbulence 공항 기상 보관(GH Rev.11)
매시간 취항 공항 METAR·TAF·국제 SIGMET을 미국 항공기상센터(AWC) 공개 API에서 받아 날짜별로 누적 보관하고,
'착륙·이륙이 어려웠을 수 있는 날'(돌풍 35kt 이상·윈드시어 보고·뇌우 관측·공항 주변 뇌우/난류 SIGMET)을 사례 후보로 자동 기록.
출력(작업 폴더 = wxlog 브랜치 내용): metar/YYYYMMDD.json · taf/YYYYMMDD.json · sig/YYYYMMDD.json · cases.json
· 하루 파일은 UTC 날짜 기준, 같은 원문은 한 번만 저장 · 90일 지난 파일은 정리
"""
import json, os, sys, re, math, time, datetime as dt, urllib.request

ICAO = {'ICN': 'RKSI', 'GMP': 'RKSS', 'CJU': 'RKPC', 'PUS': 'RKPK', 'CJJ': 'RKTU', 'TAE': 'RKTN', 'MWX': 'RKJB', 'NRT': 'RJAA',
        'KIX': 'RJBB', 'FUK': 'RJFF', 'OKA': 'ROAH', 'CTS': 'RJCC', 'NGO': 'RJGG', 'MYJ': 'RJOM', 'TPE': 'RCTP', 'HKG': 'VHHH',
        'MFM': 'VMMC', 'PVG': 'ZSPD', 'BKK': 'VTBS', 'DAD': 'VVDN', 'HAN': 'VVNB', 'SGN': 'VVTS', 'CXR': 'VVCR', 'PQC': 'VVPQ',
        'CEB': 'RPVM', 'MNL': 'RPLL', 'CRK': 'RPLC', 'BKI': 'WBKK', 'GUM': 'PGUM', 'SPN': 'PGSN', 'UBN': 'ZMCK', 'VTE': 'VLVT'}
AWC = 'https://aviationweather.gov/api/data'
KEEP_DAYS = 90
UA = {'User-Agent': '7C-Turbulence-wxlog/1.0 (GitHub Actions; cabin safety reference)'}


def get(path):
    for i in range(3):
        try:
            req = urllib.request.Request(AWC + path, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return json.loads(b) if b.strip() else []
        except Exception as e:
            print('retry', path, e, file=sys.stderr); time.sleep(5 * (i + 1))
    return None


def day(ts):
    return dt.datetime.utcfromtimestamp(ts).strftime('%Y%m%d')


def load(p):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return []


def merge(folder, items, key, tkey):
    os.makedirs(folder, exist_ok=True); n = 0
    by = {}
    for it in items:
        ts = it.get(tkey)
        if isinstance(ts, str):
            try: ts = int(dt.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp())
            except Exception: ts = None
        if not ts: ts = int(time.time())
        by.setdefault(day(int(ts)), []).append(it)
    for d, L in by.items():
        p = os.path.join(folder, d + '.json'); old = load(p); seen = {key(x) for x in old}
        for x in L:
            k = key(x)
            if k and k not in seen: old.append(x); seen.add(k); n += 1
        json.dump(old, open(p, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return n


def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 3440.065 * math.asin(min(1, math.sqrt(h)))


def in_poly(la, lo, P):
    c = False
    for i in range(len(P)):
        a, b = P[i], P[i - 1]
        if (a[1] > lo) != (b[1] > lo):
            x = a[0] + (lo - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if la < x: c = not c
    return c


def flags(metars, sigs, apll):
    """사례 후보: 공항·날짜별 최고 돌풍, 윈드시어·뇌우 관측, 공항 반경 50NM SIGMET(뇌우·난류)"""
    out = {}
    for m in metars or []:
        raw = str(m.get('rawOb') or ''); ic = m.get('icaoId'); ts = m.get('obsTime')
        if not ic or not ts: continue
        w = re.search(r'\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)\b', raw)
        g = 0
        if w:
            s = int(w.group(2)); gg = int(w.group(3)) if w.group(3) else 0
            if w.group(4) == 'MPS': s, gg = round(s * 1.944), round(gg * 1.944)
            g = max(s, gg)
        ws = bool(re.search(r'\bWS\s+(R\d|ALL|RWY)', raw)); ts_ = bool(re.search(r'(?<![A-Z])(\+|-)?TS', raw.split(' RMK')[0])) or ' CB' in raw
        if g >= 35 or ws or ts_:
            k = ic + '|' + day(int(ts)); o = out.setdefault(k, {'icao': ic, 'day': day(int(ts)), 'gmax': 0, 'ws': False, 'ts': False, 'first': ts, 'last': ts, 'n': 0})
            o['gmax'] = max(o['gmax'], g); o['ws'] |= ws; o['ts'] |= ts_; o['first'] = min(o['first'], ts); o['last'] = max(o['last'], ts); o['n'] += 1
    for s in sigs or []:
        hz = s.get('hazard'); P = [(c['lat'], c['lon']) for c in (s.get('coords') or []) if 'lat' in c]
        if hz not in ('TS', 'TURB', 'MTW') or len(P) < 3: continue
        for ic, ll in apll.items():
            near = in_poly(ll[0], ll[1], P) or min(nm(ll, p) for p in P) <= 50
            if not near: continue
            k = ic + '|' + day(int(s.get('validTimeFrom') or time.time()))
            o = out.setdefault(k, {'icao': ic, 'day': k.split('|')[1], 'gmax': 0, 'ws': False, 'ts': False, 'first': s.get('validTimeFrom'), 'last': s.get('validTimeTo'), 'n': 0})
            o.setdefault('sig', [])
            lab = f"{s.get('firId', '')} {s.get('seriesId', '')} {hz}"
            if lab not in o['sig']: o['sig'].append(lab)
    return out


def main(outdir):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'adsb'))
    try:
        import proc; apll = {ICAO[k]: v for k, v in proc.AP.items() if k in ICAO}
    except Exception:
        apll = {}
    ids = ','.join(sorted(set(ICAO.values())))
    met = get(f'/metar?ids={ids}&format=json&hours=3') or []
    taf = get(f'/taf?ids={ids}&format=json') or []
    sig = get('/isigmet?format=json') or []
    n1 = merge(os.path.join(outdir, 'metar'), met, lambda x: x.get('rawOb'), 'obsTime')
    n2 = merge(os.path.join(outdir, 'taf'), taf, lambda x: x.get('rawTAF'), 'issueTime')
    n3 = merge(os.path.join(outdir, 'sig'), sig, lambda x: x.get('rawSigmet') or json.dumps(x, sort_keys=True)[:400], 'validTimeFrom')
    cp = os.path.join(outdir, 'cases.json'); cases = load(cp) if os.path.exists(cp) else []
    if isinstance(cases, list): cases = {c['icao'] + '|' + c['day']: c for c in cases}
    for k, o in flags(met, sig, apll).items():
        c = cases.get(k)
        if not c: cases[k] = o; continue
        c['gmax'] = max(c.get('gmax', 0), o['gmax']); c['ws'] = c.get('ws') or o['ws']; c['ts'] = c.get('ts') or o['ts']
        c['first'] = min(c.get('first') or o['first'], o['first'] or c.get('first')); c['last'] = max(c.get('last') or 0, o['last'] or 0); c['n'] = c.get('n', 0) + o['n']
        if o.get('sig'): c['sig'] = sorted(set(c.get('sig', []) + o['sig']))
    cut = (dt.datetime.utcnow() - dt.timedelta(days=KEEP_DAYS)).strftime('%Y%m%d')
    L = sorted([c for c in cases.values() if c['day'] >= cut], key=lambda c: (c['day'], c['icao']), reverse=True)
    json.dump(L, open(cp, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    for f in ('metar', 'taf', 'sig'):
        d = os.path.join(outdir, f)
        for fn in os.listdir(d) if os.path.isdir(d) else []:
            if fn[:8] < cut: os.remove(os.path.join(d, fn))
    print(f'metar +{n1} taf +{n2} sig +{n3} cases {len(L)}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'wxlog_out')
