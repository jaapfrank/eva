import json,pathlib,re,sys
root=pathlib.Path(__file__).parent;turn=sys.argv[1]
fixtures=json.loads((root/'fixtures.private.json').read_text());files=json.loads((root/turn/'files.private.json').read_text());text=files[turn]['text']
lines=text.removeprefix('\ufeff').replace('\r\n','\n').splitlines()
assert lines[0]=='WEBVTT' and len(lines)>1 and lines[1]=='', 'header'
blocks=[x.split('\n') for x in re.split(r'\n{2,}', '\n'.join(lines[2:]).strip()) if x]
def milliseconds(value):
 m=re.fullmatch(r'(?:(\d{2,}):)?(\d{2}):(\d{2})\.(\d{3})',value);assert m,'timestamp syntax'
 h,mi,se,ms=m.groups();assert int(mi)<60 and int(se)<60
 return ((int(h or 0)*60+int(mi))*60+int(se))*1000+int(ms)
cues=[]
for block in blocks:
 if '-->' not in block[0]:block=block[1:]
 assert block and block[0].count('-->')==1
 start,end=block[0].split(' --> ');a,b=milliseconds(start),milliseconds(end);assert b>a,'interval'
 cues.append({'start_ms':a,'end_ms':b,'lines':block[1:]})
assert all(cues[i]['start_ms']<=cues[i+1]['start_ms'] for i in range(len(cues)-1))
assert cues==fixtures[turn]['expected'], 'cue times or exact Unicode/text lines differ'
result={'turn':turn,'semantic_checks':'passed','cues':cues,'file_sha256':files[turn]['sha256'],'procedure_sha256':files['procedure']['sha256'],'checker':'Independent Python checker run by coordinator after Eva completed; not an Eva-executed test.'}
(root/turn/'semantic-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
