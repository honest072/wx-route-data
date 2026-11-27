"""7C Turbulence 원자료 파이프라인 (GH Rev.6 · AIFS 측면 지점 추가 — 앱 주 자료 바람 = IFS·AIFS 평균)
ECMWF IFS 0.25° 공개 자료(AWS 복제본, CC-BY-4.0)에서 노선별 계산 지점의 고도별 바람·기온·고도와
지상 변수(CAPE·돌풍·강수·상층운)를 뽑아 노선마다 작은 JSON으로 저장.
+ NOAA GFS 0.25° 공개 자료(미국 정부 공공 자료)에서 같은 지점의 고도별 바람·기온·고도를 뽑아 같은 파일의 'g'에 추가
  (교차 확인용 · GFS를 못 받아도 ECMWF 결과는 그대로 올림)
실행: python pipeline/make.py --out out            (최신 발표분 자동 선택)
      python pipeline/make.py --out out --local DIR  (시험용: 미리 받은 GRIB2 폴더 사용)
"""
import argparse, json, math, os, sys, time, glob, shutil, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import eccodes as ec

LEV = [850, 700, 600, 500, 400, 300, 250, 200, 150]      # 앱 ALLHPA와 같음
LOW = [925, 1000]                                         # GH Rev.4: 저층(약 2,500ft·지상 부근) — 기존 사용처 호환 위해 목록 끝에 붙임
LEVA = LEV + LOW
PL = ['u', 'v', 't', 'gh']
EX = ['w', 'd', 'r']                                      # 연직 속도(Pa/s)·발산(1/s)·상대습도(%) — LEV 9개 층
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
    if lt == 'pl':
        lv = int(meta.get('levelist', 0)); return (pa in PL and lv in LEVA) or (pa in EX and lv in LEV)
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

# ---------- GFS 받기 (교차 확인용) ----------
GFS_SOURCES = os.environ.get('GFS_SOURCES', 'https://noaa-gfs-bdp-pds.s3.amazonaws.com,'
                             'https://storage.googleapis.com/global-forecast-system,'
                             'https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod').split(',')
GFS_VAR = {'UGRD': 'u', 'VGRD': 'v', 'TMP': 't', 'HGT': 'gh'}

def gfs_url(src, run, step):
    d = run.strftime('%Y%m%d'); h = run.strftime('%H')
    return f"{src}/gfs.{d}/{h}/atmos/gfs.t{h}z.pgrb2.0p25.f{step:03d}"

def gfs_latest():
    """마지막 단계(72h) 색인이 올라온 가장 최근 GFS 발표분"""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    run = now.replace(hour=now.hour - now.hour % 6)
    for _ in range(8):
        for src in GFS_SOURCES:
            u = gfs_url(src, run, STEPS[-1]) + '.idx'
            try:
                http('HEAD', u, tries=1, timeout=20); log('GFS 확인', u); return run, src
            except Exception as e:
                log('GFS 없음', str(e)[:140])
        run -= dt.timedelta(hours=6)
    raise IOError('최근 이틀 안에 받을 수 있는 GFS 발표분 없음')

def gfs_sel(text):
    """.idx('번호:시작바이트:d=…:변수:층:예보:') → 필요한 기압면 필드의 (시작, 끝) 목록"""
    rows = []
    for l in text.splitlines():
        a = l.split(':')
        if len(a) >= 5 and a[1].isdigit(): rows.append((int(a[1]), a[3], a[4]))
    want_lv = {f'{l} mb' for l in LEVA}; out = []
    for i, (off, var, lev) in enumerate(rows):
        if var in GFS_VAR and lev in want_lv:
            end = rows[i + 1][0] - 1 if i + 1 < len(rows) else None
            out.append((off, end, var, lev))
    return out

def gfs_step(run, step, src0, target):
    order = [src0] + [s for s in GFS_SOURCES if s != src0]; err = None
    for src in order:
        base = gfs_url(src, run, step)
        try:
            sel = gfs_sel(http('GET', base + '.idx').text)
            if len(sel) < len(GFS_VAR) * len(LEVA): raise IOError(f'색인 필드 부족 {len(sel)}')
            def one(x):
                a, b = x[0], x[1]
                return http('GET', base, headers={'Range': f'bytes={a}-' + ('' if b is None else str(b))}).content
            with ThreadPoolExecutor(8) as ex: parts = list(ex.map(one, sel))
            with open(target, 'wb') as f:
                for c in parts: f.write(c)
            return len(sel), src
        except Exception as e:
            err = e; log('GFS 실패 → 다음 저장소', str(e)[:160])
    raise IOError(f'GFS step {step} 받기 실패: {err}')

def gfs_download(run, src, work):
    files = []
    for k in STEPS:
        f = os.path.join(work, f'g_{k:03d}.grib2')
        n, src = gfs_step(run, k, src, f)
        files.append(f); log(f'GFS 받음 step {k} · {n}개 필드 · {src.split("/")[2]}')
    return files

# ---------- ECMWF AIFS (인공지능 모델 · 교차 확인 세 번째 모델) ----------
AIFS_STEPS = list(range(0, STEPS[-1] + 1, 6))
AIFS_ERR = ''

def aifs_url(src, run):
    d = run.strftime('%Y%m%d'); h = run.strftime('%H')
    return f"{src}/{d}/{h}z/aifs-single/0p25/oper/{d}{h}0000-{{step}}h-oper-fc"

def aifs_latest():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    run = now.replace(hour=now.hour - now.hour % 6)
    for _ in range(8):
        for src in SOURCES:
            u = aifs_url(src, run).format(step=AIFS_STEPS[-1]) + '.index'
            try:
                http('HEAD', u, tries=1, timeout=20); log('AIFS 확인', u); return run, src
            except Exception as e:
                log('AIFS 없음', str(e)[:140])
        run -= dt.timedelta(hours=6)
    raise IOError('최근 이틀 안에 받을 수 있는 AIFS 발표분 없음')

def aifs_step(run, step, src0, target):
    order = [src0] + [x for x in SOURCES if x != src0]; err = None
    for src in order:
        base = aifs_url(src, run).format(step=step)
        try:
            idx = [json.loads(l) for l in http('GET', base + '.index').text.splitlines() if l.strip()]
            sel = sorted([m for m in idx if m.get('levtype') == 'pl' and m.get('param') in PL and int(m.get('levelist', 0)) in LEV], key=lambda m: m['_offset'])
            if len(sel) < len(PL) * len(LEV): raise IOError(f'AIFS 색인 필드 부족 {len(sel)}')
            rng = []
            for m in sel:
                a, b = m['_offset'], m['_offset'] + m['_length'] - 1
                if rng and a <= rng[-1][1] + 1: rng[-1][1] = max(rng[-1][1], b)
                else: rng.append([a, b])
            with ThreadPoolExecutor(6) as ex:
                parts = list(ex.map(lambda r: http('GET', base + '.grib2', headers={'Range': f'bytes={r[0]}-{r[1]}'}).content, rng))
            with open(target, 'wb') as f:
                for c in parts: f.write(c)
            return len(sel), src
        except Exception as e:
            err = e; log('AIFS 실패 → 다음 저장소', str(e)[:160])
    raise IOError(f'AIFS step {step} 받기 실패: {err}')

def aifs_download(run, src, work):
    files = []
    for k in AIFS_STEPS:
        f = os.path.join(work, f'a_{k:03d}.grib2'); n, src = aifs_step(run, k, src, f); files.append(f)
        log(f'AIFS 받음 step {k} · {n}개 필드 · {src.split("/")[2]}')
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
GFS_ERR = ''
def run_ts(run): return int(run.replace(tzinfo=dt.timezone.utc).timestamp())

def pl_block(data, grid, pts, steps, full, levs=None):
    levs = levs or LEVA
    """[단계][지점][층] 풍속(0.1kt)·풍향, full이면 기온(0.1℃)·고도(m)도"""
    I, W = grid.weights(pts); at = lambda arr: (arr[I] * W).sum(axis=1)
    o = {'ws': [], 'wd': []}
    if full: o.update(t=[], gh=[])
    for k in steps:
        ws, wd, tt, gh = [], [], [], []
        for l in levs:
            u = at(data[(k, 'u', l)]); v = at(data[(k, 'v', l)])
            ws.append(np.round(np.hypot(u, v) * KN * 10).astype(int)); wd.append(np.round((270 - np.degrees(np.arctan2(v, u))) % 360).astype(int) % 360)
            if full: tt.append(np.round((at(data[(k, 't', l)]) - 273.15) * 10).astype(int)); gh.append(np.round(at(data[(k, 'gh', l)])).astype(int))
        o['ws'].append(np.array(ws).T.tolist()); o['wd'].append(np.array(wd).T.tolist())
        if full: o['t'].append(np.array(tt).T.tolist()); o['gh'].append(np.array(gh).T.tolist())
    return o

def build(data, grid, routes, run, out, gfs=None, aifs=None, prevdir=None):
    """gfs = (data, grid, run) 또는 None"""
    os.makedirs(os.path.join(out, 'r'), exist_ok=True)
    steps = sorted({k[0] for k in data})
    miss = [(k, p, l) for k in steps for p in PL for l in LEVA if (k, p, l) not in data]
    if miss: raise SystemExit(f'기압면 자료 빠짐 {miss[:5]}')
    def at(I, W, arr): return (arr[I] * W).sum(axis=1)
    nfile = 0
    for key, R in routes.items():
        res = {'v': 2, 'key': key, 'run': run_ts(run), 'steps': steps, 'lev': LEVA, 'p': R['p'], 'o': R['o'],
               'scale': {'ws': 10, 'wd': 1, 't': 10, 'gh': 1, 'cape': 1, 'g10': 10, 'sh': 100, 'pr': 100, 'hcc': 1},
               'm': {'ws': [], 'wd': [], 't': [], 'gh': []}, 's': {'cape': [], 'g10': [], 'sh': [], 'pr': [], 'hcc': []}, 'o_': {'ws': [], 'wd': []}}
        for tag, pts in (('m', R['p']), ('o_', R['o'])):
            I, W = grid.weights(pts)
            for k in steps:
                ws, wd, tt, gh = [], [], [], []
                for l in LEVA:
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
            if tag == 'm' and all((k, e, l) in data for k in steps for e in EX for l in LEV):
                # 연직 속도 Pa/s ×1000 · 발산 1/s ×1e7 · 상대습도 %
                sc = {'w': 1000, 'd': 1e7, 'r': 1}; res['x'] = {e: [] for e in EX}
                for k in steps:
                    for e in EX:
                        res['x'][e].append(np.array([np.round(at(I, W, data[(k, e, l)]) * sc[e]).astype(int) for l in LEV]).T.tolist())
                res['scale'].update(w=1000, dv=1e7, rh=1)
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
        if gfs:
            gd, gg, gr = gfs; gsteps = sorted({k[0] for k in gd})
            g = {'run': run_ts(gr), 'steps': gsteps, 'm': pl_block(gd, gg, R['p'], gsteps, True)}
            ow = pl_block(gd, gg, R['o'], gsteps, False); g['ow'] = ow
            res['g'] = g
        if aifs:
            ad, ag, ar = aifs; asteps = sorted({k[0] for k in ad})
            res['a'] = {'run': run_ts(ar), 'steps': asteps, 'lev': LEV, 'm': pl_block(ad, ag, R['p'], asteps, True, LEV)}
            res['a']['ow'] = pl_block(ad, ag, R['o'], asteps, False, LEV)   # GH Rev.6: 측면 지점(앱의 IFS·AIFS 평균용)
        with open(os.path.join(out, 'r', key + '.json'), 'w') as f: json.dump(res, f, separators=(',', ':'))
        nfile += 1
    man = {'v': 1, 'src': 'ECMWF IFS 0.25° open data (AWS) · CC-BY-4.0', 'model': 'ecmwf_ifs025', 'run': run_ts(run),
           'runIso': run.strftime('%Y-%m-%dT%H:00Z'), 'steps': steps, 'lev': LEVA, 'xlev': LEV, 'v': 2, 'made': int(time.time()), 'routes': sorted(routes),
           'gfs': ({'src': 'NOAA GFS 0.25° (public domain)', 'model': 'gfs025', 'run': run_ts(gfs[2]), 'runIso': gfs[2].strftime('%Y-%m-%dT%H:00Z')} if gfs else None),
           'aifs': ({'src': 'ECMWF AIFS Single 0.25° (CC BY 4.0)', 'model': 'aifs025', 'run': run_ts(aifs[2]), 'runIso': aifs[2].strftime('%Y-%m-%dT%H:00Z')} if aifs else None)}
    if AIFS_ERR: man['aifsErr'] = AIFS_ERR[:200]
    # 직전 ECMWF 발표분 노선 파일을 p/에 보관(앱의 '직전 발표 대비' 비교용)
    if prevdir and os.path.isdir(prevdir):
        try:
            pm = json.load(open(os.path.join(prevdir, 'manifest.json')))
            src = os.path.join(prevdir, 'r') if pm.get('run') != man['run'] else os.path.join(prevdir, 'p')
            prun = pm.get('run') if pm.get('run') != man['run'] else pm.get('prevRun')
            if prun and os.path.isdir(src):
                shutil.copytree(src, os.path.join(out, 'p'), dirs_exist_ok=True); man['prevRun'] = prun
                man['prevIso'] = dt.datetime.fromtimestamp(prun, dt.timezone.utc).strftime('%Y-%m-%dT%H:00Z')
                log('직전 발표분 보관', man['prevIso'])
        except Exception as e:
            log('직전 발표분 보관 생략', e)
    if GFS_ERR: man['gfsErr'] = GFS_ERR[:200]
    with open(os.path.join(out, 'manifest.json'), 'w') as f: json.dump(man, f, separators=(',', ':'))
    log('노선 파일', nfile, '개 저장 · 발표', man['runIso'], '· GFS', man['gfs']['runIso'] if gfs else '없음')

def gfs_check_missing(gd):
    steps = sorted({k[0] for k in gd})
    miss = [(k, p, l) for k in steps for p in PL for l in LEVA if (k, p, l) not in gd]
    if miss or len(steps) < len(STEPS): raise IOError(f'GFS 자료 빠짐 {miss[:3]} 단계 {len(steps)}')

def main():
    global GFS_ERR, AIFS_ERR
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='out'); ap.add_argument('--local'); ap.add_argument('--local-gfs'); ap.add_argument('--run'); ap.add_argument('--gfs-run')
    ap.add_argument('--force', action='store_true'); ap.add_argument('--prev-dir'); ap.add_argument('--local-aifs'); ap.add_argument('--aifs-run'); ap.add_argument('--no-aifs', action='store_true'); ap.add_argument('--prev', help='직전 manifest.json(같은 발표분이면 건너뜀)'); ap.add_argument('--no-gfs', action='store_true')
    a = ap.parse_args()
    routes = json.load(open(os.path.join(HERE, 'routes.json'), encoding='utf-8'))['routes']
    allp = [x for R in routes.values() for x in R['p'] + R['o']]
    bb = (min(p[0] for p in allp) - 2, max(p[0] for p in allp) + 2, min(p[1] for p in allp) - 2, max(p[1] for p in allp) + 2)
    gfs, grun, gsrc = None, None, None
    aifs, arun, asrc, afiles = None, None, None, None
    if a.local:
        files = sorted(glob.glob(os.path.join(a.local, '*.grib2')))
        run = dt.datetime.strptime(a.run, '%Y-%m-%dT%H') if a.run else dt.datetime(2026, 1, 1, 0)
        if a.local_gfs:
            grun = dt.datetime.strptime(a.gfs_run, '%Y-%m-%dT%H') if a.gfs_run else run
            gfiles = sorted(glob.glob(os.path.join(a.local_gfs, '*.grib2')))
        if a.local_aifs:
            arun = dt.datetime.strptime(a.aifs_run, '%Y-%m-%dT%H') if a.aifs_run else run
            afiles = sorted(glob.glob(os.path.join(a.local_aifs, '*.grib2')))
    else:
        run, src, stm = latest_run(); log('최신 발표', run, stm)
        if not a.no_gfs:
            try: grun, gsrc = gfs_latest(); log('GFS 최신 발표', grun)
            except Exception as e: GFS_ERR = f'GFS 발표분 확인 실패: {e}'; log(GFS_ERR)
        if not a.no_aifs:
            try: arun, asrc = aifs_latest(); log('AIFS 최신 발표', arun)
            except Exception as e: AIFS_ERR = f'AIFS 발표분 확인 실패: {e}'; log(AIFS_ERR)
        if a.prev and os.path.exists(a.prev) and not a.force:
            try:
                pv = json.load(open(a.prev)); pg = (pv.get('gfs') or {}).get('run')
                pa = (pv.get('aifs') or {}).get('run')
                if pv['run'] == run_ts(run) and (grun is None or pg == run_ts(grun)) and (arun is None or pa == run_ts(arun)):
                    log('이미 처리한 발표분(ECMWF·GFS·AIFS 모두 같음) — 건너뜀'); print('SKIP')
                    if os.environ.get('GITHUB_OUTPUT'): open(os.environ['GITHUB_OUTPUT'], 'a').write('skip=1\n')
                    return
            except Exception: pass
        work = os.path.join('/tmp', 'ecmwf'); os.makedirs(work, exist_ok=True)
        files = download(run, src, stm, work)
        gfiles = None
        if grun:
            try: gfiles = gfs_download(grun, gsrc, work)
            except Exception as e: GFS_ERR = str(e); log('GFS 받기 실패 — ECMWF만 올림:', e)
        if arun:
            try: afiles = aifs_download(arun, asrc, work)
            except Exception as e: AIFS_ERR = str(e); log('AIFS 받기 실패 — 제외하고 올림:', e)
    data, grid = read(files, bb)
    log('읽음', len(data), '개 필드 · 격자', grid.key())
    if grun and gfiles:
        try:
            gd, gg = read(gfiles, bb); gfs_check_missing(gd); gfs = (gd, gg, grun)
            log('GFS 읽음', len(gd), '개 필드 · 격자', gg.key())
        except (Exception, SystemExit) as e:
            GFS_ERR = f'GFS 읽기 실패: {e}'; log(GFS_ERR); gfs = None
    if arun and afiles:
        try:
            ad, agr = read(afiles, bb); steps_a = sorted({k[0] for k in ad})
            miss = [(k, p, l) for k in steps_a for p in PL for l in LEV if (k, p, l) not in ad]
            if miss or len(steps_a) < len(AIFS_STEPS): raise IOError(f'AIFS 자료 빠짐 {miss[:3]} 단계 {len(steps_a)}')
            aifs = (ad, agr, arun); log('AIFS 읽음', len(ad), '개 필드')
        except (Exception, SystemExit) as e:
            AIFS_ERR = f'AIFS 읽기 실패: {e}'; log(AIFS_ERR); aifs = None
    build(data, grid, routes, run, a.out, gfs, aifs, a.prev_dir)

if __name__ == '__main__':
    main()
