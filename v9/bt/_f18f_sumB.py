import re
txt=open('/root/v9/bt/_f18f_partB.out').read()
blocks=txt.split('### PART B')
row_re=re.compile(r'^    (.+?)\s+n=\s*(\d+) PF=\s*(\S+) net\$=\s*(-?[\d,]+) sumR=\s*(\S+) win%=\s*(\S+) DD=\s*(-?[\d,]+)')
hdr_re=re.compile(r'^  (\w+)  \(n=')
for lbl,sec in [('FULL',blocks[1]),('RECENT',blocks[2])]:
    d={}; cur=None
    for ln in sec.splitlines():
        h=hdr_re.match(ln)
        if h: cur=h.group(1); d[cur]=[]; continue
        m=row_re.match(ln)
        if m and cur:
            nm,n,pf,net,sr,win,dd=m.groups()
            d[cur].append((nm.strip(),int(n),float(pf) if pf not in('inf','nan') else 0,int(net.replace(',','')),dd))
    print('\n##########',lbl)
    for slv,rs in d.items():
        nat=next(r for r in rs if r[0]=='NATIVE')
        beats=[r for r in rs if r[0]!='NATIVE' and r[3]>nat[3] and r[2]>nat[2]]
        bestnet=max((r for r in rs if r[0]!='NATIVE'),key=lambda r:r[3])
        print(f'{slv:14} NATIVE PF={nat[2]:.2f} net={nat[3]:,} DD={nat[4]} | bestNet={bestnet[0]} PF={bestnet[2]:.2f} net={bestnet[3]:,} | #beat(PF&net):{len(beats)}')
        for b in sorted(beats,key=lambda r:-r[3])[:4]:
            print(f'      +{b[0]:14} PF={b[2]:.2f} net={b[3]:,} DD={b[4]}')
