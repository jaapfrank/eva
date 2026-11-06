import sys,sysconfig,ssl,json,pathlib,hashlib,asyncio,socket,threading,datetime,tempfile,os,ctypes,importlib.metadata as md,zoneinfo
import sqlalchemy,_ssl,pyexpat,zlib
cert_pem=b'-----BEGIN CERTIFICATE-----\nMIIDHzCCAgegAwIBAgIUGFrRfNkt89ohzStXLkzHEKYy01gwDQYJKoZIhvcNAQEL\nBQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MTEwNjE4MzUyN1oXDTI2MTEw\nNzE4MzUyN1owFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF\nAAOCAQ8AMIIBCgKCAQEAwirQ0ZYxxApxnH5OATvWhSDVxzuoXZXF/CoMMln4JYwJ\noMYW8eBCv8+qRo0jnlNTBRSm0gN4ab6sESL03kgqIjRVGngGJNxDwXo2y7sYM9jC\nVJ91VS/cAm5GidWhz7VPvf6FNcFeyi6Si/tqG6p1TtXbAnJmPUfnUJYqWmwGt74F\nyWIOGhyFZZrkMH8snbY67Pq72xq8p4yyGJLqcNye07eJ5yqwqErAQUSHcbI1MFge\n/0XzmRmVtQVLPBBuFfbXaa9caJ8DDk3OYe+NS6Wp0IpayUNT6r2CC4FucJDbkEaa\nJkdsYjvYM0jzURvqqC0nrBymMDGLDzLyAjD+M6CHQQIDAQABo2kwZzAdBgNVHQ4E\nFgQUONedy1VblPdAiqKZwcmfPeVUnfYwHwYDVR0jBBgwFoAUONedy1VblPdAiqKZ\nwcmfPeVUnfYwDwYDVR0TAQH/BAUwAwEB/zAUBgNVHREEDTALgglsb2NhbGhvc3Qw\nDQYJKoZIhvcNAQELBQADggEBAJFbdMo+6oIXjI7RX5CbR8tBQ7+uBzOy3VolooF+\nkpmZp3hcf5vJb+pExITxtkO5medABjTMSgonS4ZwrPLa3dhl38nRvuywWkIibKD4\nN4JbZA3aGguS3DeZOfMyxikAIqWUdqR5AcBJsLBbx4oLsaw5dl5/aGavyNMlaz/7\nxUgxQOaAZsg/booxjk+hKJixbJ37nESciFaAIXARYs0kvGao5O4NoqQCpcWLs+SN\n43dW/YJLdlar5BRoMrK06dCjgWCclFCJDjxYLqTkA10axq5l7OJy1KUz31sPbDzi\n9rn9bqvKMS/Dmg2+IHkPu7t8gyISZfMUH3V1jZFZ6oNn7IE=\n-----END CERTIFICATE-----\n'
key_pem=b'-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDCKtDRljHECnGc\nfk4BO9aFINXHO6hdlcX8KgwyWfgljAmgxhbx4EK/z6pGjSOeU1MFFKbSA3hpvqwR\nIvTeSCoiNFUaeAYk3EPBejbLuxgz2MJUn3VVL9wCbkaJ1aHPtU+9/oU1wV7KLpKL\n+2obqnVO1dsCcmY9R+dQlipabAa3vgXJYg4aHIVlmuQwfyydtjrs+rvbGrynjLIY\nkupw3J7Tt4nnKrCoSsBBRIdxsjUwWB7/RfOZGZW1BUs8EG4V9tdpr1xonwMOTc5h\n741LpanQilrJQ1PqvYILgW5wkNuQRpomR2xiO9gzSPNRG+qoLSesHKYwMYsPMvIC\nMP4zoIdBAgMBAAECggEAAMW+RRyuEdDNshbmqxqmjjrWzeEx6K4mspO1XuaIqifT\nDpsmIogDFPezzn47j2LXCV3LQnnv/tnyzdpF94vJgt+C3IkwY1F24apRhSzHRkbH\nImJ8Y0p2i2IOXNhTMZAP3z5nz9KHNLpD5Aub6Nx0JTQ8p+c+CtGuQO3ZgRjFm7HT\nlACs6p+DCo5agyY+4hki/+TlezlpPPs0ypHu+i6ZoXDxjgvr6A6E+0it4BQP/9sZ\nJ3MsoXDhir9WRM33lx870SAh5XRegBeNCu9FO3myHnpxkHFd+TazJjdYwi2IzZcU\nFsqeFCTgplKBMHbJGmZYiIhDeuv8gKgHivbr8LCZAQKBgQD3pAShE4HHbIHFLTV1\ngAwCEbn2aOMa05Y0RWoAbRS8Km0/mkmw7JA08oHhF4GeeY+yO1xUlqOnbR8+w+12\nDEUPbzfY++S3BX5rv+b1NaZfBxyUEy4A1vXbInV6zByQYQxBLT81RZg+CMKvdgho\nyXejcYlRjNP+YBCzKpypefF6gQKBgQDIuLU7ZGKNDobvU4zxifYZxgeLXWVrYHW/\njkRz81HAEfF2dwBII6MvuULwO6C5ptK5nPVbBcNdQ1Kf+9E0xE5FL7FVAYBRKosS\nf9m6fLzj8iWYbY0q/EoiSGMW756gTbDrlnxcSj+W5sZyCTBYmf7TbBWySfvk2o0r\nTVHW3wUswQKBgQC6B6yylETZ2tlhliSisB5VL5vTT9fLP0eG3N5Uk50P/Y80SGMz\nVYK/Q63tugrVUdxwt8cUhFVMYN0n+WdZVr5KafI6PoACPU5LA04sxrDEkkMVuZLF\nbrYieDBonwyWKVFbZPImwrEraMlXpgOLNE17qr7NVE2sud3RuiEV71VIAQKBgD86\n2HSGMflneyBHPJ4WLL1/wX6ohDtoHo+iXHP+UWFlBaCg6ijPeSeef7S3w/DX2Yf5\nJt1umlV10MBvDj9GG7X1yTITD/4Bnd/NAKSbiNFxxWAYGqFQ6nWUQgrGnzy2XKZK\nOJDGHvQFbLwdouo2UKz/XnJUlhJIurHKYJ6EkNfBAoGAYnEnvfGRIT93TKaJqkO5\nUwsdXkNgqx6SLfprB0CoAXbeFOusHLrZ4yY4E9UrWkrN2iWM4iobesH2ZV5PQgeU\nSRjBvTbIdr1TiKfSktlJ2bbxJ9r8OYNTZE1g7x0JxRY3beiSybH5Ua4RAaqyw27J\nUl4izZxDZE1aDlfXT8AbOj0=\n-----END PRIVATE KEY-----\n'
def handshake(trusted):
 with tempfile.TemporaryDirectory() as d:
  c=pathlib.Path(d)/'cert';k=pathlib.Path(d)/'key';c.write_bytes(cert_pem);k.write_bytes(key_pem);k.chmod(0o600)
  server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(c,k)
  listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);address=listener.getsockname();errors=[]
  def serve():
   connection,_=listener.accept()
   try:
    with server.wrap_socket(connection,server_side=True) as stream:stream.sendall(b'synthetic-tls')
   except ssl.SSLError:pass
   finally:listener.close()
  thread=threading.Thread(target=serve);thread.start();context=ssl.create_default_context(cafile=str(c) if trusted else None)
  try:
   with socket.create_connection(address,timeout=5) as connection:
    with context.wrap_socket(connection,server_hostname='localhost') as stream:
     assert trusted;assert stream.recv(64)==b'synthetic-tls';return stream.version()
  except ssl.SSLCertVerificationError:
   assert not trusted;return 'untrusted_certificate_rejected'
  finally:thread.join(5);assert not thread.is_alive()

status={}
for p in pathlib.Path('/var/lib/dpkg/status.d').iterdir():
 if p.name.endswith('.md5sums'):continue
 fields={l.split(':',1)[0]:l.split(':',1)[1].strip() for l in p.read_text().splitlines() if l and not l.startswith(' ') and ':' in l}
 status[fields['Package']]=fields['Version']
libc=ctypes.CDLL(None);libc.gnu_get_libc_version.restype=ctypes.c_char_p
loaded={}
for line in pathlib.Path('/proc/self/maps').read_text().splitlines():
 parts=line.split()
 if len(parts)>5 and any(parts[-1].endswith('/'+n) for n in ['libssl.so.3','libcrypto.so.3','libc.so.6']):
  p=pathlib.Path(parts[-1]);loaded[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
packages=sorted((d.metadata['Name'],d.version) for d in md.distributions())
result={'python':sys.version,'soabi':sysconfig.get_config_var('SOABI'),'openssl':ssl.OPENSSL_VERSION,'glibc':libc.gnu_get_libc_version().decode(),'status':status,'loadedNativeLibraryHashes':loaded,'defaultCaDerSha256':sorted(hashlib.sha256(c).hexdigest() for c in ssl.create_default_context().get_ca_certs(binary_form=True)),'tlsTrusted':handshake(True),'tlsUntrusted':handshake(False),'packages':packages,'providerCalls':0,'databaseCalls':0,'zoneInfoEdmontonNovemberHours':datetime.datetime(2026,11,6,tzinfo=zoneinfo.ZoneInfo('America/Edmonton')).utcoffset().total_seconds()/3600,'zoneInfoAmsterdamNovemberHours':datetime.datetime(2026,11,6,tzinfo=zoneinfo.ZoneInfo('Europe/Amsterdam')).utcoffset().total_seconds()/3600}
assert status['libc6']=='2.41-12+deb13u4';assert status['libssl3t64']=='3.5.7-1~deb13u3';assert status['ca-certificates']=='20250419+deb13u1';assert result['zoneInfoEdmontonNovemberHours']==-6;assert result['zoneInfoAmsterdamNovemberHours']==1
print(json.dumps(result))
