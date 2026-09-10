//go:build !windows

package main

import (
	"os"
	"syscall"
)

// shutdownSignals lists the signals that end the multiplexer on macOS and
// other Unix systems: Ctrl-C from a terminal and the SIGTERM the desktop app
// (and launchd) sends when the app-server connection is torn down.
func shutdownSignals() []os.Signal {
	return []os.Signal{os.Interrupt, syscall.SIGTERM}
}
