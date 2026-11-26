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
STEPS = list(range(0, int(os.environ.get('WX_MAXSTEP', 72)) + 1, 3))   # 0~72시간, 3시간 간격
KN = 1.943844                                             # m/s → kt
HERE = os.path.dirname(os.path.abspath(__file__))

def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)

# ---------- 받기 ----------
# ECMWF 공개 자료 저장소(앞에서부터 시도). 같은 파일이 여러 클라우드에 복제되어 있음
SOURCES = os.environ.get('WX_SOURCES', 'https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com,'
                         'https://storage.googleapis.com/ecmwf-open-data,'
                         'https://data.ecmwf.int/forecasts').split(',')
UA = {'User-Agent': '7C-Turbulence-pipeline/1 (cabin safety briefing; ECMWF open data)'}

def streams(h): return ['oper'] if h in (0, 12) else ['scda', 'oper']

def furl(src, run, stream, step):
    d = run.strftime('%Y%m%d'); h = run.strftime('%H')
    return f"{src}/{d}/{h}z/ifs/0p25/{stream}/{d}{h}0000-{step}h-{stream}-fc"

def http(method, url, headers=None, tries=3, timeout=60):
    import requests
    last = None
    for i in range(tries):
        try:
            r = requests.request(method, url, headers={**UA, **(headers or {})}, timeout=timeout)
            if r.status_code in (200, 206): return r
            last = f'HTTP {r.status_code}'
            if r.status_code in (403, 404): break          # 없는 파일 — 재시도 무의미
        except Exception as e:
            last = type(e).__name__
        time.sleep([5, 15, 30][min(i, 2)])
    raise IOError(f'{last} · {url}')

def latest_run():
    """지금부터 6시간 단위로 거슬러 올라가며, 마지막 단계(72h) 색인 파일이 있는 첫 발표분"""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    run = now.replace(hour=now.hour - now.hour % 6)
    for _ in range(10):
        for src in SOURCES:
            for stm in streams(run.hour):
                u = furl(src, run, stm, STEPS[-1]) + '.index'
                try:
                    http('HEAD', u, tries=1, timeout=20); log('확인', u); return run, src, stm
                except Exception as e:
                    log('없음', str(e)[:140])
        run -= dt.timedelta(hours=6)
    raise SystemExit('최근 이틀 안에 받을 수 있는 발표분을 찾지 못함')

def want(meta):
    lt = meta.get('levtype'); pa = meta.get('param')
    if lt == 'pl': return pa in PL and int(meta.get('levelist', 0)) in LEV
    if lt == 'sfc': return pa in SFC or pa in ('10fg3', 'fg10', 'i10fg')
    return False

def fetch_step(run, step, src0, stm0, target):
    order = [(src0, stm0)] + [(s, t) for s in SOURCES for t in streams(run.hour) if (s, t) != (src0, stm0)]
    err = None
    for src, stm in order:
        base = furl(src, run, stm, step)
        try:
            idx = [json.loads(l) for l in http('GET', base + '.index').text.splitlines() if l.strip()]
            sel = sorted([m for m in idx if want(m)], key=lambda m: m['_offset'])
            if not sel: raise IOError('색인에 필요한 변수가 없음')
            # 붙어 있는 구간은 한 번에
            rng = []
            for m in sel:
                a, b = m['_offset'], m['_offset'] + m['_length'] - 1
                if rng and a <= rng[-1][1] + 1: rng[-1][1] = max(rng[-1][1], b)
                else: rng.append([a, b])
            with open(target, 'wb') as f:
                for a, b in rng:
                    f.write(http('GET', base + '.grib2', headers={'Range': f'bytes={a}-{b}'}).content)
            return len(sel), src, stm
        except Exception as e:
            err = e; log('실패 → 다음 저장소', str(e)[:160])
    raise SystemExit(f'step {step} 받기 실패: {err}')

def download(run, src, stm, work):
    files = []
    for k in STEPS:
        f = os.path.join(work, f'f_{k:03d}.grib2')
        n, used, ustm = fetch_step(run, k, src, stm, f)
        src, stm = used, ustm      # 다음 단계는 방금 성공한 저장소부터(실패한 곳을 매번 다시 기다리지 않음)
        files.append(f); log(f'받음 step {k} · {n}개 필드 · {used.split("/")[2]}')
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
        run, src, stm = latest_run(); log('최신 발표', run, stm)
        if a.prev and os.path.exists(a.prev) and not a.force:
            try:
                if json.load(open(a.prev))['run'] == run_ts(run):
                    log('이미 처리한 발표분 — 건너뜀'); print('SKIP')
                    if os.environ.get('GITHUB_OUTPUT'): open(os.environ['GITHUB_OUTPUT'], 'a').write('skip=1\n')
                    return
            except Exception: pass
        work = os.path.join('/tmp', 'ecmwf'); os.makedirs(work, exist_ok=True)
        files = download(run, src, stm, work)
    allp = [x for R in routes.values() for x in R['p'] + R['o']]
    bb = (min(p[0] for p in allp) - 2, max(p[0] for p in allp) + 2, min(p[1] for p in allp) - 2, max(p[1] for p in allp) + 2)
    data, grid = read(files, bb)
    log('읽음', len(data), '개 필드 · 격자', grid.key())
    build(data, grid, routes, run, a.out)

if __name__ == '__main__':
    main()
