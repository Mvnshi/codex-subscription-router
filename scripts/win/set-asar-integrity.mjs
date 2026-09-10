// Rewrite one asar header digest inside a Windows Electron executable.
//
// Usage: node scripts/win/set-asar-integrity.mjs <exe> <file> <sha256hex> [--output <path>]
//
// Electron on Windows validates app.asar against the INTEGRITY/ELECTRONASAR
// resource of the main executable (a JSON list of {file, alg, value}). The
// patcher repacks app.asar, so the recorded header digest must be replaced
// or, once the embedded-asar-integrity fuse is on, every launch aborts with
// "Integrity check failed for asar archive". This tool changes ONLY the
// "value" of the entry whose "file" matches <file> (compared case-
// insensitively after normalising / and \); every other byte of the resource
// list is carried over.
//
// The executable is loaded with { ignoreCert: true } because the official
// build is Authenticode-signed. pe-library drops the certificate blob on
// load and clears the Certificate data directory on generate(), so THE
// OUTPUT IS UNSIGNED. That is expected: a locally patched copy cannot carry
// OpenAI's signature anyway, and Windows runs unsigned executables (SmartScreen
// may warn once). A note is printed to stderr when a signature was dropped.
//
// Writes go to a sibling temp file, are re-read and verified from disk, and
// only then renamed over the target (--output or the input itself). On
// success the new integrity list is printed to stdout as JSON.
//
// Exit status: 0 success; 1 the resource is absent, no entry matches (the
// existing entries are listed), the digest is not 64 hex characters, or a
// verification failed; 2 usage error (arguments, unreadable file, not a PE,
// or an --output location whose directory is missing or unwritable).

import path from "node:path";
import {
  ToolError,
  UsageError,
  decodeIntegrityList,
  encodeIntegrityList,
  findIntegrityEntry,
  INTEGRITY_RESOURCE_ID,
  INTEGRITY_RESOURCE_TYPE,
  isSigned,
  normaliseDigest,
  publicIntegrityList,
  readExecutable,
  resourceEntryKeys,
  runCli,
  updatedIntegrityList,
  writeFileAtomically,
} from "./pe.mjs";

const USAGE =
  "usage: node scripts/win/set-asar-integrity.mjs <exe> <file> <sha256hex> [--output <path>]";

function parseArguments(args) {
  const positionals = [];
  let output = null;
  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (argument === "-h" || argument === "--help") {
      throw new UsageError(USAGE);
    }
    if (argument === "--output") {
      if (output !== null || index + 1 >= args.length) {
        throw new UsageError(USAGE);
      }
      index += 1;
      output = args[index];
    } else if (argument.startsWith("--output=")) {
      if (output !== null) {
        throw new UsageError(USAGE);
      }
      output = argument.slice("--output=".length);
    } else if (argument.startsWith("--")) {
      throw new UsageError(`unknown option ${argument}\n${USAGE}`);
    } else {
      positionals.push(argument);
    }
  }
  if (positionals.length !== 3 || output === "") {
    throw new UsageError(USAGE);
  }
  return { positionals, output };
}

/**
 * Parse the bytes at `candidatePath` and confirm they carry `expectedList`
 * and the same set of resources as `original`. Used on the temp file before
 * it replaces the target and again on the final file.
 */
function verifyWrittenExecutable(candidatePath, expectedList, originalKeys) {
  const { resource } = readExecutable(candidatePath);
  const entry = findIntegrityEntry(resource);
  if (entry === null) {
    throw new ToolError(`verification failed: ${candidatePath} lost its integrity resource`);
  }
  const actual = decodeIntegrityList(entry);
  if (JSON.stringify(actual) !== JSON.stringify(expectedList)) {
    throw new ToolError(
      `verification failed: ${candidatePath} does not contain the expected integrity list`,
    );
  }
  const keys = resourceEntryKeys(resource);
  if (JSON.stringify(keys) !== JSON.stringify(originalKeys)) {
    throw new ToolError(
      `verification failed: resource table of ${candidatePath} changed ` +
        `(before: ${originalKeys.join(" ")}; after: ${keys.join(" ")})`,
    );
  }
  return actual;
}

function setAsarIntegrity(args) {
  const { positionals, output } = parseArguments(args);
  const [exeArgument, file, digestArgument] = positionals;
  const digest = normaliseDigest(digestArgument);
  const inputPath = path.resolve(exeArgument);
  const outputPath = output === null ? inputPath : path.resolve(output);

  const { exe, resource } = readExecutable(inputPath);
  const entry = findIntegrityEntry(resource);
  if (entry === null) {
    throw new ToolError(
      `${inputPath} has no ${INTEGRITY_RESOURCE_TYPE}/${INTEGRITY_RESOURCE_ID} resource; ` +
        "this build does not record an asar digest, so there is nothing to rewrite",
    );
  }
  const currentList = decodeIntegrityList(entry);
  const expectedList = updatedIntegrityList(currentList, file, digest);
  const originalKeys = resourceEntryKeys(resource);
  const wasSigned = isSigned(exe);

  encodeIntegrityList(entry, expectedList);
  resource.outputResource(exe);
  const generated = Buffer.from(exe.generate());

  writeFileAtomically(outputPath, generated, (temporaryPath) => {
    verifyWrittenExecutable(temporaryPath, expectedList, originalKeys);
  });
  const finalList = verifyWrittenExecutable(outputPath, expectedList, originalKeys);

  if (wasSigned) {
    process.stderr.write(
      `set-asar-integrity: note: ${inputPath} was Authenticode-signed; ` +
        `${outputPath} is unsigned\n`,
    );
  }
  return publicIntegrityList(finalList);
}

runCli("set-asar-integrity", setAsarIntegrity);
