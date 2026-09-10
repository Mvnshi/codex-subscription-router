//go:build windows

package main

import "os"

// shutdownSignals lists the signals that end the multiplexer on Windows.
//
// Go maps console control events (CTRL_C_EVENT, CTRL_BREAK_EVENT, and the
// CTRL_CLOSE/LOGOFF/SHUTDOWN events) to os.Interrupt; syscall.SIGTERM exists
// as a constant on Windows but is never delivered, so listing it would only
// suggest a shutdown path that does not exist. When the desktop app exits it
// closes our stdin instead, which ends serveClientMessages on its own.
func shutdownSignals() []os.Signal {
	return []os.Signal{os.Interrupt}
}
