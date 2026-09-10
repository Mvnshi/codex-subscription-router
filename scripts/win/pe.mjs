// Shared helpers for the Windows PE tools (exe-info.mjs, set-asar-integrity.mjs).
//
// WHY this exists: on macOS the asar header digest lives in Info.plist
// (ElectronAsarIntegrity) and the patcher edits a plist. On Windows Electron
// reads the same digest from a resource compiled into the main executable:
// type "INTEGRITY", name "ELECTRONASAR" (written by @electron/packager, read
// by shell/common/asar/archive_win.cc via FindResource). Rewriting a PE
// resource table has no standard-library equivalent, so the Python patcher
// delegates to these Node helpers, which wrap the pinned resedit/pe-library
// packages already vendored for the macOS build.
//
// Loading always passes { ignoreCert: true } because the official executable
// is Authenticode-signed and pe-library refuses signed input otherwise. That
// option DROPS the certificate: pe-library ignores the security blob on load
// and zeroes the Certificate data directory in generate(), so every file
// written by these tools is unsigned. That is expected for a locally patched
// copy (the macOS build is re-signed with the operator's own identity; the
// Windows build simply runs unsigned).
//
// House style: fail closed. Anything that is present but not exactly what
// Electron would consume (unparsable JSON, duplicate resources, ambiguous
// file matches, a non-SHA256 algorithm) is an error, never a silent default.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Format, NtExecutable, NtExecutableResource, Resource } from "resedit";

/** Exit status for logical failures (missing resource, no match, bad digest). */
export const EXIT_FAILURE = 1;
/** Exit status for usage errors (wrong arguments, unreadable input, unwritable output, not a PE). */
export const EXIT_USAGE = 2;

/** Resource type and name Electron looks up with FindResource on Windows. */
export const INTEGRITY_RESOURCE_TYPE = "INTEGRITY";
export const INTEGRITY_RESOURCE_ID = "ELECTRONASAR";

/** The only algorithm Electron's Windows integrity check accepts. */
export const INTEGRITY_ALGORITHM = "SHA256";

/** Thrown for wrong invocation or unreadable/non-PE input; maps to EXIT_USAGE. */
export class UsageError extends Error {}

/** Thrown for a check the tool cannot satisfy; maps to EXIT_FAILURE. */
export class ToolError extends Error {}

const MACHINE_NAMES = new Map([
  [0x8664, "x64"],
  [0xaa64, "arm64"],
  [0x14c, "x86"],
]);

const SHA256_HEX = /^[0-9a-fA-F]{64}$/;

/** Copy a Node Buffer into a standalone ArrayBuffer (pe-library's input type). */
export function toArrayBuffer(buffer) {
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

/** IMAGE_FILE_MACHINE_* to the names the patcher compares against. */
export function machineName(machine) {
  return MACHINE_NAMES.get(machine) ?? `0x${machine.toString(16)}`;
}

/** Decode a VS_FIXEDFILEINFO MS/LS pair as major.minor.build.revision. */
export function decodeVersionNumber(mostSignificant, leastSignificant) {
  return [
    mostSignificant >>> 16,
    mostSignificant & 0xffff,
    leastSignificant >>> 16,
    leastSignificant & 0xffff,
  ].join(".");
}

/**
 * Key used to match a caller-supplied asar path against resource entries.
 * Callers may pass "resources/app.asar" while packager stores
 * "resources\\app.asar". Electron (shell/common/asar/archive_win.cc,
 * LoadIntegrityConfig and HeaderIntegrity, v30 through main) lower-cases
 * both the stored "file" and the relative path it looks up, but never
 * normalises separators; so lower-casing here matches Electron exactly and
 * folding separators only forgives the operator's spelling. The stored value
 * itself is never rewritten. Electron inserts entries last-wins, so two
 * stored keys differing only by case silently collide there; that is why
 * updatedIntegrityList refuses more than one match instead of picking one.
 */
export function normaliseAsarFileKey(file) {
  return file.replace(/\\/g, "/").toLowerCase();
}

/** Parse an executable already in memory; `label` names it in errors. */
export function parseExecutable(buffer, label) {
  // pe-library reports a short or foreign file as a DataView range error;
  // check the MZ magic first so the operator sees what actually went wrong.
  if (buffer.byteLength < 2 || buffer[0] !== 0x4d || buffer[1] !== 0x5a) {
    throw new UsageError(`${label} is not a PE executable (no MZ header)`);
  }
  let exe;
  try {
    exe = NtExecutable.from(toArrayBuffer(buffer), { ignoreCert: true });
  } catch (error) {
    throw new UsageError(`${label} is not a supported PE executable: ${error.message}`);
  }
  let resource;
  try {
    resource = NtExecutableResource.from(exe);
  } catch (error) {
    throw new ToolError(`${label} has a resource section that could not be parsed: ${error.message}`);
  }
  return { buffer, exe, resource };
}

/** Read and parse the executable at `filePath` (usage error if unreadable). */
export function readExecutable(filePath) {
  let buffer;
  try {
    buffer = fs.readFileSync(filePath);
  } catch (error) {
    throw new UsageError(`cannot read ${filePath}: ${error.message}`);
  }
  return parseExecutable(buffer, filePath);
}

/**
 * Whether the PE carries an Authenticode signature. The header copy kept by
 * pe-library still holds the original Certificate directory even though the
 * blob itself was skipped on load, so this reflects the file on disk.
 */
export function isSigned(exe) {
  const certificate = exe.newHeader.optionalHeaderDataDirectory.get(
    Format.ImageDirectoryEntry.Certificate,
  );
  return certificate.size > 0;
}

function matchesResourceName(actual, expected) {
  // FindResource compares string names case-insensitively and rc.exe stores
  // them upper-cased, so match the way the loader does.
  return typeof actual === "string" && actual.toUpperCase() === expected;
}

/** All INTEGRITY/ELECTRONASAR entries, regardless of language. */
export function findIntegrityEntries(resource) {
  return resource.entries.filter(
    (entry) =>
      matchesResourceName(entry.type, INTEGRITY_RESOURCE_TYPE) &&
      matchesResourceName(entry.id, INTEGRITY_RESOURCE_ID),
  );
}

/**
 * The single INTEGRITY/ELECTRONASAR entry, or null when the executable has
 * none. Electron calls FindResource without a language, so if several
 * language variants exist we cannot know which one it would load; refuse.
 */
export function findIntegrityEntry(resource) {
  const entries = findIntegrityEntries(resource);
  if (entries.length === 0) {
    return null;
  }
  if (entries.length > 1) {
    const languages = entries.map((entry) => String(entry.lang)).join(", ");
    throw new ToolError(
      `found ${entries.length} ${INTEGRITY_RESOURCE_TYPE}/${INTEGRITY_RESOURCE_ID} resources ` +
        `(languages ${languages}); refusing to guess which one Electron loads`,
    );
  }
  return entries[0];
}

/**
 * Decode the JSON list stored in an integrity entry. Electron skips malformed
 * items silently; this tool does not, because a malformed list means the
 * executable is not laid out the way the patcher was verified against.
 */
export function decodeIntegrityList(entry) {
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(entry.bin));
  } catch (error) {
    throw new ToolError(`integrity resource is not UTF-8: ${error.message}`);
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new ToolError(`integrity resource is not valid JSON: ${error.message}`);
  }
  if (!Array.isArray(parsed)) {
    throw new ToolError("integrity resource must be a JSON array");
  }
  parsed.forEach((item, index) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      throw new ToolError(`integrity entry ${index} is not an object`);
    }
    for (const key of ["file", "alg", "value"]) {
      if (typeof item[key] !== "string") {
        throw new ToolError(`integrity entry ${index} lacks a string "${key}" field`);
      }
    }
  });
  return parsed;
}

/** Store a JSON list into an existing integrity entry (lang/codepage kept). */
export function encodeIntegrityList(entry, list) {
  // Compact JSON matches what @electron/packager writes.
  entry.bin = toArrayBuffer(Buffer.from(JSON.stringify(list), "utf8"));
}

/** Project integrity entries to the documented {file, alg, value} shape. */
export function publicIntegrityList(list) {
  return list.map(({ file, alg, value }) => ({ file, alg, value }));
}

/** null when the resource is absent, otherwise the validated public list. */
export function readIntegrityList(resource) {
  const entry = findIntegrityEntry(resource);
  return entry === null ? null : publicIntegrityList(decodeIntegrityList(entry));
}

/**
 * Validate a caller-supplied SHA-256 digest and return it lower-cased.
 * Case does not affect launch: archive_win.cc LoadIntegrityConfig stores
 * base::ToLowerASCII(value) and asar_util.cc ValidateIntegrityOrDie compares
 * it against lower-case HexEncode output. We lower-case anyway so the value
 * we write, re-read, verify and print is byte-identical to what Electron
 * caches and to what @electron/packager emits.
 */
export function normaliseDigest(digest) {
  if (typeof digest !== "string" || !SHA256_HEX.test(digest)) {
    const shown = typeof digest === "string" ? `${digest.length} characters` : typeof digest;
    throw new ToolError(`expected a 64-character hexadecimal SHA-256 digest, got ${shown}`);
  }
  return digest.toLowerCase();
}

/**
 * Return a copy of `list` with the entry for `file` carrying `digest`.
 * Exactly one entry must match; the error for zero matches lists what exists
 * so the operator can see how the official build names its archives.
 */
export function updatedIntegrityList(list, file, digest) {
  const wanted = normaliseAsarFileKey(file);
  const matches = list
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => normaliseAsarFileKey(item.file) === wanted);
  if (matches.length === 0) {
    const existing = list.length === 0 ? "(none)" : list.map((item) => item.file).join(", ");
    throw new ToolError(`no integrity entry matches "${file}"; entries present: ${existing}`);
  }
  if (matches.length > 1) {
    const names = matches.map(({ item }) => item.file).join(", ");
    throw new ToolError(`"${file}" matches ${matches.length} integrity entries (${names})`);
  }
  const { item, index } = matches[0];
  if (item.alg.toUpperCase() !== INTEGRITY_ALGORITHM) {
    throw new ToolError(
      `integrity entry "${item.file}" uses algorithm "${item.alg}", not ${INTEGRITY_ALGORITHM}; ` +
        "refusing to store a SHA-256 digest under it",
    );
  }
  // Spread keeps key order and any extra keys Electron ignores.
  return list.map((current, position) =>
    position === index ? { ...current, value: digest } : current,
  );
}

/** Stable identity of a resource entry, for before/after comparisons. */
export function resourceEntryKeys(resource) {
  return resource.entries
    .map((entry) => `${String(entry.type)}/${String(entry.id)}/${String(entry.lang)}`)
    .sort();
}

/**
 * Version information from the first RT_VERSION resource, or null. Strings
 * come from the first StringFileInfo table actually present (not from
 * VarFileInfo translations, which some tools emit without a matching table).
 */
export function versionInfoOf(resource) {
  let infos;
  try {
    infos = Resource.VersionInfo.fromEntries(resource.entries);
  } catch (error) {
    throw new ToolError(`version resource could not be parsed: ${error.message}`);
  }
  if (infos.length === 0) {
    return null;
  }
  const info = infos[0];
  const fixed = info.fixedInfo;
  const languages = info.getAllLanguagesForStringValues();
  const strings = languages.length > 0 ? { ...info.getStringValues(languages[0]) } : {};
  return {
    fileVersion: decodeVersionNumber(fixed.fileVersionMS, fixed.fileVersionLS),
    productVersion: decodeVersionNumber(fixed.productVersionMS, fixed.productVersionLS),
    strings,
  };
}

/**
 * Write `data` to `targetPath` via a sibling temp file and rename, so a
 * crash or a failed check never leaves a half-written executable behind.
 * `verify(temporaryPath)` runs on the on-disk bytes BEFORE the rename; if it
 * throws, the temp file is removed and the target is untouched. A directory
 * that does not exist or cannot be written is a UsageError.
 */
export function writeFileAtomically(targetPath, data, verify = () => {}) {
  const directory = path.dirname(targetPath);
  const suffix = `${process.pid}-${crypto.randomBytes(6).toString("hex")}`;
  const temporaryPath = path.join(directory, `.${path.basename(targetPath)}.${suffix}.tmp`);
  let mode = 0o755;
  try {
    mode = fs.statSync(targetPath).mode & 0o7777;
  } catch {
    // New file: keep the executable default.
  }
  let descriptor;
  try {
    descriptor = fs.openSync(temporaryPath, "wx", mode);
  } catch (error) {
    // A missing or unwritable output directory is the operator's --output
    // argument, not a bug: report it as a usage error (exit 2, no stack
    // trace), the same way readExecutable treats an unreadable input.
    throw new UsageError(`cannot write ${temporaryPath}: ${error.message}`);
  }
  try {
    try {
      fs.writeFileSync(descriptor, data);
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
    verify(temporaryPath);
    fs.renameSync(temporaryPath, targetPath);
  } catch (error) {
    fs.rmSync(temporaryPath, { force: true });
    throw error;
  }
}

/**
 * Run a CLI body: on success print exactly one JSON document (2-space
 * indent, trailing newline) to stdout; on failure print the message to
 * stderr and set the exit status. process.exitCode (not process.exit) lets
 * piped stdout drain, so the patcher never receives truncated JSON.
 */
export function runCli(toolName, main) {
  try {
    const result = main(process.argv.slice(2));
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exitCode = 0;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`${toolName}: ${message}\n`);
    if (!(error instanceof UsageError) && !(error instanceof ToolError) && error?.stack) {
      // An unexpected exception is a bug; show where, but still fail closed.
      process.stderr.write(`${error.stack}\n`);
    }
    process.exitCode = error instanceof UsageError ? EXIT_USAGE : EXIT_FAILURE;
  }
}
