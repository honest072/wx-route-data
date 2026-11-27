"""ADS-B 항적 → 공항별 실제 사용 활주로 방향(진방위 10° 단위) 통계
사용: python3 -I rwy.py d1025 d1026 ...  → rwy.json  {공항: {'arr': {방위: 편수}, 'dep': {방위: 편수}}}
· 도착: 공항 12NM 이내·고도 300~3,000ft 구간의 진행 방위(최종 접근)
· 출발: 공항 10NM 이내·고도 300~3,000ft 구간의 진행 방위(이륙 직후)
· 앱이 활주로 번호(자방위)로 바꿔 '최근 실제 사용 활주로'로 표시"""
import sys, os, glob, json, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
from proc import nm, AP, _brg
out = collections.defaultdict(lambda: {'arr': collections.Counter(), 'dep': collections.Counter()})
seen = set()
def hdg(pts):
    if len(pts) < 3: return None
    a, b = pts[0], pts[-1]
    if nm(a, b) < 1.5: return None
    return round((math.degrees(_brg(a, b)) % 360) / 10) * 10 % 360
for dd in sys.argv[1:]:
    for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
        try: js = proc.load(fp)
        except Exception: continue
        for seg in proc.segments(js):
            c = proc.classify(seg)
            if not c: continue
            o, d = (c['key'].split('-')[::-1] if c['rev'] else c['key'].split('-'))
            sid = (js.get('icao', fp), round(seg[0]['t'] / 600))
            if sid in seen: continue
            seen.add(sid)
            P = [(p['lat'], p['lon'], p['a'] or 0) for p in seg]
            arr = [(la, lo) for la, lo, a in P[-200:] if 300 <= a <= 3000 and nm((la, lo), AP[d]) <= 12]
            dep = [(la, lo) for la, lo, a in P[:200] if 300 <= a <= 3000 and nm((la, lo), AP[o]) <= 10]
            h = hdg(arr)
            if h is not None: out[d]['arr'][h] += 1
            h = hdg(dep)
            if h is not None: out[o]['dep'][h] += 1
json.dump({k: {'arr': dict(v['arr']), 'dep': dict(v['dep'])} for k, v in out.items()}, open('rwy.json', 'w'), ensure_ascii=False)
print('airports', len(out))
