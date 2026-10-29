"""Invented byte fixtures for receipt-reader tests; no browser execution claim."""
import base64
import hashlib
import struct
import zlib


def png(width,height,rgba):
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    rows=b''.join(b'\0'+rgba[y*width*4:(y+1)*width*4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')


def pixel_fixture(identity):
    encode=lambda b:base64.b64encode(b).decode()
    def scene(w,h):return bytearray(v for y in range(h) for x in range(w) for v in ((x*7+y*3)%180+20,(x*3+y*11)%180+20,(x*13+y*5)%180+20,255))
    def crop(raw,w,h,r):
        x,y,cw,ch=r;out=bytearray(cw*ch*4)
        for j in range(ch):
            for i in range(cw):
                if 0<=x+i<w and 0<=y+j<h:out[(j*cw+i)*4:(j*cw+i+1)*4]=raw[((y+j)*w+x+i)*4:((y+j)*w+x+i+1)*4]
        return out
    def crops(raw,w,h,canvas=False):
        rects=[[0,0,1,1],[w-1,0,1,1],[0,h-1,1,1],[w-1,h-1,1,1],[1,1,1,1],[0,7,1,9],[w-1,7,1,9],[8,0,9,1],[8,h-1,9,1],[11,13,18,16]]
        if canvas:rects.append([-1,-1,3,3])
        return [{'rect':r,'raw':encode(crop(raw,w,h,r))} for r in rects]
    value={'version':1,'errors':[],'canvas':{},'webgl':{}}
    for name in ('flat','text','gradient','embedded'):
        w,h=(64 if name=='flat' else 128),(96 if name=='embedded' else 64)
        raw=bytearray(bytes([137,73,91,255])*(w*h)) if name=='flat' else scene(w,h)
        if name=='embedded':
            for y in range(32,64):
                for x in range(64,104):raw[(y*w+x)*4:(y*w+x+1)*4]=bytes([137,73,91,255])
        if identity=='canvas_shardx' and name!='flat':raw[(20*w+20)*4]^=1
        row={'width':w,'height':h,'full':encode(raw),'repeat':encode(raw),'crops':crops(raw,w,h,True),'png':encode(png(w,h,raw))}
        if name=='flat':row['expected']=[137,73,91,255]
        if name=='embedded':row['flatRegion']={'rect':[66,34,36,28],'expected':[137,73,91,255],'raw':encode(bytes([137,73,91,255])*(36*28))}
        value['canvas'][name]=row
    for kind in ('webgl','webgl2'):
        raw=scene(64,64)
        if identity=='webgl_shardx':raw[(20*64+20)*4]^=1
        clear=encode(bytes([137,73,91,255])*4096)
        flipped=b''.join(raw[y*256:(y+1)*256] for y in reversed(range(64)))
        row={'supported':True,'dimensions':[64,64],'error':0,'clear':{'expected':[137,73,91,255],'full':clear,'repeat':clear},'full':encode(raw),'repeat':encode(raw),'crops':crops(raw,64,64),'png':encode(png(64,64,flipped))}
        if kind=='webgl2':
            packed=bytearray([165]*18528)
            for y in range(64):
                at=16+(2+y)*280+12;packed[at:at+256]=raw[y*256:(y+1)*256]
            row['pbo']={'layout':{'offset':16,'stride':280,'skipRows':2,'skipPixels':3,'total':18528,'sentinel':165},'raw':encode(packed),'unpacked':encode(raw),'unwrittenUntouched':True,'error':0,'state':{'alignment':8,'rowLength':69,'skipRows':2,'skipPixels':3,'pboBinding':True,'readFramebuffer':True,'readBuffer':True}}
        value['webgl'][kind]=row
    return value


def headless_fixture(identity):
    scales={'headless_custom_dpr2':2,'headless_custom_fractional':1.25,'headless_explicit_info':1.5,'headless_force_scale':1.5}
    scale=scales.get(identity,1);width=height=int(64*scale);image=png(width,height,bytes([255]*width*height*4))
    command='chrome --headless=new'
    if identity.startswith('headless_custom_'):command+=' --screen-info={1280x960 devicePixelRatio='+str(scale)+'}'
    elif identity=='headless_explicit_info':command+=' --screen-info={960x720 devicePixelRatio=1.5}'
    elif identity=='headless_force_scale':command+=' --force-device-scale-factor=1.5'
    return {'version':1,'page':{'screen':{'width':640,'height':480,'availWidth':640,'availHeight':440},'dpr':scale,'innerWidth':64,'innerHeight':64},'layout':{'cssVisualViewport':{'clientWidth':64,'clientHeight':64}},'effectiveCommandLine':command,'screenshot':{'width':width,'height':height,'bytes':len(image),'sha256':hashlib.sha256(image).hexdigest(),'png':base64.b64encode(image).decode()}}
