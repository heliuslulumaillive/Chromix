//go:build windows

// Package harness is the standalone candidate adapter. The desktop adapter
// continues to exercise its production Manager against the same scenarios.
package harness

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var user32 = windows.NewLazySystemDLL("user32.dll")
var enumWindows = user32.NewProc("EnumWindows")
var windowProcess = user32.NewProc("GetWindowThreadProcessId")
var visibleWindow = user32.NewProc("IsWindowVisible")
var sendMessage = user32.NewProc("SendMessageTimeoutW")
var registerMessage = user32.NewProc("RegisterWindowMessageW")
var findWindow = user32.NewProc("FindWindowExW")
var inJob = windows.NewLazySystemDLL("kernel32.dll").NewProc("IsProcessInJob")

const exitAck = 0x53484c58

type Browser struct {
	cmd    *exec.Cmd
	job    windows.Handle
	done   chan struct{}
	client *cdpClient
}

type windowQuery struct {
	browser *Browser
	action  string
	count   int
	message uintptr
	ack     bool
	err     error
}

var queries sync.Map
var nextQuery atomic.Uint64

// Reuse a single callback; NewCallback slots cannot be released by Go.
var visitWindow = syscall.NewCallback(func(hwnd, parameter uintptr) uintptr {
	value, ok := queries.Load(parameter)
	if !ok {
		return 0
	}
	q := value.(*windowQuery)
	var pid uint32
	windowProcess.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
	visible, _, _ := visibleWindow.Call(hwnd)
	if pid != uint32(q.browser.cmd.Process.Pid) || visible == 0 {
		return 1
	}
	if !q.browser.owned() {
		q.err = fmt.Errorf("candidate process ownership changed")
		return 0
	}
	q.count++
	switch q.action {
	case "hide":
		user32.NewProc("ShowWindow").Call(hwnd, 0)
	case "close-window":
		ok, _, _ := user32.NewProc("PostMessageW").Call(hwnd, 0x0010, 0, 0)
		if ok == 0 {
			q.err = fmt.Errorf("cannot close owned window")
		}
		return 0
	case "exit":
		q.ack = q.browser.sendExit(hwnd, q.message)
		if q.ack {
			return 0
		}
	}
	return 1
})

func (b *Browser) scan(action string, message uintptr) *windowQuery {
	q := &windowQuery{browser: b, action: action, message: message}
	id := uintptr(nextQuery.Add(1))
	queries.Store(id, q)
	defer queries.Delete(id)
	enumWindows.Call(visitWindow, id)
	return q
}

func (b *Browser) owned() bool {
	if !b.Alive() {
		return false
	}
	process, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(b.cmd.Process.Pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(process)
	var member int32
	ok, _, _ := inJob.Call(uintptr(process), uintptr(b.job), uintptr(unsafe.Pointer(&member)))
	return ok != 0 && member != 0
}

func (b *Browser) Alive() bool {
	select {
	case <-b.done:
		return false
	default:
		return true
	}
}
func (b *Browser) CleanExit() bool {
	return !b.Alive() && b.cmd.ProcessState != nil && b.cmd.ProcessState.Success()
}
func (b *Browser) Wait(ctx context.Context) error {
	select {
	case <-b.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (b *Browser) Crash() error { return windows.TerminateJobObject(b.job, 1) }
func (b *Browser) Request(ctx context.Context, method string, params map[string]any, session string) (map[string]any, error) {
	return b.client.request(ctx, method, params, session)
}
func (b *Browser) VisibleWindows() (int, error) { q := b.scan("count", 0); return q.count, q.err }
func (b *Browser) HideWindows() error           { return b.scan("hide", 0).err }
func (b *Browser) CloseVisibleWindow() error {
	q := b.scan("close-window", 0)
	if q.count == 0 && q.err == nil {
		return fmt.Errorf("no owned visible window to close")
	}
	return q.err
}
func (b *Browser) sendExit(hwnd, message uintptr) bool {
	if !b.owned() {
		return false
	}
	var result uintptr
	ok, _, _ := sendMessage.Call(hwnd, message, exitAck, 0, 0x0003, 1000, uintptr(unsafe.Pointer(&result)))
	return ok != 0 && result == exitAck
}
func (b *Browser) Close(ctx context.Context) error {
	if !b.Alive() {
		return nil
	}
	if !b.owned() {
		return fmt.Errorf("candidate process ownership changed")
	}
	name, _ := windows.UTF16PtrFromString("ShardX.Lite.ExitEnvironment.v1")
	message, _, _ := registerMessage.Call(uintptr(unsafe.Pointer(name)))
	if message == 0 {
		return fmt.Errorf("cannot register native exit message")
	}
	ack := false
	deadline := time.Now().Add(2 * time.Second)
	for previous := uintptr(0); !ack && time.Now().Before(deadline) && b.owned() && ctx.Err() == nil; {
		hwnd, _, _ := findWindow.Call(^uintptr(2), previous, 0, 0) // HWND_MESSAGE
		if hwnd == 0 {
			break
		}
		previous = hwnd
		var pid uint32
		windowProcess.Call(hwnd, uintptr(unsafe.Pointer(&pid)))
		if pid == uint32(b.cmd.Process.Pid) {
			ack = b.sendExit(hwnd, message)
		}
	}
	if !ack && b.Alive() && ctx.Err() == nil {
		ack = b.scan("exit", message).ack
	}
	if !ack && b.Alive() {
		return fmt.Errorf("owned browser did not acknowledge native exit")
	}
	return b.Wait(ctx)
}

func (b *Browser) CloseNormally() error {
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	return b.Close(ctx)
}

func Launch(t *testing.T, binary, data string, restore bool, start string) *Browser {
	t.Helper()
	if err := os.MkdirAll(data, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(data, "DevToolsActivePort")); err != nil && !os.IsNotExist(err) {
		t.Fatal(err)
	}
	args := []string{"--user-data-dir=" + data, "--profile-directory=Default", "--no-first-run", "--no-default-browser-check",
		"--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", "--disable-background-networking",
		"--shardx-managed-environment=a7423600-295b-4a29-93d6-b5dcd6824571", "--shardx-history-retention-days=0",
		"--shardx-restore-previous-session=" + strconv.FormatBool(restore),
		"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"}
	if start != "" {
		args = append(args, start)
	}
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&info)), uint32(unsafe.Sizeof(info))); err != nil {
		windows.CloseHandle(job)
		t.Fatal(err)
	}
	cmd := exec.Command(binary, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000}
	log, err := os.CreateTemp(filepath.Dir(data), "native-session-*.log")
	if err != nil {
		windows.CloseHandle(job)
		t.Fatal(err)
	}
	cmd.Stdout = log
	cmd.Stderr = log
	if err = cmd.Start(); err != nil {
		log.Close()
		windows.CloseHandle(job)
		t.Fatal(err)
	}
	process, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(cmd.Process.Pid))
	if err == nil {
		err = windows.AssignProcessToJobObject(job, process)
		windows.CloseHandle(process)
	}
	if err != nil {
		cmd.Process.Kill()
		cmd.Wait()
		log.Close()
		windows.CloseHandle(job)
		t.Fatal(err)
	}
	b := &Browser{cmd: cmd, job: job, done: make(chan struct{})}
	go func() { cmd.Wait(); log.Close(); close(b.done) }()
	t.Cleanup(func() {
		if b.client != nil {
			b.client.Close()
		}
		if b.Alive() {
			if !t.Failed() {
				t.Error("candidate remained alive after its scenario")
			}
			if err := b.Crash(); err != nil {
				t.Error("owned candidate cleanup", err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if err := b.Wait(ctx); err != nil {
				t.Error("owned candidate cleanup", err)
			}
		}
		if err := windows.CloseHandle(job); err != nil {
			t.Error("owned job close", err)
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	port := ""
	for port == "" {
		if !b.Alive() {
			t.Fatalf("candidate exited before readiness; inspect %s", log.Name())
		}
		if bytes, err := os.ReadFile(filepath.Join(data, "DevToolsActivePort")); err == nil {
			lines := strings.Split(string(bytes), "\n")
			if n, err := strconv.Atoi(strings.TrimSpace(lines[0])); err == nil && n > 0 && n <= 65535 {
				port = strconv.Itoa(n)
			}
		}
		if port == "" {
			if err := pause(ctx, 50*time.Millisecond); err != nil {
				t.Fatal(err)
			}
		}
	}
	b.client, err = connectCDP(ctx, "http://127.0.0.1:"+port)
	if err != nil {
		t.Fatal(err)
	}
	for {
		count, err := b.VisibleWindows()
		if err != nil {
			t.Fatal(err)
		}
		if count > 0 {
			break
		}
		if err := pause(ctx, 100*time.Millisecond); err != nil {
			t.Fatal("native window not ready", err)
		}
	}
	return b
}
