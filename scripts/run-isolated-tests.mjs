#!/usr/bin/env node

import { spawnSync } from 'node:child_process';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const REPOSITORY = fs.realpathSync(process.env.BOTTIE_REPOSITORY_ROOT
  || process.cwd());
const DATA_MOUNT = path.join(REPOSITORY, 'data');
const UNHARE = '/usr/bin/unshare';
const MOUNT = '/usr/bin/mount';
const SETPRIV = '/usr/bin/setpriv';
const CHROOT = '/usr/sbin/chroot';
const COMPILER = '/usr/bin/x86_64-linux-gnu-gcc-13';
const IP = '/usr/bin/ip';
const INTERNAL_NAMESPACE = '--internal-namespace';
const INTERNAL_VERIFY = '--internal-verify';
const INTERNAL_TOKEN_ENV = 'EVA_ISOLATED_INTERNAL_TOKEN';
const TEST_RUNNER_ENV = 'EVA_ISOLATED_TEST_RUNNER';
const SELF_GATE_ENV = 'EVA_ISOLATED_SECURITY_SELF_GATE';
const COVERAGE_FLAG = '--coverage';
const NODE_WASM_FLAG = '--disable-wasm-trap-handler';
const NESTED_NODE_SELF_GATE_SOURCE = String.raw`
  import assert from 'node:assert/strict';
  import fs from 'node:fs';
  import http from 'node:http';

  const NODE_WASM_FLAG = ${JSON.stringify(NODE_WASM_FLAG)};
  assert.equal(process.env.NODE_OPTIONS, NODE_WASM_FLAG);
  assert.equal(process.allowedNodeEnvironmentFlags.has(NODE_WASM_FLAG), true);
  assert.equal(process.execArgv.filter((argument) => argument === NODE_WASM_FLAG).length, 0,
    'de normale nested Node hoort de vlag uit de runneromgeving te erven, niet uit argv');
  assert.deepEqual(process.execArgv.slice(0, 2), ['--input-type=module', '--eval']);
  const limits = fs.readFileSync('/proc/self/limits', 'utf8');
  assert.match(limits,
    /^Max address space\s+6442450944\s+6442450944\s+bytes\s*$/mu);
  const server = http.createServer((_request, response_) => {
    response_.writeHead(200, {
      'content-type': 'text/plain; charset=utf-8', connection: 'close',
    });
    response_.end('NESTED_NODE_FETCH_BODY');
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  try {
    const response = await globalThis.fetch(
      'http://127.0.0.1:' + server.address().port + '/nested-self-gate',
    );
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'NESTED_NODE_FETCH_BODY');
    assert.equal(response.bodyUsed, true);
  } finally {
    await new Promise((resolve, reject) => server.close((error) => {
      if (error) reject(error); else resolve();
    }));
  }
  assert.equal(server.listening, false);
  process.stdout.write('NESTED_NODE_FETCH_BODY\n');
`;
const CAP_SYS_ADMIN = 21n;
const MAX_COVERAGE_FILES_PER_TEST = 512;
const MAX_COVERAGE_FILE_BYTES = 64 * 1024 * 1024;
const MAX_COVERAGE_BYTES_PER_TEST = 512 * 1024 * 1024;
const MAX_PUBLISHED_COVERAGE_FILES = 8192;
const MAX_PUBLISHED_COVERAGE_BYTES = 4 * 1024 * 1024 * 1024;
const MAX_SAFE_BIND_ENTRIES = 50_000;
const MAX_SAFE_BIND_DEPTH = 64;
const MAX_SNAPSHOT_FILE_BYTES = 128 * 1024 * 1024;
const MAX_DIRECTORY_ENTRIES = 4096;
const REPOSITORY_ENTRIES = [
  '.c8rc.json', '.env.example', '.gitignore', 'README.md', 'benchmark', 'deploy', 'docs',
  'eslint.complexity.config.js', 'eslint.config.js', 'eva_brain', 'node_modules',
  'native', 'package-lock.json', 'package.json', 'quality-baseline.json', 'scripts', 'src',
  'test', 'tsconfig.json', 'typescript-diagnostic-debt.json',
];
const SYSTEM_FILES = [
  '/etc/gai.conf', '/etc/group', '/etc/host.conf', '/etc/hosts',
  '/etc/ld.so.cache', '/etc/localtime', '/etc/nsswitch.conf', '/etc/passwd',
  '/etc/resolv.conf', '/etc/services',
];
const SYSTEM_EXECUTABLES = [
  process.execPath, '/usr/bin/getent', '/usr/bin/git', '/usr/bin/id', '/usr/bin/mkfifo', '/usr/bin/mount',
  '/usr/bin/setpriv', '/usr/bin/setsid', '/usr/bin/umount', '/usr/bin/unshare',
];
const SECCOMP_LAUNCHER_SOURCE = String.raw`
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/memfd.h>
#include <linux/seccomp.h>
#include <sched.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#ifndef __X32_SYSCALL_BIT
#define __X32_SYSCALL_BIT 0x40000000
#endif

#define DENY_ERRNO(nr, error_value) \
  BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1), \
  BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (error_value))

static int status_has(const char *needle) {
  char buffer[16384];
  int fd = open("/proc/self/status", O_RDONLY | O_CLOEXEC);
  if (fd < 0) return 0;
  ssize_t length = read(fd, buffer, sizeof(buffer) - 1);
  close(fd);
  if (length < 0) return 0;
  buffer[length] = '\0';
  return strstr(buffer, needle) != NULL;
}

static void require_zero_caps_and_nnp(void) {
  const char *required[] = {
    "CapInh:\t0000000000000000", "CapPrm:\t0000000000000000",
    "CapEff:\t0000000000000000", "CapBnd:\t0000000000000000",
    "CapAmb:\t0000000000000000", "NoNewPrivs:\t1"
  };
  for (size_t index = 0; index < sizeof(required) / sizeof(required[0]); index++) {
    if (!status_has(required[index])) {
      fprintf(stderr, "seccomp-launcher: ontbrekende privilegegarantie: %s\n", required[index]);
      exit(125);
    }
  }
}

static void require_exact_inherited_descriptors(void) {
#ifdef SYS_close_range
  if (syscall(SYS_close_range, 3U, 99U, 0U) != 0
      || syscall(SYS_close_range, 105U, ~0U, 0U) != 0) {
    perror("seccomp-launcher: kan onverwachte inherited fd's niet sluiten");
    exit(125);
  }
#else
  fprintf(stderr, "seccomp-launcher: kernelheaders missen close_range\n");
  exit(125);
#endif
  struct stat runner;
  const int required_seals = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE;
  if (fstat(STDIN_FILENO, &runner) != 0 || !S_ISREG(runner.st_mode)
      || runner.st_nlink != 0 || runner.st_size <= 0
      || fcntl(STDIN_FILENO, F_GET_SEALS) != required_seals) {
    fprintf(stderr, "seccomp-launcher: fd 0 is niet de volledig sealed runner\n");
    exit(125);
  }
  for (int descriptor = 0; descriptor <= 104; descriptor++) {
    const int expected = descriptor <= 2 || (descriptor >= 100 && descriptor <= 104);
    errno = 0;
    const int flags = fcntl(descriptor, F_GETFD);
    if ((expected && flags < 0) || (!expected && (flags >= 0 || errno != EBADF))) {
      fprintf(stderr, "seccomp-launcher: inherited fd-allowlist wijkt af bij %d\n", descriptor);
      exit(125);
    }
  }
}

struct pinned_device {
  const char *path;
  unsigned int major_number;
  unsigned int minor_number;
  int descriptor;
};

static void enter_with_pinned_devices(int argc, char **argv) {
  if (argc < 4) {
    fprintf(stderr, "seccomp-launcher: device-brokerargumenten ontbreken\n");
    exit(125);
  }
  const struct pinned_device devices[] = {
    { "/dev/null", 1, 3, 100 }, { "/dev/zero", 1, 5, 101 },
    { "/dev/full", 1, 7, 102 }, { "/dev/random", 1, 8, 103 },
    { "/dev/urandom", 1, 9, 104 },
  };
  for (size_t index = 0; index < sizeof(devices) / sizeof(devices[0]); index++) {
    int source = open(devices[index].path, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (source < 0) {
      perror("seccomp-launcher: kan exact hostdevice niet openen");
      exit(125);
    }
    struct stat status;
    if (fstat(source, &status) != 0 || !S_ISCHR(status.st_mode)
        || major(status.st_rdev) != devices[index].major_number
        || minor(status.st_rdev) != devices[index].minor_number) {
      fprintf(stderr, "seccomp-launcher: hostdevice heeft verkeerde identiteit: %s\n",
        devices[index].path);
      exit(125);
    }
    if (dup3(source, devices[index].descriptor, 0) != devices[index].descriptor) {
      perror("seccomp-launcher: kan device niet naar vaste descriptor pinnen");
      exit(125);
    }
    if (close(source) != 0) {
      perror("seccomp-launcher: kan tijdelijke devicedescriptor niet sluiten");
      exit(125);
    }
    if ((fcntl(devices[index].descriptor, F_GETFD) & FD_CLOEXEC) != 0) {
      fprintf(stderr, "seccomp-launcher: gepinde devicedescriptor is CLOEXEC\n");
      exit(125);
    }
  }
  execv(argv[2], &argv[2]);
  perror("seccomp-launcher: device-broker execv");
  exit(125);
}

static void install_filter(void) {
#define NAMESPACE_FLAGS (CLONE_NEWCGROUP | CLONE_NEWIPC | CLONE_NEWNET \
  | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWTIME | CLONE_NEWUSER | CLONE_NEWUTS)
  struct sock_filter filter[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, __X32_SYSCALL_BIT, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
#ifdef __NR_clone
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_clone, 0, 3),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
    BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K, NAMESPACE_FLAGS, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_clone3
    DENY_ERRNO(__NR_clone3, ENOSYS),
#endif
#ifdef __NR_socket
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socket, 0, 5),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_UNIX, 3, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET, 2, 0),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_INET6, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_socketpair
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socketpair, 0, 3),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AF_UNIX, 1, 0),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
#endif
#ifdef __NR_keyctl
    DENY_ERRNO(__NR_keyctl, EPERM),
#endif
#ifdef __NR_add_key
    DENY_ERRNO(__NR_add_key, EPERM),
#endif
#ifdef __NR_request_key
    DENY_ERRNO(__NR_request_key, EPERM),
#endif
#ifdef __NR_unshare
    DENY_ERRNO(__NR_unshare, EPERM),
#endif
#ifdef __NR_setns
    DENY_ERRNO(__NR_setns, EPERM),
#endif
#ifdef __NR_mount
    DENY_ERRNO(__NR_mount, EPERM),
#endif
#ifdef __NR_umount2
    DENY_ERRNO(__NR_umount2, EPERM),
#endif
#ifdef __NR_pivot_root
    DENY_ERRNO(__NR_pivot_root, EPERM),
#endif
#ifdef __NR_chroot
    DENY_ERRNO(__NR_chroot, EPERM),
#endif
#ifdef __NR_move_mount
    DENY_ERRNO(__NR_move_mount, EPERM),
#endif
#ifdef __NR_open_tree
    DENY_ERRNO(__NR_open_tree, EPERM),
#endif
#ifdef __NR_fsopen
    DENY_ERRNO(__NR_fsopen, EPERM),
#endif
#ifdef __NR_fsconfig
    DENY_ERRNO(__NR_fsconfig, EPERM),
#endif
#ifdef __NR_fsmount
    DENY_ERRNO(__NR_fsmount, EPERM),
#endif
#ifdef __NR_fspick
    DENY_ERRNO(__NR_fspick, EPERM),
#endif
#ifdef __NR_mount_setattr
    DENY_ERRNO(__NR_mount_setattr, EPERM),
#endif
#ifdef __NR_open_by_handle_at
    DENY_ERRNO(__NR_open_by_handle_at, EPERM),
#endif
#ifdef __NR_name_to_handle_at
    DENY_ERRNO(__NR_name_to_handle_at, EPERM),
#endif
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
  };
  struct sock_fprog program = {
    .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
    .filter = filter,
  };
  if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) {
    perror("seccomp-launcher: PR_SET_SECCOMP");
    exit(125);
  }
  if (prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER) {
    fprintf(stderr, "seccomp-launcher: filter niet aantoonbaar actief\n");
    exit(125);
  }
  errno = 0;
  if (unshare(0) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: unshare-probe werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
  errno = 0;
  if (syscall(__X32_SYSCALL_BIT | __NR_getpid) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: x32-syscallbit werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
#ifdef __NR_socket
  errno = 0;
  if (syscall(__NR_socket, AF_VSOCK, SOCK_STREAM, 0) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: AF_VSOCK werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
#endif
#ifdef __NR_socketpair
  int sockets[2];
  errno = 0;
  if (syscall(__NR_socketpair, AF_VSOCK, SOCK_STREAM, 0, sockets) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: AF_VSOCK socketpair werd niet geblokkeerd\n");
    exit(125);
  }
#endif
#ifdef __NR_keyctl
  errno = 0;
  if (syscall(__NR_keyctl, 0, 0, 0, 0, 0) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: keyctl werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
#endif
#ifdef __NR_add_key
  errno = 0;
  if (syscall(__NR_add_key, NULL, NULL, NULL, 0, 0) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: add_key werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
#endif
#ifdef __NR_request_key
  errno = 0;
  if (syscall(__NR_request_key, NULL, NULL, NULL, 0) != -1 || errno != EPERM) {
    fprintf(stderr, "seccomp-launcher: request_key werd niet met EPERM geblokkeerd\n");
    exit(125);
  }
#endif
}

int main(int argc, char **argv) {
  if (argc >= 2 && strcmp(argv[1], "--enter-with-pinned-devices") == 0) {
    enter_with_pinned_devices(argc, argv);
  }
  require_zero_caps_and_nnp();
  install_filter();
  if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return 0;
  if (argc >= 3 && strcmp(argv[1], "--exec-with-exact-fds") == 0) {
    require_exact_inherited_descriptors();
    execv(argv[2], &argv[2]);
    perror("seccomp-launcher: exact-fd execv");
    return 125;
  }
  if (argc < 2) {
    fprintf(stderr, "seccomp-launcher: uitvoerbaar programma ontbreekt\n");
    return 125;
  }
  execv(argv[1], &argv[1]);
  perror("seccomp-launcher: execv");
  return 125;
}
`;

function fail(message) {
  throw new Error(message);
}

function readDirectoryBounded(filename, maximum = MAX_DIRECTORY_ENTRIES) {
  const directory = fs.opendirSync(filename);
  const entries = [];
  try {
    for (;;) {
      const entry = directory.readSync();
      if (entry === null) break;
      entries.push(entry);
      if (entries.length > maximum) fail(`te veel directoryentries in ${filename}`);
    }
  } finally {
    directory.closeSync();
  }
  return entries;
}

function assertStaticBootstrapEnvironment() {
  const root = process.env.BOTTIE_BOOTSTRAP_ROOT;
  if (process.env.BOTTIE_STATIC_BOOTSTRAP !== '1'
      || typeof root !== 'string'
      || !/^\/tmp\/bottie-test-bootstrap-[A-Za-z0-9]{6}$/u.test(root)) {
    fail('directe Node-start geweigerd: statische bootstrap ontbreekt');
  }
  assertExactNodeWasmFlag();
  const expected = new Set([
    'PATH', 'LANG', 'LC_ALL', 'HOME', 'TMPDIR', 'BOTTIE_STATIC_BOOTSTRAP',
    'BOTTIE_BOOTSTRAP_ROOT',
    'BOTTIE_REPOSITORY_ROOT',
    'EVA_ISOLATED_RESOURCE_GATE',
  ]);
  if (Object.hasOwn(process.env, 'BOTTIE_COVERAGE_OUTPUT_FD')) {
    expected.add('BOTTIE_COVERAGE_OUTPUT_FD');
  }
  const actual = Object.keys(process.env);
  for (const name of actual) {
    if (!expected.has(name)) fail(`onverwachte bootstrapomgeving geweigerd: ${name}`);
  }
  for (const name of expected) {
    if (!Object.hasOwn(process.env, name)) fail(`bootstrapomgeving mist ${name}`);
  }
  if (process.env.PATH !== '/usr/bin:/bin' || process.env.LANG !== 'C.UTF-8'
      || process.env.LC_ALL !== 'C.UTF-8' || process.env.HOME !== root
      || process.env.TMPDIR !== '/tmp' || process.env.BOTTIE_REPOSITORY_ROOT !== REPOSITORY
      || process.cwd() !== REPOSITORY || process.env.EVA_ISOLATED_RESOURCE_GATE !== '1') {
    fail('bootstrapomgeving bevat geen exacte vertrouwde waarden');
  }
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.uid !== process.getuid()
      || (stat.mode & 0o777) !== 0o700 || fs.realpathSync(root) !== root) {
    fail('bootstraproot is niet privé en canoniek');
  }
  return { root, identity: identity(stat) };
}

function assertExactNodeWasmFlag() {
  const flags = process.execArgv.filter((argument) => argument === NODE_WASM_FLAG);
  if (flags.length !== 1) fail('Node-boundary mist exact één disable-wasm-trap-handler-vlag');
}

function sameSecret(left, right) {
  if (typeof left !== 'string' || typeof right !== 'string') return false;
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function encodePayload(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url');
}

function decodePayload(encoded) {
  try {
    const decoded = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'));
    if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded)) fail('invalid payload');
    return decoded;
  } catch {
    fail('ongeldige interne runner-payload');
  }
}

function identity(stat) {
  return { dev: String(stat.dev), ino: String(stat.ino) };
}

function assertIdentity(stat, expected, label) {
  if (String(stat.dev) !== expected?.dev || String(stat.ino) !== expected?.ino) {
    fail(`${label} veranderde tijdens de geïsoleerde start`);
  }
}

function namespaceIdentity(name) {
  return identity(fs.statSync(`/proc/self/ns/${name}`));
}

function assertDifferentIdentity(actual, original, label) {
  if (actual.dev === original?.dev && actual.ino === original?.ino) {
    fail(`${label} is niet geïsoleerd`);
  }
}

function assertSameIdentity(actual, expected, label) {
  if (actual.dev !== expected?.dev || actual.ino !== expected?.ino) {
    fail(`${label} veranderde onverwacht`);
  }
}

function assertRootTool(filename) {
  const stat = fs.lstatSync(filename);
  if (!stat.isFile() || stat.isSymbolicLink()) fail(`${filename} is geen vast regulier bestand`);
  const trustedRootUid = stat.uid === 0
    || (process.env.EVA_ISOLATED_RESOURCE_GATE === '1' && stat.uid === 65_534);
  if (!trustedRootUid || (stat.mode & 0o022) !== 0) {
    fail(`${filename} heeft onveilige ownership/mode`);
  }
  fs.accessSync(filename, fs.constants.X_OK);
}

function assertPrivateDirectory(filename, expected, label) {
  const stat = fs.lstatSync(filename);
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail(`${label} is geen echte directory`);
  if (stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o700) {
    fail(`${label} is niet privé voor de huidige gebruiker`);
  }
  if (fs.realpathSync(filename) !== filename) fail(`${label} bevat een symlinkomleiding`);
  if (expected) assertIdentity(stat, expected, label);
  return stat;
}

function assertDataMountTarget(expected) {
  let stat;
  try {
    stat = fs.lstatSync(DATA_MOUNT);
  } catch (error) {
    fail(`data mount target ontbreekt: ${error.message}`);
  }
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    fail('data mount target mag geen symbolic link/symlink zijn');
  }
  if (fs.realpathSync(DATA_MOUNT) !== DATA_MOUNT) fail('data mount target bevat een symlink');
  if (expected) assertIdentity(stat, expected, 'data mount target');
  const descriptor = fs.openSync(
    DATA_MOUNT,
    fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
  );
  try {
    assertIdentity(fs.fstatSync(descriptor), identity(stat), 'data mount target descriptor');
  } finally {
    fs.closeSync(descriptor);
  }
  return stat;
}

function procStatus() {
  const fields = new Map();
  for (const line of fs.readFileSync('/proc/self/status', 'utf8').split('\n')) {
    const match = /^([^:]+):\s*(.*)$/u.exec(line);
    if (match) fields.set(match[1], match[2].trim());
  }
  return fields;
}

function assertNamespaceMountCapability() {
  const value = procStatus().get('CapEff');
  if (!value || (BigInt(`0x${value}`) & (1n << CAP_SYS_ADMIN)) === 0n) {
    fail('nieuwe user namespace bezit geen tijdelijke CAP_SYS_ADMIN voor de shadowmount');
  }
}

function assertUidMapping(outerUid, outerGid) {
  const parseMap = (filename) => fs.readFileSync(filename, 'utf8').trim().split(/\s+/u)
    .map((value) => Number.parseInt(value, 10));
  const uidMap = parseMap('/proc/self/uid_map');
  const gidMap = parseMap('/proc/self/gid_map');
  if (uidMap.length !== 3 || uidMap[0] !== outerUid || uidMap[1] !== outerUid
      || uidMap[2] !== 1) fail('user namespace uid-map is niet exact --map-current-user');
  if (gidMap.length !== 3 || gidMap[0] !== outerGid || gidMap[1] !== outerGid
      || gidMap[2] !== 1) fail('user namespace gid-map is niet exact --map-current-user');
  if (process.getuid() !== outerUid || process.getgid() !== outerGid) {
    fail('user namespace veranderde de uitvoerende uid/gid');
  }
}

function isolatedNetworkState() {
  const interfaces = fs.readFileSync('/proc/net/dev', 'utf8').split('\n').slice(2)
    .map((line) => line.split(':')[0].trim()).filter(Boolean).sort();
  const ipv4Routes = fs.readFileSync('/proc/net/route', 'utf8').trim().split('\n').slice(1)
    .filter((line) => line.trim());
  const ipv6NonLoopbackRoutes = fs.readFileSync('/proc/net/ipv6_route', 'utf8').trim()
    .split('\n').filter((line) => line.trim()).filter((line) => {
      const fields = line.trim().split(/\s+/u);
      return fields.at(-1) !== 'lo';
    });
  return { interfaces, ipv4Routes, ipv6NonLoopbackRoutes };
}

function assertIsolatedNetwork() {
  const state = isolatedNetworkState();
  if (state.interfaces.length !== 1 || state.interfaces[0] !== 'lo') {
    fail(`netwerknamespace bevat andere interfaces dan lo: ${state.interfaces.join(',')}`);
  }
  if (state.ipv4Routes.length !== 0 || state.ipv6NonLoopbackRoutes.length !== 0) {
    fail('netwerknamespace bevat een niet-lokale route');
  }
}

function mountInfoEscaped(filename) {
  return filename.replace(/\\/gu, '\\134').replace(/ /gu, '\\040')
    .replace(/\t/gu, '\\011').replace(/\n/gu, '\\012');
}

function assertReadOnlyShadowMount(payload) {
  const targetStat = fs.statSync(payload.dataMount);
  assertIdentity(targetStat, payload.shadowIdentity, 'read-only data shadowmount');
  const encodedTarget = mountInfoEscaped(payload.dataMount);
  const entries = fs.readFileSync('/proc/self/mountinfo', 'utf8').trim().split('\n')
    .map((line) => line.split(' ')).filter((fields) => fields[4] === encodedTarget);
  if (entries.length !== 1) fail('data shadowmount is niet eenduidig aantoonbaar in mountinfo');
  const options = new Set(entries[0][5].split(','));
  for (const required of ['ro', 'nosuid', 'nodev', 'noexec']) {
    if (!options.has(required)) fail(`data shadowmount mist mountoptie ${required}`);
  }
  if (readDirectoryBounded(payload.dataMount, 1).length !== 0) {
    fail('data shadowmount is niet leeg');
  }
}

function assertCapabilitiesDropped(parentSeccompFilters) {
  const status = procStatus();
  if (status.get('NoNewPrivs') !== '1') fail('no_new_privs is niet aantoonbaar actief');
  for (const field of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) {
    const value = status.get(field);
    if (!value || !/^0+$/u.test(value)) fail(`${field} is niet volledig nul`);
  }
  if (status.get('Seccomp') !== '2') fail('seccomp filtermodus is niet aantoonbaar actief');
  const filterCount = Number.parseInt(status.get('Seccomp_filters'), 10);
  if (!Number.isSafeInteger(filterCount) || filterCount <= parentSeccompFilters) {
    fail('de extra mount/namespace-seccompfilter is niet aantoonbaar actief');
  }
}

function spawnChecked(filename, args, descriptors = []) {
  const child = spawnSync(filename, args, {
    stdio: ['ignore', 'pipe', 'pipe', ...descriptors],
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  });
  if (child.error) fail(`${filename} kon niet starten: ${child.error.message}`);
  if (child.signal || child.status !== 0) {
    const detail = (child.stderr || child.stdout || '').trim().slice(0, 1000);
    fail(`${filename} faalde gesloten${detail ? `: ${detail}` : ''}`);
  }
}

function jailPath(jailRoot, absolutePath) {
  if (!path.isAbsolute(absolutePath)) fail(`geen absoluut jailpad: ${absolutePath}`);
  const target = path.join(jailRoot, absolutePath);
  const relative = path.relative(jailRoot, target);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
    fail(`jailpad ontsnapt uit de root: ${absolutePath}`);
  }
  return target;
}

function makeMountTarget(jailRoot, absolutePath, sourceStat) {
  const target = jailPath(jailRoot, absolutePath);
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o755 });
  if (!fs.existsSync(target)) {
    if (sourceStat.isDirectory()) fs.mkdirSync(target, { mode: 0o755 });
    else fs.closeSync(fs.openSync(target, 'wx', 0o600));
  } else {
    const targetStat = fs.lstatSync(target);
    if (targetStat.isSymbolicLink()
        || targetStat.isDirectory() !== sourceStat.isDirectory()
        || (!sourceStat.isDirectory() && !targetStat.isFile())) {
      fail(`bestaand jailmounttarget heeft verkeerd type: ${absolutePath}`);
    }
  }
  return target;
}

function assertSafeBindDescriptor(descriptor, rootDevice, budget, label, depth = 0) {
  if (depth > MAX_SAFE_BIND_DEPTH) fail(`bindbronboom is te diep: ${label}`);
  const stat = fs.fstatSync(descriptor);
  if (String(stat.dev) !== rootDevice) fail(`bindbron bevat submount: ${label}`);
  if (stat.uid !== process.getuid() && stat.uid !== 0) {
    fail(`bindbron heeft onverwachte eigenaar: ${label}`);
  }
  if (stat.isFile()) {
    if (stat.nlink !== 1) fail(`bindbron bevat hardlink: ${label}`);
    return;
  }
  if (!stat.isDirectory()) fail(`bindbron bevat speciaal bestand: ${label}`);
  const descriptorPath = `/proc/self/fd/${descriptor}`;
  for (const entry of readDirectoryBounded(descriptorPath, MAX_SAFE_BIND_ENTRIES)) {
    budget.count += 1;
    if (budget.count > MAX_SAFE_BIND_ENTRIES) fail(`bindbronboom is te groot: ${label}`);
    const entryPath = `${descriptorPath}/${entry.name}`;
    const entryStat = fs.lstatSync(entryPath);
    if (entryStat.isSymbolicLink()) fail(`bindbron bevat symlink: ${label}/${entry.name}`);
    if (String(entryStat.dev) !== rootDevice) {
      fail(`bindbron bevat ander filesystem: ${label}/${entry.name}`);
    }
    if (entryStat.uid !== process.getuid() && entryStat.uid !== 0) {
      fail(`bindbron heeft onverwachte eigenaar: ${label}/${entry.name}`);
    }
    if (entryStat.isFile()) {
      if (entryStat.nlink !== 1) fail(`bindbron bevat hardlink: ${label}/${entry.name}`);
      continue;
    }
    if (!entryStat.isDirectory()) {
      fail(`bindbron bevat speciaal bestand: ${label}/${entry.name}`);
    }
    const child = fs.openSync(entryPath,
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    try {
      assertIdentity(fs.fstatSync(child), identity(entryStat),
        `bindbron ${label}/${entry.name}`);
      assertSafeBindDescriptor(child, rootDevice, budget, `${label}/${entry.name}`, depth + 1);
    } finally {
      fs.closeSync(child);
    }
  }
}

function assertSafeBindSource(source) {
  const initial = fs.lstatSync(source);
  if (initial.isSymbolicLink() || (!initial.isDirectory() && !initial.isFile())) {
    fail(`bindbron is geen reguliere directory/file: ${source}`);
  }
  const flags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
    | (initial.isDirectory() ? fs.constants.O_DIRECTORY : 0);
  const descriptor = fs.openSync(source, flags);
  try {
    assertIdentity(fs.fstatSync(descriptor), identity(initial), `bindbron ${source}`);
    assertSafeBindDescriptor(descriptor, String(initial.dev), { count: 0 }, source);
  } finally {
    fs.closeSync(descriptor);
  }
}

function bindMountIntoJail(jailRoot, source, destination = source, options = {}) {
  assertSafeBindSource(source);
  const sourceStat = fs.lstatSync(source);
  if ((!sourceStat.isDirectory() && !sourceStat.isFile()) || sourceStat.isSymbolicLink()) {
    fail(`onveilig bindmount-brontype: ${source}`);
  }
  const target = makeMountTarget(jailRoot, destination, sourceStat);
  const sourceFlags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
    | (sourceStat.isDirectory() ? fs.constants.O_DIRECTORY : 0);
  const targetFlags = fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW
    | (sourceStat.isDirectory() ? fs.constants.O_DIRECTORY : 0);
  const sourceDescriptor = fs.openSync(source, sourceFlags);
  const targetDescriptor = fs.openSync(target, targetFlags);
  try {
    assertIdentity(fs.fstatSync(sourceDescriptor), identity(sourceStat), `bindbron ${source}`);
    spawnChecked(MOUNT, ['--bind', '/proc/self/fd/3', '/proc/self/fd/4'], [
      sourceDescriptor, targetDescriptor,
    ]);
  } finally {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(targetDescriptor);
  }
  assertIdentity(fs.statSync(target), identity(sourceStat), `bindmount ${destination}`);
  const mountOptions = options.device
    ? 'remount,bind,rw,nosuid,noexec'
    : `remount,bind,${options.readonly === false ? 'rw' : 'ro'},nosuid,nodev`;
  spawnChecked(MOUNT, ['-o', mountOptions, target]);
  return target;
}

function copyPathIntoJail(jailRoot, source, destination = source, state = { count: 0 }) {
  state.count += 1;
  if (state.count > MAX_SAFE_BIND_ENTRIES) fail(`jailkopieboom is te groot: ${source}`);
  const sourceStat = fs.lstatSync(source);
  const target = jailPath(jailRoot, destination);
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o755 });
  if (sourceStat.isSymbolicLink()) {
    if (pathLexicallyExists(target)) return;
    fs.symlinkSync(fs.readlinkSync(source), target);
    return;
  }
  if (sourceStat.isDirectory()) {
    if (pathLexicallyExists(target)) return;
    fs.mkdirSync(target, { mode: 0o700 });
    for (const entry of readDirectoryBounded(source)) {
      copyPathIntoJail(jailRoot, path.join(source, entry.name),
        path.join(destination, entry.name), state);
    }
    fs.chmodSync(target, sourceStat.mode & 0o777);
    return;
  }
  if (!sourceStat.isFile()) fail(`kopiebron is geen regulier bestand: ${source}`);
  if (pathLexicallyExists(target)) return;
  fs.copyFileSync(source, target, fs.constants.COPYFILE_EXCL);
  fs.chmodSync(target, sourceStat.mode & 0o777);
}

function pathLexicallyExists(filename) {
  try {
    fs.lstatSync(filename);
    return true;
  } catch (error) {
    if (error?.code === 'ENOENT') return false;
    throw error;
  }
}

function copyPathAndSymlinkTargetIntoJail(jailRoot, source) {
  copyPathIntoJail(jailRoot, source);
  if (fs.lstatSync(source).isSymbolicLink()) {
    const resolved = fs.realpathSync(source);
    if (resolved !== source) copyPathIntoJail(jailRoot, resolved);
  }
}

function copySafeTreeEntry(source, target, state, options, depth = 0) {
  if (depth > MAX_SAFE_BIND_DEPTH) fail(`snapshotbron is te diep: ${source}`);
  const initial = fs.lstatSync(source);
  if (initial.isSymbolicLink()) {
    if (options.skipSymlinks) return;
    fail(`snapshotbron bevat symlink: ${source}`);
  }
  if (String(initial.dev) !== state.rootDevice) {
    fail(`snapshotbron bevat ander filesystem: ${source}`);
  }
  if (initial.uid !== process.getuid() && initial.uid !== 0) {
    fail(`snapshotbron heeft onverwachte eigenaar: ${source}`);
  }
  state.count += 1;
  if (state.count > MAX_SAFE_BIND_ENTRIES) fail(`snapshotbronboom is te groot: ${source}`);
  if (initial.isFile()) {
    if (initial.nlink !== 1 || initial.size < 0 || initial.size > MAX_SNAPSHOT_FILE_BYTES) {
      fail(`snapshotbestand is geen begrensd enkelvoudig bestand: ${source}`);
    }
    const sourceDescriptor = fs.openSync(source, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const targetDescriptor = fs.openSync(target,
      fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL
        | fs.constants.O_NOFOLLOW, initial.mode & 0o777);
    try {
      assertIdentity(fs.fstatSync(sourceDescriptor), identity(initial), `snapshotbron ${source}`);
      const buffer = Buffer.allocUnsafe(64 * 1024);
      let copied = 0;
      for (;;) {
        const count = fs.readSync(sourceDescriptor, buffer, 0, buffer.length, null);
        if (count === 0) break;
        let written = 0;
        while (written < count) {
          written += fs.writeSync(targetDescriptor, buffer, written, count - written, null);
        }
        copied += count;
        if (copied > MAX_SNAPSHOT_FILE_BYTES) fail(`snapshotbestand groeide: ${source}`);
      }
      const finalSource = fs.fstatSync(sourceDescriptor);
      assertIdentity(finalSource, identity(initial), `snapshotbron ${source}`);
      if (copied !== initial.size || finalSource.size !== initial.size) {
        fail(`snapshotbestand veranderde tijdens kopiëren: ${source}`);
      }
      fs.fchmodSync(targetDescriptor, initial.mode & 0o777);
    } finally {
      fs.closeSync(targetDescriptor);
      fs.closeSync(sourceDescriptor);
    }
    return;
  }
  if (!initial.isDirectory()) fail(`snapshotbron bevat speciaal bestand: ${source}`);
  fs.mkdirSync(target, { mode: 0o700 });
  const descriptor = fs.openSync(source,
    fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try {
    assertIdentity(fs.fstatSync(descriptor), identity(initial), `snapshotbron ${source}`);
    const descriptorPath = `/proc/self/fd/${descriptor}`;
    for (const entry of readDirectoryBounded(descriptorPath, MAX_SAFE_BIND_ENTRIES)) {
      copySafeTreeEntry(`${descriptorPath}/${entry.name}`, path.join(target, entry.name),
        state, options, depth + 1);
    }
    fs.chmodSync(target, initial.mode & 0o777);
  } finally {
    fs.closeSync(descriptor);
  }
}

function copySafeTree(source, target, options = {}) {
  const initial = fs.lstatSync(source);
  copySafeTreeEntry(source, target, { count: 0, rootDevice: String(initial.dev) }, {
    skipSymlinks: options.skipSymlinks === true,
  });
  assertSafeBindSource(target);
}

function createSafeRepositorySnapshot(bootstrapRoot) {
  const target = path.join(bootstrapRoot, 'safe-repository');
  fs.mkdirSync(target, { mode: 0o700 });
  for (const entry of REPOSITORY_ENTRIES) {
    if (entry === 'node_modules') continue;
    const source = path.join(REPOSITORY, entry);
    if (fs.existsSync(source)) copySafeTree(source, path.join(target, entry));
  }
  assertSafeBindSource(target);
  return target;
}

function createSafeNodeModulesMirror(bootstrapRoot) {
  const source = path.join(REPOSITORY, 'node_modules');
  const target = path.join(bootstrapRoot, 'safe-node-modules');
  if (fs.existsSync(target)) fail('safe node_modules-mirror bestaat al');
  if (fs.existsSync(source)) {
    copySafeTree(source, target, { skipSymlinks: true });
  } else {
    fs.mkdirSync(target, { mode: 0o700 });
    assertSafeBindSource(target);
  }
  return target;
}

function dynamicLibraries(filename) {
  const child = spawnSync('/usr/bin/ldd', [filename], {
    cwd: '/tmp', encoding: 'utf8', maxBuffer: 1024 * 1024,
  });
  if (child.error || child.signal || child.status !== 0) {
    fail(`ldd faalde gesloten voor ${filename}: ${(child.stderr || child.stdout || '').trim()}`);
  }
  const libraries = new Set();
  for (const line of child.stdout.split('\n')) {
    const match = /(?:=>\s+)?(\/[^\s]+)\s+\(0x[0-9a-f]+\)/iu.exec(line);
    if (match) libraries.add(match[1]);
  }
  if (libraries.size === 0) fail(`geen dynamische bibliotheken gevonden voor ${filename}`);
  return libraries;
}

function createSystemRuntime(jailRoot) {
  const systemBinStat = fs.lstatSync('/usr/bin');
  if (!systemBinStat.isDirectory() || systemBinStat.isSymbolicLink()
      || ![0, 65_534].includes(systemBinStat.uid) || (systemBinStat.mode & 0o022) !== 0
      || fs.realpathSync('/usr/bin') !== '/usr/bin') {
    fail('systeem-executableauthority is niet root-owned readonly');
  }
  for (const directory of [
    '/usr/bin', '/usr/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/sbin', '/usr/share',
  ]) {
    fs.mkdirSync(jailPath(jailRoot, directory), { recursive: true, mode: 0o755 });
  }
  const libraries = new Set();
  for (const executable of SYSTEM_EXECUTABLES) {
    const executableStat = fs.lstatSync(executable);
    if (!executableStat.isFile() || executableStat.isSymbolicLink()
        || executableStat.uid !== systemBinStat.uid || executableStat.nlink !== 1
        || (executableStat.mode & 0o022) !== 0 || fs.realpathSync(executable) !== executable) {
      fail(`onveilige runtime-executable: ${executable}`);
    }
    copyPathAndSymlinkTargetIntoJail(jailRoot, executable);
    for (const library of dynamicLibraries(executable)) libraries.add(library);
  }
  for (const library of libraries) copyPathAndSymlinkTargetIntoJail(jailRoot, library);
  copyPathAndSymlinkTargetIntoJail(jailRoot, '/lib64/ld-linux-x86-64.so.2');
  if (fs.existsSync('/usr/lib/git-core')) copyPathIntoJail(jailRoot, '/usr/lib/git-core');
  if (fs.existsSync('/usr/share/git-core')) copyPathIntoJail(jailRoot, '/usr/share/git-core');
  if (fs.existsSync('/usr/share/zoneinfo')) copyPathIntoJail(jailRoot, '/usr/share/zoneinfo');
}

function remountJailDirectoryReadOnly(jailRoot, directory, { noexec = false } = {}) {
  const target = jailPath(jailRoot, directory);
  const descriptor = fs.openSync(target,
    fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  try {
    const before = identity(fs.fstatSync(descriptor));
    spawnChecked(MOUNT, ['--bind', '/proc/self/fd/3', '/proc/self/fd/4'], [
      descriptor, descriptor,
    ]);
    spawnChecked(MOUNT, [
      '-o', `remount,bind,ro,nosuid,nodev${noexec ? ',noexec' : ''}`, '/proc/self/fd/3',
    ], [descriptor]);
    assertIdentity(fs.fstatSync(descriptor), before, `${directory} read-only jailmount`);
  } finally {
    fs.closeSync(descriptor);
  }
}

function assertNoRepositorySubmounts(coverageEnabled) {
  const encoded = mountInfoEscaped(REPOSITORY);
  const mounts = fs.readFileSync('/proc/self/mountinfo', 'utf8').trim().split('\n')
    .map((line) => line.split(' ')[4])
    .filter((mountpoint) => mountpoint === encoded || mountpoint.startsWith(`${encoded}/`));
  const rootMounts = mounts.filter((mountpoint) => mountpoint === encoded);
  const coverageMount = `${encoded}/coverage`;
  const allowedCoverageMounts = mounts.filter((mountpoint) => mountpoint === coverageMount);
  const nested = mounts.filter((mountpoint) => mountpoint !== encoded
    && mountpoint !== coverageMount);
  const expectedCoverageMounts = coverageEnabled ? 1 : 0;
  if (rootMounts.length !== 1 || nested.length > 0) {
    fail('repository mist de exacte resourcegate-bind of bevat onverwachte submounts');
  }
  if (allowedCoverageMounts.length !== expectedCoverageMounts) {
    fail('repository heeft geen exact begrensde coverage-reportbind');
  }
}

function createFilesystemJail(payload) {
  assertPrivateDirectory(payload.jailRoot, payload.jailIdentity, 'jailroot');
  assertNoRepositorySubmounts(Boolean(payload.coverageDir));
  for (const directory of ['/dev', '/etc', '/isolated', '/proc', '/tmp']) {
    fs.mkdirSync(jailPath(payload.jailRoot, directory), { recursive: true, mode: 0o755 });
  }
  const deviceRoot = jailPath(payload.jailRoot, '/dev');
  spawnChecked(MOUNT, [
    '-t', 'tmpfs', '-o', 'rw,nosuid,noexec,size=64k,mode=755', 'tmpfs', deviceRoot,
  ]);
  for (const [name, descriptor] of [
    ['null', 100], ['zero', 101], ['full', 102], ['random', 103], ['urandom', 104],
  ]) {
    fs.symlinkSync(`/proc/1/fd/${descriptor}`, path.join(deviceRoot, name));
  }
  fs.chmodSync(jailPath(payload.jailRoot, '/tmp'), 0o1777);
  for (const [link, target] of [
    ['/bin', 'usr/bin'], ['/lib', 'usr/lib'], ['/lib64', 'usr/lib64'], ['/sbin', 'usr/sbin'],
  ]) fs.symlinkSync(target, jailPath(payload.jailRoot, link));

  createSystemRuntime(payload.jailRoot);
  for (const filename of SYSTEM_FILES) {
    if (fs.existsSync(filename)) copyPathIntoJail(payload.jailRoot, filename);
  }
  for (const certificatePath of ['/etc/ssl/certs', '/etc/ssl/openssl.cnf']) {
    if (fs.existsSync(certificatePath)) {
      copyPathIntoJail(payload.jailRoot, certificatePath);
    }
  }
  remountJailDirectoryReadOnly(payload.jailRoot, '/usr');
  remountJailDirectoryReadOnly(payload.jailRoot, '/etc', { noexec: true });
  const jailCgroup = jailPath(payload.jailRoot, '/sys/fs/cgroup');
  if (pathLexicallyExists(jailCgroup)) fail('jail bevat onverwacht een cgroup-controlpad');
  for (const [link, target] of [
    ['/dev/fd', '/proc/self/fd'], ['/dev/stdin', '/proc/self/fd/0'],
    ['/dev/stdout', '/proc/self/fd/1'], ['/dev/stderr', '/proc/self/fd/2'],
  ]) fs.symlinkSync(target, jailPath(payload.jailRoot, link));

  fs.mkdirSync(jailPath(payload.jailRoot, payload.repository), { recursive: true, mode: 0o755 });
  fs.mkdirSync(jailPath(payload.jailRoot, payload.dataMount), { mode: 0o700 });
  for (const entry of REPOSITORY_ENTRIES) {
    const source = entry === 'node_modules'
      ? payload.safeNodeModules : path.join(payload.safeRepository, entry);
    if (fs.existsSync(source)) {
      bindMountIntoJail(payload.jailRoot, source, path.join(payload.repository, entry));
    }
  }
  bindMountIntoJail(payload.jailRoot, payload.isolationRoot, payload.isolationRoot, {
    readonly: false,
  });
  assertIdentity(fs.lstatSync(payload.seccompLauncher), payload.seccompIdentity,
    'seccomp-launcher');
  bindMountIntoJail(payload.jailRoot, payload.seccompLauncher, '/isolated/seccomp-launcher');
  const shadow = jailPath(payload.jailRoot, '/isolated/empty-data-shadow');
  fs.mkdirSync(shadow, { mode: 0o700 });
  payload.shadowIdentity = identity(fs.lstatSync(shadow));
  bindMountIntoJail(payload.jailRoot, shadow, payload.dataMount);
  spawnChecked(MOUNT, [
    '-o', 'remount,bind,ro,nosuid,nodev,noexec', jailPath(payload.jailRoot, payload.dataMount),
  ]);
  const procTarget = jailPath(payload.jailRoot, '/proc');
  spawnChecked(MOUNT, ['-t', 'proc', '-o', 'nosuid,nodev,noexec', 'proc', procTarget]);
}

function internalNamespace(payload) {
  assertDifferentIdentity(namespaceIdentity('user'), payload.parentUserNamespace, 'user namespace');
  assertDifferentIdentity(namespaceIdentity('mnt'), payload.parentMountNamespace, 'mount namespace');
  assertDifferentIdentity(namespaceIdentity('pid'), payload.parentPidNamespace, 'pid namespace');
  assertDifferentIdentity(namespaceIdentity('net'), payload.parentNetNamespace, 'net namespace');
  assertDifferentIdentity(namespaceIdentity('ipc'), payload.parentIpcNamespace, 'ipc namespace');
  if (process.pid !== 1) fail('namespace-bootstrap is niet PID 1');
  assertUidMapping(payload.uid, payload.gid);
  assertNamespaceMountCapability();
  spawnChecked(IP, ['link', 'set', 'dev', 'lo', 'up']);
  assertIsolatedNetwork();
  assertPrivateDirectory(payload.isolationRoot, payload.isolationIdentity, 'isolatieroot');
  assertPrivateDirectory(payload.dataDir, payload.dataIdentity, 'EVA_DATA_DIR');
  if (payload.coverageDir) {
    assertPrivateDirectory(payload.coverageDir, payload.coverageIdentity,
      'child-coveragedirectory');
  }
  assertPrivateDirectory(payload.jailRoot, payload.jailIdentity, 'jailroot');
  assertDataMountTarget(payload.dataMountIdentity);
  createFilesystemJail(payload);

  const namespacePayload = {
    ...payload,
    userNamespace: namespaceIdentity('user'),
    mountNamespace: namespaceIdentity('mnt'),
    pidNamespace: namespaceIdentity('pid'),
    netNamespace: namespaceIdentity('net'),
    ipcNamespace: namespaceIdentity('ipc'),
  };
  if (typeof process.execve !== 'function') fail('process.execve ontbreekt; cap-drop niet bewijsbaar');
  const bootstrapEnvironment = {
    PATH: '/usr/bin:/bin',
    LANG: 'C.UTF-8',
    TMPDIR: payload.isolationRoot,
    EVA_DATA_DIR: payload.dataDir,
    BOTTIE_REPOSITORY_ROOT: payload.repository,
    [INTERNAL_TOKEN_ENV]: payload.token,
  };
  process.execve(payload.seccompLauncher, [
    payload.seccompLauncher,
    '--enter-with-pinned-devices',
    CHROOT,
    payload.jailRoot,
    SETPRIV,
    '--no-new-privs',
    '--bounding-set=-all',
    '--inh-caps=-all',
    '--ambient-caps=-all',
    '/isolated/seccomp-launcher',
    '--exec-with-exact-fds',
    process.execPath,
    NODE_WASM_FLAG,
    '--experimental-default-type=module',
    '--preserve-symlinks-main',
    '/proc/self/fd/0',
    INTERNAL_VERIFY,
    payload.token,
    encodePayload(namespacePayload),
  ], bootstrapEnvironment);
  fail('chroot/setpriv exec keerde onverwacht terug');
}

function readTestEnvironment(payload) {
  const stat = fs.lstatSync(payload.environmentFile);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== process.getuid()
      || (stat.mode & 0o777) !== 0o600 || stat.nlink !== 1) {
    fail('testenvironmentbestand is niet privé en regulier');
  }
  if (fs.realpathSync(path.dirname(payload.environmentFile)) !== payload.isolationRoot) {
    fail('testenvironmentbestand staat buiten de isolatieroot');
  }
  const parsed = JSON.parse(fs.readFileSync(payload.environmentFile, 'utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    fail('testenvironmentbestand bevat geen object');
  }
  return Object.fromEntries(Object.entries(parsed).filter(([, value]) => typeof value === 'string'));
}

function internalVerify(payload) {
  assertSameIdentity(namespaceIdentity('user'), payload.userNamespace, 'user namespace');
  assertSameIdentity(namespaceIdentity('mnt'), payload.mountNamespace, 'mount namespace');
  assertSameIdentity(namespaceIdentity('pid'), payload.pidNamespace, 'pid namespace');
  assertSameIdentity(namespaceIdentity('net'), payload.netNamespace, 'net namespace');
  assertSameIdentity(namespaceIdentity('ipc'), payload.ipcNamespace, 'ipc namespace');
  if (process.pid !== 1) fail('geverifieerde bootstrap is niet PID 1');
  assertCapabilitiesDropped(payload.parentSeccompFilters);
  assertIsolatedNetwork();
  assertIdentity(fs.statSync('/'), payload.jailIdentity, 'chroot-root');
  assertDifferentIdentity(identity(fs.statSync('/')), payload.hostRootIdentity, 'chroot-root');
  assertIdentity(fs.statSync('/proc/1/root'), payload.jailIdentity, '/proc/1/root');
  assertPrivateDirectory(payload.isolationRoot, payload.isolationIdentity, 'isolatieroot');
  assertPrivateDirectory(payload.dataDir, payload.dataIdentity, 'EVA_DATA_DIR');
  if (payload.coverageDir) {
    assertPrivateDirectory(payload.coverageDir, payload.coverageIdentity,
      'child-coveragedirectory');
  }
  assertReadOnlyShadowMount(payload);

  for (const [name, descriptor, deviceId] of [
    ['null', 100, 259], ['zero', 101, 261], ['full', 102, 263],
    ['random', 103, 264], ['urandom', 104, 265],
  ]) {
    const devicePath = `/dev/${name}`;
    if (fs.readlinkSync(devicePath) !== `/proc/1/fd/${descriptor}`) {
      fail(`${devicePath} wijst niet naar de gepinde PID1-descriptor`);
    }
    const descriptorStat = fs.statSync(`/proc/1/fd/${descriptor}`);
    const deviceStat = fs.statSync(devicePath);
    if (!descriptorStat.isCharacterDevice() || !deviceStat.isCharacterDevice()
        || descriptorStat.rdev !== deviceId || deviceStat.rdev !== deviceId) {
      fail(`${devicePath} heeft niet de bewezen character-device-identiteit`);
    }
  }

  for (const { name: descriptor } of readDirectoryBounded('/proc/self/fd', 1024)) {
    if (Number.parseInt(descriptor, 10) <= 2) continue;
    try {
      const target = fs.readlinkSync(`/proc/self/fd/${descriptor}`);
      if (target === payload.repository || target.startsWith(`${payload.repository}/`)
          || target === payload.jailRoot || target.startsWith(`${payload.jailRoot}/`)) {
        fail(`inherited fd wijst naar host-repository/jailroot: ${target}`);
      }
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }

  const testEnvironment = readTestEnvironment(payload);
  fs.unlinkSync(payload.environmentFile);
  testEnvironment.EVA_DATA_DIR = payload.dataDir;
  testEnvironment.TMPDIR = payload.isolationRoot;
  testEnvironment[TEST_RUNNER_ENV] = '1';
  if (payload.coverageDir) testEnvironment.NODE_V8_COVERAGE = payload.coverageDir;
  process.chdir(payload.repository);
  const child = spawnSync(process.execPath, [NODE_WASM_FLAG, '--test', payload.testFile], {
    cwd: payload.repository,
    env: testEnvironment,
    stdio: ['ignore', 'inherit', 'inherit'],
  });
  if (child.error) fail(`Node-test kon niet starten: ${child.error.message}`);
  if (child.signal) fail(`Node-test eindigde door signaal ${child.signal}`);
  process.exit(child.status ?? 1);
}

function internalEntry(args) {
  const [phase, token, encodedPayload] = args;
  if (phase !== INTERNAL_NAMESPACE && phase !== INTERNAL_VERIFY) return false;
  assertExactNodeWasmFlag();
  if (!sameSecret(token, process.env[INTERNAL_TOKEN_ENV])) fail('interne runnerfase geweigerd');
  const payload = decodePayload(encodedPayload);
  if (!sameSecret(token, payload.token)) fail('interne runnerpayload geweigerd');
  if (payload.repository !== REPOSITORY || payload.dataMount !== DATA_MOUNT) {
    fail('interne repositorybinding klopt niet');
  }
  if (phase === INTERNAL_NAMESPACE) internalNamespace(payload);
  else internalVerify(payload);
  return true;
}

function assertHostPrerequisites() {
  if (process.platform !== 'linux') fail('de geïsoleerde runner vereist Linux');
  if (typeof process.getuid !== 'function' || typeof process.getgid !== 'function') {
    fail('uid/gid-controle ontbreekt');
  }
  if (typeof process.execve !== 'function') fail('deze Node-versie mist process.execve');
  if (process.arch !== 'x64') fail('de seccompfilter is uitsluitend voor Linux x86_64 bewezen');
  for (const tool of [UNHARE, MOUNT, SETPRIV, CHROOT, COMPILER, IP, process.execPath]) {
    assertRootTool(tool);
  }
  const temporary = fs.lstatSync('/tmp');
  if (!temporary.isDirectory() || temporary.isSymbolicLink()
      || fs.realpathSync('/tmp') !== '/tmp' || (temporary.mode & 0o1000) === 0) {
    fail('/tmp is geen vaste sticky temporary directory');
  }
  fs.accessSync('/proc/self/status', fs.constants.R_OK);
  fs.accessSync('/proc/self/mountinfo', fs.constants.R_OK);
  fs.accessSync('/proc/self/uid_map', fs.constants.R_OK);
  for (const [name, deviceId] of [
    ['null', 259], ['zero', 261], ['full', 263], ['random', 264], ['urandom', 265],
  ]) {
    const devicePath = `/dev/${name}`;
    const stat = fs.lstatSync(devicePath);
    const trustedRootUid = stat.uid === 0
      || (process.env.EVA_ISOLATED_RESOURCE_GATE === '1' && stat.uid === 65_534);
    if (!stat.isCharacterDevice() || stat.isSymbolicLink() || !trustedRootUid
        || stat.rdev !== deviceId || stat.nlink !== 1) {
      fail(`${devicePath} is niet het verwachte root-owned host-character-device`);
    }
    const descriptor = fs.openSync(devicePath,
      fs.constants.O_RDWR | fs.constants.O_NOFOLLOW);
    try {
      const opened = fs.fstatSync(descriptor);
      if (!opened.isCharacterDevice() || opened.rdev !== deviceId) {
        fail(`${devicePath} veranderde tijdens veilige opening`);
      }
    } finally {
      fs.closeSync(descriptor);
    }
  }
}

function privateTemporaryDirectory(bootstrapRoot, prefix) {
  const directory = fs.mkdtempSync(path.join(bootstrapRoot, `${prefix}-`));
  fs.chmodSync(directory, 0o700);
  if (fs.realpathSync(path.dirname(directory)) !== bootstrapRoot) {
    fail(`tijdelijke ${prefix}-parent veranderde`);
  }
  return directory;
}

function buildSeccompLauncher(bootstrapRoot) {
  const controlRoot = privateTemporaryDirectory(bootstrapRoot, 'control');
  const controlIdentity = identity(assertPrivateDirectory(
    controlRoot, null, 'seccomp-controlroot',
  ));
  try {
    const source = path.join(controlRoot, 'seccomp-launcher.c');
    const launcher = path.join(controlRoot, 'seccomp-launcher');
    fs.writeFileSync(source, SECCOMP_LAUNCHER_SOURCE, {
      encoding: 'utf8', mode: 0o600, flag: 'wx',
    });
    spawnChecked(COMPILER, [
      '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIE', '-pie',
      '-Wl,-z,relro,-z,now', '-o', launcher, source,
    ]);
    fs.chmodSync(launcher, 0o700);
    const stat = fs.lstatSync(launcher);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.uid !== process.getuid()
        || (stat.mode & 0o777) !== 0o700 || stat.nlink !== 1) {
      fail('gecompileerde seccomp-launcher is niet privé en regulier');
    }
    const selfTest = spawnSync(UNHARE, [
      '--user', '--map-current-user', '--keep-caps', '--fork',
      SETPRIV, '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all',
      '--ambient-caps=-all', launcher, '--self-test',
    ], {
      cwd: '/tmp',
      env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', TMPDIR: controlRoot },
      encoding: 'utf8',
      maxBuffer: 1024 * 1024,
    });
    if (selfTest.error || selfTest.signal || selfTest.status !== 0) {
      const detail = (selfTest.stderr || selfTest.stdout || selfTest.error?.message || '').trim();
      fail(`seccomp-launcher selftest faalde gesloten${detail ? `: ${detail}` : ''}`);
    }
    return { controlRoot, controlIdentity, launcher, launcherIdentity: identity(stat) };
  } catch (error) {
    removePrivateRoot(controlRoot, controlIdentity, `${bootstrapRoot}/control-`,
      'seccomp-controlroot');
    throw error;
  }
}

function parseRunnerArguments(arguments_) {
  let coverage = false;
  const testArguments = [];
  for (const argument of arguments_) {
    if (argument === COVERAGE_FLAG) {
      if (coverage) fail('coverage mode is dubbel opgegeven');
      if (testArguments.length > 0) fail('coverage mode moet vóór testbestanden staan');
      coverage = true;
    } else {
      testArguments.push(argument);
    }
  }
  return { coverage, testArguments };
}

function prepareCoverageOutput(enabled) {
  const encoded = process.env.BOTTIE_COVERAGE_OUTPUT_FD;
  if (!enabled) {
    if (encoded !== undefined) fail('coverage-fd aanwezig buiten coverage mode');
    return null;
  }
  if (!/^\d{1,4}$/u.test(encoded || '')) fail('coverage mode mist geërfde output-fd');
  const descriptor = Number.parseInt(encoded, 10);
  if (!Number.isSafeInteger(descriptor) || descriptor < 3) fail('ongeldige coverage-output-fd');
  const privateStat = fs.fstatSync(descriptor);
  if (!privateStat.isDirectory() || privateStat.uid !== process.getuid()
      || (privateStat.mode & 0o777) !== 0o700) {
    fail('coverage-output-fd is niet een private eigen directory');
  }
  if (readDirectoryBounded(`/proc/self/fd/${descriptor}`, 1).length !== 0) {
    fail('coverage-output moet leeg door de statische bootstrap worden aangeleverd');
  }
  return {
    identity: identity(privateStat),
    descriptor,
    publishedFiles: 0,
    publishedBytes: 0,
  };
}

function assertCoverageOutput(output) {
  assertIdentity(fs.fstatSync(output.descriptor), output.identity, 'coverage-outputdescriptor');
}

function validV8CoverageDocument(document) {
  return document && typeof document === 'object' && !Array.isArray(document)
    && Number.isFinite(document.timestamp) && Array.isArray(document.result)
    && document.result.every((script) => script && typeof script === 'object'
      && typeof script.url === 'string' && Array.isArray(script.functions));
}

function publishCoverageProfiles(coverageDir, coverageIdentity, output, testSequence) {
  assertPrivateDirectory(coverageDir, coverageIdentity, 'child-coveragedirectory');
  assertCoverageOutput(output);
  const directoryDescriptor = fs.openSync(
    coverageDir,
    fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
  );
  try {
    assertIdentity(fs.fstatSync(directoryDescriptor), coverageIdentity,
      'child-coveragedirectorydescriptor');
    const directoryPath = `/proc/self/fd/${directoryDescriptor}`;
    const entries = readDirectoryBounded(directoryPath, MAX_COVERAGE_FILES_PER_TEST)
      .map(({ name }) => name).sort();
    let testBytes = 0;
    entries.forEach((entry, profileIndex) => {
      if (!/^coverage-\d+-\d+-\d+\.json$/u.test(entry)) {
        fail(`onverwacht bestand in child-coveragedirectory: ${entry}`);
      }
      const profilePath = `${directoryPath}/${entry}`;
      const initialStat = fs.lstatSync(profilePath);
      if (!initialStat.isFile() || initialStat.isSymbolicLink()
          || initialStat.uid !== process.getuid() || (initialStat.mode & 0o777) !== 0o600
          || initialStat.nlink !== 1 || initialStat.size <= 0
          || initialStat.size > MAX_COVERAGE_FILE_BYTES) {
        fail(`onveilig child-coverageprofiel: ${entry}`);
      }
      testBytes += initialStat.size;
      if (testBytes > MAX_COVERAGE_BYTES_PER_TEST) fail('child-coveragedata is te groot');
      const profileDescriptor = fs.openSync(
        profilePath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW,
      );
      let bytes;
      try {
        assertIdentity(fs.fstatSync(profileDescriptor), identity(initialStat),
          `child-coverageprofiel ${entry}`);
        bytes = fs.readFileSync(profileDescriptor);
        const finalStat = fs.fstatSync(profileDescriptor);
        assertIdentity(finalStat, identity(initialStat), `child-coverageprofiel ${entry}`);
        if (bytes.length !== initialStat.size || finalStat.size !== initialStat.size) {
          fail(`child-coverageprofiel veranderde tijdens uitlezen: ${entry}`);
        }
      } finally {
        fs.closeSync(profileDescriptor);
      }
      let document;
      try {
        document = JSON.parse(bytes.toString('utf8'));
      } catch {
        fail(`child-coverageprofiel is geen JSON: ${entry}`);
      }
      if (!validV8CoverageDocument(document)) {
        fail(`child-coverageprofiel heeft geen V8-contract: ${entry}`);
      }
      if (output.publishedFiles + 1 > MAX_PUBLISHED_COVERAGE_FILES
          || output.publishedBytes + bytes.length > MAX_PUBLISHED_COVERAGE_BYTES) {
        fail('totale coverage-output overschrijdt de vaste grens');
      }
      const digest = createHash('sha256').update(bytes).digest('hex').slice(0, 24);
      const outputName = `isolated-${testSequence}-${profileIndex}-${digest}.json`;
      const outputPath = `/proc/self/fd/${output.descriptor}/${outputName}`;
      const outputDescriptor = fs.openSync(outputPath,
        fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL
          | fs.constants.O_NOFOLLOW, 0o600);
      try {
        fs.writeFileSync(outputDescriptor, bytes);
        fs.fsyncSync(outputDescriptor);
        const outputStat = fs.fstatSync(outputDescriptor);
        if (!outputStat.isFile() || outputStat.uid !== process.getuid()
            || (outputStat.mode & 0o777) !== 0o600 || outputStat.nlink !== 1
            || outputStat.size !== bytes.length) {
          fail(`gepubliceerd coverageprofiel is niet privé/regulier: ${outputName}`);
        }
      } finally {
        fs.closeSync(outputDescriptor);
      }
      output.publishedFiles += 1;
      output.publishedBytes += bytes.length;
    });
  } finally {
    fs.closeSync(directoryDescriptor);
  }
  assertCoverageOutput(output);
}

function resolveTestFiles(arguments_) {
  if (arguments_.length === 0) fail('geef minstens één expliciet testbestand op');
  const seen = new Set();
  return arguments_.map((requested) => {
    if (requested.startsWith('-')) fail(`onbekende runneroptie: ${requested}`);
    const candidate = path.resolve(REPOSITORY, requested);
    const relative = path.relative(REPOSITORY, candidate);
    if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) {
      fail(`testbestand valt buiten de repository: ${requested}`);
    }
    const parts = relative.split(path.sep);
    if (!['test', 'scripts'].includes(parts[0]) || !/\.test\.(?:c?js|mjs)$/u.test(relative)) {
      fail(`geen begrensd projecttestbestand: ${requested}`);
    }
    const stat = fs.lstatSync(candidate);
    if (!stat.isFile() || stat.isSymbolicLink()) fail(`testbestand is niet regulier: ${requested}`);
    const real = fs.realpathSync(candidate);
    if (real !== candidate) fail(`testbestand bevat een symlink: ${requested}`);
    if (seen.has(real)) fail(`dubbel testbestand geweigerd: ${requested}`);
    seen.add(real);
    return real;
  });
}

function safeTestEnvironment(isolationRoot) {
  const environment = {
    PATH: '/usr/bin:/bin',
    LANG: 'C.UTF-8',
    HOME: isolationRoot,
    EVA_ISOLATED_RESOURCE_GATE: '1',
    // Normale childprocessen erven deze runner-authority. Testcode die haar eigen
    // omgeving wist of vervangt valt expliciet buiten de availabilityclaim.
    NODE_OPTIONS: NODE_WASM_FLAG,
  };
  const safeLocaleValue = /^[A-Za-z0-9_.@+-]{1,64}$/u;
  for (const name of ['LANG', 'LC_ALL', 'LC_CTYPE', 'LC_MESSAGES']) {
    const value = process.env[name];
    if (typeof value === 'string' && safeLocaleValue.test(value)) environment[name] = value;
  }
  const timezone = process.env.TZ;
  if (typeof timezone === 'string' && timezone.length <= 128
      && /^[A-Za-z0-9_+./:-]+$/u.test(timezone) && !timezone.includes('..')) {
    environment.TZ = timezone;
  }
  return environment;
}

function securitySelfGateSource(dataMount, coverageEnabled) {
  return `
    import assert from 'node:assert/strict';
    import { spawnSync } from 'node:child_process';
    import fs from 'node:fs';
    import http from 'node:http';
    import path from 'node:path';
    import test from 'node:test';

    const NESTED_NODE_SELF_GATE_SOURCE = ${JSON.stringify(NESTED_NODE_SELF_GATE_SOURCE)};

    test('mandatory outer-bootstrap security self-gate', async () => {
      assert.equal(process.env.${TEST_RUNNER_ENV}, '1');
      assert.equal(process.env.${SELF_GATE_ENV}, '1');
      for (const name of [
        'LD_PRELOAD', 'LD_LIBRARY_PATH', 'NODE_PATH', 'GCC_EXEC_PREFIX',
        'COMPILER_PATH', 'OPENAI_API_KEY', 'HA_TOKEN', 'HA_URL', 'TELEGRAM_BOT_TOKEN',
        'BOTTIE_STATIC_BOOTSTRAP', 'BOTTIE_BOOTSTRAP_ROOT',
        'BOTTIE_REPOSITORY_ROOT',
      ]) assert.equal(process.env[name], undefined, name + ' lekte naar de self-gate');
      assert.equal(process.env.NODE_OPTIONS, ${JSON.stringify(NODE_WASM_FLAG)});
      assert.equal(process.env.EVA_ISOLATED_RESOURCE_GATE, '1');
      assert.equal(${coverageEnabled}, Object.hasOwn(process.env, 'NODE_V8_COVERAGE'));
      assert.equal(process.execArgv.filter(
        (argument) => argument === ${JSON.stringify(NODE_WASM_FLAG)}).length, 1);
      const limits = fs.readFileSync('/proc/self/limits', 'utf8');
      assert.match(limits,
        /^Max address space\\s+6442450944\\s+6442450944\\s+bytes\\s*$/mu);
      assert.match(process.env.EVA_DATA_DIR,
        /^\\/tmp\\/bottie-test-bootstrap-[A-Za-z0-9]{6}\\/test-[^/]+\\/data$/u);
      assert.deepEqual(fs.readdirSync(${JSON.stringify(dataMount)}), []);
      assert.throws(() => fs.readFileSync(path.join(${JSON.stringify(dataMount)}, 'eva.db')),
        { code: 'ENOENT' });
      assert.throws(
        () => fs.writeFileSync(path.join(${JSON.stringify(dataMount)}, 'self-gate-write'), 'x'),
        (error) => error?.code === 'EROFS' || error?.code === 'EACCES',
      );
      const status = fs.readFileSync('/proc/self/status', 'utf8');
      assert.match(status, /^NoNewPrivs:\\s+1$/mu);
      for (const capability of ['CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb']) {
        assert.match(status, new RegExp('^' + capability + ':\\\\s+0+$', 'mu'));
      }
      assert.match(status, /^Seccomp:\\s+2$/mu);
      const nullDescriptor = fs.openSync('/dev/null', 'r+');
      const nullBuffer = Buffer.alloc(4, 0x7f);
      assert.equal(fs.writeSync(nullDescriptor, Buffer.from('drop')), 4);
      assert.equal(fs.readSync(nullDescriptor, nullBuffer, 0, nullBuffer.length, null), 0);
      fs.closeSync(nullDescriptor);
      const zeroBuffer = Buffer.alloc(32, 0xff);
      const zeroDescriptor = fs.openSync('/dev/zero', 'r');
      assert.equal(fs.readSync(zeroDescriptor, zeroBuffer, 0, zeroBuffer.length, null), 32);
      fs.closeSync(zeroDescriptor);
      assert.deepEqual([...zeroBuffer], Array(32).fill(0));
      assert.throws(() => fs.writeFileSync('/dev/full', 'must-fail'), { code: 'ENOSPC' });
      for (const randomDevice of ['/dev/random', '/dev/urandom']) {
        const random = Buffer.alloc(32);
        const descriptor = fs.openSync(randomDevice, 'r');
        assert.equal(fs.readSync(descriptor, random, 0, random.length, null), random.length);
        fs.closeSync(descriptor);
        assert.notDeepEqual([...random], Array(32).fill(0));
      }
      assert.deepEqual(fs.readFileSync('/proc/net/dev', 'utf8').split('\\n').slice(2)
        .map((line) => line.split(':')[0].trim()).filter(Boolean), ['lo']);
      assert.deepEqual(fs.readFileSync('/proc/net/route', 'utf8').trim().split('\\n').slice(1)
        .filter((line) => line.trim()), []);
      const getentStat = fs.lstatSync('/usr/bin/getent');
      assert.equal(getentStat.isFile(), true);
      assert.equal(getentStat.isSymbolicLink(), false);
      assert.equal(getentStat.nlink, 1);
      assert.equal(getentStat.mode & 0o022, 0);
      assert.throws(() => fs.writeFileSync('/usr/bin/getent', 'replace'),
        (error) => error?.code === 'EROFS' || error?.code === 'EACCES');
      assert.throws(() => fs.writeFileSync('/etc/nsswitch.conf', 'passwd: dns'),
        (error) => error?.code === 'EROFS' || error?.code === 'EACCES');
      const nsswitch = fs.readFileSync('/etc/nsswitch.conf', 'utf8');
      assert.match(nsswitch, /^passwd:\\s+files(?:\\s+systemd)?\\s*$/mu);
      assert.match(nsswitch, /^group:\\s+files(?:\\s+systemd)?\\s*$/mu);
      const getentEnvironment = { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' };
      const passwd = spawnSync('/usr/bin/getent', ['passwd', String(process.getuid())], {
        encoding: 'utf8', env: getentEnvironment,
      });
      assert.equal(passwd.status, 0, passwd.stderr || passwd.stdout);
      assert.match(passwd.stdout, new RegExp('^[^:]+:[^:]*:' + process.getuid() + ':', 'u'));
      const group = spawnSync('/usr/bin/getent', ['group', String(process.getgid())], {
        encoding: 'utf8', env: getentEnvironment,
      });
      assert.equal(group.status, 0, group.stderr || group.stdout);
      assert.match(group.stdout, new RegExp('^[^:]+:[^:]*:' + process.getgid() + ':', 'u'));
      const launcher = spawnSync('/isolated/seccomp-launcher', ['--self-test'], {
        encoding: 'utf8', env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' },
      });
      assert.equal(launcher.status, 0, launcher.stderr || launcher.stdout);
      const server = http.createServer((_request, response_) => {
        response_.writeHead(200, {
          'content-type': 'text/plain; charset=utf-8', connection: 'close',
        });
        response_.end('SELF_GATE_LOOPBACK');
      });
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', resolve);
      });
      let response;
      try {
        response = await globalThis.fetch(
          'http://127.0.0.1:' + server.address().port + '/self-gate',
        );
        assert.equal(response.status, 200);
        assert.equal(await response.text(), 'SELF_GATE_LOOPBACK');
        assert.equal(response.bodyUsed, true);
      } finally {
        await new Promise((resolve, reject) => server.close((error) => {
          if (error) reject(error); else resolve();
        }));
      }
      const nested = spawnSync(process.execPath,
        ['--input-type=module', '--eval', NESTED_NODE_SELF_GATE_SOURCE], {
          encoding: 'utf8', timeout: 30_000, maxBuffer: 1024 * 1024,
        });
      assert.equal(nested.error, undefined, nested.error?.message);
      assert.equal(nested.signal, null, nested.stderr || nested.stdout);
      assert.equal(nested.status, 0, nested.stderr || nested.stdout);
      assert.equal(nested.stdout.trim(), 'NESTED_NODE_FETCH_BODY');
      const database = path.join(process.env.EVA_DATA_DIR, 'self-gate.db');
      fs.writeFileSync(database, 'private');
      assert.equal(fs.readFileSync(database, 'utf8'), 'private');
    });
  `;
}

function removePrivateRoot(root, expectedIdentity, prefix, label) {
  if (!root.startsWith(prefix)) {
    fail(`weigering om een onbegrensde ${label} op te ruimen`);
  }
  assertPrivateDirectory(root, expectedIdentity, `op te ruimen ${label}`);
  fs.rmSync(root, { recursive: true, force: true });
}

function runOne(testFile, stable, testSequence, securitySelfGate = false) {
  const isolationRoot = privateTemporaryDirectory(stable.bootstrapRoot, 'test');
  const isolationStat = assertPrivateDirectory(isolationRoot, null, 'isolatieroot');
  const isolationIdentity = identity(isolationStat);
  const jailRoot = privateTemporaryDirectory(stable.bootstrapRoot, 'jail');
  const jailIdentity = identity(assertPrivateDirectory(jailRoot, null, 'jailroot'));
  try {
    const dataDir = path.join(isolationRoot, 'data');
    const environmentFile = path.join(isolationRoot, 'test-environment.json');
    fs.mkdirSync(dataDir, { mode: 0o700 });
    const dataIdentity = identity(assertPrivateDirectory(dataDir, null, 'EVA_DATA_DIR'));
    const coverageDir = stable.coverageOutput
      ? path.join(isolationRoot, 'v8-coverage') : null;
    if (coverageDir) fs.mkdirSync(coverageDir, { mode: 0o700 });
    const coverageIdentity = coverageDir
      ? identity(assertPrivateDirectory(coverageDir, null, 'child-coveragedirectory')) : null;
    const testEnvironment = safeTestEnvironment(isolationRoot);
    let effectiveTestFile = testFile;
    if (securitySelfGate) {
      testEnvironment[SELF_GATE_ENV] = '1';
      effectiveTestFile = path.join(isolationRoot, 'bootstrap-security-selfgate.test.mjs');
      fs.writeFileSync(effectiveTestFile,
        securitySelfGateSource(DATA_MOUNT, Boolean(stable.coverageOutput)), {
          encoding: 'utf8', mode: 0o600, flag: 'wx',
        });
    }
    fs.writeFileSync(environmentFile, JSON.stringify(testEnvironment), {
      encoding: 'utf8', mode: 0o600, flag: 'wx',
    });
    const token = randomBytes(32).toString('hex');
    const payload = {
      token,
      repository: REPOSITORY,
      dataMount: DATA_MOUNT,
      dataMountIdentity: stable.dataMountIdentity,
      parentUserNamespace: stable.parentUserNamespace,
      parentMountNamespace: stable.parentMountNamespace,
      parentPidNamespace: stable.parentPidNamespace,
      parentNetNamespace: stable.parentNetNamespace,
      parentIpcNamespace: stable.parentIpcNamespace,
      parentSeccompFilters: stable.parentSeccompFilters,
      hostRootIdentity: stable.hostRootIdentity,
      uid: process.getuid(),
      gid: process.getgid(),
      isolationRoot,
      isolationIdentity,
      jailRoot,
      jailIdentity,
      dataDir,
      dataIdentity,
      coverageDir,
      coverageIdentity,
      environmentFile,
      testFile: effectiveTestFile,
      seccompLauncher: stable.seccompLauncher,
      seccompIdentity: stable.seccompIdentity,
      safeNodeModules: stable.safeNodeModules,
      safeRepository: stable.safeRepository,
    };
    const bootstrapEnvironment = {
      PATH: '/usr/bin:/bin',
      LANG: 'C.UTF-8',
      TMPDIR: isolationRoot,
      EVA_DATA_DIR: dataDir,
      BOTTIE_REPOSITORY_ROOT: REPOSITORY,
      [INTERNAL_TOKEN_ENV]: token,
    };
    const previousUmask = process.umask(0o077);
    let child;
    try {
      child = spawnSync(UNHARE, [
        '--user',
        '--map-current-user',
        '--keep-caps',
        '--mount',
        '--pid',
        '--net',
        '--ipc',
        '--propagation', 'private',
        '--fork',
        '--kill-child=SIGKILL',
        process.execPath,
        NODE_WASM_FLAG,
        '--experimental-default-type=module',
        '--preserve-symlinks-main',
        '/proc/self/fd/0',
        INTERNAL_NAMESPACE,
        token,
        encodePayload(payload),
      ], {
        cwd: REPOSITORY,
        env: bootstrapEnvironment,
        stdio: [0, 'inherit', 'inherit'],
      });
    } finally {
      process.umask(previousUmask);
    }
    if (child.error) fail(`unshare kon niet starten: ${child.error.message}`);
    if (coverageDir) {
      publishCoverageProfiles(
        coverageDir, coverageIdentity, stable.coverageOutput, testSequence,
      );
    }
    return child.signal ? 1 : (child.status ?? 1);
  } finally {
    removePrivateRoot(jailRoot, jailIdentity, `${stable.bootstrapRoot}/jail-`, 'jailroot');
    removePrivateRoot(
      isolationRoot, isolationIdentity, `${stable.bootstrapRoot}/test-`, 'isolatieroot',
    );
  }
}

function main(arguments_) {
  const bootstrap = assertStaticBootstrapEnvironment();
  assertHostPrerequisites();
  const options = parseRunnerArguments(arguments_);
  const tests = resolveTestFiles(options.testArguments);
  const dataMountIdentity = identity(assertDataMountTarget());
  const coverageOutput = prepareCoverageOutput(options.coverage);
  const seccomp = buildSeccompLauncher(bootstrap.root);
  const safeRepository = createSafeRepositorySnapshot(bootstrap.root);
  const safeNodeModules = createSafeNodeModulesMirror(bootstrap.root);
  try {
    const parentSeccompFilters = Number.parseInt(procStatus().get('Seccomp_filters'), 10);
    if (!Number.isSafeInteger(parentSeccompFilters)) fail('host-seccompstatus is niet leesbaar');
    const stable = {
      dataMountIdentity,
      parentUserNamespace: namespaceIdentity('user'),
      parentMountNamespace: namespaceIdentity('mnt'),
      parentPidNamespace: namespaceIdentity('pid'),
      parentNetNamespace: namespaceIdentity('net'),
      parentIpcNamespace: namespaceIdentity('ipc'),
      parentSeccompFilters,
      hostRootIdentity: identity(fs.statSync('/')),
      seccompLauncher: seccomp.launcher,
      seccompIdentity: seccomp.launcherIdentity,
      coverageOutput,
      bootstrapRoot: bootstrap.root,
      bootstrapIdentity: bootstrap.identity,
      safeNodeModules,
      safeRepository,
    };
    if (runOne(null, stable, 0, true) !== 0) {
      fail('verplichte outer-bootstrap security self-gate faalde');
    }
    let failures = 0;
    for (const [testSequence, testFile] of tests.entries()) {
      assertDataMountTarget(dataMountIdentity);
      if (runOne(testFile, stable, testSequence + 1) !== 0) failures += 1;
    }
    assertDataMountTarget(dataMountIdentity);
    if (failures > 0) fail(`${failures} geïsoleerde testbestand(en) faalden`);
  } finally {
    removePrivateRoot(seccomp.controlRoot, seccomp.controlIdentity,
      `${bootstrap.root}/control-`, 'seccomp-controlroot');
    if (coverageOutput) {
      assertCoverageOutput(coverageOutput);
      fs.closeSync(coverageOutput.descriptor);
    }
  }
}

try {
  if (!internalEntry(process.argv.slice(2))) main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`isolated-test-runner: ${error?.message || String(error)}\n`);
  process.exitCode = 1;
}
