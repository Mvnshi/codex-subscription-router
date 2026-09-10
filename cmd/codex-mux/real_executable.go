package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// realExecutableCandidates returns the file names the mux accepts for the
// original Codex binary the patcher parks beside it, most specific first.
//
// macOS keeps the historical single name "codex.real" so existing installs
// are untouched. On Windows the patcher renames the official codex.exe to
// codex.real.exe (Windows will not execute a file without an executable
// extension, and CreateProcess appends ".exe" only when no extension is
// present, which "codex.real" already has); the bare name is still accepted
// second so a hand-built install that copied the macOS layout keeps working.
func realExecutableCandidates(goos string) []string {
	if goos == "windows" {
		return []string{"codex.real.exe", "codex.real"}
	}
	return []string{"codex.real"}
}

// findRealExecutable returns the first candidate that exists in directory.
//
// It is separate from resolveRealExecutable so the lookup can be tested
// against a temporary directory: os.Executable cannot be redirected in tests.
// Any stat failure other than "does not exist" is returned immediately rather
// than skipped, because a candidate we cannot inspect must not be silently
// passed over in favour of a later one.
func findRealExecutable(directory, goos string) (string, error) {
	candidates := realExecutableCandidates(goos)
	for _, candidate := range candidates {
		path := filepath.Join(directory, candidate)
		_, err := os.Stat(path)
		if err == nil {
			return path, nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", fmt.Errorf("find bundled %s: %w", candidate, err)
		}
	}
	return "", fmt.Errorf(
		"find bundled real Codex executable: none of %s exists in %s",
		strings.Join(candidates, ", "),
		directory,
	)
}

// resolveRealExecutable locates the original Codex binary the mux wraps.
// CODEX_MUX_REAL_CODEX overrides the lookup for development and tests;
// otherwise the binary must sit next to the mux itself, exactly where the
// patcher put it.
func resolveRealExecutable() (string, error) {
	if configured := os.Getenv("CODEX_MUX_REAL_CODEX"); configured != "" {
		return configured, nil
	}
	executable, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("resolve wrapper executable: %w", err)
	}
	return findRealExecutable(filepath.Dir(executable), runtime.GOOS)
}
