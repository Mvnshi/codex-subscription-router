package main

import (
	"path/filepath"
	"strings"
	"testing"
)

// drivePath builds an absolute fixture path rooted at a Windows drive letter.
// filepath.Join("C:", "Users", ...) must not be used for these: Join keeps a
// bare drive letter drive-RELATIVE on Windows ("C:Users\me"), a shape the
// launcher never receives, so the windows CI job would model the wrong input.
// Appending the separator to the drive ("C:\" on Windows, "C:/" on POSIX,
// where these tests also run) keeps the result absolute. The elements are
// spelled separately rather than as one slash-separated string so the tracked
// source never contains a slash-delimited "Users" home prefix, which the
// release check rejects as a machine-specific path.
func drivePath(drive string, elements ...string) string {
	return filepath.Join(append([]string{drive + string(filepath.Separator)}, elements...)...)
}

func setElectronExecutable(t *testing.T, name string) {
	t.Helper()
	previous := electronExecutable
	electronExecutable = name
	t.Cleanup(func() { electronExecutable = previous })
}

func TestLauncherCommandDefaultExecutable(t *testing.T) {
	if electronExecutable != "ChatGPT.exe" {
		t.Fatalf("default electronExecutable = %q, want ChatGPT.exe", electronExecutable)
	}
	installDir := drivePath("C:", "Users", "me", "AppData", "Local", "Programs", "Codex Subscription Router")
	launcher := filepath.Join(installDir, "Codex Subscription Router.exe")
	appData := drivePath("C:", "Users", "me", "AppData", "Roaming")

	executable, arguments, err := launcherCommand(launcher, appData, nil)
	if err != nil {
		t.Fatalf("launcherCommand: %v", err)
	}
	if want := filepath.Join(installDir, "ChatGPT.exe"); executable != want {
		t.Fatalf("executable = %q, want %q", executable, want)
	}
	wantArguments := []string{"--user-data-dir=" + filepath.Join(appData, "Codex Subscription Router")}
	if strings.Join(arguments, "\x00") != strings.Join(wantArguments, "\x00") {
		t.Fatalf("arguments = %q, want %q", arguments, wantArguments)
	}
}

func TestLauncherCommandOverriddenExecutable(t *testing.T) {
	setElectronExecutable(t, "Codex.exe")
	installDir := drivePath("D:", "Apps", "Router")
	executable, _, err := launcherCommand(filepath.Join(installDir, "launcher.exe"), drivePath("D:", "Roaming"), nil)
	if err != nil {
		t.Fatalf("launcherCommand: %v", err)
	}
	if want := filepath.Join(installDir, "Codex.exe"); executable != want {
		t.Fatalf("executable = %q, want %q", executable, want)
	}
}

func TestLauncherCommandPassesArgumentsThroughInOrder(t *testing.T) {
	appData := drivePath("C:", "Users", "me", "AppData", "Roaming")
	passthrough := []string{"codex://open?thread=1", "--enable-logging", "--user-data-dir=ignored", "-c", "x=y"}
	_, arguments, err := launcherCommand(drivePath("C:", "app", "launcher.exe"), appData, passthrough)
	if err != nil {
		t.Fatalf("launcherCommand: %v", err)
	}
	if len(arguments) != len(passthrough)+1 {
		t.Fatalf("got %d arguments, want %d", len(arguments), len(passthrough)+1)
	}
	if want := "--user-data-dir=" + filepath.Join(appData, "Codex Subscription Router"); arguments[0] != want {
		t.Fatalf("arguments[0] = %q, want %q", arguments[0], want)
	}
	for index, want := range passthrough {
		if arguments[index+1] != want {
			t.Fatalf("arguments[%d] = %q, want %q", index+1, arguments[index+1], want)
		}
	}
	// The returned slice must not alias the caller's slice.
	passthrough[0] = "mutated"
	if arguments[1] == "mutated" {
		t.Fatal("arguments alias the caller's slice")
	}
}

func TestLauncherCommandErrors(t *testing.T) {
	launcher := drivePath("C:", "app", "launcher.exe")
	appData := drivePath("C:", "Users", "me", "AppData", "Roaming")

	if _, _, err := launcherCommand("", appData, nil); err == nil {
		t.Fatal("empty launcher path unexpectedly succeeded")
	}
	if _, _, err := launcherCommand(launcher, "", nil); err == nil {
		t.Fatal("empty APPDATA unexpectedly succeeded")
	}
	for _, invalid := range []string{"", `..\ChatGPT.exe`, "../ChatGPT.exe", `C:\Windows\System32\cmd.exe`, "sub/ChatGPT.exe"} {
		setElectronExecutable(t, invalid)
		if _, _, err := launcherCommand(launcher, appData, nil); err == nil {
			t.Fatalf("electronExecutable %q unexpectedly succeeded", invalid)
		}
	}
}
