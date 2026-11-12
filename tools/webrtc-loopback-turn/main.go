package main

import (
 "bufio"
 "encoding/json"
 "fmt"
 "net"
 "os"
 "sync/atomic"

 "github.com/pion/turn/v4"
)

var accepted,readBytes,writtenBytes,authRequests atomic.Int64
type tracedConn struct { net.Conn }
func (c tracedConn) Read(b []byte)(int,error){ n,e:=c.Conn.Read(b);readBytes.Add(int64(n));return n,e }
func (c tracedConn) Write(b []byte)(int,error){ n,e:=c.Conn.Write(b);writtenBytes.Add(int64(n));return n,e }
type tracedListener struct { net.Listener }
func (l tracedListener) Accept()(net.Conn,error){ c,e:=l.Listener.Accept();if e!=nil{return nil,e};accepted.Add(1);return tracedConn{c},nil }
func main(){
 listener,e:=net.Listen("tcp4","127.0.0.1:0");if e!=nil{panic(e)}
 const realm="owned.shardx.test"
 server,e:=turn.NewServer(turn.ServerConfig{Realm:realm,AuthHandler:func(username,realm string,source net.Addr)([]byte,bool){authRequests.Add(1);if username!="owned"{return nil,false};return turn.GenerateAuthKey(username,realm,"owned-fixture-only"),true},ListenerConfigs:[]turn.ListenerConfig{{Listener:tracedListener{listener},RelayAddressGenerator:&turn.RelayAddressGeneratorStatic{RelayAddress:net.ParseIP("127.0.0.1"),Address:"127.0.0.1"}}}})
 if e!=nil{panic(e)}
 encoder:=json.NewEncoder(os.Stdout);encoder.Encode(map[string]any{"ready":true,"port":listener.Addr().(*net.TCPAddr).Port,"realm":realm})
 scanner:=bufio.NewScanner(os.Stdin)
 for scanner.Scan(){ if scanner.Text()=="stats"{encoder.Encode(map[string]any{"stats":true,"accepted":accepted.Load(),"readBytes":readBytes.Load(),"writtenBytes":writtenBytes.Load(),"authRequests":authRequests.Load()})};if scanner.Text()=="stop"{break} }
 if e=server.Close();e!=nil{fmt.Fprintln(os.Stderr,e)}
 encoder.Encode(map[string]any{"stopped":true,"accepted":accepted.Load(),"readBytes":readBytes.Load(),"writtenBytes":writtenBytes.Load(),"authRequests":authRequests.Load()})
}
