"""7C Turbulence 라디오존데 대조 (GH Rev.5 · 일회성/수시 실행)
실제 고층 관측(NOAA IGRA2 · 노선 주변 관측소 · 하루 2회)과 같은 시각의 모델 '예보'(24·48시간 전 발표분)를 비교.
· 분석값이 아니라 예보값으로 비교함 — 분석값은 관측을 이미 반영해 만든 값이라 검증 의미가 없음
· 모델: ECMWF IFS · NOAA GFS · ECMWF AIFS (같은 날짜·관측소·층만 모아 공정 비교)
· 층: 관측 지정 기압면 850·700·500·400·300·250·200·150hPa (600hPa는 관측 지정면이 아님)
출력: <out>/summary.json(보고서용 요약) · <out>/pairs.csv(원값)
"""
import argparse, csv, io, json, math, os, re, sys, time, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import make as M
log = M.log

IGRA = os.environ.get('IGRA_BASE', 'https://www.ncei.noaa.gov/data/integrated-global-radiosonde-archive')
SLV = [850, 700, 500, 400, 300, 250, 200, 150]
CRU = [300, 250, 200]
KN = M.KN

# ---------- 관측(IGRA2) ----------
def km(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0]); dl = math.radians(b[1] - a[1])
    return 6371 * math.acos(max(-1, min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))

def stations(text, pts, radius, year):
    """igra2-station-list.txt: ID 1-11, LAT 13-20, LON 22-30, NAME 42-71, LSTYEAR 78-81"""
    out = []
    for l in text.splitlines():
        if len(l) < 81: continue
        try:
            sid, la, lo, name, ly = l[0:11].strip(), float(l[12:20]), float(l[21:30]), l[41:71].strip(), int(l[77:81])
        except ValueError:
            continue
        if ly < year - 1 or la < -90: continue
        d = min(km((la, lo), p) for p in pts)
        if d <= radius: out.append({'id': sid, 'name': name.title(), 'lat': la, 'lon': lo, 'd': round(d)})
    return sorted(out, key=lambda x: x['d'])

def parse_igra(text, t0, t1):
    """IGRA2 자료: 헤더 '#ID YEAR MO DY HR …' · 자료행 LVLTYP1(1) PRESS(10-15,Pa) TEMP(23-27,0.1℃) WDIR(41-45) WSPD(47-51,0.1m/s)
    → {관측시각(epoch): {층: (풍속kt, 풍향, 기온℃)}} (지정 기압면만)"""
    out, cur, keep = {}, None, False
    for l in text.splitlines():
        if l.startswith('#'):
            try:
                y, mo, d, h = int(l[13:17]), int(l[18:20]), int(l[21:23]), int(l[24:26])
            except ValueError:
                keep = False; continue
            if h not in (0, 12): keep = False; continue
            ts = int(dt.datetime(y, mo, d, h, tzinfo=dt.timezone.utc).timestamp())
            keep = t0 <= ts <= t1
            if keep: cur = out.setdefault(ts, {})
            continue
        if not keep or len(l) < 51: continue
        try:
            typ, p, t, wd, ws = int(l[0]), int(l[9:15]), int(l[22:27]), int(l[40:45]), int(l[46:51])
        except ValueError:
            continue
        if typ != 1 or p % 100: continue
        hp = p // 100
        if hp not in SLV or wd < 0 or ws < 0 or wd > 360: continue
        cur[hp] = (ws / 10 * KN, wd % 360, t / 10 if t > -9000 else None)
    return {k: v for k, v in out.items() if v}

def load_sondes(sts, t0, t1, local=None):
    obs = {}
    listing = '' if local else M.http('GET', IGRA + '/access/data-y2d/').text
    for s in sts:
        try:
            if local:
                f = os.path.join(local, s['id'] + '.txt')
                if not os.path.exists(f): continue
                txt = open(f, encoding='latin-1').read()
            else:
                m = re.findall(re.escape(s['id']) + r'-data[^"\'<>\s]*\.zip', listing)
                if not m: log('관측 파일 없음', s['id']); continue
                z = zipfile.ZipFile(io.BytesIO(M.http('GET', IGRA + '/access/data-y2d/' + m[0], timeout=120).content))
                txt = z.read(z.namelist()[0]).decode('latin-1')
            o = parse_igra(txt, t0, t1)
            if o: obs[s['id']] = o; log(f"관측 {s['id']} {s['name']} · {len(o)}회")
        except Exception as e:
            log('관측 실패', s['id'], str(e)[:120])
    return obs

# ---------- 모델 예보 받기 ----------
def ranges(sel):
    rng = []
    for a, b in sel:
        if rng and a <= rng[-1][1] + 1: rng[-1][1] = max(rng[-1][1], b)
        else: rng.append([a, b])
    return rng

def get_ranges(url, rng, target):
    with ThreadPoolExecutor(6) as ex:
        parts = list(ex.map(lambda r: M.http('GET', url, headers={'Range': f'bytes={r[0]}-' + ('' if r[1] is None else str(r[1]))}).content, rng))
    with open(target, 'wb') as f:
        for c in parts: f.write(c)

def fetch_model(model, run, step, target):
    err = None
    if model in ('ifs', 'aifs'):
        for src in M.SOURCES:
            base = M.furl(src, run, 'oper', step) if model == 'ifs' else M.aifs_url(src, run).format(step=step)
            try:
                idx = [json.loads(l) for l in M.http('GET', base + '.index', tries=2).text.splitlines() if l.strip()]
                sel = sorted([(m['_offset'], m['_offset'] + m['_length'] - 1) for m in idx
                              if m.get('levtype') == 'pl' and m.get('param') in ('u', 'v', 't') and int(m.get('levelist', 0)) in SLV])
                if len(sel) < 3 * len(SLV): raise IOError(f'필드 부족 {len(sel)}')
                get_ranges(base + '.grib2', ranges(sel), target); return
            except Exception as e:
                err = e
    else:
        for src in M.GFS_SOURCES:
            base = M.gfs_url(src, run, step)
            try:
                rows = []
                for l in M.http('GET', base + '.idx', tries=2).text.splitlines():
                    a = l.split(':')
                    if len(a) >= 5 and a[1].isdigit(): rows.append((int(a[1]), a[3], a[4]))
                want = {f'{l} mb' for l in SLV}; sel = []
                for i, (off, var, lev) in enumerate(rows):
                    if var in ('UGRD', 'VGRD', 'TMP') and lev in want:
                        sel.append((off, rows[i + 1][0] - 1 if i + 1 < len(rows) else None))
                if len(sel) < 3 * len(SLV): raise IOError(f'필드 부족 {len(sel)}')
                get_ranges(base, sel, target); return
            except Exception as e:
                err = e
    raise IOError(f'{model} {run:%m-%d %HZ}+{step}h 받기 실패: {str(err)[:100]}')

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='verif')
    ap.add_argument('--days', type=int, default=30); ap.add_argument('--leads', default='24,48'); ap.add_argument('--models', default='ifs,gfs,aifs')
    ap.add_argument('--radius', type=float, default=200, help='노선 지점에서 관측소까지 최대 거리(km)')
    ap.add_argument('--max-st', type=int, default=24); ap.add_argument('--local-sonde'); ap.add_argument('--local-model'); ap.add_argument('--now')
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    leads = [int(x) for x in a.leads.split(',')]; models = a.models.split(',')
    routes = json.load(open(os.path.join(HERE, 'routes.json'), encoding='utf-8'))['routes']
    pts = []
    for R in routes.values():
        for p in R['p']:
            if all(abs(p[0] - q[0]) + abs(p[1] - q[1]) > 0.5 for q in pts[-50:]): pts.append(p)
    now = dt.datetime.strptime(a.now, '%Y-%m-%dT%H') if a.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    t1 = int((now - dt.timedelta(hours=12)).replace(tzinfo=dt.timezone.utc).timestamp()); t0 = t1 - a.days * 86400
    stxt = open(os.path.join(a.local_sonde, 'stations.txt'), encoding='latin-1').read() if a.local_sonde else M.http('GET', IGRA + '/doc/igra2-station-list.txt', timeout=120).text
    sts = stations(stxt, pts, a.radius, now.year)[:a.max_st]
    log('관측소', len(sts), '곳:', ', '.join(f"{s['id']}({s['d']}km)" for s in sts))
    obs = load_sondes(sts, t0, t1, a.local_sonde)
    sts = [s for s in sts if s['id'] in obs]
    if not sts: raise SystemExit('관측 자료를 받지 못함')
    vts = sorted({t for o in obs.values() for t in o})
    bb = (min(s['lat'] for s in sts) - 1, max(s['lat'] for s in sts) + 1, min(s['lon'] for s in sts) - 1, max(s['lon'] for s in sts) + 1)
    spts = [[s['lat'], s['lon']] for s in sts]
    pairs = []; work = '/tmp/sonde'; os.makedirs(work, exist_ok=True); got = {m: 0 for m in models}; miss = {m: 0 for m in models}
    for m in models:
        for L in leads:
            for V in vts:
                run = dt.datetime.fromtimestamp(V - L * 3600, dt.timezone.utc).replace(tzinfo=None)
                try:
                    if a.local_model:
                        f = os.path.join(a.local_model, f'{m}_{run:%Y%m%d%H}_{L}.grib2')
                        if not os.path.exists(f): raise IOError('시험 파일 없음')
                    else:
                        f = os.path.join(work, 'm.grib2'); fetch_model(m, run, L, f)
                    data, grid = M.read([f], bb); I, W = grid.weights(spts); at = lambda arr: (arr[I] * W).sum(axis=1)
                    U = {l: at(data[(L, 'u', l)]) for l in SLV if (L, 'u', l) in data}; Vv = {l: at(data[(L, 'v', l)]) for l in SLV if (L, 'v', l) in data}
                    T = {l: at(data[(L, 't', l)]) for l in SLV if (L, 't', l) in data}
                    for i, s in enumerate(sts):
                        o = obs[s['id']].get(V)
                        if not o: continue
                        for l, (os_, od, ot) in o.items():
                            if l not in U: continue
                            mu, mv = U[l][i] * KN, Vv[l][i] * KN; ou, ov = -os_ * math.sin(math.radians(od)), -os_ * math.cos(math.radians(od))
                            ms = math.hypot(mu, mv); md = (270 - math.degrees(math.atan2(mv, mu))) % 360
                            mt = T[l][i] - 273.15 if l in T else None
                            pairs.append([m, L, s['id'], V, l, round(os_, 1), od, ot, round(ms, 1), round(md), None if mt is None else round(mt, 1), round(mu - ou, 2), round(mv - ov, 2)])
                    got[m] += 1
                except Exception as e:
                    miss[m] += 1; log('건너뜀', m, f'+{L}h', dt.datetime.fromtimestamp(V, dt.timezone.utc).strftime('%m-%d %HZ'), str(e)[:90])
            log(f'{m} +{L}h 완료 · 누적 받음 {got[m]} · 건너뜀 {miss[m]}')
    # GH Rev.6: 앱 주 자료(IFS·AIFS 바람 평균)도 같은 방식으로 채점
    if 'ifs' in models and 'aifs' in models:
        I = {(p[1], p[2], p[3], p[4]): p for p in pairs if p[0] == 'ifs'}; mix = []
        for p in pairs:
            if p[0] != 'aifs': continue
            q = I.get((p[1], p[2], p[3], p[4]))
            if not q: continue
            uv_ = lambda s_, d_: (-s_ * math.sin(math.radians(d_)), -s_ * math.cos(math.radians(d_)))
            a1, a2 = uv_(q[8], q[9]), uv_(p[8], p[9]); mu, mv = (a1[0] + a2[0]) / 2, (a1[1] + a2[1]) / 2
            mt = None if q[10] is None else q[10]
            mix.append(['mix', p[1], p[2], p[3], p[4], p[5], p[6], p[7], round(math.hypot(mu, mv), 1), round((math.degrees(math.atan2(-mu, -mv)) + 360) % 360), mt,
                        round((q[11] + p[11]) / 2, 2), round((q[12] + p[12]) / 2, 2)])
        base_models = list(models); pairs += mix
    else:
        base_models = list(models)
    # 공정 비교: 모든 모델에 값이 있는 (리드·관측소·시각·층)만
    key = lambda p: (p[1], p[2], p[3], p[4]); cnt = {}
    for p in pairs: cnt.setdefault(key(p), set()).add(p[0])
    common = {k for k, v in cnt.items() if all(m in v for m in base_models)}
    if 'mix' in {p[0] for p in pairs}: models = base_models + ['mix']; got['mix'] = got.get('ifs', 0); miss['mix'] = miss.get('ifs', 0)
    def stat(ps):
        if not ps: return None
        du = np.array([p[11] for p in ps]); dv = np.array([p[12] for p in ps]); ds = np.array([p[8] - p[5] for p in ps])
        dd = np.array([min(abs(p[9] - p[6]) % 360, 360 - abs(p[9] - p[6]) % 360) for p in ps if p[5] >= 20])
        dt_ = np.array([p[10] - p[7] for p in ps if p[10] is not None and p[7] is not None])
        return {'n': len(ps), 'vrmse': round(float(np.sqrt(np.mean(du ** 2 + dv ** 2))), 2), 'sbias': round(float(ds.mean()), 2), 'smae': round(float(np.abs(ds).mean()), 2),
                'dmae': round(float(dd.mean()), 1) if len(dd) else None, 'tbias': round(float(dt_.mean()), 2) if len(dt_) else None, 'tmae': round(float(np.abs(dt_).mean()), 2) if len(dt_) else None}
    S = {}
    for m in models:
        S[m] = {}
        for L in leads:
            P = [p for p in pairs if p[0] == m and p[1] == L and key(p) in common]
            S[m][str(L)] = {'lev': {str(l): stat([p for p in P if p[4] == l]) for l in SLV},
                            'cruise': stat([p for p in P if p[4] in CRU]),
                            'strong': stat([p for p in P if p[4] in CRU and p[5] >= 80]),
                            'all': stat(P),
                            'st': {s['id']: stat([p for p in P if p[2] == s['id'] and p[4] in CRU]) for s in sts}}
    summ = {'v': 1, 'made': int(time.time()), 'days': a.days, 'from': t0, 'to': t1, 'leads': leads, 'models': models, 'levels': SLV, 'cruise': CRU,
            'stations': [{**s, 'n': len(obs[s['id']])} for s in sts], 'got': got, 'miss': miss, 'common': len(common), 'stats': S,
            'note': '예보(24·48시간 전 발표분) vs 라디오존데 관측 · 모든 모델에 값이 있는 경우만 비교 · vrmse=바람 벡터 오차 제곱평균제곱근(kt)'}
    json.dump(summ, open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    with open(os.path.join(a.out, 'pairs.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['모델', '리드h', '관측소', '관측시각UTC', '층hPa', '관측풍속kt', '관측풍향', '관측기온C', '모델풍속kt', '모델풍향', '모델기온C', '동서오차kt', '남북오차kt'])
        for p in pairs: w.writerow(p[:3] + [dt.datetime.fromtimestamp(p[3], dt.timezone.utc).strftime('%Y-%m-%d %H')] + p[4:])
    for m in models:
        for L in leads:
            c = S[m][str(L)]['cruise']
            if c: log(f'{m} +{L}h 순항대 바람 오차 {c["vrmse"]}kt (n={c["n"]}) · 풍속 치우침 {c["sbias"]}kt')
    log('완료 · 공정 비교 대상', len(common), '건')

if __name__ == '__main__':
    main()
