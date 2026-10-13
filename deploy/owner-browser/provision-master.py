#!/usr/bin/env python3
from pathlib import Path
import os,stat
root=Path(__file__).resolve().parent
assert os.geteuid()==0
source=root/'secrets/viewer-master-token';target=root/'secrets/viewer-master-token-root'
value=source.read_bytes();assert 32<=len(value)<=1024
if target.exists():
 info=target.lstat();assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o400
 assert target.read_bytes()==value
else:
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
 with os.fdopen(fd,'wb') as output:output.write(value);output.flush();os.fsync(output.fileno())
print('Root-owned FD source is provisioned; no credential printed.')
