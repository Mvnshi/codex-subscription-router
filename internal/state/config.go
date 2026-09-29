package state

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const isolatedCredentialConfig = `cli_auth_credentials_store = "file"
mcp_oauth_credentials_store = "file"`

// syncIsolatedConfig shares desktop-managed settings and MCP servers with an
// isolated subscription while keeping its credentials local.
func syncIsolatedConfig(primaryCodexHome, isolatedCodexHome string) error {
	if isolatedCodexHome == "" {
		return errors.New("isolated Codex home is required")
	}
	if err := os.MkdirAll(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("create isolated Codex home: %w", err)
	}
	if err := os.Chmod(isolatedCodexHome, 0o700); err != nil {
		return fmt.Errorf("secure isolated Codex home: %w", err)
	}

	primaryConfig, err := readConfig(filepath.Join(primaryCodexHome, "config.toml"))
	if err != nil {
		return fmt.Errorf("read primary config: %w", err)
	}
	configPath := filepath.Join(isolatedCodexHome, "config.toml")
	isolatedConfig, err := readConfig(configPath)
	if err != nil {
		return fmt.Errorf("read isolated config: %w", err)
	}

	managed := filterConfig(primaryConfig, func(section string) bool {
		return !isProjectSection(section)
	})
	managed = removeTopLevelCredentialSettings(managed)
	// Every subscription works on the same local folders, so a project the
	// Primary account trusts is trusted everywhere. Entries an isolated
	// account created itself are kept unless Primary defines the same project.
	projects := mergeProjectSections(
		filterConfig(primaryConfig, isProjectSection),
		filterConfig(isolatedConfig, isProjectSection),
	)

	parts := []string{isolatedCredentialConfig}
	if managed = strings.TrimSpace(managed); managed != "" {
		parts = append(parts, managed)
	}
	if projects = strings.TrimSpace(projects); projects != "" {
		parts = append(parts, projects)
	}
	contents := []byte(strings.Join(parts, "\n\n") + "\n")
	temporaryPath := configPath + ".tmp"
	if err := os.WriteFile(temporaryPath, contents, 0o600); err != nil {
		return fmt.Errorf("write temporary config: %w", err)
	}
	if err := os.Chmod(temporaryPath, 0o600); err != nil {
		return fmt.Errorf("secure temporary config: %w", err)
	}
	if err := os.Rename(temporaryPath, configPath); err != nil {
		return fmt.Errorf("commit config: %w", err)
	}
	return nil
}

func readConfig(path string) ([]byte, error) {
	contents, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	return contents, err
}

func filterConfig(contents []byte, keep func(section string) bool) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(string(contents), "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if keep(section) {
			builder.WriteString(line)
			builder.WriteByte('\n')
		}
	}
	return builder.String()
}

func removeTopLevelCredentialSettings(contents string) string {
	var builder strings.Builder
	section := ""
	for _, line := range strings.Split(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section = strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
		}
		if section == "" && (strings.HasPrefix(trimmed, "cli_auth_credentials_store =") ||
			strings.HasPrefix(trimmed, "mcp_oauth_credentials_store =")) {
			continue
		}
		builder.WriteString(line)
		builder.WriteByte('\n')
	}
	return builder.String()
}

// mergeProjectSections returns primary's project tables followed by any of
// isolated's tables whose project primary does not define. A project's
// sub-tables (for example [projects."/path".foo]) travel with their parent.
func mergeProjectSections(primary, isolated string) string {
	primaryBlocks := projectBlocks(primary)
	defined := make(map[string]struct{}, len(primaryBlocks))
	parts := make([]string, 0, len(primaryBlocks))
	for _, block := range primaryBlocks {
		defined[block.project] = struct{}{}
		parts = append(parts, block.text)
	}
	for _, block := range projectBlocks(isolated) {
		if _, ok := defined[block.project]; ok {
			continue
		}
		parts = append(parts, block.text)
	}
	return strings.Join(parts, "")
}

type projectBlock struct {
	project string
	text    string
}

func projectBlocks(contents string) []projectBlock {
	blocks := make([]projectBlock, 0)
	var current *projectBlock
	for _, line := range strings.SplitAfter(contents, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			section := strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(trimmed, "["), "]"))
			key := projectKey(section)
			if current == nil || current.project != key {
				blocks = append(blocks, projectBlock{project: key})
				current = &blocks[len(blocks)-1]
			}
		}
		if current == nil {
			continue
		}
		current.text += line
	}
	return blocks
}

// projectKey reduces `projects."/a/b".sub` to `projects."/a/b"`.
func projectKey(section string) string {
	rest := strings.TrimPrefix(section, "projects")
	rest = strings.TrimPrefix(rest, ".")
	if strings.HasPrefix(rest, "\"") {
		if end := strings.Index(rest[1:], "\""); end >= 0 {
			return "projects." + rest[:end+2]
		}
	}
	if dot := strings.Index(rest, "."); dot >= 0 {
		rest = rest[:dot]
	}
	return "projects." + rest
}

func isProjectSection(section string) bool {
	return section == "projects" || strings.HasPrefix(section, "projects.")
}

func samePath(left, right string) bool {
	if left == "" || right == "" {
		return false
	}
	leftAbsolute, leftErr := filepath.Abs(left)
	rightAbsolute, rightErr := filepath.Abs(right)
	if leftErr != nil || rightErr != nil {
		return filepath.Clean(left) == filepath.Clean(right)
	}
	return filepath.Clean(leftAbsolute) == filepath.Clean(rightAbsolute)
}
