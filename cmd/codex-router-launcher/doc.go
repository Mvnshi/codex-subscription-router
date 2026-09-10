// Command codex-router-launcher is the Windows counterpart of native/launcher.c.
//
// The patcher builds it as "Codex Subscription Router.exe" in the root of the
// copied application directory, beside the official Electron executable
// (ChatGPT.exe unless -ldflags "-X main.electronExecutable=..." says otherwise
// at build time). When run it starts that executable from the same directory
// with "--user-data-dir=%APPDATA%\Codex Subscription Router" as the first
// argument, followed by every argument it received, inheriting environment and
// standard handles, and exits with the child's exit code. APPDATA comes from
// the environment with os.UserConfigDir as the fallback.
//
// Why a launcher instead of a shortcut carrying the flag: Chromium reads
// --user-data-dir before Electron's JavaScript runs, so it has to arrive on
// the command line, and on Windows the command line is owned by whoever starts
// the process. Taskbar pins, Start-menu entries, "Open with", and URL-scheme
// (protocol) activations all invoke the registered .exe directly with their own
// arguments; only a shortcut that the user happened to double-click would carry
// the flag. Putting the flag inside the .exe is the only way every entry point
// lands in the isolated profile instead of the official app's, which is the
// property the macOS build gets from launcher.c.
//
// The program is built with -H=windowsgui so no console window flashes, which
// also means nothing it prints is visible; every failure is therefore reported
// through a MessageBox as well as stderr. The pure argument construction lives
// in arguments.go without a build tag so it is unit-tested on every platform;
// main_other.go keeps "go build ./..." working on macOS and Linux by compiling
// to a stub that only explains the program is Windows-only.
package main
