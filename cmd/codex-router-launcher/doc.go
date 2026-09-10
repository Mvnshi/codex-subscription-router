// Command codex-router-launcher is the Windows counterpart of native/launcher.c.
//
// The patcher builds it as "Codex Subscription Router.exe" in the root of the
// copied application directory, beside the official Electron executable
// (ChatGPT.exe unless -ldflags "-X main.electronExecutable=..." says otherwise
// at build time). When run it starts that executable from the same directory
// with "--user-data-dir=%APPDATA%\Codex Subscription Router" as the first
// argument, followed by every argument it received, inheriting environment and
// standard handles, and exits with the child's exit code. APPDATA must be set
// in the environment; without it the launcher refuses to start, as launcher.c
// does without HOME.
//
// Why a launcher instead of a shortcut carrying the flag: Chromium reads
// --user-data-dir before Electron's JavaScript runs, so it has to arrive on
// the command line, and on Windows the command line is owned by whoever starts
// the process. Taskbar pins, Start-menu entries, "Open with", and a double
// click in Explorer all start the .exe they point at with their own arguments;
// only a shortcut that the user happened to double-click would carry the flag.
// Putting the flag inside the .exe the patcher's shortcut targets is how those
// entry points land in the isolated profile instead of the official app's,
// which is the property the macOS build gets from launcher.c.
//
// URL-scheme (protocol) activations are the exception: Windows starts whatever
// app.setAsDefaultProtocolClient recorded, and Electron's default for that is
// process.execPath, the Electron executable itself, so they bypass the launcher
// and carry no --user-data-dir. Isolation on that path rests on the userData
// rewrite the patcher applies in the main process (setPath('userData',
// appData + '/Codex Subscription Router')), which resolves to the same
// directory as the launcher's flag; the flag is defence in depth for the
// entry points that do go through the launcher, not the sole mechanism.
//
// The program is built with -H=windowsgui so no console window flashes, which
// also means nothing it prints is visible; every failure is therefore reported
// through a MessageBox as well as stderr. The pure argument construction lives
// in arguments.go without a build tag so it is unit-tested on every platform;
// main_other.go keeps "go build ./..." working on macOS and Linux by compiling
// to a stub that only explains the program is Windows-only.
package main
