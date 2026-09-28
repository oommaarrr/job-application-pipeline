import re,sys
css=open(sys.argv[1]).read()
light=dict(re.findall(r'(--[\w-]+):\s*(#[0-9a-fA-F]{6})',css.split('@media (prefers-color-scheme: dark)')[0]))
dark=dict(light); dark.update(dict(re.findall(r'(--[\w-]+):\s*(#[0-9a-fA-F]{6})',css.split(':root[data-theme="dark"]')[1].split('}')[0])))
def L(h):
    c=[int(h[i:i+2],16)/255 for i in (1,3,5)]
    c=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in c]
    return .2126*c[0]+.7152*c[1]+.0722*c[2]
def cr(a,b):
    a,b=L(a),L(b); return (max(a,b)+.05)/(min(a,b)+.05)
fails=0
for name,t in (('light',light),('dark',dark)):
    checks=[]
    for fg in ['ink','dim','accent']:
        for bg in ['card','bg','bg2']: checks.append((fg,bg,4.5))
    checks.append(('accent','accent-bg',4.5)); checks.append(('accent-ink','accent',4.5))
    for s in ['ok','warn','bad','info','teal','idle']:
        checks+= [(s,'card',4.5),(s,s+'-bg',4.5),(s,'bg',4.5)]
    checks+=[('on-color','ok-solid',4.5),('on-color','bad-solid',4.5),('log-ink','log-bg',4.5),('log-dim','log-bg',4.5),('log-err','log-bg',4.5)]
    checks+=[('line-strong','card',1.0)]
    for s in ['ok','warn','bad','accent','info','teal']: checks.append((s,'bg2',3.0))
    for fg,bg,need in checks:
        r=cr(t['--'+fg],t['--'+bg]); flag='' if r>=need else '  <-- FAIL'
        if flag: fails+=1
        print(f"{name:5} {fg:11} on {bg:10} {r:5.2f}{flag}")
print('fails',fails)
