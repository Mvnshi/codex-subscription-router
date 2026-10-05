package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/control"
	"github.com/b-nnett/codex-subscription-router/internal/mux"
	"github.com/b-nnett/codex-subscription-router/internal/protocol"
	"github.com/b-nnett/codex-subscription-router/internal/state"
)

const defaultControlPort = 48123

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	realExecutable, err := resolveRealExecutable()
	if err != nil {
		return err
	}
	args := os.Args[1:]
	if !isInteractiveAppServer(args) {
		return passthrough(realExecutable, args)
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("resolve home directory: %w", err)
	}
	root := os.Getenv("CODEX_MUX_HOME")
	if root == "" {
		root = filepath.Join(home, ".codex-mux")
	}
	primaryCodexHome := os.Getenv("CODEX_HOME")
	if primaryCodexHome == "" {
		primaryCodexHome = filepath.Join(home, ".codex")
	}
	store, err := state.Open(root, primaryCodexHome)
	if err != nil {
		return err
	}
	engineProvider, err := resolveEngineProvider(root, os.Getenv)
	if err != nil {
		return err
	}

	// syscall.SIGTERM is meaningful on Windows too: Go delivers CTRL_CLOSE,
	// CTRL_LOGOFF and CTRL_SHUTDOWN console events as SIGTERM (Ctrl-C and
	// Ctrl-Break arrive as os.Interrupt), so this list is the same everywhere.
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	multiplexer, err := mux.New(mux.Options{
		RealExecutable: realExecutable,
		RealArgs:       args,
		Environment:    os.Environ(),
		Store:          store,
		Output:         os.Stdout,
		EngineProvider: engineProvider,
	})
	if err != nil {
		return err
	}
	if err := multiplexer.Start(ctx); err != nil {
		return err
	}
	defer multiplexer.Close()

	token, err := loadOrCreateToken(root)
	if err != nil {
		return err
	}
	port := defaultControlPort
	if value := os.Getenv("CODEX_MUX_CONTROL_PORT"); value != "" {
		if parsed, parseErr := strconv.Atoi(value); parseErr == nil && parsed > 0 && parsed <= 65535 {
			port = parsed
		}
	}
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		fmt.Fprintf(os.Stderr, "codex-mux: account UI unavailable: %v\n", err)
	} else {
		controlServer := control.New(
			listener.Addr().String(),
			token,
			multiplexer,
			os.Getenv("CODEX_MUX_UI_TESTS") == "1",
		)
		go func() {
			if serveErr := controlServer.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
				fmt.Fprintf(os.Stderr, "codex-mux: control server: %v\n", serveErr)
			}
		}()
		defer func() {
			shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer shutdownCancel()
			_ = controlServer.Shutdown(shutdownCtx)
		}()
	}

	return serveClientMessages(
		ctx,
		os.Stdin,
		multiplexer.HandleClient,
		os.Stderr,
	)
}

func serveClientMessages(
	ctx context.Context,
	input io.Reader,
	handle func(protocol.Message),
	diagnostics io.Writer,
) error {
	finished := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(input)
		scanner.Buffer(make([]byte, 64*1024), 64*1024*1024)
		for scanner.Scan() {
			select {
			case <-ctx.Done():
				finished <- nil
				return
			default:
			}
			message, parseErr := protocol.Parse(scanner.Bytes())
			if parseErr != nil {
				fmt.Fprintf(diagnostics, "codex-mux: ignore invalid client JSON: %v\n", parseErr)
				continue
			}
			handle(message)
		}
		finished <- scanner.Err()
	}()

	select {
	case err := <-finished:
		return err
	case <-ctx.Done():
		return nil
	}
}

// resolveEngineProvider reads the optional model-provider override for the
// engines the router starts: CODEX_MUX_ENGINE_PROVIDER, else the first line of
// <state root>/engine-provider. Empty (the default) leaves the Codex config alone.
// A value that is not a plain provider id is an error rather than being ignored,
// so a typo cannot silently send traffic through a provider that was meant to be
// bypassed.
func resolveEngineProvider(root string, getenv func(string) string) (string, error) {
	source := "CODEX_MUX_ENGINE_PROVIDER"
	value := strings.TrimSpace(getenv(source))
	if value == "" {
		path := filepath.Join(root, "engine-provider")
		data, err := os.ReadFile(path)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				return "", nil
			}
			return "", fmt.Errorf("read %s: %w", path, err)
		}
		source = path
		value, _, _ = strings.Cut(strings.TrimSpace(string(data)), "\n")
		value = strings.TrimSpace(value)
	}
	if value == "" {
		return "", nil
	}
	if !mux.ValidEngineProvider(value) {
		return "", fmt.Errorf("engine provider %q from %s is not a plain provider id (letters, digits, '.', '_' and '-')", value, source)
	}
	return value, nil
}

func isInteractiveAppServer(args []string) bool {
	for index, argument := range args {
		if argument != "app-server" {
			continue
		}
		if index+1 < len(args) {
			switch args[index+1] {
			case "daemon", "proxy", "generate-ts", "generate-json-schema", "help":
				return false
			}
		}
		return true
	}
	return false
}

func passthrough(realExecutable string, args []string) error {
	command := exec.Command(realExecutable, args...)
	command.Stdin = os.Stdin
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	command.Env = os.Environ()
	if err := command.Run(); err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			os.Exit(exitError.ExitCode())
		}
		return err
	}
	return nil
}

func loadOrCreateToken(root string) (string, error) {
	if configured := os.Getenv("CODEX_MUX_CONTROL_TOKEN"); configured != "" {
		return validateControlToken(configured)
	}
	path := filepath.Join(root, "control-token")
	if data, err := os.ReadFile(path); err == nil {
		token, validateErr := validateControlToken(string(data))
		if validateErr != nil {
			return "", fmt.Errorf("read control token: %w", validateErr)
		}
		if chmodErr := os.Chmod(path, 0o600); chmodErr != nil {
			return "", fmt.Errorf("secure control token: %w", chmodErr)
		}
		return token, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", fmt.Errorf("read control token: %w", err)
	}
	bytes := make([]byte, 32)
	if _, err := rand.Read(bytes); err != nil {
		return "", fmt.Errorf("generate control token: %w", err)
	}
	token := hex.EncodeToString(bytes)
	if err := os.WriteFile(path, []byte(token), 0o600); err != nil {
		return "", fmt.Errorf("write control token: %w", err)
	}
	return token, nil
}

func validateControlToken(value string) (string, error) {
	token := strings.TrimSpace(value)
	decoded, err := hex.DecodeString(token)
	if err != nil || len(decoded) != 32 {
		return "", errors.New("control token must be exactly 32 random bytes encoded as hexadecimal")
	}
	return token, nil
}
