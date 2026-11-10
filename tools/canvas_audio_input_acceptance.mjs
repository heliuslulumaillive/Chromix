// Owned-page regression: input pixels/PCM, mixed drawing, and frozen exports.
export const INPUT_SCENARIOS=['all_native','legacy_seeded','canvas_shardx','hardware_cloak','shard_surfaces'];
export async function inputPreservationProbe(){
  const b64=value=>{const bytes=new Uint8Array(value.buffer??value,value.byteOffset??0,value.byteLength??value.length);let s='';for(let at=0;at<bytes.length;at+=4096)s+=String.fromCharCode(...bytes.subarray(at,at+4096));return btoa(s);};
  const draw=(x,w=128,h=64)=>{const gradient=x.createLinearGradient(0,0,w,h);gradient.addColorStop(0,'#13375f');gradient.addColorStop(.43,'#b35b31');gradient.addColorStop(1,'#35b7e3');x.fillStyle=gradient;x.fillRect(0,0,w,h);x.font='20px Arial';x.fillStyle='#e8e2d9';x.fillText('Input preservation Wm 37',3,31);};
  const result={version:1,canvas:{},audio:{},errors:[]};
  for(const name of ['opaque','partial-alpha','gpu-requested','opaque-context','display-p3','clipped','dirty','mixed','reset','async-snapshot','offscreen']){
    try{
      const c=name==='offscreen'?new OffscreenCanvas(128,64):document.createElement('canvas');c.width=128;c.height=64;
      const x=c.getContext('2d',{willReadFrequently:name!=='gpu-requested',alpha:name!=='opaque-context',colorSpace:name==='display-p3'?'display-p3':'srgb'});const data=x.createImageData(128,64);
      for(let p=0;p<128*64;p++){data.data[p*4]=(p*17+31)&255;data.data[p*4+1]=(p*37+66)&255;data.data[p*4+2]=(p*13+123)&255;data.data[p*4+3]=['partial-alpha','opaque-context'].includes(name)?[1,64,127,254][p%4]:255;}
      if(name==='clipped')x.putImageData(data,-13,-7);
      else if(name==='dirty')x.putImageData(data,0,0,11,13,87,35);
      else x.putImageData(data,0,0);
      if(name==='mixed'){x.save();x.beginPath();x.rect(64,0,64,64);x.clip();draw(x);x.restore();}
      if(name==='reset'){x.reset();draw(x);}
      const full=x.getImageData(0,0,128,64).data;
      const row={attributes:x.getContextAttributes(),raw:b64(full),repeat:b64(x.getImageData(0,0,128,64).data),crop:b64(x.getImageData(11,13,18,16).data)};
      if(name==='reset'){const fresh=document.createElement('canvas');fresh.width=128;fresh.height=64;const fx=fresh.getContext('2d',{willReadFrequently:true});draw(fx);row.fresh=b64(fx.getImageData(0,0,128,64).data);}
      if(name==='async-snapshot'){
        const pending=new Promise(resolve=>c.toBlob(resolve,'image/png'));
        data.data.fill(17);for(let i=3;i<data.data.length;i+=4)data.data[i]=255;
        x.putImageData(data,0,0);const blob=await pending;if(!blob)throw Error('Missing pending PNG');row.png=b64(await blob.arrayBuffer());
      }else if(name==='offscreen'){row.png=b64(await(await c.convertToBlob({type:'image/png'})).arrayBuffer());}
      else row.png=c.toDataURL('image/png').split(',')[1];
      result.canvas[name]=row;
    }catch(e){result.errors.push({case:name,error:String(e.stack)});}
  }
  try{
    const buffer=new AudioBuffer({numberOfChannels:2,length:128,sampleRate:44100});
    const input=Float32Array.from({length:128},(_,i)=>Math.sin(i*.013)*.75);
    const bits=new Uint32Array(input.buffer);bits.set([0,0x80000000,1,0x7f800000,0xff800000,0x7fc01234]);
    buffer.copyToChannel(input,0);const view=buffer.getChannelData(0),copy=new Float32Array(128);buffer.copyFromChannel(copy,0);
    result.audio.input={expected:b64(input),view:b64(view),copy:b64(copy)};
    const changes=Float32Array.from([.123,.456,.789,-.321]);view.set(changes,17);
    result.audio.viewEdit={expected:b64(changes),actual:b64(buffer.getChannelData(0).subarray(17,21))};
    buffer.copyToChannel(changes,1,31);const second=buffer.getChannelData(1);
    result.audio.offsetWrite={expected:b64(changes),actual:b64(second.subarray(31,35)),prefixZero:second.subarray(0,31).every(v=>v===0),suffixZero:second.subarray(35).every(v=>v===0)};
    const offline=new OfflineAudioContext(1,5000,44100),osc=offline.createOscillator(),compressor=offline.createDynamicsCompressor();
    osc.type='triangle';osc.frequency.value=10000;compressor.threshold.value=-50;compressor.knee.value=40;compressor.ratio.value=12;compressor.attack.value=0;compressor.release.value=.25;osc.connect(compressor);compressor.connect(offline.destination);osc.start();
    const rendered=await offline.startRendering(),original=rendered.getChannelData(0),saved=b64(original),copied=new Float32Array(5000);rendered.copyFromChannel(copied,0);
    result.audio.rendered={raw:saved,copy:b64(copied),repeat:b64(rendered.getChannelData(0))};
    rendered.copyToChannel(changes,0,43);result.audio.renderedEdit={expected:b64(changes),actual:b64(rendered.getChannelData(0).subarray(43,47)),sameView:original===rendered.getChannelData(0),visibleToExistingView:b64(original.subarray(43,47))};
  }catch(e){result.errors.push({case:'audio',error:String(e.stack)});}
  return result;
}

export function validateInputPreservation(records,decodePNG){
  const checks=[],add=(scenario,name,passed)=>checks.push({scenario,name:'input_'+name,passed:passed===true,acceptance_group:'application_input_preservation'});
  const bytes=x=>typeof x==='string'?Buffer.from(x,'base64'):null;
  const eq=(a,b)=>{const x=bytes(a),y=bytes(b);return !!x?.length&&!!y?.length&&x.equals(y)};
  const crop=raw=>{const source=bytes(raw),out=Buffer.alloc(18*16*4);if(source?.length!==128*64*4)return null;for(let y=0;y<16;y++)source.copy(out,y*18*4,((y+13)*128+11)*4,((y+13)*128+29)*4);return out;};
  const baseline=records.all_native?.extra?.input_preservation;
  for(const id of INPUT_SCENARIOS){
    const p=records[id]?.extra?.input_preservation;add(id,'probe_present',p?.version===1&&Array.isArray(p.errors)&&p.errors.length===0);
    for(const name of ['opaque','partial-alpha','gpu-requested','opaque-context','display-p3','clipped','dirty','mixed','reset','async-snapshot','offscreen']){
      const row=p?.canvas?.[name],native=baseline?.canvas?.[name];
      add(id,name+'_repeat',eq(row?.raw,row?.repeat));
      const cropped=crop(row?.raw);add(id,name+'_crop',!!cropped&&!!bytes(row?.crop)?.equals(cropped));
      let decoded;try{decoded=decodePNG(bytes(row?.png));}catch{}
      let pngExpected=bytes(row?.raw);if(name==='display-p3'){try{pngExpected=decodePNG(bytes(native?.png)).rgba;}catch{pngExpected=null;}}
      add(id,name+'_png',!!decoded&&decoded.width===128&&decoded.height===64&&!!pngExpected?.equals(decoded.rgba));
      if(name==='display-p3')add(id,'display_p3_requested',row?.attributes?.colorSpace==='display-p3');
      if(!['mixed','reset'].includes(name))add(id,name+'_native_inputs',eq(row?.raw,native?.raw));
      if(name==='reset')add(id,'reset_matches_fresh_drawing',eq(row?.raw,row?.fresh));
      if(name==='mixed'){
        const actual=bytes(row?.raw),expected=bytes(native?.raw);let same=actual?.length===32768&&expected?.length===32768;
        if(same)for(let y=0;y<64;y++)same&&=actual.subarray(y*512,y*512+256).equals(expected.subarray(y*512,y*512+256));
        add(id,'mixed_untouched_half',same);
      }
    }
    const a=p?.audio;add(id,'pcm_view',eq(a?.input?.expected,a?.input?.view));add(id,'pcm_copy',eq(a?.input?.expected,a?.input?.copy));
    add(id,'pcm_view_edit',eq(a?.viewEdit?.expected,a?.viewEdit?.actual));
    add(id,'pcm_offset_write',eq(a?.offsetWrite?.expected,a?.offsetWrite?.actual)&&a?.offsetWrite?.prefixZero&&a?.offsetWrite?.suffixZero);
    add(id,'rendered_read_paths',eq(a?.rendered?.raw,a?.rendered?.copy)&&eq(a?.rendered?.raw,a?.rendered?.repeat));
    add(id,'rendered_write_preserved',eq(a?.renderedEdit?.expected,a?.renderedEdit?.actual)&&eq(a?.renderedEdit?.expected,a?.renderedEdit?.visibleToExistingView)&&a?.renderedEdit?.sameView);
  }
  return {checks,not_tested:[]};
}
