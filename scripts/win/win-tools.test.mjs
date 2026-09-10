// Tests for the Windows PE helpers. Run with: node --test "scripts/win/*.test.mjs"
//
// The fixture is a real PE32+ GUI executable cross-compiled from a two-line
// Go program at test start, so the helpers are exercised against genuine
// linker output rather than a hand-made header. Resources are then added
// with resedit directly (not through the code under test) so a regression in
// the helpers cannot mask itself. Every test that needs the fixture is
// skipped with an explicit reason when no `go` toolchain is on PATH.

import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { NtExecutable, NtExecutableResource, Resource } from "resedit";
import {
  decodeVersionNumber,
  machineName,
  normaliseAsarFileKey,
  normaliseDigest,
  ToolError,
  UsageError,
  updatedIntegrityList,
  writeFileAtomically,
} from "./pe.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EXE_INFO = path.join(HERE, "exe-info.mjs");
const SET_ASAR_INTEGRITY = path.join(HERE, "set-asar-integrity.mjs");

const APP_ASAR = "resources\\app.asar";
const OTHER_ASAR = "resources\\other.asar";
const OLD_APP_DIGEST = "ab".repeat(32);
const OTHER_DIGEST = "cd".repeat(32);
const NEW_DIGEST = "0123456789abcdef".repeat(4);
const INTEGRITY_LIST = [
  { file: APP_ASAR, alg: "SHA256", value: OLD_APP_DIGEST },
  { file: OTHER_ASAR, alg: "SHA256", value: OTHER_DIGEST },
];

function goToolchainMissing() {
  try {
    execFileSync("go", ["version"], { stdio: "pipe" });
    return false;
  } catch {
    return "go toolchain not on PATH; cannot cross-compile the Windows fixture";
  }
}

const skip = goToolchainMissing();

const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "codex-win-tools-"));
after(() => {
  fs.rmSync(workspace, { recursive: true, force: true });
});

let fixtureCounter = 0;
function freshPath(name) {
  fixtureCounter += 1;
  return path.join(workspace, `${fixtureCounter}-${name}`);
}

/** Cross-compile a minimal windowsgui PE32+ executable; cached per run. */
let plainFixture = null;
function plainExe() {
  if (plainFixture === null) {
    const source = path.join(workspace, "fixture-src");
    fs.mkdirSync(source);
    fs.writeFileSync(path.join(source, "go.mod"), "module fixture\n\ngo 1.21\n");
    fs.writeFileSync(path.join(source, "main.go"), "package main\n\nfunc main() {}\n");
    const output = path.join(workspace, "fixture.exe");
    execFileSync(
      "go",
      ["build", "-trimpath", "-ldflags", "-s -w -H=windowsgui", "-o", output, "."],
      { cwd: source, stdio: "pipe", env: { ...process.env, GOOS: "windows", GOARCH: "amd64" } },
    );
    plainFixture = output;
  }
  return plainFixture;
}

function toArrayBuffer(buffer) {
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

/**
 * Copy the plain fixture and add what an @electron/packager build carries:
 * an INTEGRITY/ELECTRONASAR JSON list and an RT_VERSION block.
 */
function resourcedExe(name = "resourced.exe", { integrityLists = [INTEGRITY_LIST] } = {}) {
  const exe = NtExecutable.from(toArrayBuffer(fs.readFileSync(plainExe())), { ignoreCert: true });
  const resource = NtExecutableResource.from(exe);
  integrityLists.forEach((list, index) => {
    resource.entries.push({
      type: "INTEGRITY",
      id: "ELECTRONASAR",
      lang: 1033 + index,
      codepage: 0,
      bin: toArrayBuffer(Buffer.from(JSON.stringify(list))),
    });
  });
  const version = Resource.VersionInfo.createEmpty();
  version.setFileVersion(26, 901, 51231, 8109, 1033);
  version.setProductVersion(26, 901, 51231, 8109, 1033);
  version.setStringValues(
    { lang: 1033, codepage: 1200 },
    {
      ProductName: "ChatGPT",
      FileVersion: "26.901.51231.8109",
      ProductVersion: "26.901.51231.8109",
      CompanyName: "OpenAI",
    },
  );
  version.outputToResourceEntries(resource.entries);
  resource.outputResource(exe);
  const target = freshPath(name);
  fs.writeFileSync(target, Buffer.from(exe.generate()));
  return target;
}

/**
 * Append a fake WIN_CERTIFICATE and point the Certificate data directory at
 * it, so the file looks Authenticode-signed to a PE parser.
 */
function fakeSigned(sourcePath) {
  const original = fs.readFileSync(sourcePath);
  const payload = Buffer.alloc(8 + 24, 0xee);
  payload.writeUInt32LE(payload.length, 0); // dwLength
  payload.writeUInt16LE(0x0200, 4); // wRevision = WIN_CERT_REVISION_2_0
  payload.writeUInt16LE(0x0002, 6); // wCertificateType = PKCS_SIGNED_DATA
  const signed = Buffer.concat([original, payload]);
  const ntHeaders = signed.readUInt32LE(0x3c);
  assert.equal(signed.readUInt16LE(ntHeaders + 24), 0x20b, "fixture must be PE32+");
  const certificateDirectory = ntHeaders + 24 + 112 + 4 * 8;
  signed.writeUInt32LE(original.length, certificateDirectory);
  signed.writeUInt32LE(payload.length, certificateDirectory + 4);
  const target = freshPath("signed.exe");
  fs.writeFileSync(target, signed);
  return target;
}

function run(script, args) {
  const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
  assert.equal(result.error, undefined);
  return result;
}

function exeInfo(file) {
  const result = run(EXE_INFO, [file]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "");
  const parsed = JSON.parse(result.stdout);
  assert.equal(result.stdout, `${JSON.stringify(parsed, null, 2)}\n`, "2-space JSON, trailing newline");
  return parsed;
}

function expectFailure(script, args, status) {
  const result = run(script, args);
  assert.equal(result.status, status, `stderr: ${result.stderr}`);
  assert.equal(result.stdout, "", "nothing but JSON on stdout, and only on success");
  assert.notEqual(result.stderr.trim(), "");
  return result.stderr;
}

// ---------------------------------------------------------------------------
// Pure helpers (no fixture required)

test("machine codes map to the names the patcher expects", () => {
  assert.equal(machineName(0x8664), "x64");
  assert.equal(machineName(0xaa64), "arm64");
  assert.equal(machineName(0x14c), "x86");
  assert.equal(machineName(0x1c0), "0x1c0");
});

test("fixed version words decode as a.b.c.d", () => {
  assert.equal(decodeVersionNumber((26 << 16) | 901, (51231 << 16) | 8109), "26.901.51231.8109");
  assert.equal(decodeVersionNumber(0, 0), "0.0.0.0");
  assert.equal(decodeVersionNumber(0xffffffff, 0xffffffff), "65535.65535.65535.65535");
});

test("asar file keys ignore separator style and case, nothing else", () => {
  assert.equal(normaliseAsarFileKey("Resources\\App.asar"), "resources/app.asar");
  assert.equal(normaliseAsarFileKey("resources/app.asar"), "resources/app.asar");
  assert.notEqual(normaliseAsarFileKey("resources/app.asar"), normaliseAsarFileKey("app.asar"));
});

test("digests must be exactly 64 hex characters and are stored lower-case", () => {
  assert.equal(normaliseDigest("AB".repeat(32)), "ab".repeat(32));
  for (const bad of ["a".repeat(63), "a".repeat(65), "g".repeat(64), "", " ".repeat(64)]) {
    assert.throws(() => normaliseDigest(bad), ToolError, JSON.stringify(bad));
  }
});

test("only the matching entry changes and non-SHA256 entries are refused", () => {
  const updated = updatedIntegrityList(INTEGRITY_LIST, "Resources/App.asar", NEW_DIGEST);
  assert.deepEqual(updated, [
    { file: APP_ASAR, alg: "SHA256", value: NEW_DIGEST },
    { file: OTHER_ASAR, alg: "SHA256", value: OTHER_DIGEST },
  ]);
  assert.deepEqual(INTEGRITY_LIST[0].value, OLD_APP_DIGEST, "input must not be mutated");
  assert.throws(
    () => updatedIntegrityList(INTEGRITY_LIST, "resources/missing.asar", NEW_DIGEST),
    (error) =>
      error instanceof ToolError && error.message.includes(APP_ASAR) && error.message.includes(OTHER_ASAR),
  );
  assert.throws(
    () => updatedIntegrityList([{ file: APP_ASAR, alg: "SHA1", value: "x" }], APP_ASAR, NEW_DIGEST),
    ToolError,
  );
  assert.throws(
    () =>
      updatedIntegrityList(
        [...INTEGRITY_LIST, { file: "resources/APP.asar", alg: "SHA256", value: "x" }],
        APP_ASAR,
        NEW_DIGEST,
      ),
    ToolError,
    "two entries matching one key is ambiguous",
  );
});

test("writeFileAtomically leaves the target untouched when verify throws", () => {
  const directory = freshPath("atomic");
  fs.mkdirSync(directory);
  const target = path.join(directory, "target.exe");
  const oldData = Buffer.from("old executable bytes");
  const newData = Buffer.from("new executable bytes, longer than the old ones");
  fs.writeFileSync(target, oldData);
  // 0o600 survives any umask. Windows only models the read-only bit, so the
  // expectation is whatever mode the platform reports back, not 0o600 itself.
  fs.chmodSync(target, 0o600);
  const mode = fs.statSync(target).mode & 0o7777;
  const temporaryFiles = () => fs.readdirSync(directory).filter((name) => name.endsWith(".tmp"));

  // verify throws: its error propagates, the temp file is removed, the target
  // is byte-identical. verify must have seen the new bytes on disk while the
  // target still held the old ones (that is the point of verifying first).
  const failure = new Error("verification says no");
  let seen = null;
  assert.throws(
    () =>
      writeFileAtomically(target, newData, (temporaryPath) => {
        seen = {
          directory: path.dirname(temporaryPath),
          isTemp: temporaryPath.endsWith(".tmp"),
          temporaryBytes: fs.readFileSync(temporaryPath),
          targetBytes: fs.readFileSync(target),
        };
        throw failure;
      }),
    (error) => error === failure,
  );
  assert.notEqual(seen, null, "verify was called");
  assert.equal(seen.directory, directory, "temp file is a sibling of the target");
  assert.ok(seen.isTemp);
  assert.ok(seen.temporaryBytes.equals(newData), "verify sees the new bytes on disk");
  assert.ok(seen.targetBytes.equals(oldData), "target not yet replaced during verify");
  assert.ok(fs.readFileSync(target).equals(oldData), "target untouched after failure");
  assert.equal(fs.statSync(target).mode & 0o7777, mode);
  assert.deepEqual(temporaryFiles(), [], "temp file removed after failure");

  // Success over an existing file: bytes replaced, mode preserved, temp renamed away.
  writeFileAtomically(target, newData);
  assert.ok(fs.readFileSync(target).equals(newData));
  assert.equal(fs.statSync(target).mode & 0o7777, mode, "existing mode preserved");
  assert.deepEqual(temporaryFiles(), []);

  // A directory that does not exist is the operator's mistake: a usage error,
  // and nothing is created on the way to discovering it.
  const missing = path.join(directory, "no-such-dir", "out.exe");
  assert.throws(
    () => writeFileAtomically(missing, newData),
    (error) => error instanceof UsageError && /cannot write/.test(error.message),
  );
  assert.equal(fs.existsSync(path.dirname(missing)), false);
  assert.deepEqual(temporaryFiles(), []);
});

// ---------------------------------------------------------------------------
// CLI behaviour against a real cross-compiled executable

test("exe-info describes a plain Go windowsgui build", { skip }, () => {
  const file = plainExe();
  const info = exeInfo(file);
  assert.deepEqual(info, {
    path: path.resolve(file),
    size: fs.statSync(file).size,
    machine: "x64",
    subsystem: 2,
    signed: false,
    versionInfo: null,
    asarIntegrity: null,
  });
});

test("exe-info usage errors exit 2 and print nothing to stdout", { skip }, () => {
  expectFailure(EXE_INFO, [], 2);
  expectFailure(EXE_INFO, [plainExe(), "extra"], 2);
  expectFailure(EXE_INFO, [path.join(workspace, "missing.exe")], 2);
  const text = freshPath("not-a-pe.exe");
  fs.writeFileSync(text, "MZ but not really a portable executable\n");
  expectFailure(EXE_INFO, [text], 2);
  const empty = freshPath("empty.exe");
  fs.writeFileSync(empty, "");
  expectFailure(EXE_INFO, [empty], 2);
});

test("set-asar-integrity exits 1 when the resource is absent", { skip }, () => {
  const file = freshPath("plain-copy.exe");
  fs.copyFileSync(plainExe(), file);
  const before = fs.readFileSync(file);
  const stderr = expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST], 1);
  assert.match(stderr, /INTEGRITY\/ELECTRONASAR/);
  assert.ok(before.equals(fs.readFileSync(file)), "input untouched");
  assert.deepEqual(
    fs.readdirSync(workspace).filter((name) => name.endsWith(".tmp")),
    [],
    "no temp file left behind",
  );
});

test("exe-info reports packager-style integrity and version resources", { skip }, () => {
  const info = exeInfo(resourcedExe());
  assert.deepEqual(info.asarIntegrity, INTEGRITY_LIST);
  assert.equal(info.versionInfo.fileVersion, "26.901.51231.8109");
  assert.equal(info.versionInfo.productVersion, "26.901.51231.8109");
  assert.equal(info.versionInfo.strings.ProductName, "ChatGPT");
  assert.equal(info.versionInfo.strings.CompanyName, "OpenAI");
  assert.equal(info.versionInfo.strings.FileVersion, "26.901.51231.8109");
  assert.equal(info.signed, false);
  assert.equal(info.subsystem, 2);
});

test("set-asar-integrity rewrites only the matching entry in place", { skip }, () => {
  const file = resourcedExe();
  const result = run(SET_ASAR_INTEGRITY, [file, "resources/App.asar", NEW_DIGEST]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, "", "an unsigned input produces no signature note");
  const expected = [
    { file: APP_ASAR, alg: "SHA256", value: NEW_DIGEST },
    { file: OTHER_ASAR, alg: "SHA256", value: OTHER_DIGEST },
  ];
  assert.deepEqual(JSON.parse(result.stdout), expected);
  assert.equal(result.stdout, `${JSON.stringify(expected, null, 2)}\n`);

  // Round trip: the rewritten file is still a parseable PE with everything else intact.
  const info = exeInfo(file);
  assert.deepEqual(info.asarIntegrity, expected);
  assert.equal(info.versionInfo.strings.ProductName, "ChatGPT");
  assert.equal(info.versionInfo.fileVersion, "26.901.51231.8109");
  assert.equal(info.machine, "x64");
  assert.equal(info.subsystem, 2);
  assert.equal(info.signed, false);
  assert.deepEqual(
    fs.readdirSync(workspace).filter((name) => name.endsWith(".tmp")),
    [],
    "temp file renamed away",
  );
});

test("set-asar-integrity accepts an upper-case digest and stores it lower-case", { skip }, () => {
  const file = resourcedExe();
  const result = run(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST.toUpperCase()]);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(exeInfo(file).asarIntegrity[0].value, NEW_DIGEST);
});

test("--output writes a new file and leaves the input byte-identical", { skip }, () => {
  const input = resourcedExe("input.exe");
  const before = fs.readFileSync(input);
  const output = freshPath("output.exe");
  for (const form of [["--output", output], [`--output=${output}`]]) {
    fs.rmSync(output, { force: true });
    const result = run(SET_ASAR_INTEGRITY, [input, APP_ASAR, NEW_DIGEST, ...form]);
    assert.equal(result.status, 0, result.stderr);
    assert.ok(before.equals(fs.readFileSync(input)), "input unchanged");
    assert.equal(exeInfo(output).asarIntegrity[0].value, NEW_DIGEST);
    assert.equal(exeInfo(input).asarIntegrity[0].value, OLD_APP_DIGEST);
  }
});

test("set-asar-integrity rejects digests that are not 64 hex characters", { skip }, () => {
  const file = resourcedExe();
  const before = fs.readFileSync(file);
  for (const bad of ["a".repeat(63), "g".repeat(64), "a".repeat(65), ""]) {
    const stderr = expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, bad], 1);
    assert.match(stderr, /64-character hexadecimal/);
  }
  assert.ok(before.equals(fs.readFileSync(file)), "input untouched");
});

test("set-asar-integrity lists the existing entries when no file matches", { skip }, () => {
  const file = resourcedExe();
  const before = fs.readFileSync(file);
  const stderr = expectFailure(SET_ASAR_INTEGRITY, [file, "resources/missing.asar", NEW_DIGEST], 1);
  assert.ok(stderr.includes(APP_ASAR), stderr);
  assert.ok(stderr.includes(OTHER_ASAR), stderr);
  assert.ok(before.equals(fs.readFileSync(file)), "input untouched");
});

test("set-asar-integrity usage errors exit 2", { skip }, () => {
  const file = resourcedExe();
  expectFailure(SET_ASAR_INTEGRITY, [], 2);
  expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR], 2);
  expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST, "extra"], 2);
  expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST, "--output"], 2);
  expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST, "--bogus"], 2);
  expectFailure(SET_ASAR_INTEGRITY, [path.join(workspace, "missing.exe"), APP_ASAR, NEW_DIGEST], 2);
  const unwritable = path.join(workspace, "no-such-dir", "out.exe");
  const stderr = expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST, "--output", unwritable], 2);
  assert.match(stderr, /cannot write/);
  assert.doesNotMatch(stderr, /\n\s+at /, "a usage error prints no stack trace");
  assert.equal(exeInfo(file).asarIntegrity[0].value, OLD_APP_DIGEST, "input untouched");
});

test("duplicate INTEGRITY/ELECTRONASAR resources are refused, not guessed", { skip }, () => {
  const file = resourcedExe("duplicate.exe", { integrityLists: [INTEGRITY_LIST, INTEGRITY_LIST] });
  const stderr = expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST], 1);
  assert.match(stderr, /found 2 INTEGRITY\/ELECTRONASAR resources/);
  expectFailure(EXE_INFO, [file], 1);
});

test("a present but malformed integrity resource is an error, not null", { skip }, () => {
  const file = resourcedExe("malformed.exe", { integrityLists: [{ file: APP_ASAR }] });
  const stderr = expectFailure(EXE_INFO, [file], 1);
  assert.match(stderr, /must be a JSON array/);
  expectFailure(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST], 1);
});

test("signed input is reported and the rewritten output is unsigned", { skip }, () => {
  const file = fakeSigned(resourcedExe());
  assert.equal(exeInfo(file).signed, true);
  const output = freshPath("unsigned.exe");
  const result = run(SET_ASAR_INTEGRITY, [file, APP_ASAR, NEW_DIGEST, "--output", output]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /Authenticode-signed; .* is unsigned/);
  const info = exeInfo(output);
  assert.equal(info.signed, false);
  assert.equal(info.asarIntegrity[0].value, NEW_DIGEST);
  assert.equal(info.versionInfo.strings.ProductName, "ChatGPT");
});
