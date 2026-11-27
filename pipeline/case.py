"""7C Turbulence 과거 사례 재현 (GH Rev.6)
지정한 과거 시각(UTC)에 대해 '그때 앱이 받았을' 발표분들의 노선 파일을 지금 파이프라인과 같은 형식으로 만듦.
· 발표분 선택(기본): 브리핑 시점(사례 시각 2시간 전)에 실제로 받을 수 있었던 최신 IFS 발표분(공개 지연 약 8시간 반영)
  + 그 하루 전 00/12Z + 이틀 전 00/12Z + 사후 최선(사례 직전 00/12Z, 당시엔 아직 없던 자료)
· ECMWF 과거분(AWS·구글, 2024.4~) · AIFS · GFS(과거 보관 범위 안일 때만) — 못 받는 모델은 빼고 만듦
출력: <out>/<사례ID>/<발표분 YYYYMMDDHH>/manifest.json + r/<노선>.json, <out>/<사례ID>/case.json
앱: 주소 뒤에 ?gh=<…/case/<사례ID>/<발표분>> 를 붙여 열면 그 자료로 같은 계산을 함
"""
import argparse, json, os, sys, shutil, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import make as M
log = M.log

def runs_for(at):
    lag = dt.timedelta(hours=8)                      # ECMWF 공개 지연(대략)
    brief = at - dt.timedelta(hours=2)
    def floor6(t): return t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 6)
    def floor12(t): return t.replace(minute=0, second=0, microsecond=0, hour=t.hour - t.hour % 12)
    avail = floor6(brief - lag)
    best = floor12(at)
    out = [('브리핑 시점 최신', avail), ('하루 전', floor12(at - dt.timedelta(hours=24))), ('이틀 전', floor12(at - dt.timedelta(hours=48))), ('사후 최선', best)]
    seen, res = set(), []
    for lab, r in out:
        if r not in seen: seen.add(r); res.append((lab, r))
    return res

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--at', required=True, help='사례 시각 UTC, 예 2026-08-22T05:24')
    ap.add_argument('--routes', required=True, help='노선(쉼표), 예 GMP-CJU,CJU-GMP'); ap.add_argument('--id', required=True)
    ap.add_argument('--out', default='case_out'); ap.add_argument('--after', type=float, default=8, help='사례 시각 뒤로 덮을 시간(h)')
    a = ap.parse_args()
    at = dt.datetime.strptime(a.at, '%Y-%m-%dT%H:%M')
    allr = json.load(open(os.path.join(HERE, 'routes.json'), encoding='utf-8'))['routes']
    routes = {k: allr[k] for k in a.routes.split(',') if k in allr}
    if not routes: raise SystemExit('노선을 찾지 못함: ' + a.routes)
    allp = [x for R in routes.values() for x in R['p'] + R['o']]
    bb = (min(p[0] for p in allp) - 2, max(p[0] for p in allp) + 2, min(p[1] for p in allp) - 2, max(p[1] for p in allp) + 2)
    base = os.path.join(a.out, a.id); os.makedirs(base, exist_ok=True); info = []
    for lab, run in runs_for(at):
        smax = int(((at + dt.timedelta(hours=a.after) - run).total_seconds() / 3600 + 2.999) // 3 * 3)
        M.STEPS[:] = list(range(0, smax + 1, 3)); M.AIFS_STEPS[:] = list(range(0, smax + 6, 6))
        work = f'/tmp/case/{run:%Y%m%d%H}'; os.makedirs(work, exist_ok=True)
        log(f'== {lab}: {run:%Y-%m-%d %HZ} (사례까지 {(at - run).total_seconds() / 3600:.0f}시간) · 단계 0~{smax}h')
        try:
            files = M.download(run, M.SOURCES[0], M.streams(run.hour)[0], work)
            data, grid = M.read(files, bb)
        except Exception as e:
            log('IFS 받기 실패 — 이 발표분 건너뜀:', e); info.append({'label': lab, 'run': M.run_ts(run), 'ok': 0, 'why': str(e)[:160]}); continue
        gfs = aifs = None; M.GFS_ERR = M.AIFS_ERR = ''
        try:
            gf = M.gfs_download(run, M.GFS_SOURCES[0], work); gd, gg = M.read(gf, bb); M.gfs_check_missing(gd); gfs = (gd, gg, run)
        except (Exception, SystemExit) as e: M.GFS_ERR = f'GFS 과거분 없음: {str(e)[:120]}'; log(M.GFS_ERR)
        try:
            af = M.aifs_download(run, M.SOURCES[0], work); ad, ag = M.read(af, bb); aifs = (ad, ag, run)
        except (Exception, SystemExit) as e: M.AIFS_ERR = f'AIFS 과거분 없음: {str(e)[:120]}'; log(M.AIFS_ERR)
        out = os.path.join(base, f'{run:%Y%m%d%H}')
        M.build(data, grid, routes, run, out, gfs, aifs, None)
        info.append({'label': lab, 'run': M.run_ts(run), 'runIso': run.strftime('%Y-%m-%dT%H:00Z'), 'lead': round((at - run).total_seconds() / 3600, 1),
                     'dir': f'{run:%Y%m%d%H}', 'gfs': bool(gfs), 'aifs': bool(aifs), 'ok': 1})
        shutil.rmtree(work, ignore_errors=True)
    json.dump({'id': a.id, 'at': a.at, 'routes': list(routes), 'runs': info}, open(os.path.join(base, 'case.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    log('완료', json.dumps(info, ensure_ascii=False))

if __name__ == '__main__':
    main()
