package main

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/b-nnett/codex-subscription-router/internal/protocol"
)

func TestInteractiveAppServerDetection(t *testing.T) {
	tests := []struct {
		args []string
		want bool
	}{
		{args: []string{"-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled"}, want: true},
		{args: []string{"app-server", "daemon", "version"}, want: false},
		{args: []string{"app-server", "generate-ts", "--out", "/tmp/schema"}, want: false},
		{args: []string{"exec", "hello"}, want: false},
	}
	for _, test := range tests {
		if got := isInteractiveAppServer(test.args); got != test.want {
			t.Fatalf("isInteractiveAppServer(%q)=%v, want %v", test.args, got, test.want)
		}
	}
}

func TestValidateControlToken(t *testing.T) {
	valid := "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	if got, err := validateControlToken("\n" + valid + "\t"); err != nil || got != valid {
		t.Fatalf("validateControlToken(valid) = %q, %v", got, err)
	}
	for _, invalid := range []string{"short", valid + "00", valid[:63] + "z"} {
		if _, err := validateControlToken(invalid); err == nil {
			t.Fatalf("validateControlToken(%q) unexpectedly succeeded", invalid)
		}
	}
}

func TestServeClientMessagesReturnsWhenContextIsCanceled(t *testing.T) {
	reader, writer := io.Pipe()
	t.Cleanup(func() {
		_ = reader.Close()
		_ = writer.Close()
	})

	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		result <- serveClientMessages(
			ctx,
			reader,
			func(protocol.Message) {},
			io.Discard,
		)
	}()

	cancel()
	select {
	case err := <-result:
		if err != nil {
			t.Fatalf("canceled client reader returned an error: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("client reader stayed blocked on stdin after cancellation")
	}
}

func TestRealExecutableCandidates(t *testing.T) {
	tests := []struct {
		goos string
		want []string
	}{
		{goos: "windows", want: []string{"codex.real.exe", "codex.real"}},
		{goos: "darwin", want: []string{"codex.real"}},
		{goos: "linux", want: []string{"codex.real"}},
		{goos: "", want: []string{"codex.real"}},
	}
	for _, test := range tests {
		got := realExecutableCandidates(test.goos)
		if strings.Join(got, ",") != strings.Join(test.want, ",") {
			t.Fatalf("realExecutableCandidates(%q)=%q, want %q", test.goos, got, test.want)
		}
	}
}

func TestFindRealExecutable(t *testing.T) {
	tests := []struct {
		name    string
		goos    string
		present []string
		want    string
		wantErr []string
	}{
		{name: "windows prefers exe", goos: "windows", present: []string{"codex.real.exe", "codex.real"}, want: "codex.real.exe"},
		{name: "windows exe only", goos: "windows", present: []string{"codex.real.exe"}, want: "codex.real.exe"},
		{name: "windows bare fallback", goos: "windows", present: []string{"codex.real"}, want: "codex.real"},
		{name: "windows none names both", goos: "windows", wantErr: []string{"codex.real.exe", "codex.real"}},
		{name: "darwin bare", goos: "darwin", present: []string{"codex.real"}, want: "codex.real"},
		{name: "darwin ignores exe", goos: "darwin", present: []string{"codex.real.exe"}, wantErr: []string{"codex.real"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			directory := t.TempDir()
			for _, name := range test.present {
				if err := os.WriteFile(filepath.Join(directory, name), []byte("#!/bin/sh\n"), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			got, err := findRealExecutable(directory, test.goos)
			if len(test.wantErr) > 0 {
				if err == nil {
					t.Fatalf("findRealExecutable returned %q, want error", got)
				}
				for _, candidate := range test.wantErr {
					if !strings.Contains(err.Error(), candidate) {
						t.Fatalf("error %q does not name candidate %q", err, candidate)
					}
				}
				if !strings.Contains(err.Error(), directory) {
					t.Fatalf("error %q does not name directory %q", err, directory)
				}
				return
			}
			if err != nil {
				t.Fatalf("findRealExecutable: %v", err)
			}
			if want := filepath.Join(directory, test.want); got != want {
				t.Fatalf("findRealExecutable=%q, want %q", got, want)
			}
		})
	}
}

func TestResolveRealExecutableHonoursOverride(t *testing.T) {
	override := filepath.Join(t.TempDir(), "custom-codex")
	t.Setenv("CODEX_MUX_REAL_CODEX", override)
	got, err := resolveRealExecutable()
	if err != nil || got != override {
		t.Fatalf("resolveRealExecutable() = %q, %v; want %q", got, err, override)
	}
}
