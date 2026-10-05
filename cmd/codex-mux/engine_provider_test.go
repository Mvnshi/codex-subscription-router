package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func envOf(values map[string]string) func(string) string {
	return func(key string) string { return values[key] }
}

func writeProviderFile(t *testing.T, root, contents string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(root, "engine-provider"), []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestEngineProviderIsOffByDefault(t *testing.T) {
	got, err := resolveEngineProvider(t.TempDir(), envOf(nil))
	if err != nil || got != "" {
		t.Fatalf("with nothing configured the Codex config must be left alone, got %q (%v)", got, err)
	}
}

func TestEngineProviderComesFromTheEnvironmentFirst(t *testing.T) {
	root := t.TempDir()
	writeProviderFile(t, root, "from-file\n")
	got, err := resolveEngineProvider(root, envOf(map[string]string{"CODEX_MUX_ENGINE_PROVIDER": "  openai "}))
	if err != nil || got != "openai" {
		t.Fatalf("the environment must win and be trimmed, got %q (%v)", got, err)
	}
}

func TestEngineProviderFileUsesItsFirstLineOnly(t *testing.T) {
	for contents, want := range map[string]string{
		"openai":                   "openai",
		"openai\n":                 "openai",
		"  openai  \r\n":           "openai",
		"openai\nignored\nlines\n": "openai",
		"\n":                       "",
		"":                         "",
	} {
		root := t.TempDir()
		writeProviderFile(t, root, contents)
		got, err := resolveEngineProvider(root, envOf(nil))
		if err != nil || got != want {
			t.Errorf("file %q: got %q (%v), want %q", contents, got, err, want)
		}
	}
}

func TestInvalidEngineProviderIsAnErrorNotSilentlyIgnored(t *testing.T) {
	for _, bad := range []string{"a b", "-c", `a"b`, "a=b", "../x", "a;b"} {
		_, err := resolveEngineProvider(t.TempDir(), envOf(map[string]string{"CODEX_MUX_ENGINE_PROVIDER": bad}))
		if err == nil || !strings.Contains(err.Error(), "CODEX_MUX_ENGINE_PROVIDER") {
			t.Errorf("env %q must be rejected and name its source, got %v", bad, err)
		}
		root := t.TempDir()
		writeProviderFile(t, root, bad+"\n")
		_, err = resolveEngineProvider(root, envOf(nil))
		if err == nil || !strings.Contains(err.Error(), "engine-provider") {
			t.Errorf("file %q must be rejected and name its source, got %v", bad, err)
		}
	}
}

func TestAnUnreadableEngineProviderFileIsAnError(t *testing.T) {
	root := t.TempDir()
	// A directory where the file should be: present but not readable as a file.
	if err := os.Mkdir(filepath.Join(root, "engine-provider"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveEngineProvider(root, envOf(nil)); err == nil {
		t.Fatal("a setting that exists but cannot be read must not be treated as unset")
	}
}
