//go:build windows

package nativesession_test

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	suite "shardx-lite/native-session"
	"shardx-lite/native-session/internal/harness"
)

func candidate(t *testing.T) suite.Launcher {
	t.Helper()
	binary := os.Getenv("CHROMIX_LITE_SESSION_CANDIDATE")
	expected := os.Getenv("CHROMIX_LITE_SESSION_CANDIDATE_DLL_SHA256")
	if binary == "" || len(expected) != 64 {
		t.Fatal("an exact isolated candidate and DLL hash are required; acceptance cannot skip")
	}
	digest, err := hex.DecodeString(expected)
	if err != nil || len(digest) != 32 {
		t.Fatal("invalid candidate DLL hash")
	}
	file, err := os.Open(filepath.Join(filepath.Dir(binary), "chrome.dll"))
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.New()
	_, err = io.Copy(hash, file)
	file.Close()
	if err != nil || hex.EncodeToString(hash.Sum(nil)) != expected {
		t.Fatal("candidate DLL differs", err)
	}
	caps, err := os.ReadFile(filepath.Join(filepath.Dir(binary), "native-browser-capabilities.json"))
	if err != nil {
		t.Fatal(err)
	}
	_, source, _, _ := runtime.Caller(0)
	current, err := os.ReadFile(filepath.Join(filepath.Dir(source), "..", "..", "build", "native-browser-capabilities.json"))
	if err != nil {
		t.Fatal(err)
	}
	if sha256.Sum256(caps) != sha256.Sum256(current) {
		t.Fatal("candidate contract differs from this checkout")
	}
	var contract map[string]any
	if json.Unmarshal(caps, &contract) != nil {
		t.Fatal("invalid candidate contract")
	}
	lifecycle, ok := contract["session_lifecycle"].(map[string]any)
	if !ok || lifecycle["whole_environment_exit"] != "ShardX.Lite.ExitEnvironment.v1" {
		t.Fatal("candidate lacks the native exit contract")
	}
	t.Logf("native session candidate DLL: %s", expected)
	return func(t *testing.T, data string, restore bool, start string) suite.Browser {
		return harness.Launch(t, binary, data, restore, start)
	}
}

func TestCandidateNativeSessionLifecycle(t *testing.T) { suite.NativeSessionLifecycle(t, candidate(t)) }

func TestCandidateNativeExitRespectsBeforeUnloadCancellation(t *testing.T) {
	suite.NativeExitRespectsBeforeUnloadCancellation(t, candidate(t))
}

func TestCandidateNativeExitWithoutVisibleWindows(t *testing.T) {
	suite.NativeExitWithoutVisibleWindows(t, candidate(t))
}

func TestCandidateNativePendingNewTabSurvivesNormalExit(t *testing.T) {
	suite.NativePendingNewTabSurvivesNormalExit(t, candidate(t))
}

func TestCandidateNativePendingTabSurvivesLastWindowClose(t *testing.T) {
	suite.NativePendingTabSurvivesLastWindowClose(t, candidate(t))
}
