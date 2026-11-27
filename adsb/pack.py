"""앱용 묶음: app.json = {period, made, PFX, PFA, CSM, RWY, SHR(실측 얇은 층 바람 급변 격자), RTE(대표 경로), n}"""
import json, sys, time, os
period = sys.argv[1]
def j(f, d=None):
    try: return json.load(open(f, encoding='utf-8'))
    except Exception: return d
fl = j('fl737.json', {}) or {}
app = {'v': 1, 'made': int(time.time()), 'period': period, 'src': '공개 ADS-B 항적(adsb.lol, ODbL)',
       'PFX': j('pfx_app.json', {}), 'PFA': j('pfa_app.json', {}), 'CSM': (fl.get('cs') or {}), 'RWY': j('rwy.json', {})}
sh = (j('shear.json', {}) or {}).get('grid', {})
tot = [sum(v[i] for v in sh.values()) for i in range(3)] if sh else [0, 0, 0]
app['SHR'] = {'grid': sh, 'base7': round(tot[1] / tot[0], 3) if tot[0] else None, 'n': tot[0]}
app['RTE'] = j('route.json', {}) or {}   # 노선·방향별 대표 경로(여러 편 중앙값) — 앱·계산 지점은 다음 앱 개정 때 함께 교체
app['n'] = {'routes': len(app['PFX']), 'airports': len(app['PFA']), 'flights': len(app['CSM']), 'paths': sum(1 for v in app['RTE'].values() if v.get('qual') == 'ok')}
json.dump(app, open('app.json', 'w'), ensure_ascii=False)
print(app['n'])
