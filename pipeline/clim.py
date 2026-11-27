"""7C Turbulence '평소 대비' 자료 (GH Rev.4)
ECMWF IFS 공개 자료 과거분(AWS·구글 클라우드 복제본, 2024.4~ 보관)에서 하루 2회(00Z·12Z) 발표 시각 값(분석)의
순항 고도대 바람(600~150hPa 7개 층)을 노선 지점별로 뽑아 최근 N일(기본 60일)을 노선 파일로 유지.
· 매일 실행하면 새 시각만 받아 붙이고, N일보다 오래된 시각은 버림
· 노선 지점이 바뀐 노선은 전체를 다시 채움
· 앱은 이 값으로 기존 '평소 대비(최근 31일)'와 같은 계산(순항 고도 SR 분포)을 함

출력: <out>/c/<노선>.json, <out>/c/index.json
  b64 = [시각][지점][층][풍속 kt(0~255), 풍향/2(0~179)] 바이트열을 base64로
"""
import argparse, base64, json, os, sys, time, glob, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import make as M                                       # 같은 받기·읽기 코드 사용

CL = [600, 500, 400, 300, 250, 200, 150]               # 약 FL140~FL450
log = M.log

def fetch_t(run, target):
    """발표 시각 run의 0시간(분석) u·v 7개 층만 받음 — 저장소 순서대로 시도"""
    err = None
    for src in M.SOURCES:
        base = M.furl(src, run, 'oper', 0)
        try:
            idx = [json.loads(l) for l in M.http('GET', base + '.index', tries=2).text.splitlines() if l.strip()]
            sel = sorted([m for m in idx if m.get('levtype') == 'pl' and m.get('param') in ('u', 'v') and int(m.get('levelist', 0)) in CL],
                         key=lambda m: m['_offset'])
            if len(sel) < 2 * len(CL): raise IOError(f'색인 필드 부족 {len(sel)}')
            rng = []
            for m in sel:
                a, b = m['_offset'], m['_offset'] + m['_length'] - 1
                if rng and a <= rng[-1][1] + 1: rng[-1][1] = max(rng[-1][1], b)
                else: rng.append([a, b])
            with ThreadPoolExecutor(6) as ex:
                parts = list(ex.map(lambda r: M.http('GET', base + '.grib2', headers={'Range': f'bytes={r[0]}-{r[1]}'}).content, rng))
            with open(target, 'wb') as f:
                for c in parts: f.write(c)
            return src
        except Exception as e:
            err = e; log('과거분 실패 → 다음 저장소', run.strftime('%m-%d %HZ'), str(e)[:120])
    raise IOError(f'{run:%Y-%m-%d %HZ} 받기 실패: {err}')

def enc(sp, dr):
    """sp,dr: [지점][층] → 바이트"""
    a = np.empty(sp.shape + (2,), np.uint8)
    a[..., 0] = np.clip(np.round(sp), 0, 255); a[..., 1] = np.round(dr / 2).astype(int) % 180
    return a.tobytes()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', default='clim_out')
    ap.add_argument('--days', type=int, default=int(os.environ.get('WX_CLIM_DAYS', 60)))
    ap.add_argument('--max-new', type=int, default=240, help='한 번에 새로 받을 최대 시각 수(나눠 채우기)')
    ap.add_argument('--local', help='시험용: t_YYYYMMDDHH.grib2 파일 폴더')
    ap.add_argument('--now', help='시험용 기준 시각 YYYY-MM-DDTHH')
    a = ap.parse_args()
    routes = json.load(open(os.path.join(HERE, 'routes.json'), encoding='utf-8'))['routes']
    allp = [x for R in routes.values() for x in R['p']]
    bb = (min(p[0] for p in allp) - 1, max(p[0] for p in allp) + 1, min(p[1] for p in allp) - 1, max(p[1] for p in allp) + 1)
    cdir = os.path.join(a.out, 'c'); os.makedirs(cdir, exist_ok=True)
    now = dt.datetime.strptime(a.now, '%Y-%m-%dT%H') if a.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    last = (now - dt.timedelta(hours=9)).replace(minute=0, second=0, microsecond=0)
    last = last.replace(hour=last.hour - last.hour % 12)
    want = [last - dt.timedelta(hours=12 * i) for i in range(a.days * 2)]
    wts = {M.run_ts(t) for t in want}
    # 기존 파일 읽기
    have = {}
    for key, R in routes.items():
        f = os.path.join(cdir, key + '.json'); have[key] = {}
        if not os.path.exists(f): continue
        try:
            j = json.load(open(f, encoding='utf-8'))
            if j.get('p') != R['p'] or j.get('lev') != CL: log('지점 변경 — 다시 채움', key); continue
            raw = np.frombuffer(base64.b64decode(j['b64']), np.uint8).reshape(len(j['t']), len(R['p']), len(CL), 2)
            for i, t in enumerate(j['t']):
                if t in wts: have[key][t] = raw[i].tobytes()
        except Exception as e:
            log('기존 파일 읽기 실패', key, e)
    need = [t for t in want if any(M.run_ts(t) not in have[k] for k in routes)][:a.max_new]
    log(f'목표 {len(want)}개 시각 · 새로 받을 {len(need)}개')
    work = '/tmp/clim'; os.makedirs(work, exist_ok=True); wcache = {}; ok = fail = 0
    for t in need:
        ts = M.run_ts(t)
        try:
            if a.local:
                f = os.path.join(a.local, f't_{t:%Y%m%d%H}.grib2')
                if not os.path.exists(f): raise IOError('시험 파일 없음')
            else:
                f = os.path.join(work, 't.grib2'); fetch_t(t, f)
            data, grid = M.read([f], bb)
            gk = grid.key()
            for key, R in routes.items():
                if ts in have[key]: continue
                ck = (key, gk)
                if ck not in wcache: wcache[ck] = grid.weights(R['p'])
                I, W = wcache[ck]; at = lambda arr: (arr[I] * W).sum(axis=1)
                sp, dr = [], []
                for l in CL:
                    u = at(data[(0, 'u', l)]); v = at(data[(0, 'v', l)])
                    sp.append(np.hypot(u, v) * M.KN); dr.append((270 - np.degrees(np.arctan2(v, u))) % 360)
                have[key][ts] = enc(np.array(sp).T, np.array(dr).T)
            ok += 1; log(f'받음 {t:%m-%d %HZ} ({ok}/{len(need)})')
        except Exception as e:
            fail += 1; log(f'건너뜀 {t:%m-%d %HZ}: {str(e)[:120]}')
    n = []
    for key, R in routes.items():
        ts = sorted(t for t in have[key] if t in wts)
        if not ts: continue
        b = b''.join(have[key][t] for t in ts)
        json.dump({'v': 1, 'key': key, 'src': 'ECMWF IFS 0.25° 분석(00Z·12Z)', 'lev': CL, 'p': R['p'], 't': ts, 'b64': base64.b64encode(b).decode()},
                  open(os.path.join(cdir, key + '.json'), 'w', encoding='utf-8'), separators=(',', ':'))
        n.append(len(ts))
    json.dump({'v': 1, 'made': int(time.time()), 'days': a.days, 'lev': CL, 'routes': len(n), 'tMin': min(n) if n else 0, 'tMax': max(n) if n else 0,
               'newOk': ok, 'newFail': fail}, open(os.path.join(cdir, 'index.json'), 'w'), ensure_ascii=False)
    log(f'노선 {len(n)}개 저장 · 시각 수 {min(n) if n else 0}~{max(n) if n else 0} · 이번 실행 성공 {ok} 실패 {fail}')

if __name__ == '__main__':
    main()
