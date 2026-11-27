"""ADS-B 항적 → 노선·방향별 대표 경로(여러 편 중앙값)  → route.json
사용: python3 -I route.py d1101 d1102 ...
- 대표 한 편(medoid) 대신, 대권 기준 진행 구간 40칸마다 '편별 측면 거리'의 중앙값을 이어 경로를 만듦
  → 그날 한 편의 기상 회피·관제 지시 흔적이 들어가지 않음
- 정제: B737 계열, 경로 길이 > 대권×1.35+40NM 제외(두 구간이 이어진 항적), 중앙 경로에서 평균 12NM 넘게 벗어난 편 제외 후 재계산
- 양끝 4%(이착륙 활주로 방향에 따라 갈리는 구간)는 비움 — 앱이 공항 좌표로 잇는다
- 두 갈래 이상(편별 퍼짐 중앙값 15NM 초과)인 노선은 판정 보류(qual=split)
"""
import sys, os, glob, json, math, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
from proc import nm, AP, _atxt, _dest, _brg

T737 = {'B738', 'B38M', 'B737', 'B739', 'B39M', 'B37M'}
NB, LO, HI = 40, 0.04, 0.96


def plen(pts):
    return sum(nm(pts[i][:2], pts[i + 1][:2]) for i in range(len(pts) - 1))


def prof(A, B, L, pts):
    """편 하나: 칸별 측면 거리 중앙값(NM, 왼쪽 −)"""
    bins = [[] for _ in range(NB)]
    for p in pts:
        at, xt = _atxt(A, B, p[:2])
        f = at / L
        if LO <= f <= HI:
            bins[min(NB - 1, int((f - LO) / (HI - LO) * NB))].append(xt)
    return [statistics.median(b) if b else None for b in bins]


def consensus(P, need):
    out = []
    for i in range(NB):
        v = [q[i] for q in P if q[i] is not None]
        out.append(statistics.median(v) if len(v) >= need else None)
    return out


def smooth(c):
    """3칸 이동 중앙값 → 3칸 이동 평균(잔흔들림 제거, 꺾임은 유지)"""
    def med3(a):
        return [statistics.median([x for x in a[max(0, i - 1):i + 2] if x is not None]) if a[i] is not None else None for i in range(len(a))]
    def avg3(a):
        return [sum(x for x in a[max(0, i - 1):i + 2] if x is not None) / len([x for x in a[max(0, i - 1):i + 2] if x is not None]) if a[i] is not None else None for i in range(len(a))]
    return avg3(med3(c))


def fill(c):
    k = [i for i, v in enumerate(c) if v is not None]
    if not k:
        return c
    o = list(c)
    for i in range(k[0], k[-1] + 1):
        if o[i] is None:
            lo = max(j for j in k if j < i); hi = min(j for j in k if j > i)
            o[i] = c[lo] + (c[hi] - c[lo]) * (i - lo) / (hi - lo)
    return o


def main(dirs):
    fl = {}
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
            try:
                js = proc.load(fp)
            except Exception:
                continue
            if js.get('t', '') not in T737:
                continue
            for seg in proc.segments(js):
                c = proc.classify(seg)
                if not c:
                    continue
                o, d = c['key'].split('-')
                key = f'{d}-{o}' if c['rev'] else c['key']
                a, b = key.split('-')
                pts = [(p[0], p[1]) for p in c['pts']]  # 비행 순서(출발→도착)
                gc = nm(AP[a], AP[b])
                if plen(pts) > gc * 1.35 + 40:
                    continue
                fl.setdefault(key, []).append((c['cs'], pts))
    out = {}
    for key, F in sorted(fl.items()):
        a, b = key.split('-'); A, B = AP[a], AP[b]; L = nm(A, B)
        P = [prof(A, B, L, p) for _, p in F]
        P = [q for q in P if sum(v is not None for v in q) >= NB * 0.4]
        if len(P) < 8:
            out[key] = {'n': len(P), 'qual': 'few'}
            continue
        need = max(4, int(len(P) * 0.3))
        c0 = consensus(P, need)
        dev = lambda q, c: statistics.mean([abs(x - y) for x, y in zip(q, c) if x is not None and y is not None] or [99])
        keep = [q for q in P if dev(q, c0) <= 12]
        if len(keep) >= 8:
            P = keep
            need = max(4, int(len(P) * 0.3))
        c = fill(smooth(consensus(P, need)))
        cov = sum(v is not None for v in c) / NB
        sp = statistics.median([dev(q, c) for q in P])
        qual = 'ok' if cov >= 0.7 and sp <= 15 else ('split' if sp > 15 else 'gap')
        brg = _brg(A, B); w = []
        for i in range(0, NB, 2):
            if c[i] is None:
                continue
            f = LO + (i + 0.5) / NB * (HI - LO)
            on = _dest(A, brg, f * L)
            p = _dest(on, _brg(on, B) + math.pi / 2, c[i])
            w.append([round(p[0], 3), round(p[1], 3)])
        out[key] = {'n': len(P), 'jja': sum(1 for cs, _ in F if cs.startswith('JJA')), 'cov': round(cov, 2), 'sp': round(sp, 1), 'qual': qual, 'w': w}
    json.dump(out, open('route.json', 'w'), ensure_ascii=False)
    ok = sum(1 for v in out.values() if v.get('qual') == 'ok')
    print(f'route.json: {len(out)} 방향, 사용 가능 {ok}')


if __name__ == '__main__':
    main(sys.argv[1:])
