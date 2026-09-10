//go:build windows

package backend

import (
	"errors"
	"os"
	"time"
)

// terminateGrace bounds how long a Windows child may take to exit after its
// stdin is closed before it is killed outright. Two seconds is generous for an
// idle app-server that has just seen EOF, and a longer wait buys nothing: the
// child's sqlite state is crash-safe (WAL journal), the mux itself is already
// shutting down because the desktop app went away, and Multiplexer.Close runs
// children sequentially so the wait is paid once per account.
const terminateGrace = 2 * time.Second

// terminate implements Close on Windows.
//
// Go's os.Process.Signal only supports os.Kill on Windows; Signal(os.Interrupt)
// returns "not supported by windows" and would leave every child running after
// the mux exits (Windows has no process groups that die with the parent). The
// closest equivalent of SIGINT for a stdio JSON-RPC server is closing its
// stdin: the codex app-server treats EOF on stdin as "client gone" and exits
// through the same orderly path SIGINT triggers on macOS. If it has not exited
// within terminateGrace it is killed so the mux never leaves an orphan behind.
func (c *Child) terminate() error {
	if c.command.Process == nil {
		return nil
	}
	// *os.File is safe for concurrent Write and Close (documented on os.File),
	// so this does not race SendRaw: a Write that arrives after Close fails
	// with ErrClosed, which is correct during shutdown, and a Write already
	// blocked on a full pipe is cancelled (poll.FD.Close issues CancelIoEx for
	// pipes) instead of stalling this function before the timer starts. exec's
	// Wait closes the same *os.File again later; that second Close only
	// returns ErrClosed, which Wait's closeDescriptors ignores. StdinPipe
	// hands back the bare *os.File from os.Pipe, so there is no close-once
	// wrapper to lean on: the double close is simply harmless.
	_ = c.stdin.Close()

	timer := time.NewTimer(terminateGrace)
	defer timer.Stop()
	select {
	case <-c.closed:
		return nil
	case <-timer.C:
	}

	err := c.command.Process.Kill()
	if err == nil || errors.Is(err, os.ErrProcessDone) {
		// ErrProcessDone means the child exited between the timeout and the
		// kill, which is the outcome we wanted.
		return nil
	}
	return err
}
