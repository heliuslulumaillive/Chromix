"""Compile and publish signed, immutable country/font data without a kernel build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid

from tools.google_services_inputs import plain, write_if_changed

MAGIC=b'SXFONT2\0'


def encode(document,version):
    assert re.fullmatch(r'[!-~]{1,64}',version)
    countries,groups=document['countries'],document['groups']
    assert 0<len(countries)<=512 and 0<len(groups)<=512
    unique={}
    for name,assets in groups.items():
        assert re.fullmatch(r'[a-z][a-z0-9-]{0,95}',name) and 0<len(assets)<=128
        assert len({a['sha256'] for a in assets})==len(assets)
        assert 0<sum(a['kind']=='font' for a in assets)<=64 and any(a['kind']=='license' for a in assets)
        assert sum(a['size'] for a in assets)<=512*1024*1024
        for asset in assets:
            assert re.fullmatch('[a-f0-9]{64}',asset['sha256']) and asset['file']==asset['sha256']+'.blob'
            assert asset['kind'] in ('font','license') and type(asset['size']) is int and 0<asset['size']<128*1024*1024
            identity={k:asset[k] for k in ('sha256','file','kind','size')}
            assert asset['sha256'] not in unique or unique[asset['sha256']]==identity
            unique[asset['sha256']]=identity
    assert 0<len(unique)<=4096
    assets=sorted(unique.values(),key=lambda a:a['sha256']);indexes={a['sha256']:i for i,a in enumerate(assets)}
    names=sorted(groups);group_indexes={name:i for i,name in enumerate(names)}
    result=bytearray(MAGIC+struct.pack('<5H',2,len(assets),len(groups),len(countries),len(version))+version.encode('ascii'))
    for asset in assets:
        result.extend(struct.pack('<BQ',asset['kind']=='font',asset['size'])+bytes.fromhex(asset['sha256']))
    for name in names:
        members=sorted(indexes[a['sha256']] for a in groups[name])
        result.extend(struct.pack('<B',len(name))+name.encode('ascii')+struct.pack('<H',len(members)))
        result.extend(struct.pack('<'+'H'*len(members),*members))
    for country,group in sorted(countries.items()):
        assert re.fullmatch('[A-Z]{2}',country) and group in group_indexes
        result.extend(country.encode('ascii')+struct.pack('<H',group_indexes[group]))
    assert len(result)<=1024*1024
    return bytes(result),assets


def publish(manifest,store,version,key,signer,powershell,activate):
    document=json.loads(plain(manifest).read_bytes())
    payload,assets=encode(document,version)
    root=plain(store);objects=root/'objects';plain(objects)
    for asset in assets:
        file=plain(objects/asset['file'])
        assert file.is_file() and file.stat().st_size==asset['size']
        assert hashlib.file_digest(file.open('rb'),'sha256').hexdigest()==asset['sha256']
    identity=hashlib.sha256(payload).hexdigest();generation=plain(root/'catalogs'/identity)
    generation.mkdir(parents=True,exist_ok=True)
    file=generation/'catalog.bin'
    if file.exists():
        assert file.read_bytes()==payload
    else:
        with file.open('xb') as stream:stream.write(payload)
    subprocess.run([str(powershell),'-NoProfile','-File',str(signer),'-Mode','Sign','-PublicKeyFile',str(plain(key)),
                    '-PayloadFile',str(file),'-SignatureFile',str(generation/'catalog.sig')],check=True)
    if activate:
        pointer={'schemaVersion':2,'catalogSHA256':identity}
        content=(json.dumps(pointer,sort_keys=True)+'\n').encode()
        current=root/'current.json';plain(current)
        if not current.exists() or current.read_bytes()!=content:
            previous=root/'previous.json'
            if current.exists():write_if_changed(previous,current.read_bytes())
            temporary=root/('.current-'+uuid.uuid4().hex)
            try:
                with temporary.open('xb') as stream:stream.write(content)
                os.replace(temporary,current)
            finally:temporary.unlink(missing_ok=True)
    return {'status':'published' if activate else 'signed-generation-ready','protocol':2,'version':version,
            'catalogSHA256':identity,'generation':str(generation),'countries':len(document['countries']),
            'groups':len(document['groups']),'assets':len(assets),'kernelBuildRequired':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--store',type=Path,required=True)
    p.add_argument('--version',required=True);p.add_argument('--public-key',type=Path,required=True)
    p.add_argument('--powershell',type=Path,required=True);p.add_argument('--activate',action='store_true')
    a=p.parse_args()
    print(json.dumps(publish(a.manifest,a.store,a.version,a.public_key,Path(__file__).with_name('font_catalog_sign.ps1'),a.powershell,a.activate)))
