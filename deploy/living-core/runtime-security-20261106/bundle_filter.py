"""Remove only exact authenticated Debian root DER fingerprints, preserve other bytes."""
import re,ssl,hashlib
CERT=re.compile(rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----(?:\r?\n)?")
def remove_blacklisted_roots(bundle,blacklist):
    if not isinstance(bundle,bytes) or not blacklist:raise ValueError('Explicit byte bundle and blacklist required')
    pieces=[];offset=0;removed=[];seen={h:0 for h in blacklist}
    for match in CERT.finditer(bundle):
        between=bundle[offset:match.start()]
        if between.strip():raise ValueError('Unexpected non-certificate bundle content')
        der=ssl.PEM_cert_to_DER_cert(match.group().decode('ascii'));digest=hashlib.sha256(der).hexdigest()
        if digest in blacklist:seen[digest]+=1;removed.append(digest);pieces.append(between)
        else:pieces.extend([between,match.group()])
        offset=match.end()
    if bundle[offset:].strip():raise ValueError('Unexpected trailing bundle content')
    pieces.append(bundle[offset:])
    if any(count!=1 for count in seen.values()):raise ValueError('Each authenticated removed root must occur exactly once')
    return b''.join(pieces),removed
