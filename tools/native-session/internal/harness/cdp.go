package harness

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

type cdpReply struct {
	result object
	err    error
}
type cdpClient struct {
	conn      *websocket.Conn
	next      atomic.Uint64
	mu        sync.Mutex
	pending   map[uint64]chan cdpReply
	done      chan struct{}
	closeOnce sync.Once
	writes    chan struct{}
	targets   chan struct{}
	active    string
	sessions  map[string]string
}

func connectCDP(ctx context.Context, endpoint string) (*cdpClient, error) {
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: time.Second}
	var last error
	for {
		req, e := http.NewRequestWithContext(ctx, "GET", endpoint+"/json/version", nil)
		if e != nil {
			return nil, e
		}
		resp, e := client.Do(req)
		if e == nil {
			var version object
			e = json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&version)
			_ = resp.Body.Close()
			if e == nil && resp.StatusCode == 200 && str(version, "webSocketDebuggerUrl") != "" {
				dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
				conn, _, dialErr := dialer.DialContext(ctx, str(version, "webSocketDebuggerUrl"), nil)
				if dialErr == nil {
					c := &cdpClient{conn: conn, pending: map[uint64]chan cdpReply{}, done: make(chan struct{}), writes: make(chan struct{}, 1), targets: make(chan struct{}, 1), sessions: map[string]string{}}
					conn.SetReadLimit(64 << 20)
					go c.read()
					return c, nil
				}
				e = dialErr
			} else if e == nil {
				e = fmt.Errorf("invalid browser CDP version response (%d)", resp.StatusCode)
			}
		}
		last = e
		if e = pause(ctx, 100*time.Millisecond); e != nil {
			return nil, fmt.Errorf("connect browser CDP: %w (last response: %v)", e, last)
		}
	}
}
func (c *cdpClient) Close() {
	c.closeOnce.Do(func() {
		close(c.done)
		_ = c.conn.Close()
		c.mu.Lock()
		for id, ch := range c.pending {
			ch <- cdpReply{err: errors.New("CDP connection closed")}
			delete(c.pending, id)
		}
		c.mu.Unlock()
	})
}
func (c *cdpClient) read() {
	defer c.Close()
	for {
		var msg struct {
			ID     uint64 `json:"id"`
			Result object `json:"result"`
			Error  *struct {
				Code    int    `json:"code"`
				Message string `json:"message"`
				Data    any    `json:"data"`
			} `json:"error"`
		}
		if e := c.conn.ReadJSON(&msg); e != nil {
			return
		}
		if msg.ID == 0 {
			continue
		}
		reply := cdpReply{result: msg.Result}
		if reply.result == nil {
			reply.result = object{}
		}
		if msg.Error != nil {
			reply.err = fmt.Errorf("CDP error %d: %s", msg.Error.Code, msg.Error.Message)
		}
		c.mu.Lock()
		ch := c.pending[msg.ID]
		delete(c.pending, msg.ID)
		c.mu.Unlock()
		if ch != nil {
			ch <- reply
		}
	}
}
func (c *cdpClient) request(ctx context.Context, method string, params object, session string) (object, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	if method == "" {
		return nil, errors.New("CDP method is required")
	}
	id := c.next.Add(1)
	reply := make(chan cdpReply, 1)
	c.mu.Lock()
	select {
	case <-c.done:
		c.mu.Unlock()
		return nil, errors.New("CDP connection closed")
	default:
	}
	c.pending[id] = reply
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, id); c.mu.Unlock() }()
	if params == nil {
		params = object{}
	}
	message := object{"id": id, "method": method, "params": params}
	if session != "" {
		message["sessionId"] = session
	}
	select {
	case c.writes <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-c.done:
		return nil, errors.New("CDP connection closed")
	}
	deadline := time.Now().Add(10 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	_ = c.conn.SetWriteDeadline(deadline)
	e := c.conn.WriteJSON(message)
	<-c.writes
	if e != nil {
		c.Close()
		return nil, e
	}
	select {
	case r := <-reply:
		return r.result, r.err
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-c.done:
		return nil, errors.New("CDP connection closed")
	}
}

type object = map[string]any

func str(value object, key string) string { result, _ := value[key].(string); return result }
func pause(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
