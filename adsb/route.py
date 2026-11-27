"""ADS-B 항적 → 노선·방향별 대표 경로(여러 편 중앙값) → route.json, rprof.json
사용: python3 -I route.py d1101 d1102 ...   (이전 주 누적분: prev/rprof.json 이 있으면 함께 사용)

방식
- 편마다 '대권 기준 진행 구간 40칸별 측면 거리(NM)'만 저장(rprof.json, 주마다 누적 · 같은 IATA 시즌 안에서만 사용)
- 대표 경로 = 칸별 편 중앙값 → 3칸 이동 중앙값·평균으로 잔흔들림 제거 (대표 한 편 방식의 지그재그 없음)
- 표본이 모자라면(정제 후 12편 미만, 관측으로 덮인 칸 80% 미만, 중간 빈 구간 80NM 초과) 기간을 14→28→42…일로 넓힘(시즌 시작일까지)
- 그래도 모자라면 국적 등록기(71x) 다른 기종(A320·A321 등)까지 포함 — 같은 항로를 탐
- 정제: 경로 길이 > 대권×1.35+40NM 제외(두 구간이 이어진 항적), 중앙 경로에서 평균 12NM 넘게 벗어난 편 제외(기상 회피·관제 지시)
- 두 갈래 노선: 편들을 2묶음으로 나눠 작은 묶음이 35% 이상이고 두 묶음이 15NM 넘게 떨어져 있으면 큰 묶음을 대표로, 갈래 비율(lanes)을 함께 기록
- 양끝 4%는 비움(활주로 방향에 따라 갈리는 이착륙 구간) — 앱이 공항 좌표로 잇는다
"""
import sys, os, glob, json, math, statistics, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
from proc import nm, AP, _atxt, _dest, _brg

T737 = {'B738', 'B38M', 'B737', 'B739', 'B39M', 'B37M'}
NB, LO, HI = 40, 0.04, 0.96
NEED, COV = 12, 0.8


def season_start(d):
    """IATA 시즌 시작일: 3월·10월 마지막 일요일"""
    def last_sun(y, m):
        x = dt.date(y, m, 31)
        return x - dt.timedelta(days=(x.weekday() + 1) % 7)
    s = last_sun(d.year, 3); w = last_sun(d.year, 10)
    if d >= w: return w
    if d >= s: return s
    return last_sun(d.year - 1, 10)


def plen(pts):
    return sum(nm(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def prof(A, B, L, pts):
    bins = [[] for _ in range(NB)]
    for p in pts:
        at, xt = _atxt(A, B, p)
        f = at / L
        if LO <= f <= HI:
            bins[min(NB - 1, int((f - LO) / (HI - LO) * NB))].append(xt)
    return [round(statistics.median(b)) if b else None for b in bins]


def consensus(P, need):
    out = []
    for i in range(NB):
        v = [q[i] for q in P if q[i] is not None]
        out.append(statistics.median(v) if len(v) >= need else None)
    return out


def smooth(c):
    """3칸 이동 중앙값(한 칸짜리 튐 제거) → 가벼운 가중 평균(1:2:1) — 실제 꺾임은 남기고 잔흔들림만 줄임"""
    def win(a, i): return [x for x in a[max(0, i - 1):i + 2] if x is not None]
    m = [statistics.median(win(c, i)) if c[i] is not None else None for i in range(len(c))]
    o = []
    for i in range(len(m)):
        if m[i] is None: o.append(None); continue
        a = m[i - 1] if i > 0 and m[i - 1] is not None else m[i]; b = m[i + 1] if i + 1 < len(m) and m[i + 1] is not None else m[i]
        v = (a + 2 * m[i] + b) / 4
        o.append(v if abs(v - c[i] if c[i] is not None else 0) <= 4 else m[i])   # 꺾임에서 4NM 넘게 깎이면 중앙값 그대로
    return o


def fill(c):
    k = [i for i, v in enumerate(c) if v is not None]
    if not k: return c
    o = list(c)
    for i in range(k[0], k[-1] + 1):
        if o[i] is None:
            lo = max(j for j in k if j < i); hi = min(j for j in k if j > i)
            o[i] = c[lo] + (c[hi] - c[lo]) * (i - lo) / (hi - lo)
    return o


def dev(q, c):
    d = [abs(x - y) for x, y in zip(q, c) if x is not None and y is not None]
    return statistics.mean(d) if len(d) >= 5 else 99


def two_groups(P):
    """간단한 2-메도이드: 가장 멀리 떨어진 두 편을 씨앗으로 3회 재배정"""
    if len(P) < 12: return None
    a = P[0]; b = max(P, key=lambda q: dev(q, a)); a = max(P, key=lambda q: dev(q, b))
    for _ in range(3):
        G1 = [q for q in P if dev(q, a) <= dev(q, b)]; G2 = [q for q in P if dev(q, a) > dev(q, b)]
        if not G1 or not G2: return None
        a = min(G1, key=lambda q: sum(dev(q, r) for r in G1)); b = min(G2, key=lambda q: sum(dev(q, r) for r in G2))
    return (G1, G2) if len(G1) >= len(G2) else (G2, G1)


def build(P):
    P0 = P
    c0 = consensus(P, max(4, int(len(P) * 0.3)))
    keep = [q for q in P if dev(q, c0) <= 12]
    if len(keep) >= max(8, len(P) * 0.5):
        P = keep
    cnt = [sum(q[i] is not None for q in P) for i in range(NB)]
    raw = consensus(P, max(4, int(max(cnt) * 0.25)))   # 칸별 기준: 가장 많이 관측된 칸의 25% 이상
    c = fill(smooth(raw))
    cov = sum(v is not None for v in raw) / NB          # 실제 관측으로 덮인 칸(채운 칸 제외)
    k = [i for i, v in enumerate(raw) if v is not None]; g = run = 0
    for i in range(k[0], k[-1] + 1) if k else []:
        run = run + 1 if raw[i] is None else 0; g = max(g, run)
    sp = statistics.median([dev(q, c) for q in P]) if P else 99
    # 채운 칸(관측 부족) 검증: 3편 이상 관측된 칸의 중앙값과 25NM 넘게 다르면 '불확실'(다른 경로를 타는 편이 있음)
    bad = 0
    for i in range(NB):
        if raw[i] is None and c[i] is not None:
            v = [q[i] for q in P0 if q[i] is not None]   # 정제 전 전체 편 기준
            if len(v) >= 3 and abs(statistics.median(v) - c[i]) > 25: bad += 1
    return c, cov, sp, len(P), g + (100 if bad else 0)


def to_w(A, B, L, c):
    brg = _brg(A, B); w = []
    for i in range(0, NB, 2):
        if c[i] is None: continue
        f = LO + (i + 0.5) / NB * (HI - LO)
        on = _dest(A, brg, f * L)
        p = _dest(on, _brg(on, B) + math.pi / 2, c[i])
        w.append([round(p[0], 3), round(p[1], 3)])
    return w


def collect(dirs):
    """새로 받은 항적 → {key: {편 id: [날짜, 737여부, 편명, 칸값…]}}"""
    out = {}
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
            try: js = proc.load(fp)
            except Exception: continue
            hexid = str(js.get('icao') or os.path.basename(fp)[11:17]).lower()
            if not hexid.startswith('71'):   # 국적 등록기만(외항사는 다른 항로를 탈 수 있음)
                continue
            is737 = 1 if js.get('t', '') in T737 else 0
            for seg in proc.segments(js):
                c = proc.classify(seg)
                if not c: continue
                o, d = c['key'].split('-')
                key = f'{d}-{o}' if c['rev'] else c['key']
                a, b = key.split('-')
                pts = [(p[0], p[1]) for p in c['pts']]
                if plen(pts) > nm(AP[a], AP[b]) * 1.35 + 40: continue
                t0 = seg[0]['t']; day = dt.datetime.fromtimestamp(t0 + 9 * 3600, dt.timezone.utc).strftime('%Y%m%d')
                out.setdefault(key, {})[f'{hexid}@{int(t0 // 600)}'] = [day, is737, c['cs']] + prof(AP[a], AP[b], nm(AP[a], AP[b]), pts)
    return out


def main(dirs, today=None):
    today = today or dt.date.today(); s0 = season_start(today)
    store = {}
    try: store = json.load(open('prev/rprof.json', encoding='utf-8')).get('f', {})
    except Exception: pass
    for k, F in collect(dirs).items():
        store.setdefault(k, {}).update(F)
    lim = s0.strftime('%Y%m%d')
    for k in list(store):
        store[k] = {i: v for i, v in store[k].items() if v[0] >= lim}
        if not store[k]: del store[k]
    json.dump({'v': 1, 'season': lim, 'made': int(dt.datetime.now(dt.timezone.utc).timestamp()), 'f': store}, open('rprof.json', 'w'), separators=(',', ':'))
    out = {}; days_all = max(1, (today - s0).days + 1)
    for key, F in sorted(store.items()):
        a, b = key.split('-'); A, B = AP[a], AP[b]; L = nm(A, B)
        rows = list(F.values()); res = None
        for only737 in (True, False):
            for W in [14, 28, 42, 56, 84, 120, 183]:
                W = min(W, days_all); since = (today - dt.timedelta(days=W)).strftime('%Y%m%d')
                P = [v[3:] for v in rows if v[0] >= since and (v[1] or not only737)]
                P = [q for q in P if sum(x is not None for x in q) >= NB * 0.4]
                if len(P) >= 8:
                    G = two_groups(P); lanes = None
                    if G:
                        g1, g2 = G; c1 = consensus(g1, max(3, len(g1) // 3)); c2 = consensus(g2, max(3, len(g2) // 3))
                        if len(g2) / len(P) >= 0.35 and dev(c1, c2) > 15:
                            lanes = [round(len(g1) / len(P), 2), round(len(g2) / len(P), 2)]; P = g1
                    c, cov, sp, n, gp = build(P)
                    res = (c, cov, sp, n, W, only737, lanes, gp)
                    if n >= NEED and cov >= COV and gp * L * (HI - LO) / NB <= 80: break
                if W >= days_all: break
            if res and res[3] >= NEED and res[1] >= COV and res[7] * L * (HI - LO) / NB <= 80: break
        if not res:
            out[key] = {'n': len(rows), 'qual': 'few'}; continue
        c, cov, sp, n, W, only737, lanes, gp = res
        # 사용 가능: 8편 이상 · 퍼짐 12NM 이하 · 실제 관측 칸 40% 이상 · 중간 빈 구간 80NM 이하
        # (양끝 빈 칸은 앱이 공항까지 곧게 이음 — 제주 부근 저고도처럼 수신이 안 되는 곳)
        gnm = round(gp * L * (HI - LO) / NB)   # 중간 빈 구간 길이(NM) — 그 사이는 곧게 이음
        if gp >= 100: out[key] = {'n': n, 'days': W, 'qual': 'unsure', 'cov': round(cov, 2), 'sp': round(sp, 1)}; continue   # 공백 구간을 다른 경로로 지나는 편이 있음 → 기존 경로 유지
        qual = ('ok' if n >= 8 and cov >= 0.4 and gnm <= 80 and sp <= 12 else
                'part' if n >= 12 and cov >= 0.4 and sp <= 8 else          # 바다 위 등 수신 공백이 길어 그 구간은 관측점 사이 직선
                'wide' if sp > 12 else 'gap')
        o = {'n': n, 'days': W, 'b737': only737, 'cov': round(cov, 2), 'gapNM': gnm, 'sp': round(sp, 1), 'qual': qual, 'w': to_w(A, B, L, c)}
        if lanes: o['lanes'] = lanes
        out[key] = o
    json.dump(out, open('route.json', 'w'), ensure_ascii=False)
    ok = sum(1 for v in out.values() if v.get('qual') == 'ok')
    print(f'route.json: {len(out)} 방향, 사용 가능 {ok} · 누적 편 {sum(len(v) for v in store.values())} (시즌 시작 {lim})')


if __name__ == '__main__':
    td = os.environ.get('TODAY')
    main(sys.argv[1:], dt.date.fromisoformat(td) if td else None)
