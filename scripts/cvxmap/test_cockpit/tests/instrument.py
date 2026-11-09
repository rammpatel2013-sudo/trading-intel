import sys
src,out=sys.argv[1],sys.argv[2]; h=open(src,encoding='utf-8').read()
hook='\nwindow.__T={st,buildMap,mapRead,greekCol,greekColWeek,mapRowsAt,nearRows,weekIVShift,wkDayISO,liveClock,get SB(){return SB;},get TG(){try{return TG;}catch(e){return null;}},get MAP_T1(){return MAP_T1;},x:(s)=>eval(s)};\n})();\n</script>\n</body>'
assert h.count('\n})();\n</script>\n</body>')==1
open(out,'w',encoding='utf-8').write(h.replace('\n})();\n</script>\n</body>',hook))
