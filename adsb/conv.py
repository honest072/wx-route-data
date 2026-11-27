# pfx2/pfa2 → 앱 형식. 상승 시각은 '2,000ft 이하 첫 관측' 기준이라 +1.0분(이륙 시점 보정)
import json
X=json.load(open('pfx2.json'));A=json.load(open('pfa2.json'))
def adj(o):
    o=dict(o)
    for k in ('c10','c25','c75','toc','toc35'):
        if k in o: o[k]=round(o[k]+1.0,1)
    for k in ('cci','kci'):
        if k in o: o[k]=[round(x+1.0,1) for x in o[k]]
    return o
px={}
for k,o in X.items():
    o=adj(o)
    if o.get('n',0)<5:   # 강하 표본 5편 미만은 노선값으로 쓰지 않음(공항 묶음·일반값 사용)
        for f in ('tod','p25','p75','n','ten','e25','e75','tci','eci'): o.pop(f,None)
    if o.get('cn',0)<3:
        for f in ('c10','c25','c75','cn','cci'): o.pop(f,None)
    if o.get('kn',0)<3:
        for f in ('toc','kn','kci'): o.pop(f,None)
    if o: px[k]=o
pa={k:adj(o) for k,o in A.items()}
for k,o in list(pa.items()):
    if o.get('kn',0)<5: o.pop('toc35',None); o.pop('kn',None); o.pop('kci',None)
    if k.startswith('arr:') and o.get('n',0)<5:
        for f in ('tod35','p25','p75','n','tci'): o.pop(f,None)
json.dump(px,open('pfx_app.json','w'),ensure_ascii=False);json.dump(pa,open('pfa_app.json','w'),ensure_ascii=False)
print(len(px),len(pa))
