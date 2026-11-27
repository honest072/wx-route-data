import sys, os, glob, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proc
T737 = {'B738','B38M','B737','B739','B39M','B37M'}
fl = collections.defaultdict(collections.Counter); cs = collections.defaultdict(collections.Counter); types=collections.Counter()
for dd in sys.argv[1:]:
    for fp in glob.glob(os.path.join(dd, '**', 'trace_full_*'), recursive=True):
        try: js = proc.load(fp)
        except Exception: continue
        t = js.get('t', '')
        for seg in proc.segments(js):
            c = proc.classify(seg)
            if not c: continue
            if c['cs'].startswith('JJA'):
                d = c['key'] if not c['rev'] else '-'.join(c['key'].split('-')[::-1])
                cs[c['cs'].strip()][d] += 1
            if t not in T737: continue
            types[t] += 1
            alts = [p[2] for p in c['pts'] if p[2]]
            if not alts: continue
            mx = max(alts)
            # 순항: 최고고도 -1500ft 이내에 머문 점들의 최빈 1000ft 단위
            cr = collections.Counter(round(a/1000) for a in alts if a >= mx-1500)
            f = cr.most_common(1)[0][0]*10
            if 100 <= f <= 410: fl[c['key'] if not c['rev'] else '-'.join(c['key'].split('-')[::-1])][f] += 1
out = {'fl': {k: dict(sorted(v.items())) for k, v in fl.items()},
       'cs': {k: v.most_common(1)[0][0] for k, v in cs.items()}, 'types': dict(types)}
json.dump(out, sys.stdout, ensure_ascii=False)
