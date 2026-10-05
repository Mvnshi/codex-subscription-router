package mux

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/b-nnett/codex-subscription-router/internal/state"
)

func newTestStore(t *testing.T) *state.Store {
	t.Helper()
	store, err := state.Open(t.TempDir(), filepath.Join(t.TempDir(), "home"))
	if err != nil {
		t.Fatal(err)
	}
	return store
}

func TestEngineArgumentsLeaveTheConfigAloneByDefault(t *testing.T) {
	args := []string{"-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled"}
	got := engineArguments("", args)
	if !reflect.DeepEqual(got, args) {
		t.Fatalf("no provider must mean no change: %v", got)
	}
	got[0] = "changed"
	if args[0] != "-c" {
		t.Fatal("the result must be a copy, not the caller's slice")
	}
}

func TestEngineArgumentsPutTheProviderFirstBesideTheDesktopsOwnSettings(t *testing.T) {
	args := []string{"-c", "features.code_mode_host=true", "app-server"}
	got := engineArguments("openai", args)
	want := []string{"-c", "model_provider=openai", "-c", "features.code_mode_host=true", "app-server"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if len(args) != 3 {
		t.Fatal("the caller's arguments must not be modified")
	}
}

func TestValidEngineProviderAcceptsOnlyPlainIds(t *testing.T) {
	for _, ok := range []string{"openai", "azure", "my-gateway", "gw_2", "a.b", "O1", strings.Repeat("a", 64)} {
		if !ValidEngineProvider(ok) {
			t.Errorf("%q should be accepted", ok)
		}
	}
	for _, bad := range []string{"", " openai", "openai ", "a b", "-c", "a=b", `a"b`, "a;b", "../x", "a/b", "a\nb", "a\\b", "$(x)", strings.Repeat("a", 65), "é"} {
		if ValidEngineProvider(bad) {
			t.Errorf("%q must be rejected: it becomes part of a command line", bad)
		}
	}
}

func TestNewRefusesAnInvalidEngineProvider(t *testing.T) {
	store := newTestStore(t)
	if _, err := New(Options{RealExecutable: "codex", Store: store, Output: &syncBuffer{}, EngineProvider: "a b"}); err == nil {
		t.Fatal("an invalid provider must stop the multiplexer from starting")
	}
	multiplexer, err := New(Options{
		RealExecutable: "codex", RealArgs: []string{"app-server"}, Store: store,
		Output: &syncBuffer{}, EngineProvider: "openai",
	})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"-c", "model_provider=openai", "app-server"}
	if !reflect.DeepEqual(multiplexer.realArgs, want) {
		t.Fatalf("engines would start with %v, want %v", multiplexer.realArgs, want)
	}
}
