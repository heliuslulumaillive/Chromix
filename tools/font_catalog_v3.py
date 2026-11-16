"""Version 3 adds signed source selections to the immutable font catalog.

The data names allowed local Windows sources; it does not redistribute them.
Every group has required common sources, optional registered package variants,
and only its explicitly selected regional sources. No country or font pin is
compiled into the native loader.
"""
import hashlib
import re
import struct
from tools.font_catalog import encode as encode_additions


import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid
from tools.google_services_inputs import plain, write_if_changed

MAGIC = b'SXFONT3\0'
SOURCE_KINDS = {'core': 0, 'regional': 1, 'shared': 2}


def encode(document, version):
    assert 'US' in document['countries']
    payload, assets = encode_additions(document, version)
    result = bytearray(MAGIC + struct.pack('<H', 3) + payload[10:])
    view = document['nativeView']
    assert view['core'] and len(view['core']) <= 1024
    sources, shared, regions = [], [], {}

    def add(pin, kind, package=''):
        file = pin['file']
        assert re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,126}', file)
        assert '..' not in file and re.fullmatch(r'[a-f0-9]{64}', pin['sha256'])
        assert type(pin['size']) is int and 0 < pin['size'] < 128 * 1024 * 1024
        assert not package or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', package)
        assert kind != 'shared' or '_' in package
        source = dict(file=file, sha256=pin['sha256'], size=pin['size'], kind=kind, package=package)
        identity = (package.lower(), file.lower())
        assert all((s['package'].lower(), s['file'].lower()) != identity for s in sources)
        sources.append(source)
        return len(sources) - 1

    common = [add(pin, 'core') for pin in view['core']]
    for package in view['inboxPackages']:
        assert package['fonts'] and len(package['fonts']) <= 64
        shared.extend(add(pin, 'shared', package['package']) for pin in package['fonts'])
    for group, pins in sorted(view['regionalGroups'].items()):
        assert group in document['groups'] and pins and len(pins) <= 256
        regions[group] = [add(pin, 'regional') for pin in pins]
    assert 0 < len(sources) <= 4096
    result.extend(struct.pack('<H', len(sources)))
    for source in sources:
        file, package = source['file'].encode('ascii'), source['package'].encode('ascii')
        result.extend(struct.pack('<BB', SOURCE_KINDS[source['kind']], len(file)) + file)
        result.extend(struct.pack('<B', len(package)) + package)
        result.extend(struct.pack('<Q', source['size']) + bytes.fromhex(source['sha256']))
    selections = {}
    for group in sorted(document['groups']):
        selected = common + shared + regions.get(group, [])
        assert 0 < len(selected) <= 1024 and len(set(selected)) == len(selected)
        result.extend(struct.pack('<H', len(selected)))
        result.extend(struct.pack('<' + 'H' * len(selected), *selected))
        selections[group] = [sources[i] for i in selected]
    assert len(result) <= 1024 * 1024
    return bytes(result), assets, selections


def payload_identity(document, version):
    return hashlib.sha256(encode(document, version)[0]).hexdigest()


def publish(manifest,store,version,key,signer,powershell,activate):
    document=json.loads(plain(manifest).read_bytes())
    payload,assets,native_groups=encode(document,version)
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
        pointer={'schemaVersion':3,'catalogSHA256':identity}
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
    return {'status':'published' if activate else 'signed-generation-ready','protocol':3,'version':version,
            'catalogSHA256':identity,'generation':str(generation),'countries':len(document['countries']),
            'groups':len(document['groups']),'assets':len(assets),'kernelBuildRequired':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--store',type=Path,required=True)
    p.add_argument('--version',required=True);p.add_argument('--public-key',type=Path,required=True)
    p.add_argument('--powershell',type=Path,required=True);p.add_argument('--activate',action='store_true')
    a=p.parse_args()
    print(json.dumps(publish(a.manifest,a.store,a.version,a.public_key,Path(__file__).with_name('font_catalog_sign.ps1'),a.powershell,a.activate)))
