package main

import (
	"context"
	"io"
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
