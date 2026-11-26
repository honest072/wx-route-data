"""7C Turbulence 원자료 파이프라인 (GH Rev.1)
ECMWF IFS 0.25° 공개 자료(AWS 복제본, CC-BY-4.0)에서 노선별 계산 지점의 고도별 바람·기온·고도와
지상 변수(CAPE·돌풍·강수·상층운)를 뽑아 노선마다 작은 JSON으로 저장.
실행: python pipeline/make.py --out out            (최신 발표분 자동 선택)
      python pipeline/make.py --out out --local DIR  (시험용: 미리 받은 GRIB2 폴더 사용)
"""
import argparse, json, math, os, sys, time, glob, datetime as dt
import numpy as np
import eccodes as ec

LEV = [850, 700, 600, 500, 400, 300, 250, 200, 150]      # 앱 ALLHPA와 같음
PL = ['u', 'v', 't', 'gh']
SFC = ['mucape', '10fg', 'cp', 'tp', 'hcc']
STEPS = list(range(0, 73, 3))                             # 0~72시간, 3시간 간격
KN = 1.943844                                             # m/s → kt
HERE = os.path.dirname(os.path.abspath(__file__))

def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)

# ---------- 받기 ----------
def latest_run():
    from ecmwf.opendata import Client
    c = Client(source='aws')
    return c.latest(type='fc', step=STEPS[-1], param='u', levelist=LEV[0])

def download(run, work):
    from ecmwf.opendata import Client
    c = Client(source='aws')
    files = []
    for k in STEPS:
        f1 = os.path.join(work, f'pl_{k:03d}.grib2'); f2 = os.path.join(work, f'sfc_{k:03d}.grib2')
        for tries in range(3):
            try:
                c.retrieve(date=run.strftime('%Y%m%d'), time=run.hour, type='fc', step=k, levtype='pl', levelist=LEV, param=PL, target=f1)
                c.retrieve(date=run.strftime('%Y%m%d'), time=run.hour, type='fc', step=k, levtype='sfc', param=SFC, target=f2)
                break
            except Exception as e:
                log('재시도', k, e); time.sleep(10)
        else:
            raise SystemExit(f'step {k} 받기 실패')
        files += [f1, f2]
        log('받음 step', k)
    return files

# ---------- 읽기 ----------
class Grid:
    def __init__(self, h):
        g = lambda k: ec.codes_get(h, k)
        self.ni, self.nj = g('Ni'), g('Nj')
        self.lat0, self.lon0 = g('latitudeOfFirstGridPointInDegrees'), g('longitudeOfFirstGridPointInDegrees')
        self.di, self.dj = g('iDirectionIncrementInDegrees'), g('jDirectionIncrementInDegrees')
        self.jpos = g('jScansPositively') == 1
    def key(self): return (self.ni, self.nj, self.lat0, self.lon0, self.di, self.dj, self.jpos)
    def crop(self, bb):
        """노선 범위(남·북·서·동, 도)만 남김 → (행 범위, 열 색인, 잘린 격자). 메모리를 1/50 수준으로 줄임"""
        s_, n_, w_, e_ = bb
        js = [j for j in range(self.nj) if s_ <= self.lat0 + (j * self.dj if self.jpos else -j * self.dj) <= n_]
        i0 = int(math.floor(((w_ - self.lon0) % 360) / self.di)); i1 = int(math.ceil(((e_ - self.lon0) % 360) / self.di))
        if i1 < i0: i1 += self.ni
        cols = [(i % self.ni) for i in range(i0, i1 + 1)]
        g = Grid.__new__(Grid); g.ni, g.nj = len(cols), len(js); g.di, g.dj, g.jpos = self.di, self.dj, self.jpos
        g.lat0 = self.lat0 + (js[0] * self.dj if self.jpos else -js[0] * self.dj); g.lon0 = (self.lon0 + i0 * self.di) % 360
        g.wrap = False
        return (js[0], js[-1] + 1), np.array(cols), g
    def weights(self, pts):
        """[위도,경도] 목록 → (색인 4개, 가중치 4개) 이중선형 보간"""
        out = []
        for la, lo in pts:
            y = (la - self.lat0) / self.dj * (1 if self.jpos else -1)
            x = ((lo - self.lon0) % 360) / self.di
            j0, i0 = int(math.floor(y)), int(math.floor(x)); fy, fx = y - j0, x - i0
            if not getattr(self, 'wrap', True) and not (0 <= i0 < self.ni - 1 and 0 <= j0 < self.nj - 1):
                raise SystemExit(f'지점 {la},{lo}이 잘라 낸 범위 밖')
            j1 = min(j0 + 1, self.nj - 1); i1 = (i0 + 1) % self.ni
            idx = [j0 * self.ni + i0, j0 * self.ni + i1, j1 * self.ni + i0, j1 * self.ni + i1]
            w = [(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy]
            out.append((idx, w))
        I = np.array([o[0] for o in out]); W = np.array([o[1] for o in out])
        return I, W

def read(files, bb):
    """{(step,param,level): 잘라 낸 1차원 값 배열}, 잘린 Grid"""
    data, grid, full, cut = {}, None, None, None
    for f in files:
        with open(f, 'rb') as fh:
            while True:
                h = ec.codes_grib_new_from_file(fh)
                if h is None: break
                try:
                    sn = ec.codes_get(h, 'shortName'); sn = '10fg' if sn.startswith(('10fg', 'fg10', 'i10fg')) else sn; st = int(ec.codes_get(h, 'endStep')) if ec.codes_is_defined(h, 'endStep') else int(ec.codes_get(h, 'step'))
                    lt = ec.codes_get(h, 'typeOfLevel'); lv = int(ec.codes_get(h, 'level')) if lt == 'isobaricInhPa' else 0
                    gr = Grid(h)
                    if full is None: full = gr; cut = gr.crop(bb); grid = cut[2]
                    elif gr.key() != full.key(): raise SystemExit('격자가 서로 다름')
                    (ja, jb), cols, _ = cut
                    data[(st, sn, lv)] = ec.codes_get_values(h).astype(np.float32).reshape(full.nj, full.ni)[ja:jb].take(cols, axis=1).ravel()
                finally:
                    ec.codes_release(h)
    return data, grid

# ---------- 계산 ----------
def run_ts(run): return int(run.replace(tzinfo=dt.timezone.utc).timestamp())

def build(data, grid, routes, run, out):
    os.makedirs(os.path.join(out, 'r'), exist_ok=True)
    steps = sorted({k[0] for k in data})
    miss = [(k, p, l) for k in steps for p in PL for l in LEV if (k, p, l) not in data]
    if miss: raise SystemExit(f'기압면 자료 빠짐 {miss[:5]}')
    def at(I, W, arr): return (arr[I] * W).sum(axis=1)
    nfile = 0
    for key, R in routes.items():
        res = {'v': 1, 'key': key, 'run': run_ts(run), 'steps': steps, 'lev': LEV, 'p': R['p'], 'o': R['o'],
               'scale': {'ws': 10, 'wd': 1, 't': 10, 'gh': 1, 'cape': 1, 'g10': 10, 'sh': 100, 'pr': 100, 'hcc': 1},
               'm': {'ws': [], 'wd': [], 't': [], 'gh': []}, 's': {'cape': [], 'g10': [], 'sh': [], 'pr': [], 'hcc': []}, 'o_': {'ws': [], 'wd': []}}
        for tag, pts in (('m', R['p']), ('o_', R['o'])):
            I, W = grid.weights(pts)
            for k in steps:
                ws, wd, tt, gh = [], [], [], []
                for l in LEV:
                    u = at(I, W, data[(k, 'u', l)]); v = at(I, W, data[(k, 'v', l)])
                    sp = np.hypot(u, v) * KN; dr = (270 - np.degrees(np.arctan2(v, u))) % 360
                    ws.append(np.round(sp * 10).astype(int)); wd.append(np.round(dr).astype(int) % 360)
                    if tag == 'm':
                        tt.append(np.round((at(I, W, data[(k, 't', l)]) - 273.15) * 10).astype(int))
                        gh.append(np.round(at(I, W, data[(k, 'gh', l)])).astype(int))
                # [지점][층]
                res[tag]['ws'].append(np.array(ws).T.tolist()); res[tag]['wd'].append(np.array(wd).T.tolist())
                if tag == 'm':
                    res['m']['t'].append(np.array(tt).T.tolist()); res['m']['gh'].append(np.array(gh).T.tolist())
            if tag == 'm':
                acc, kp = {}, None
                for k in steps:
                    g = lambda sn: at(I, W, data[(k, sn, 0)]) if (k, sn, 0) in data else None
                    cape, g10, cp, tp, hcc = g('mucape'), g('10fg'), g('cp'), g('tp'), g('hcc')
                    z = np.zeros(len(pts))
                    def rate(cur, name):   # 누적 강수(m) → 직전 단계 대비 mm/h
                        if cur is None: return z
                        p0 = acc.get(name); acc[name] = cur
                        if p0 is None or kp is None or k <= kp: return z
                        return np.maximum(0, (cur - p0) * 1000 / (k - kp))
                    sh, pr = rate(cp, 'cp'), rate(tp, 'tp'); kp = k
                    res['s']['cape'].append(np.round(cape if cape is not None else z).astype(int).tolist())
                    res['s']['g10'].append(np.round((g10 if g10 is not None else z) * KN * 10).astype(int).tolist())
                    res['s']['sh'].append(np.round(sh * 100).astype(int).tolist())
                    res['s']['pr'].append(np.round(pr * 100).astype(int).tolist())
                    res['s']['hcc'].append(np.round((hcc if hcc is not None else z) * 100).astype(int).tolist())
        res['o'] = R['o']; res['ow'] = res.pop('o_')
        with open(os.path.join(out, 'r', key + '.json'), 'w') as f: json.dump(res, f, separators=(',', ':'))
        nfile += 1
    man = {'v': 1, 'src': 'ECMWF IFS 0.25° open data (AWS) · CC-BY-4.0', 'model': 'ecmwf_ifs025', 'run': run_ts(run),
           'runIso': run.strftime('%Y-%m-%dT%H:00Z'), 'steps': steps, 'lev': LEV, 'made': int(time.time()), 'routes': sorted(routes)}
    with open(os.path.join(out, 'manifest.json'), 'w') as f: json.dump(man, f, separators=(',', ':'))
    log('노선 파일', nfile, '개 저장 · 발표', man['runIso'])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='out'); ap.add_argument('--local'); ap.add_argument('--run'); ap.add_argument('--force', action='store_true'); ap.add_argument('--prev', help='직전 manifest.json(같은 발표분이면 건너뜀)')
    a = ap.parse_args()
    routes = json.load(open(os.path.join(HERE, 'routes.json'), encoding='utf-8'))['routes']
    if a.local:
        files = sorted(glob.glob(os.path.join(a.local, '*.grib2')))
        run = dt.datetime.strptime(a.run, '%Y-%m-%dT%H') if a.run else dt.datetime(2026, 1, 1, 0)
    else:
        run = latest_run(); log('최신 발표', run)
        if a.prev and os.path.exists(a.prev) and not a.force:
            try:
                if json.load(open(a.prev))['run'] == run_ts(run):
                    log('이미 처리한 발표분 — 건너뜀'); print('SKIP')
                    if os.environ.get('GITHUB_OUTPUT'): open(os.environ['GITHUB_OUTPUT'], 'a').write('skip=1\n')
                    return
            except Exception: pass
        work = os.path.join('/tmp', 'ecmwf'); os.makedirs(work, exist_ok=True)
        files = download(run, work)
    allp = [x for R in routes.values() for x in R['p'] + R['o']]
    bb = (min(p[0] for p in allp) - 2, max(p[0] for p in allp) + 2, min(p[1] for p in allp) - 2, max(p[1] for p in allp) + 2)
    data, grid = read(files, bb)
    log('읽음', len(data), '개 필드 · 격자', grid.key())
    build(data, grid, routes, run, a.out)

if __name__ == '__main__':
    main()
