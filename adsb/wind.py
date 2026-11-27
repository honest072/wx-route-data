"""ADS-B 실측 바람(항공기가 보낸 풍향·풍속·외기온도) → ① 얇은 층 바람 급변 통계 ② 모델 검증용 관측 표본
사용: python3 -I wind.py d1025 d1026 ...  →  shear.json, obs.json
· 관측: trace 상세(dict)의 wd·ws·oat (선회 중 롤 5° 이상, 지상속도 80kt 미만 제외)
· 얇은 층 급변: 같은 항공기의 상승·강하 중(5,000ft 이상) 고도 1,500~3,000ft 차이·5분 이내 두 지점(각각 앞뒤 3개 관측 벡터 평균)의
  (수평비행 연속 관측 차이 95% 4.4kt — 1,500ft 미만 간격은 관측 잡음만으로 SR 7을 넘어 제외)
  바람 벡터 차이 ÷ 고도 차 × 1,000 = SR(kt/1,000ft) — 7 이상이면 '급변', 10 이상이면 '강한 급변'
  → 위경도 1° 격자 × 고도대(5,000ft) 별 [판정 수, 7↑ 수, 10↑ 수]
· 검증 표본: 00·06·12·18Z ±30분 · 5,000~45,000ft · 항공기당 3분에 1개
"""
import sys, os, glob, json, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc

def uv(s, d): return (-s * math.sin(math.radians(d)), -s * math.cos(math.radians(d)))

def series(js):
    t0 = js.get('timestamp', 0); out = []
    for r in js.get('trace') or []:
        det = r[8] if len(r) > 8 and isinstance(r[8], dict) else None
        if not det or det.get('ws') is None or det.get('wd') is None: continue
        a = r[3] if isinstance(r[3], (int, float)) else None
        if a is None or a < 500: continue
        if abs(det.get('roll') or 0) >= 5 or (r[4] or 0) < 80: continue
        out.append((t0 + r[0], r[1], r[2], a, float(det['wd']), float(det['ws']), det.get('oat'), r[7] or 0))
    return out

def main(dirs):
    grid = collections.defaultdict(lambda: [0, 0, 0]); obs = []; nobs = 0; nac = 0
    for dd in dirs:
        for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
            try: js = proc.load(fp)
            except Exception: continue
            S = series(js)
            if not S: continue
            nac += 1; nobs += len(S)
            # 검증 표본
            last = -1e9
            for t, la, lo, a, wd, ws, oat, vr in S:
                h = (t % 86400) / 3600; dh = min(abs(h - x) for x in (0, 6, 12, 18, 24))
                if dh <= 0.5 and 5000 <= a <= 45000 and t - last >= 180:
                    obs.append([int(t), round(la, 3), round(lo, 3), int(a), int(wd), round(ws, 1), oat]); last = t
            # 얇은 층 급변
            V = [uv(x[5], x[4]) for x in S]
            def avg(i):
                j = [k for k in (i - 1, i, i + 1) if 0 <= k < len(S) and abs(S[k][0] - S[i][0]) <= 60 and abs(S[k][3] - S[i][3]) <= 600]
                return (sum(V[k][0] for k in j) / len(j), sum(V[k][1] for k in j) / len(j), len(j))
            i = 0
            while i < len(S) - 1:
                if abs(S[i][7]) < 500 or S[i][3] < 5000: i += 1; continue          # 상승·강하 중 · 5,000ft 이상
                j = i + 1
                while j < len(S) and S[j][0] - S[i][0] <= 300 and abs(S[j][3] - S[i][3]) < 1500: j += 1
                if j >= len(S) or S[j][0] - S[i][0] > 300: i += 1; continue
                dz = abs(S[j][3] - S[i][3])
                if dz > 3000: i += 1; continue
                a1, a2 = avg(i), avg(j)
                if a1[2] >= 3 and a2[2] >= 3:
                    sr = math.hypot(a2[0] - a1[0], a2[1] - a1[1]) / dz * 1000
                    mla, mlo, ma = (S[i][1] + S[j][1]) / 2, (S[i][2] + S[j][2]) / 2, (S[i][3] + S[j][3]) / 2
                    g = grid[f'{math.floor(mla)}_{math.floor(mlo)}_{int(ma // 5000) * 5}']
                    g[0] += 1; g[1] += sr >= 7; g[2] += sr >= 10
                i = j
    json.dump({'v': 1, 'grid': {k: v for k, v in grid.items() if v[0] >= 3}}, open('shear.json', 'w'), separators=(',', ':'))
    json.dump({'v': 1, 'cols': ['t', 'lat', 'lon', 'alt_ft', 'wd', 'ws_kt', 'oat_c'], 'obs': obs}, open('obs.json', 'w'), separators=(',', ':'))
    print(f'항공기 {nac} · 관측 {nobs} · 급변 판정 격자 {len(grid)} · 검증 표본 {len(obs)}')

if __name__ == '__main__':
    main(sys.argv[1:])
