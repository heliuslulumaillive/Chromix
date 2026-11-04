// Package nativesession contains the single set of session lifecycle scenarios
// shared by kernel candidate acceptance and the desktop manager integration tests.
package nativesession

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type Object = map[string]any

// Browser separates scenario assertions from the product/standalone launchers.
// Close must use the native exit message, preserve beforeunload cancellation,
// and wait for actual process exit. Cleanup must own only the launched process.
type Browser interface {
	Request(context.Context, string, Object, string) (Object, error)
	Close(context.Context) error
	CloseNormally() error
	Crash() error
	Wait(context.Context) error
	Alive() bool
	CleanExit() bool
	HideWindows() error
	VisibleWindows() (int, error)
	CloseVisibleWindow() error
}
type Launcher func(t *testing.T, data string, restore bool, start string) Browser

type object = Object

func array(value any) []any               { result, _ := value.([]any); return result }
func obj(value any) object                { result, _ := value.(map[string]any); return result }
func str(value object, key string) string { result, _ := value[key].(string); return result }

type sessionCandidate struct{ browser Browser }

func launchSessionCandidate(t *testing.T, launch Launcher, data string, restore bool, start string) *sessionCandidate {
	t.Helper()
	return &sessionCandidate{browser: launch(t, data, restore, start)}
}

func (c *sessionCandidate) call(t *testing.T, method string, params object, session string) object {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result, err := c.browser.Request(ctx, method, params, session)
	if err != nil {
		t.Fatal(method, err)
	}
	return result
}

func (c *sessionCandidate) pages(t *testing.T) []object {
	t.Helper()
	result := c.call(t, "Target.getTargets", object{}, "")
	pages := []object{}
	for _, raw := range array(result["targetInfos"]) {
		if row := obj(raw); row["type"] == "page" {
			pages = append(pages, row)
		}
	}
	return pages
}

func (c *sessionCandidate) waitURLs(t *testing.T, expected []string) []object {
	t.Helper()
	want := append([]string{}, expected...)
	sort.Strings(want)
	deadline := time.Now().Add(15 * time.Second)
	var got []string
	for time.Now().Before(deadline) {
		pages := c.pages(t)
		got = nil
		for _, page := range pages {
			got = append(got, str(page, "url"))
		}
		sort.Strings(got)
		if reflect.DeepEqual(got, want) {
			return pages
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("restored URLs differ: got %v want %v", got, want)
	return nil
}

func (c *sessionCandidate) windowGroups(t *testing.T) []string {
	t.Helper()
	groups := map[string][]string{}
	for _, page := range c.pages(t) {
		window := c.call(t, "Browser.getWindowForTarget", object{"targetId": page["targetId"]}, "")
		key := fmt.Sprint(window["windowId"])
		groups[key] = append(groups[key], str(page, "url"))
	}
	result := []string{}
	for _, urls := range groups {
		sort.Strings(urls)
		result = append(result, strings.Join(urls, " | "))
	}
	sort.Strings(result)
	return result
}

func (c *sessionCandidate) close(t *testing.T) {
	t.Helper()
	if err := c.browser.CloseNormally(); err != nil {
		t.Fatal("native whole-environment exit", err)
	}
	if !c.browser.CleanExit() {
		t.Fatal("normal exit did not finish cleanly")
	}
}

func (c *sessionCandidate) waitExit(t *testing.T) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.browser.Wait(ctx); err != nil {
		t.Fatal("owned browser did not exit", err)
	}
}

func NativeSessionLifecycle(t *testing.T, launch Launcher) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprintf(w, "<!doctype html><title>%s</title><button id=activate>activate</button>", r.URL.Path)
	}))
	defer server.Close()
	data := filepath.Join(t.TempDir(), "session-environment")
	a, b, duplicate, start, crash := server.URL+"/alpha", server.URL+"/beta", server.URL+"/duplicate", server.URL+"/start", server.URL+"/crash"
	first := launchSessionCandidate(t, launch, data, true, a)
	first.waitURLs(t, []string{a})
	for _, url := range []string{duplicate, duplicate} {
		first.call(t, "Target.createTarget", object{"url": url}, "")
	}
	first.call(t, "Target.createTarget", object{"url": b, "newWindow": true}, "")
	first.waitURLs(t, []string{a, b, duplicate, duplicate})
	groups := first.windowGroups(t)
	if len(groups) != 2 {
		t.Fatal("test setup did not create two native windows", groups)
	}
	first.close(t)
	second := launchSessionCandidate(t, launch, data, true, a)
	second.waitURLs(t, []string{a, b, duplicate, duplicate})
	if got := second.windowGroups(t); !reflect.DeepEqual(got, groups) {
		t.Fatal("window membership changed", got, groups)
	}
	second.close(t)
	third := launchSessionCandidate(t, launch, data, true, start)
	third.waitURLs(t, []string{a, b, duplicate, duplicate, start})
	third.call(t, "Target.createTarget", object{"url": crash}, "")
	third.waitURLs(t, []string{a, b, duplicate, duplicate, start, crash})
	// Allow the native incremental session writer to persist this fixture before
	// simulating process loss. The product does not promise unflushed last edits.
	time.Sleep(4 * time.Second)
	if err := third.browser.Crash(); err != nil {
		t.Fatal("cannot simulate owned-process crash", err)
	}
	third.waitExit(t)
	fourth := launchSessionCandidate(t, launch, data, true, start)
	fourth.waitURLs(t, []string{a, b, duplicate, duplicate, start, crash})
	pages := fourth.pages(t)
	windowIDs := map[string]string{}
	closedWindow := ""
	for _, page := range pages {
		id := str(page, "targetId")
		window := fourth.call(t, "Browser.getWindowForTarget", object{"targetId": id}, "")
		windowIDs[id] = fmt.Sprint(window["windowId"])
		if str(page, "url") == b {
			closedWindow = windowIDs[id]
		}
	}
	if closedWindow == "" {
		t.Fatal("missing window to close")
	}
	remaining := []string{}
	for _, page := range pages {
		id := str(page, "targetId")
		if windowIDs[id] == closedWindow {
			fourth.call(t, "Target.closeTarget", object{"targetId": id}, "")
		} else {
			remaining = append(remaining, str(page, "url"))
		}
	}
	fourth.waitURLs(t, remaining)
	if len(fourth.windowGroups(t)) != 1 {
		t.Fatal("explicitly closed window remained")
	}
	fourth.close(t)
	fifth := launchSessionCandidate(t, launch, data, true, start)
	hasStart := false
	for _, url := range remaining {
		hasStart = hasStart || url == start
	}
	if !hasStart {
		remaining = append(remaining, start)
	}
	fifth.waitURLs(t, remaining)
	fifth.close(t)
	sixth := launchSessionCandidate(t, launch, data, false, start)
	sixth.waitURLs(t, []string{start})
	sixth.close(t)
	seventh := launchSessionCandidate(t, launch, data, false, "")
	seventh.waitURLs(t, []string{"chrome://newtab/"})
	seventh.close(t)
	t.Log("passed: multiple native windows, duplicate tabs, startup URL deduplication/addition, abnormal-exit recovery, intentionally closed window exclusion, restore disabled and empty startup")
}

func NativeExitRespectsBeforeUnloadCancellation(t *testing.T, launch Launcher) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, "<!doctype html><title>Owned unsaved-form fixture</title><button style='width:200px;height:60px'>Activate form</button>")
	}))
	defer server.Close()
	c := launchSessionCandidate(t, launch, filepath.Join(t.TempDir(), "blocked-environment"), true, server.URL+"/")
	pages := c.waitURLs(t, []string{server.URL + "/"})
	attached := c.call(t, "Target.attachToTarget", object{"targetId": pages[0]["targetId"], "flatten": true}, "")
	session := str(attached, "sessionId")
	c.call(t, "Page.enable", object{}, session)
	c.call(t, "Runtime.evaluate", object{"expression": "window.onbeforeunload = event => { event.preventDefault(); event.returnValue = ''; };", "userGesture": true}, session)
	for _, event := range []string{"mousePressed", "mouseReleased"} {
		c.call(t, "Input.dispatchMouseEvent", object{"type": event, "x": 30, "y": 30, "button": "left", "clickCount": 1}, session)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- c.browser.Close(ctx) }()
	dialogCancelled := false
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		_, err := c.browser.Request(ctx, "Page.handleJavaScriptDialog", object{"accept": false}, session)
		if err == nil {
			dialogCancelled = true
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if !dialogCancelled {
		t.Fatal("native exit did not offer the activated page's beforeunload dialog")
	}
	if err := <-done; err == nil {
		t.Fatal("cancelled native exit reported success")
	}
	if !c.browser.Alive() {
		t.Fatal("cancelled exit killed the environment")
	}
	c.call(t, "Runtime.evaluate", object{"expression": "window.onbeforeunload=null"}, session)
	c.close(t)
	t.Log("passed: native beforeunload cancellation retains the live environment; a subsequent normal exit succeeds")
}

func NativeExitWithoutVisibleWindows(t *testing.T, launch Launcher) {
	c := launchSessionCandidate(t, launch, filepath.Join(t.TempDir(), "hidden-environment"), true, "about:blank")
	if err := c.browser.HideWindows(); err != nil {
		t.Fatal(err)
	}
	visible, err := c.browser.VisibleWindows()
	if err != nil || visible != 0 || !c.browser.Alive() {
		t.Fatal("fixture did not retain a live browser with no visible windows", err)
	}
	c.close(t)
	t.Log("passed: process-level normal exit works with no visible browser windows")
}

func NativePendingNewTabSurvivesNormalExit(t *testing.T, launch Launcher) {
	var blocking atomic.Bool
	blocking.Store(true)
	requested := make(chan struct{}, 1)
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/pending" && blocking.Load() {
			select {
			case requested <- struct{}{}:
			default:
			}
			select {
			case <-r.Context().Done():
			case <-release:
			}
			return
		}
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, "<!doctype html><title>Owned pending-navigation fixture</title>")
	}))
	defer server.Close()
	defer close(release) // Unblock withheld responses before server cleanup on failure.
	data := filepath.Join(t.TempDir(), "pending-environment")
	start, pending := server.URL+"/start", server.URL+"/pending"
	first := launchSessionCandidate(t, launch, data, true, start)
	first.waitURLs(t, []string{start})
	first.call(t, "Target.createTarget", object{"url": pending, "newWindow": true}, "")
	select {
	case <-requested:
	case <-time.After(10 * time.Second):
		t.Fatal("pending tab never requested its URL")
	}
	// The response is deliberately withheld. Closing must save the visible tab's
	// pending URL, without waiting for this server or relying on a save timer.
	first.close(t)
	blocking.Store(false)
	second := launchSessionCandidate(t, launch, data, true, start)
	second.waitURLs(t, []string{start, pending})
	second.close(t)
	t.Log("passed: a new tab awaiting its first response survives normal exit")
}

func NativePendingTabSurvivesLastWindowClose(t *testing.T, launch Launcher) {
	var blocking atomic.Bool
	blocking.Store(true)
	requested := make(chan struct{}, 1)
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if blocking.Load() {
			select {
			case requested <- struct{}{}:
			default:
			}
			select {
			case <-r.Context().Done():
			case <-release:
			}
			return
		}
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, "<!doctype html><title>Owned last-window fixture</title>")
	}))
	defer server.Close()
	defer close(release) // Unblock withheld responses before server cleanup on failure.
	data := filepath.Join(t.TempDir(), "pending-last-window")
	url := server.URL + "/pending"
	first := launchSessionCandidate(t, launch, data, true, url)
	select {
	case <-requested:
	case <-time.After(10 * time.Second):
		t.Fatal("initial tab never requested its URL")
	}
	if err := first.browser.CloseVisibleWindow(); err != nil {
		t.Fatal(err)
	}
	first.waitExit(t)
	if !first.browser.CleanExit() {
		t.Fatal("last window did not close normally")
	}
	blocking.Store(false)
	second := launchSessionCandidate(t, launch, data, true, "")
	second.waitURLs(t, []string{url})
	second.close(t)
	t.Log("passed: closing the final window preserves its pending first navigation")
}
