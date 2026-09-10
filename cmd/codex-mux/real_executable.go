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
// are untouched. On Windows the patcher parks the official codex.exe as
// codex.real.exe so it keeps a conventional executable extension for the
// shell, Explorer, and the patcher's exact-name checks. CreateProcess itself
// runs any PE image whatever its extension, and Go's os/exec accepts an
// existing path that already carries one, which is why the bare macOS name
// still works and is accepted second: a hand-built install that copied the
// macOS layout keeps running.
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
	var lastErr error
	for _, candidate := range candidates {
		path := filepath.Join(directory, candidate)
		_, err := os.Stat(path)
		if err == nil {
			return path, nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", fmt.Errorf("find bundled %s: %w", candidate, err)
		}
		lastErr = err
	}
	if len(candidates) == 1 {
		// A single candidate is the macOS layout, whose failure has always
		// read "find bundled codex.real: stat <path>: no such file or
		// directory". Keep that text byte-identical and keep the *PathError
		// wrapped so errors.Is(err, fs.ErrNotExist) holds as it did before
		// the Windows candidates were added.
		return "", fmt.Errorf("find bundled %s: %w", candidates[0], lastErr)
	}
	// Several candidates: name them all so a Windows install with the wrong
	// file name is diagnosable, and still wrap the last stat error so
	// errors.Is behaves the same on every platform.
	return "", fmt.Errorf(
		"find bundled real Codex executable: none of %s exists in %s: %w",
		strings.Join(candidates, ", "),
		directory,
		lastErr,
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
