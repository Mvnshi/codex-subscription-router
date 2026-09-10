//go:build !windows

package backend

import "os"

// terminate implements Close on macOS and other Unix systems.
//
// This is byte-for-byte the behaviour the macOS product has always shipped:
// SIGINT lets the real app-server run its own shutdown path (flush sqlite,
// release its CODEX_HOME locks) instead of being torn down abruptly, and the
// mux does not wait because the desktop app is already exiting.
func (c *Child) terminate() error {
	if c.command.Process == nil {
		return nil
	}
	return c.command.Process.Signal(os.Interrupt)
}
