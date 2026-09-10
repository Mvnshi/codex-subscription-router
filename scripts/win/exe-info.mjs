// Print what the Windows patcher needs to know about a PE executable as JSON.
//
// Usage: node scripts/win/exe-info.mjs <exe>
//
// Output (stdout, 2-space JSON, trailing newline):
//   {
//     "path": string,                 absolute path that was inspected
//     "size": number,                 bytes on disk
//     "machine": "x64"|"arm64"|"x86"|"0x<hex>",
//     "subsystem": number,            2 = Windows GUI, 3 = console
//     "signed": boolean,              non-empty Authenticode directory
//     "versionInfo": null | { "fileVersion", "productVersion", "strings" },
//     "asarIntegrity": null | [{ "file", "alg", "value" }]
//   }
//
// versionInfo comes from the first RT_VERSION resource; strings are every
// key of its first StringFileInfo table. asarIntegrity is the parsed
// INTEGRITY/ELECTRONASAR resource that Electron consults for the embedded
// asar-integrity fuse; null means the resource is absent. A resource that is
// present but malformed is an error (exit 1), never reported as absent.
//
// Exit status: 0 success, 1 the file could be read but a check failed,
// 2 usage error (arguments, unreadable file, not a PE). Nothing but the JSON
// document is ever written to stdout.

import path from "node:path";
import {
  UsageError,
  isSigned,
  machineName,
  readExecutable,
  readIntegrityList,
  runCli,
  versionInfoOf,
} from "./pe.mjs";

const USAGE = "usage: node scripts/win/exe-info.mjs <exe>";

function describeExecutable(args) {
  if (args.length !== 1 || args[0] === "-h" || args[0] === "--help") {
    throw new UsageError(USAGE);
  }
  const filePath = path.resolve(args[0]);
  const { buffer, exe, resource } = readExecutable(filePath);
  return {
    path: filePath,
    size: buffer.byteLength,
    machine: machineName(exe.newHeader.fileHeader.machine),
    subsystem: exe.newHeader.optionalHeader.subsystem,
    signed: isSigned(exe),
    versionInfo: versionInfoOf(resource),
    asarIntegrity: readIntegrityList(resource),
  };
}

runCli("exe-info", describeExecutable);
