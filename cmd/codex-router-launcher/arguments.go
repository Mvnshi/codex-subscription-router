package main

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

// electronExecutable is the file name of the official Electron executable that
// sits beside the launcher. It is a var, not a const, so the patcher can pin
// the name it discovered in the copied install at build time with
// -ldflags "-X main.electronExecutable=<name>"; the default matches the
// official ChatGPT desktop layout the macOS launcher hard-codes as "ChatGPT".
var electronExecutable = "ChatGPT.exe"

// profileDirectoryName is the Chromium profile directory under %APPDATA%. It
// mirrors "~/Library/Application Support/Codex Subscription Router" on macOS
// and must never collide with the official app's own directory, or both
// builds would share cookies, sessions, and the Electron userData profile.
const profileDirectoryName = "Codex Subscription Router"

// launcherCommand computes the executable and argument list the launcher runs.
//
// launcherPath is the launcher's own resolved path (its directory is where the
// Electron executable is expected), appData is the %APPDATA% root, and args are
// the launcher's own arguments minus argv[0], passed through verbatim after
// the profile flag. Only the file name in electronExecutable is accepted: a
// path component would let a build flag redirect the launcher outside its own
// directory, which is exactly what the sibling-only lookup exists to prevent.
func launcherCommand(launcherPath, appData string, args []string) (executable string, arguments []string, err error) {
	if launcherPath == "" {
		return "", nil, errors.New("launcher path is empty")
	}
	if appData == "" {
		return "", nil, errors.New("APPDATA is not set and no user configuration directory is available")
	}
	// Check both separators regardless of host OS: this also runs in tests on
	// macOS and Linux, and a Windows executable name never contains either.
	if electronExecutable == "" || strings.ContainsAny(electronExecutable, `/\`) {
		return "", nil, fmt.Errorf("electron executable must be a bare file name, got %q", electronExecutable)
	}
	executable = filepath.Join(filepath.Dir(launcherPath), electronExecutable)
	arguments = make([]string, 0, len(args)+1)
	arguments = append(arguments, "--user-data-dir="+filepath.Join(appData, profileDirectoryName))
	arguments = append(arguments, args...)
	return executable, arguments, nil
}
