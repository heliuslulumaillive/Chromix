// Exercises the real halo acquisition helper with a controlled GL transport.
// This tests memory/layout/state behavior, not a real driver or GPU.
#include "third_party/blink/renderer/modules/webgl/uxr_shard_readback.h"
#include <array>
#include <cstring>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <vector>

class Transport final : public gpu::gles2::GLES2Interface {
 public:
  std::map<GLenum,int> pack{{GL_PACK_ALIGNMENT,8},{GL_PACK_ROW_LENGTH,19},
      {GL_PACK_SKIP_PIXELS,3},{GL_PACK_SKIP_ROWS,2}};
  GLuint pbo=73;
  int read_count=0,state_calls=0;
  bool fail_read=false,extended=true,invalid_extended_call=false,require_tight=true;
  bool owner_alive=true,lost=false;
  int owner_dead_calls=0,owners_destroyed=0,after_loss_state_calls=0;
  int buffer_writes=0,writes_after_loss=0,frame_restores=0;
  std::function<void(int)> on_read;
  std::vector<uint8_t> gpu_buffer=std::vector<uint8_t>(4096,173);
  std::vector<int> errors{0x500,0x502};
  bool SupportsReadPixelsCompletion() const override{return true;}
  void BindBuffer(GLenum target, GLuint value) override {
    ++state_calls;
    owner_dead_calls+=!owner_alive;after_loss_state_calls+=lost;
    if(!extended||target!=GL_PIXEL_PACK_BUFFER)invalid_extended_call=true;
    pbo=value;
  }
  void PixelStorei(GLenum target, GLint value) override {
    ++state_calls;
    owner_dead_calls+=!owner_alive;after_loss_state_calls+=lost;
    if(!extended&&target!=GL_PACK_ALIGNMENT)invalid_extended_call=true;
    pack[target]=value;
  }
  ReadPixelsCompletion ReadPixelsWithCompletion(GLint x, GLint y, GLsizei width,
      GLsizei height, GLenum format, GLenum type, void* output) override {
    ++read_count;
    owner_dead_calls+=!owner_alive;
    if(fail_read||format!=GL_RGBA||(require_tight&&(pack[GL_PACK_ALIGNMENT]!=1||
       (extended&&(pbo||pack[GL_PACK_ROW_LENGTH]||pack[GL_PACK_SKIP_PIXELS]||pack[GL_PACK_SKIP_ROWS])))))return {};
    const int columns=std::max(0,std::min(width,67-x));
    const int rows=std::max(0,std::min(height,33-y));
    const size_t pixel=type==GL_FLOAT?16:4;
    const size_t stride=((pack[GL_PACK_ROW_LENGTH]?pack[GL_PACK_ROW_LENGTH]:width)*pixel+
        pack[GL_PACK_ALIGNMENT]-1)/pack[GL_PACK_ALIGNMENT]*pack[GL_PACK_ALIGNMENT];
    const size_t skip=pack[GL_PACK_SKIP_ROWS]*stride+pack[GL_PACK_SKIP_PIXELS]*pixel;
    auto bytes=static_cast<uint8_t*>(output);
    for(int yy=0;yy<rows;++yy)for(int xx=0;xx<columns;++xx) {
      const size_t at=skip+static_cast<size_t>(yy)*stride+xx*pixel;
      if(type==GL_FLOAT) {
        const float value[4]{(x+xx)/100.f,(y+yy)/100.f,.375f,1};
        std::memcpy(bytes+at,value,sizeof(value));
      } else {
        bytes[at]=uint8_t(x+xx);bytes[at+1]=uint8_t(y+yy);
        bytes[at+2]=127;bytes[at+3]=255;
      }
    }
    if(on_read)on_read(read_count);
    // Deliberately report completion success even after simulated loss: the
    // actual callsite must inspect lifecycle state, not merely this boolean.
    return {true,columns,rows};
  }
  void BufferSubData(GLenum target,GLintptr offset,GLsizeiptr length,const void* data) override {
    ++buffer_writes;writes_after_loss+=lost;owner_dead_calls+=!owner_alive;
    if(target==GL_PIXEL_PACK_BUFFER&&offset>=0&&length>=0&&
       static_cast<size_t>(offset+length)<=gpu_buffer.size())
      std::memcpy(gpu_buffer.data()+offset,data,static_cast<size_t>(length));
  }
};

class DrawingBuffer : public std::enable_shared_from_this<DrawingBuffer> {
 public:
  explicit DrawingBuffer(std::shared_ptr<Transport> gl):gl_(std::move(gl)){gl_->owner_alive=true;}
  ~DrawingBuffer(){gl_->owner_alive=false;++gl_->owners_destroyed;}
  Transport* ContextGL(){return gl_.get();}
  void BeginDestruction(){destruction_in_progress_=true;}
  void RestoreFramebufferBindings(){if(!destruction_in_progress_)++gl_->frame_restores;}
 private:
  std::shared_ptr<Transport> gl_;
  bool destruction_in_progress_=false;
};
template<class T>class scoped_refptr {
 public:
  scoped_refptr(T* p):value_(p?p->shared_from_this():std::shared_ptr<T>{}){}
  explicit operator bool()const{return bool(value_);}
  T* get()const{return value_.get();}T* operator->()const{return get();}
 private:std::shared_ptr<T> value_;
};
class ScopedDrawingBufferBinder {
 public:
  ScopedDrawingBufferBinder(DrawingBuffer* owner,void*):owner_(owner){}
  ~ScopedDrawingBufferBinder(){owner_->RestoreFramebufferBindings();}
  bool Succeeded()const{return owner_!=nullptr;}
 private:DrawingBuffer* owner_;
};
namespace base {
struct UxrConfig {
 static UxrConfig& GetInstance(){static UxrConfig c;return c;}
 UxrSurfaceModes SurfaceModes()const{UxrSurfaceModes m;m.webgl.mode=UxrSurfaceMode::kShardX;m.webgl.seed=42;return m;}
};
}
struct CheckedSize{size_t value;size_t ValueOrDie()const{return value;}};
struct CallerPixels{std::vector<uint8_t> bytes=std::vector<uint8_t>(4096,173);base::span<uint8_t> ByteSpanMaybeShared(){return bytes;}};
struct BufferObject{bool deleted=false;GLuint Object()const{return deleted?0:73;}};
struct PackState{int alignment,row_length,skip_pixels,skip_rows;};
struct Attributes{bool premultiplied_alpha=false;};
using blink::UxrShardLayout;
using blink::UxrShardCompletedRegion;
using blink::UxrShardReadNeighborhood;
using blink::UxrShardApplyCompleted;

class CallsiteContext {
 public:
  CallsiteContext(std::shared_ptr<Transport> transport,bool pbo,bool extended,int lose_on_read)
      :transport_(std::move(transport)),drawing_buffer_(std::make_shared<DrawingBuffer>(transport_)),
       visible_gl_(transport_.get()),extended_(extended){
    transport_->require_tight=false;transport_->extended=extended;
    if(!extended){transport_->pack[GL_PACK_ROW_LENGTH]=0;transport_->pack[GL_PACK_SKIP_PIXELS]=0;transport_->pack[GL_PACK_SKIP_ROWS]=0;}
    transport_->pbo=pbo?73:0;
    transport_->on_read=[this,lose_on_read](int count){if(count==lose_on_read){
      drawing_buffer_->BeginDestruction();drawing_buffer_.reset();visible_gl_=nullptr;
      lost_=true;transport_->lost=true;buffer_object.deleted=true;
      owner_held_at_loss=transport_->owner_alive&&transport_->owners_destroyed==0;
    }};
  }
  bool owner_held_at_loss=false;
  Transport* visible_gl()const{return visible_gl_;}
  bool isContextLost()const{return lost_;}
  DrawingBuffer* GetDrawingBuffer(){return drawing_buffer_.get();}
  PackState GetPackPixelStoreParams(){const auto& p=transport_->pack;return {p.at(GL_PACK_ALIGNMENT),p.at(GL_PACK_ROW_LENGTH),p.at(GL_PACK_SKIP_PIXELS),p.at(GL_PACK_SKIP_ROWS)};}
  Attributes CreationAttributes()const{return {};}
  bool IsWebGL2()const{return extended_;}
  void SynthesizeGLError(GLenum,const char*,const char*){++errors_;}
  // Generated directly from the patched pinned Chromium callsites. No
  // hand-written substitute performs the lifecycle/early-return decisions.
  #include "pixel_readback_callsites.inc"
 private:
  std::shared_ptr<Transport> transport_;
  std::shared_ptr<DrawingBuffer> drawing_buffer_;
  Transport* visible_gl_;
  bool extended_,lost_=false;
  int errors_=0;
  CallerPixels caller_pixels;
  BufferObject buffer_object;
};

int main(){
 size_t checks=0,failures=0;
 auto check=[&](bool value){++checks;failures+=!value;};
 for(bool extended:{false,true})for(GLenum type:{GL_UNSIGNED_BYTE,GL_FLOAT}) {
  Transport gl;gl.extended=extended;
  if(!extended){gl.pack[GL_PACK_ROW_LENGTH]=0;gl.pack[GL_PACK_SKIP_PIXELS]=0;gl.pack[GL_PACK_SKIP_ROWS]=0;gl.pbo=0;}
  const auto pack=gl.pack;const auto pbo=gl.pbo;const auto errors=gl.errors;
  std::vector<uint8_t> original(5*6*(type==GL_FLOAT?16:4),173);
  const auto copy=original;
  base::UxrPixelRegion r{original,5*size_t(type==GL_FLOAT?16:4),5,6,5,6,2,3};
  {
   const auto n=blink::UxrShardReadNeighborhood(&gl,type,r,pack.at(GL_PACK_ALIGNMENT),
       pack.at(GL_PACK_ROW_LENGTH),pack.at(GL_PACK_SKIP_PIXELS),pack.at(GL_PACK_SKIP_ROWS),extended,pbo);
   check(!n.view.bytes.empty());check(n.view.width==7&&n.view.height==8);
   check(n.view.origin_x==1&&n.view.origin_y==2);
   check(gl.pack==pack&&gl.pbo==pbo);check(gl.errors==errors);
   check(!gl.invalid_extended_call);check(original==copy);check(gl.read_count==1);
   if(type==GL_UNSIGNED_BYTE){check(n.view.bytes[0]==1&&n.view.bytes[1]==2&&n.view.bytes[3]==255);}
   else {float pixel[4];std::memcpy(pixel,n.view.bytes.data(),16);check(pixel[0]==.01f&&pixel[1]==.02f&&pixel[3]==1);}
  }
  r.origin_x=66;r.origin_y=32;r.width=1;r.height=1;
  {
   const auto n=blink::UxrShardReadNeighborhood(&gl,type,r,pack.at(GL_PACK_ALIGNMENT),
       pack.at(GL_PACK_ROW_LENGTH),pack.at(GL_PACK_SKIP_PIXELS),pack.at(GL_PACK_SKIP_ROWS),extended,pbo);
   check(n.view.width==2&&n.view.height==2);check(n.view.origin_x==65&&n.view.origin_y==31);
   check(gl.pack==pack&&gl.pbo==pbo);check(!gl.invalid_extended_call);
  }
  gl.fail_read=true;
  {
   const auto n=blink::UxrShardReadNeighborhood(&gl,type,r,pack.at(GL_PACK_ALIGNMENT),
       pack.at(GL_PACK_ROW_LENGTH),pack.at(GL_PACK_SKIP_PIXELS),pack.at(GL_PACK_SKIP_ROWS),extended,pbo);
   check(n.view.bytes.empty());check(gl.pack==pack&&gl.pbo==pbo);check(gl.errors==errors);
  }
  const int calls=gl.state_calls;
  r.origin_x=std::numeric_limits<int>::max();r.width=2;
  const auto overflow=blink::UxrShardReadNeighborhood(&gl,type,r,8,0,0,0,extended,pbo);
  check(overflow.view.bytes.empty());check(gl.state_calls==calls);
 }
 const auto too_large=blink::UxrShardLayout(std::numeric_limits<int>::max(),
   std::numeric_limits<int>::max(),8,0,0,0,GL_RGBA,GL_FLOAT,std::numeric_limits<size_t>::max());
 check(!too_large);
 size_t loss_checks=0;
 auto loss_check=[&](bool value){++loss_checks;check(value);};
 for(const auto [pbo,extended]:{std::pair{false,false},{false,true},{true,true}})
  for(int loss:{0,1,2}) {
   auto transport=std::make_shared<Transport>();
   {
    CallsiteContext context(transport,pbo,extended,loss);
    const auto pack=transport->pack;const auto pbo_binding=transport->pbo;
    if(pbo)context.RunPbo();else context.RunTyped();
    loss_check(transport->owner_dead_calls==0);
    loss_check(transport->pack==pack&&transport->pbo==pbo_binding);
    loss_check(!transport->invalid_extended_call);
    if(loss) {
      loss_check(context.isContextLost()&&context.visible_gl()==nullptr);
      loss_check(context.owner_held_at_loss);
      loss_check(transport->owners_destroyed==1);
      loss_check(transport->read_count==loss);
      loss_check(transport->buffer_writes==0&&transport->writes_after_loss==0);
      loss_check(transport->frame_restores==0);
      if(pbo||loss==2)loss_check(transport->after_loss_state_calls>0);
      if(pbo)loss_check(std::all_of(transport->gpu_buffer.begin(),transport->gpu_buffer.end(),[](uint8_t v){return v==173;}));
    } else {
      loss_check(!context.isContextLost()&&context.visible_gl()!=nullptr);
      loss_check(transport->read_count==2);
      loss_check(transport->buffer_writes==(pbo?6:0));
      loss_check(transport->frame_restores==1);
    }
   }
   loss_check(transport->owners_destroyed==1);
   loss_check(transport->owner_dead_calls==0);
  }
 std::cout<<"{\"pixel_halo_checks\":"<<checks<<",\"pixel_callsite_loss_checks\":"<<loss_checks<<",\"failures\":"<<failures<<"}\n";
 return failures?1:0;
}
