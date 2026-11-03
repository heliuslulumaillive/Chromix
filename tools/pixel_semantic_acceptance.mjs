// Extra acceptance of the selected ShardX pixel semantics. This does not claim
// provenance tracking for arbitrary putImageData/texImage arrays.
import crypto from 'node:crypto';

export const PIXEL_SCENARIOS = ['all_native','canvas_shardx','webgl_shardx'];
export const HEADLESS_SCENARIOS = ['headless_custom_dpr2','headless_custom_fractional','headless_explicit_info','headless_force_scale','headless_native_ignored','headless_off_ignored'];
export const CANVAS_SCENES = ['flat','text','gradient','embedded'];

// Evaluated only in the controller's loopback page; all GPU resources are owned
// and released here. Opaque RGBA8 default-framebuffer coverage is intentional.
export async function pixelSemanticProbe() {
  const b64=a=>{let s='';for(let i=0;i<a.length;i+=4096)s+=String.fromCharCode(...a.subarray(i,i+4096));return btoa(s);};
  const positions=(w,h)=>[[0,0,1,1],[w-1,0,1,1],[0,h-1,1,1],[w-1,h-1,1,1],[1,1,1,1],[0,7,1,9],[w-1,7,1,9],[8,0,9,1],[8,h-1,9,1],[11,13,18,16]];
  const result={version:1,errors:[],canvas:{},webgl:{}};
  for(const name of ['flat','text','gradient','embedded']){
    try{
      const c=document.createElement('canvas');c.width=name==='flat'?64:128;c.height=name==='embedded'?96:64;
      const x=c.getContext('2d',{willReadFrequently:true});if(!x)throw Error('2D context unavailable');
      if(name==='flat'){x.fillStyle='rgb(137,73,91)';x.fillRect(0,0,c.width,c.height);}
      else if(name==='text'){
        x.fillStyle='#f2e8dd';x.fillRect(0,0,c.width,c.height);x.font='20px Arial';x.textBaseline='top';
        x.fillStyle='#214f87';x.fillText('Canvas Wm 37',2,2);x.strokeStyle='#b25331';x.lineWidth=0.8;x.strokeText('glyph 9@ Q',7,28);
      }else{
        const g=x.createLinearGradient(0,0,c.width,c.height);g.addColorStop(0,'rgb(19,41,73)');g.addColorStop(0.47,'rgb(149,101,37)');g.addColorStop(1,'rgb(51,183,227)');x.fillStyle=g;x.fillRect(0,0,c.width,c.height);
        x.fillStyle='#713467';x.beginPath();x.moveTo(4,8);x.lineTo(59,3);x.lineTo(22,57);x.fill();
        if(name==='embedded'){x.fillStyle='rgb(137,73,91)';x.fillRect(64,32,40,32);}
      }
      const full=x.getImageData(0,0,c.width,c.height).data;
      const crops=positions(c.width,c.height).map(r=>({rect:r,raw:b64(x.getImageData(...r).data)}));
      crops.push({rect:[-1,-1,3,3],raw:b64(x.getImageData(-1,-1,3,3).data)});
      result.canvas[name]={width:c.width,height:c.height,full:b64(full),repeat:b64(x.getImageData(0,0,c.width,c.height).data),crops,png:c.toDataURL('image/png').split(',')[1],
        ...(name==='flat'?{expected:[137,73,91,255]}:{}),
        ...(name==='embedded'?{flatRegion:{rect:[66,34,36,28],expected:[137,73,91,255],raw:b64(x.getImageData(66,34,36,28).data)}}:{})};
    }catch(error){result.canvas[name]={error:String(error?.message||error)};result.errors.push({surface:'canvas/'+name,error:String(error?.message||error)});}
  }
  for(const kind of ['webgl','webgl2']){
    const c=document.createElement('canvas');c.width=c.height=64;
    const gl=c.getContext(kind,{alpha:false,antialias:false,preserveDrawingBuffer:true});
    if(!gl){result.webgl[kind]={supported:false,reason:'Native WebGL context unavailable'};continue;}
    const shaders=[],buffers=[];let program;
    try{
      gl.disable(gl.DITHER);gl.clearColor(137/255,73/255,91/255,1);gl.clear(gl.COLOR_BUFFER_BIT);
      const read=(r=[0,0,64,64])=>{const a=new Uint8Array(r[2]*r[3]*4);gl.readPixels(...r,gl.RGBA,gl.UNSIGNED_BYTE,a);return a;};
      const clear=read(),clearRepeat=read();
      const compile=(type,source)=>{const s=gl.createShader(type);shaders.push(s);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
      const v=compile(gl.VERTEX_SHADER,kind==='webgl2'?'#version 300 es\nin vec2 p;void main(){gl_Position=vec4(p,0.,1.);}':'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}');
      const body='vec2 p=gl_FragCoord.xy;vec4 c=vec4(fract(p.x*.017+p.y*.011),fract(p.x*.007+p.y*.023),fract((p.x+p.y)*.013),1.);';
      const f=compile(gl.FRAGMENT_SHADER,kind==='webgl2'?'#version 300 es\nprecision highp float;out vec4 color;void main(){'+body+'color=c;}':'precision highp float;void main(){'+body+'gl_FragColor=c;}');
      program=gl.createProgram();gl.attachShader(program,v);gl.attachShader(program,f);gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
      const vertex=gl.createBuffer();buffers.push(vertex);gl.bindBuffer(gl.ARRAY_BUFFER,vertex);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);const loc=gl.getAttribLocation(program,'p');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);gl.viewport(0,0,64,64);gl.drawArrays(gl.TRIANGLES,0,3);
      const full=read(),repeat=read();const crops=positions(64,64).map(r=>({rect:r,raw:b64(read(r))}));
      const row={supported:true,dimensions:[64,64],clear:{expected:[137,73,91,255],full:b64(clear),repeat:b64(clearRepeat)},full:b64(full),repeat:b64(repeat),crops};
      if(kind==='webgl2'){
        const pbo=gl.createBuffer();buffers.push(pbo);const offset=16,stride=280,skipRows=2,skipPixels=3,total=offset+(skipRows+64)*stride+32;
        const initial=new Uint8Array(total);initial.fill(165);gl.bindBuffer(gl.PIXEL_PACK_BUFFER,pbo);gl.bufferData(gl.PIXEL_PACK_BUFFER,initial,gl.STREAM_READ);
        gl.pixelStorei(gl.PACK_ALIGNMENT,8);gl.pixelStorei(gl.PACK_ROW_LENGTH,69);gl.pixelStorei(gl.PACK_SKIP_ROWS,skipRows);gl.pixelStorei(gl.PACK_SKIP_PIXELS,skipPixels);
        const readFbo=gl.getParameter(gl.READ_FRAMEBUFFER_BINDING),readBuffer=gl.getParameter(gl.READ_BUFFER);
        gl.readPixels(0,0,64,64,gl.RGBA,gl.UNSIGNED_BYTE,offset);
        const state={alignment:gl.getParameter(gl.PACK_ALIGNMENT),rowLength:gl.getParameter(gl.PACK_ROW_LENGTH),skipRows:gl.getParameter(gl.PACK_SKIP_ROWS),skipPixels:gl.getParameter(gl.PACK_SKIP_PIXELS),pboBinding:gl.getParameter(gl.PIXEL_PACK_BUFFER_BINDING)===pbo,readFramebuffer:gl.getParameter(gl.READ_FRAMEBUFFER_BINDING)===readFbo,readBuffer:gl.getParameter(gl.READ_BUFFER)===readBuffer};
        gl.finish();const packed=new Uint8Array(total);gl.getBufferSubData(gl.PIXEL_PACK_BUFFER,0,packed);const unpacked=new Uint8Array(64*64*4);const written=new Uint8Array(total);
        for(let y=0;y<64;y++){const at=offset+(skipRows+y)*stride+skipPixels*4;unpacked.set(packed.subarray(at,at+256),y*256);written.fill(1,at,at+256);}
        row.pbo={layout:{offset,stride,skipRows,skipPixels,total,sentinel:165},raw:b64(packed),unpacked:b64(unpacked),unwrittenUntouched:packed.every((v,i)=>written[i]||v===165),state,error:gl.getError()};
        gl.bindBuffer(gl.PIXEL_PACK_BUFFER,null);gl.pixelStorei(gl.PACK_ALIGNMENT,4);gl.pixelStorei(gl.PACK_ROW_LENGTH,0);gl.pixelStorei(gl.PACK_SKIP_ROWS,0);gl.pixelStorei(gl.PACK_SKIP_PIXELS,0);
      }
      row.png=c.toDataURL('image/png').split(',')[1];row.error=gl.getError();result.webgl[kind]=row;
    }catch(error){result.webgl[kind]={supported:true,error:String(error?.message||error)};result.errors.push({surface:kind,error:String(error?.message||error)});}
    finally{if(kind==='webgl2')gl.bindBuffer(gl.PIXEL_PACK_BUFFER,null);for(const b of buffers)gl.deleteBuffer(b);if(program)gl.deleteProgram(program);for(const s of shaders)gl.deleteShader(s);gl.getExtension('WEBGL_lose_context')?.loseContext();}
  }
  return result;
}

const decode=(s,n)=>{if(typeof s!=='string'||!s.length||s.length>4*1024*1024)return null;const b=Buffer.from(s,'base64');return b.toString('base64')===s&&(n===undefined||b.length===n)?b:null;};
const same=(a,b)=>Buffer.isBuffer(a)&&Buffer.isBuffer(b)&&a.length>0&&a.equals(b);
function crop(a,w,h,r){if(!a||!Array.isArray(r)||r.length!==4||!r.every(Number.isInteger))return null;const[x,y,cw,ch]=r;if(cw<1||ch<1||cw>256||ch>256||x< -1||y< -1||x>w||y>h)return null;const out=Buffer.alloc(cw*ch*4);for(let j=0;j<ch;j++)for(let i=0;i<cw;i++){if(x+i>=0&&x+i<w&&y+j>=0&&y+j<h)a.copy(out,(j*cw+i)*4,((y+j)*w+x+i)*4,((y+j)*w+x+i+1)*4);}return out;}
function flip(a,w,h){const out=Buffer.alloc(a.length);for(let y=0;y<h;y++)a.copy(out,y*w*4,(h-y-1)*w*4,(h-y)*w*4);return out;}
function flat(a,color){return !!a&&a.length>0&&a.every((v,i)=>v===color[i%4]);}
function delta(a,b){if(!a||!b||a.length!==b.length)return null;let changed=0,max=0,alpha=0;for(let i=0;i<a.length;i++)if(a[i]!==b[i]){changed++;max=Math.max(max,Math.abs(a[i]-b[i]));if(i%4===3)alpha++;}return{changed,max,alpha};}
function cropSpecs(w,h,canvas=false){const rows=[[0,0,1,1],[w-1,0,1,1],[0,h-1,1,1],[w-1,h-1,1,1],[1,1,1,1],[0,7,1,9],[w-1,7,1,9],[8,0,9,1],[8,h-1,9,1],[11,13,18,16]];return canvas?[...rows,[-1,-1,3,3]]:rows;}
function validPboPadding(pbo){const data=decode(pbo?.raw,18528);if(!data)return false;const written=new Uint8Array(data.length);for(let y=0;y<64;y++)written.fill(1,16+(2+y)*280+12,16+(2+y)*280+12+256);return data.every((v,i)=>written[i]||v===165);}

export function validatePixelSemantics(byId,decodePNG){
  const checks=[],not_tested=[];const add=(scenario,name,passed,group=null,detail=null)=>checks.push({scenario,name,passed:passed===true,...(group?{acceptance_group:group}:{}),detail});
  const base=byId.all_native?.extra?.pixel_semantics;
  for(const id of PIXEL_SCENARIOS){
    const p=byId[id]?.extra?.pixel_semantics;add(id,'pixel_semantics_acceptance_probe_present',p?.version===1&&typeof p==='object');
    add(id,'pixel_semantics_no_errors',Array.isArray(p?.errors)&&p.errors.length===0);
    for(const name of CANVAS_SCENES){
      const r=p?.canvas?.[name],w=name==='flat'?64:128,h=name==='embedded'?96:64,n=w*h*4,full=decode(r?.full,n),native=decode(base?.canvas?.[name]?.full,n);
      const group=id==='canvas_shardx'?'shard_canvas':null;const prefix='semantic_canvas_'+name+'_';
      add(id,prefix+'present',r?.width===w&&r?.height===h&&!!full,group);
      add(id,prefix+'repeat',same(full,decode(r?.repeat,n)),group);
      const crops=r?.crops;add(id,prefix+'edge_crops',Array.isArray(crops)&&JSON.stringify(crops.map(c=>c?.rect))===JSON.stringify(cropSpecs(w,h,true))&&crops.every(c=>same(crop(full,w,h,c.rect),decode(c.raw,c.rect[2]*c.rect[3]*4))),group);
      try{const png=decodePNG(decode(r?.png));add(id,prefix+'png',png.width===w&&png.height===h&&same(full,png.rgba),group);}catch{add(id,prefix+'png',false,group);}
      if(name==='flat')add(id,prefix+'exact',flat(full,[137,73,91,255]),group);
      else if(id==='canvas_shardx'){const d=delta(native,full);add(id,prefix+'rendered_positive',!!d&&d.changed>0&&d.changed<=256&&d.max===1&&d.alpha===0,group,d);}
      if(name==='embedded'){const f=r?.flatRegion;add(id,prefix+'flat_region_exact',JSON.stringify(f?.rect)==='[66,34,36,28]'&&flat(decode(f?.raw,36*28*4),[137,73,91,255])&&same(crop(full,w,h,[66,34,36,28]),decode(f?.raw,36*28*4)),group);}
    }
    for(const kind of ['webgl','webgl2']){
      const native=base?.webgl?.[kind],r=p?.webgl?.[kind],group=id==='webgl_shardx'?'shard_webgl':null,prefix='semantic_'+kind+'_';
      if(native?.supported===false){
        add(id,prefix+'unavailable_record',r?.supported===false&&typeof r.reason==='string'&&r.reason.length>0);
        if(group)not_tested.push({scenario:id,name:prefix+'positive',reason:'Native WebGL context unavailable',acceptance_group:group});
        continue;
      }
      const full=decode(r?.full,16384);add(id,prefix+'present',r?.supported===true&&r.error===0&&!!full,group);
      add(id,prefix+'clear_exact',flat(decode(r?.clear?.full,16384),[137,73,91,255])&&same(decode(r?.clear?.full,16384),decode(r?.clear?.repeat,16384)),group);
      add(id,prefix+'repeat',same(full,decode(r?.repeat,16384)),group);
      add(id,prefix+'edge_crops',Array.isArray(r?.crops)&&JSON.stringify(r.crops.map(c=>c?.rect))===JSON.stringify(cropSpecs(64,64))&&r.crops.every(c=>same(crop(full,64,64,c.rect),decode(c.raw,c.rect[2]*c.rect[3]*4))),group);
      try{const png=decodePNG(decode(r?.png));add(id,prefix+'png',png.width===64&&png.height===64&&!!full&&same(flip(full,64,64),png.rgba),group);}catch{add(id,prefix+'png',false,group);}
      if(group){const d=delta(decode(native?.full,16384),full);add(id,prefix+'rendered_positive',!!d&&d.changed>0&&d.changed<=256&&d.max===1&&d.alpha===0,group,d);}
      if(kind==='webgl2'){
        const pbo=r?.pbo,s=pbo?.state;add(id,prefix+'pbo_readback',pbo?.error===0&&same(full,decode(pbo?.unpacked,16384)),group);
        add(id,prefix+'pack_state_restored',s?.alignment===8&&s.rowLength===69&&s.skipRows===2&&s.skipPixels===3&&s.pboBinding===true&&s.readFramebuffer===true&&s.readBuffer===true,group);
        add(id,prefix+'pbo_padding_untouched',pbo?.unwrittenUntouched===true&&validPboPadding(pbo),group);
      }
    }
  }
  return {checks,not_tested};
}

export function pngDimensions(base64){
  const png=decode(base64);if(!png||png.length<33||!png.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))||png.readUInt32BE(8)!==13||png.toString('ascii',12,16)!=='IHDR')throw Error('Missing PNG IHDR');
  const width=png.readUInt32BE(16),height=png.readUInt32BE(20);if(!width||!height||width>65536||height>65536)throw Error('Invalid screenshot dimensions');
  const crc32=bytes=>{let crc=0xffffffff;for(const byte of bytes){crc^=byte;for(let k=0;k<8;k++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}return(crc^0xffffffff)>>>0;};
  let hasData=false,ended=false,headers=0;
  for(let at=8;at+12<=png.length;){const n=png.readUInt32BE(at);if(n>png.length-at-12)throw Error('Truncated screenshot PNG');const kind=png.toString('ascii',at+4,at+8);if(crc32(png.subarray(at+4,at+8+n))!==png.readUInt32BE(at+8+n))throw Error('Screenshot PNG CRC mismatch');if(kind==='IHDR')headers++;if(kind==='IDAT'&&n>0)hasData=true;if(kind==='IEND'){ended=true;break;}at+=n+12;}
  if(headers!==1||!hasData||!ended)throw Error('Screenshot PNG has no complete image');
  return {width,height,bytes:png.length,sha256:crypto.createHash('sha256').update(png).digest('hex')};
}

export function validateHeadlessScreens(byId){
  const checks=[],not_tested=[];const add=(id,name,passed,detail=null)=>checks.push({scenario:id,name,passed:passed===true,acceptance_group:'headless_screen',detail});
  const targets={headless_custom_dpr2:2,headless_custom_fractional:1.25,headless_explicit_info:1.5,headless_force_scale:1.5};
  for(const id of HEADLESS_SCENARIOS){
    const r=byId[id]?.extra?.headless_screen,p=r?.page,s=r?.screenshot;
    add(id,'headless_screen_acceptance_probe_present',r?.version===1&&typeof r==='object');
    let png;try{png=pngDimensions(s?.png);}catch{}
    add(id,'headless_png_identity',!!png&&png.width===s.width&&png.height===s.height&&png.bytes===s.bytes&&png.sha256===s.sha256);
    const scale=targets[id]??p?.dpr;
    const vw=r?.layout?.cssVisualViewport?.clientWidth??p?.innerWidth,vh=r?.layout?.cssVisualViewport?.clientHeight??p?.innerHeight;
    add(id,'headless_raster_scale',!!png&&Number.isFinite(vw)&&vw>0&&Number.isFinite(vh)&&vh>0&&Number.isFinite(scale)&&scale>0&&Math.abs(png.width-vw*scale)<=1&&Math.abs(png.height-vh*scale)<=1,{viewport:[vw,vh],expectedScale:scale,png:png&&[png.width,png.height]});
    const command=r?.effectiveCommandLine;
    add(id,'headless_effective_command_record',typeof command==='string'&&command.includes('--headless'));
    if(id.startsWith('headless_custom_')){
      add(id,'headless_declared_display',p?.screen?.width===640&&p.screen.height===480&&p.screen.availHeight===440&&p.dpr===scale);
      add(id,'headless_native_display_injected',typeof command==='string'&&command.includes('--screen-info=')&&command.includes('devicePixelRatio='+scale));
    }else if(id==='headless_explicit_info'){
      add(id,'headless_caller_screen_preserved',typeof command==='string'&&command.includes('960x720 devicePixelRatio=1.5')&&(command.match(/--screen-info=/g)||[]).length===1);
    }else if(id==='headless_force_scale'){
      add(id,'headless_caller_scale_preserved',typeof command==='string'&&command.includes('--force-device-scale-factor=1.5')&&!command.includes('--screen-info='));
    }else{
      add(id,'headless_native_off_not_mapped',typeof command==='string'&&!command.includes('--screen-info=')&&!command.includes('--uxr-screen-width=')&&!command.includes('--uxr-device-pixel-ratio='));
    }
  }
  return {checks,not_tested};
}
