//go:build !windows

package main

import (
	"fmt"
	"os"
)

// main exists only so "go build ./..." and "go vet ./..." succeed on macOS and
// Linux, where the launcher has no job: the macOS build uses native/launcher.c.
func main() {
	fmt.Fprintln(os.Stderr, "codex-router-launcher is a Windows-only program")
	os.Exit(2)
}
