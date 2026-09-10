//go:build windows

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

const messageBoxTitle = "Codex Subscription Router"

// MessageBoxW flags: MB_OK (0x0) | MB_ICONERROR (0x10). With a NULL owner
// window the dialog is still brought up by the activating shell, so no extra
// foreground flags are needed.
const messageBoxFlags = 0x00000000 | 0x00000010

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "%s launcher: %v\n", messageBoxTitle, err)
		showError(err)
		os.Exit(1)
	}
}

func run() error {
	launcherPath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve launcher path: %w", err)
	}
	// Explorer and the shell start the launcher through whatever path the
	// pin or shortcut recorded; resolve links so the Electron executable is
	// looked up beside the real file, the same way launcher.c uses the
	// Mach-O image path.
	resolvedPath, err := filepath.EvalSymlinks(launcherPath)
	if err != nil {
		return fmt.Errorf("resolve launcher path %s: %w", launcherPath, err)
	}

	appData := os.Getenv("APPDATA")
	if appData == "" {
		// No fallback: os.UserConfigDir is the same %AppData% lookup on
		// Windows, so there is nothing independent to try. Refusing to start
		// mirrors launcher.c's HOME check and the patcher's APPDATA check;
		// guessing a profile directory could land in the official app's.
		return errors.New("APPDATA is not set")
	}

	executable, arguments, err := launcherCommand(resolvedPath, appData, os.Args[1:])
	if err != nil {
		return err
	}
	if _, err := os.Stat(executable); err != nil {
		return fmt.Errorf("the app executable is missing next to the launcher: %w", err)
	}

	command := exec.Command(executable, arguments...)
	command.Dir = filepath.Dir(resolvedPath)
	command.Env = os.Environ()
	// A -H=windowsgui process usually has no console, so these are nil or
	// NULL handles; passing them through unchanged is what the child would
	// get from Explorer anyway, and when started from a console with
	// redirection the child inherits that instead. Only non-nil files are
	// assigned: a typed-nil *os.File reaches CreateProcess as an invalid
	// handle and makes the start fail, whereas leaving the field nil gives
	// the child NUL, which is what a GUI app without a console expects.
	if os.Stdin != nil {
		command.Stdin = os.Stdin
	}
	if os.Stdout != nil {
		command.Stdout = os.Stdout
	}
	if os.Stderr != nil {
		command.Stderr = os.Stderr
	}

	// Run (not Start) so the launcher lives as long as the app: a second
	// activation of a pinned exe is a separate launcher process and Electron's
	// single-instance lock hands its arguments to the running app, then exits.
	err = command.Run()
	if err == nil {
		return nil
	}
	var exitError *exec.ExitError
	if errors.As(err, &exitError) {
		code := exitError.ExitCode()
		if code < 0 {
			// ExitCode reports -1 when no code is available; do not turn
			// that into 0xFFFFFFFF, which callers would read as success-ish
			// garbage. Any non-zero code is the honest answer.
			code = 1
		}
		os.Exit(code)
	}
	return fmt.Errorf("start %s: %w", executable, err)
}

// showError displays err in a modal MessageBox. user32.dll is loaded lazily
// through syscall so the launcher stays free of module dependencies and still
// builds with the repository's dependency-free go.mod.
func showError(err error) {
	text := fmt.Sprintf("%v\n\nThe app was not started.", err)
	textPtr, textErr := utf16Pointer(text)
	titlePtr, titleErr := utf16Pointer(messageBoxTitle)
	if textErr != nil || titleErr != nil {
		return
	}
	user32 := syscall.NewLazyDLL("user32.dll")
	messageBox := user32.NewProc("MessageBoxW")
	if messageBox.Find() != nil {
		return
	}
	_, _, _ = messageBox.Call(
		0,
		uintptr(unsafe.Pointer(textPtr)),
		uintptr(unsafe.Pointer(titlePtr)),
		uintptr(messageBoxFlags),
	)
}

// utf16Pointer converts s to a NUL-terminated UTF-16 string for Win32. Error
// text can carry a NUL from a corrupt environment; it is replaced rather than
// dropped so the dialog still appears.
func utf16Pointer(s string) (*uint16, error) {
	return syscall.UTF16PtrFromString(strings.ReplaceAll(s, "\x00", "?"))
}
