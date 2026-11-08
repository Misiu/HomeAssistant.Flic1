import re,sys
d=open(sys.argv[1],'rb').read()
minlen=int(sys.argv[2]) if len(sys.argv)>2 else 5
for m in re.finditer(rb'[\x20-\x7e]{%d,}'%minlen,d):
    print('%x'%m.start(), m.group().decode())
