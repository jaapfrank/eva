#define _GNU_SOURCE

/*
 * Reproduceerbaar development-artefact (Ubuntu 24.04 toolchain):
 * /usr/bin/x86_64-linux-gnu-gcc-13 -static -std=c11 -O2 -Wall -Wextra -Werror
 *   -fno-ident -fno-record-gcc-switches -Wl,--build-id=none -s
 *   -o scripts/run-isolated-tests-bootstrap scripts/run-isolated-tests-bootstrap.c
 *
 * npm is zelf Node en is daarom nooit de pre-Node trust boundary. Een releasegate
 * moet dit ELF buiten npm direct starten vanaf een root-owned, niet-beschrijfbaar
 * installatiepad en de vooraf goedgekeurde SHA-256 attesten. Het repository-ELF
 * is uitsluitend de reproduceerbare developerkopie; ownership door de werkuser
 * is geen release-authority.
 */

#include <dirent.h>
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/magic.h>
#include <linux/memfd.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define BOOTSTRAP_PREFIX "/tmp/bottie-test-bootstrap-"
#define NODE_BINARY "/usr/bin/node"
#define NODE_WASM_FLAG "--disable-wasm-trap-handler"
#define SYSTEMD_RUN_BINARY "/usr/bin/systemd-run"
#define SYSTEMCTL_BINARY "/usr/bin/systemctl"
#define INTERNAL_RESOURCE_FLAG "--inside-resource-unit"
#define RUNNER_SHA256 "012dc998e0804eaa6216c8acbde1d1dd83f591c8cd0d2a239ba288234cc19d27"
#define MAX_RUNNER_BYTES (2ULL * 1024ULL * 1024ULL)
#define SHA256_BYTES 32
#ifndef WALL_TIMEOUT_SECONDS
#define WALL_TIMEOUT_SECONDS 2700
#endif
#ifndef MAX_TREE_ENTRIES
#define MAX_TREE_ENTRIES 200000ULL
#endif
#ifndef MAX_TREE_BYTES
#define MAX_TREE_BYTES (3ULL * 1024ULL * 1024ULL * 1024ULL)
#endif
#ifndef MAX_TREE_DEPTH
#define MAX_TREE_DEPTH 64
#endif
#define MEMORY_MAX_BYTES 4294967296ULL
#define TMPFS_MAX_BYTES 3221225472ULL
#define TASKS_MAX 256
#define CPU_QUOTA "400000 100000"

extern char **environ;

struct tree_usage {
  uint64_t entries;
  uint64_t bytes;
};

static pid_t active_child = -1;
static bool allow_idmapped_root = false;
static volatile sig_atomic_t liveness_write_fd = -1;
static char *joined_path(const char *left, const char *right);
static void die_errno(const char *message);
static volatile sig_atomic_t pending_signal = 0;

static void record_signal(int signal_number) {
  pending_signal = signal_number;
  int descriptor = liveness_write_fd;
  if (descriptor >= 0) {
    liveness_write_fd = -1;
    close(descriptor);
  }
}

static void install_signal_handlers(void) {
  struct sigaction action = { 0 };
  action.sa_handler = record_signal;
  sigemptyset(&action.sa_mask);
  if (sigaction(SIGINT, &action, NULL) != 0
      || sigaction(SIGTERM, &action, NULL) != 0
      || sigaction(SIGHUP, &action, NULL) != 0) {
    die_errno("kan cleanup-signaalhandlers niet installeren");
  }
}

static void terminate_active_child(void) {
  int descriptor = liveness_write_fd;
  if (descriptor >= 0) {
    liveness_write_fd = -1;
    close(descriptor);
  }
  if (active_child <= 0) return;
  kill(-active_child, SIGKILL);
  kill(active_child, SIGKILL);
  waitpid(active_child, NULL, 0);
  active_child = -1;
}

static void die(const char *message) {
  terminate_active_child();
  fprintf(stderr, "isolated-test-bootstrap: %s\n", message);
  exit(125);
}

static void die_errno(const char *message) {
  int saved_errno = errno;
  terminate_active_child();
  errno = saved_errno;
  fprintf(stderr, "isolated-test-bootstrap: %s: %s\n", message, strerror(errno));
  exit(125);
}

static int open_regular(const char *filename, uid_t allowed_uid, bool executable) {
  struct stat status;
  int descriptor = open(filename, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  if (descriptor < 0 || fstat(descriptor, &status) != 0) {
    fprintf(stderr, "isolated-test-bootstrap: kan vertrouwd bestand niet openen: %s\n",
      filename);
    die_errno("descriptor-safe open faalde");
  }
  if (!S_ISREG(status.st_mode) || status.st_nlink != 1
      || (status.st_mode & (S_IWGRP | S_IWOTH)) != 0
      || (status.st_uid != 0 && status.st_uid != allowed_uid
        && !(allow_idmapped_root && status.st_uid == 65534))) {
    close(descriptor);
    die("vertrouwd bestand is niet regulier, enkelvoudig en niet publiek beschrijfbaar");
  }
  if (executable && (status.st_mode & 0111) == 0) {
    close(descriptor);
    die("vertrouwd bestand is niet uitvoerbaar");
  }
  return descriptor;
}

static void require_regular(const char *filename, uid_t allowed_uid, bool executable) {
  int descriptor = open_regular(filename, allowed_uid, executable);
  if (close(descriptor) != 0) die_errno("kan vertrouwde descriptor niet sluiten");
}

static void read_exact_at(int descriptor, void *buffer, size_t length, off_t offset,
    const char *message) {
  size_t consumed = 0;
  while (consumed < length) {
    ssize_t count = pread(descriptor, (char *)buffer + consumed, length - consumed,
      offset + (off_t)consumed);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0) die_errno(message);
    consumed += (size_t)count;
  }
}

static void require_static_self(uid_t uid, char resolved[PATH_MAX]) {
  ssize_t length = readlink("/proc/self/exe", resolved, PATH_MAX - 1);
  if (length <= 0 || (size_t)length >= PATH_MAX) die_errno("kan eigen ELF niet vinden");
  resolved[length] = '\0';
  int descriptor = open_regular(resolved, uid, true);
  Elf64_Ehdr header;
  read_exact_at(descriptor, &header, sizeof(header), 0, "kan eigen ELF-header niet lezen");
  if (memcmp(header.e_ident, ELFMAG, SELFMAG) != 0
      || header.e_ident[EI_CLASS] != ELFCLASS64
      || header.e_ident[EI_DATA] != ELFDATA2LSB
      || header.e_type != ET_EXEC || header.e_machine != EM_X86_64
      || header.e_phentsize != sizeof(Elf64_Phdr) || header.e_phnum == 0
      || header.e_phnum > 128) {
    close(descriptor);
    die("bootstrap is niet het verwachte Linux x86_64 ET_EXEC-artefact");
  }
  bool dynamic = false;
  for (uint16_t index = 0; index < header.e_phnum; index++) {
    Elf64_Phdr program;
    read_exact_at(descriptor, &program, sizeof(program),
      header.e_phoff + (off_t)index * (off_t)sizeof(program),
      "kan eigen ELF-programheaders niet lezen");
    if (program.p_type == PT_INTERP || program.p_type == PT_DYNAMIC) dynamic = true;
  }
  if (close(descriptor) != 0) die_errno("kan eigen ELF-descriptor niet sluiten");
  if (dynamic) die("bootstrap-ELF bevat een dynamische loader/sectie");
}

struct sha256_context {
  uint32_t state[8];
  uint64_t bits;
  unsigned char block[64];
  size_t used;
};

static uint32_t rotate_right(uint32_t value, unsigned int count) {
  return (value >> count) | (value << (32U - count));
}

static void sha256_transform(struct sha256_context *context) {
  static const uint32_t constants[64] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
    0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
    0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
    0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
  };
  uint32_t words[64];
  for (size_t index = 0; index < 16; index++) {
    size_t offset = index * 4;
    words[index] = ((uint32_t)context->block[offset] << 24)
      | ((uint32_t)context->block[offset + 1] << 16)
      | ((uint32_t)context->block[offset + 2] << 8)
      | (uint32_t)context->block[offset + 3];
  }
  for (size_t index = 16; index < 64; index++) {
    uint32_t first = rotate_right(words[index - 15], 7)
      ^ rotate_right(words[index - 15], 18) ^ (words[index - 15] >> 3);
    uint32_t second = rotate_right(words[index - 2], 17)
      ^ rotate_right(words[index - 2], 19) ^ (words[index - 2] >> 10);
    words[index] = words[index - 16] + first + words[index - 7] + second;
  }
  uint32_t a = context->state[0];
  uint32_t b = context->state[1];
  uint32_t c = context->state[2];
  uint32_t d = context->state[3];
  uint32_t e = context->state[4];
  uint32_t f = context->state[5];
  uint32_t g = context->state[6];
  uint32_t h = context->state[7];
  for (size_t index = 0; index < 64; index++) {
    uint32_t sum1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
    uint32_t choice = (e & f) ^ ((~e) & g);
    uint32_t temporary1 = h + sum1 + choice + constants[index] + words[index];
    uint32_t sum0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
    uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
    uint32_t temporary2 = sum0 + majority;
    h = g;
    g = f;
    f = e;
    e = d + temporary1;
    d = c;
    c = b;
    b = a;
    a = temporary1 + temporary2;
  }
  context->state[0] += a;
  context->state[1] += b;
  context->state[2] += c;
  context->state[3] += d;
  context->state[4] += e;
  context->state[5] += f;
  context->state[6] += g;
  context->state[7] += h;
}

static void sha256_digest(const unsigned char *contents, size_t length,
    unsigned char digest[SHA256_BYTES]) {
  struct sha256_context context = {
    .state = {
      0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
      0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    },
    .bits = 0,
    .block = { 0 },
    .used = 0,
  };
  for (size_t index = 0; index < length; index++) {
    context.block[context.used++] = contents[index];
    if (context.used == sizeof(context.block)) {
      sha256_transform(&context);
      context.bits += 512;
      context.used = 0;
    }
  }
  context.bits += (uint64_t)context.used * 8ULL;
  context.block[context.used++] = 0x80;
  if (context.used > 56) {
    while (context.used < sizeof(context.block)) context.block[context.used++] = 0;
    sha256_transform(&context);
    context.used = 0;
  }
  while (context.used < 56) context.block[context.used++] = 0;
  for (size_t index = 0; index < 8; index++) {
    context.block[63 - index] = (unsigned char)(context.bits >> (index * 8));
  }
  sha256_transform(&context);
  for (size_t index = 0; index < 8; index++) {
    digest[index * 4] = (unsigned char)(context.state[index] >> 24);
    digest[index * 4 + 1] = (unsigned char)(context.state[index] >> 16);
    digest[index * 4 + 2] = (unsigned char)(context.state[index] >> 8);
    digest[index * 4 + 3] = (unsigned char)context.state[index];
  }
}

static int create_sealed_runner(const char *filename, uid_t uid) {
  int descriptor = open_regular(filename, uid, false);
  struct stat status;
  if (fstat(descriptor, &status) != 0 || status.st_size <= 0
      || (uint64_t)status.st_size > MAX_RUNNER_BYTES) {
    close(descriptor);
    die("runnerbron heeft geen begrensde bestandsgrootte");
  }
  unsigned char *contents = malloc((size_t)status.st_size);
  if (contents == NULL) {
    close(descriptor);
    die_errno("kan runnerdigestbuffer niet alloceren");
  }
  read_exact_at(descriptor, contents, (size_t)status.st_size, 0,
    "kan runnerbron niet descriptor-safe lezen");
  unsigned char digest[SHA256_BYTES];
  sha256_digest(contents, (size_t)status.st_size, digest);
  char encoded[SHA256_BYTES * 2 + 1];
  for (size_t index = 0; index < SHA256_BYTES; index++) {
    if (snprintf(encoded + index * 2, 3, "%02x", digest[index]) != 2) {
      close(descriptor);
      die("kan runnerdigest niet encoderen");
    }
  }
  if (strcmp(encoded, RUNNER_SHA256) != 0) {
    free(contents);
    close(descriptor);
    die("runnerbron wijkt af van de in het statische ELF gebonden SHA-256");
  }
  int sealed = (int)syscall(SYS_memfd_create, "bottie-trusted-runner.mjs",
    MFD_CLOEXEC | MFD_ALLOW_SEALING);
  if (sealed < 0) {
    free(contents);
    close(descriptor);
    die_errno("kan sealed runner-memfd niet maken");
  }
  size_t written = 0;
  while (written < (size_t)status.st_size) {
    ssize_t amount = write(sealed, contents + written, (size_t)status.st_size - written);
    if (amount < 0 && errno == EINTR) continue;
    if (amount <= 0) {
      free(contents);
      close(sealed);
      close(descriptor);
      die_errno("kan sealed runnerbytes niet schrijven");
    }
    written += (size_t)amount;
  }
  free(contents);
  if (close(descriptor) != 0) die_errno("kan runnerbrondescriptor niet sluiten");
  const int seals = F_SEAL_SEAL | F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_WRITE;
  if (fchmod(sealed, 0400) != 0 || fcntl(sealed, F_ADD_SEALS, seals) != 0
      || fcntl(sealed, F_GET_SEALS) != seals) {
    close(sealed);
    die_errno("kan runner-memfd niet volledig sealen");
  }
  struct stat sealed_status;
  if (fstat(sealed, &sealed_status) != 0 || !S_ISREG(sealed_status.st_mode)
      || sealed_status.st_nlink != 0 || sealed_status.st_size != status.st_size) {
    close(sealed);
    die("sealed runner-memfd heeft niet de verwachte identiteit");
  }
  return sealed;
}

static void close_inherited_descriptors(void) {
#ifdef SYS_close_range
  if (syscall(SYS_close_range, 3U, ~0U, 0U) != 0) {
    die_errno("kan geërfde file descriptors niet atomair sluiten");
  }
#else
  die("kernelheaders missen close_range; bootstrap weigert zwakke fd-sanitatie");
#endif
}

static void set_limit(int resource, rlim_t current, rlim_t maximum) {
  struct rlimit limit = { .rlim_cur = current, .rlim_max = maximum };
  if (setrlimit(resource, &limit) != 0) die_errno("kan resourcegrens niet zetten");
}

static void apply_child_limits(void) {
  set_limit(RLIMIT_CORE, 0, 0);
  set_limit(RLIMIT_NOFILE, 512, 512);
  set_limit(RLIMIT_NPROC, 256, 256);
  set_limit(RLIMIT_FSIZE, 128ULL * 1024ULL * 1024ULL, 128ULL * 1024ULL * 1024ULL);
  set_limit(RLIMIT_AS, 6ULL * 1024ULL * 1024ULL * 1024ULL,
    6ULL * 1024ULL * 1024ULL * 1024ULL);
  set_limit(RLIMIT_CPU, 900, 900);
}

static bool liveness_lost(int descriptor) {
  if (descriptor < 0) return false;
  struct pollfd state = {
    .fd = descriptor,
    .events = POLLIN | POLLHUP | POLLERR,
    .revents = 0,
  };
  int result;
  do {
    result = poll(&state, 1, 0);
  } while (result < 0 && errno == EINTR);
  if (result < 0) die_errno("kan outer-bootstrap-liveness niet pollen");
  return result > 0 && (state.revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL)) != 0;
}

static void attest_liveness_pipe(void) {
  struct stat status;
  if (fstat(STDIN_FILENO, &status) != 0 || !S_ISFIFO(status.st_mode)
      || liveness_lost(STDIN_FILENO)) {
    die("resource-unit mist een levende outer-bootstrap-pipe");
  }
}

static void exact_environment(const char *root, const char *repository, int coverage_fd,
    bool resource_gate) {
  if (clearenv() != 0) die_errno("kan geërfde omgeving niet wissen");
  if (setenv("PATH", "/usr/bin:/bin", 1) != 0
      || setenv("LANG", "C.UTF-8", 1) != 0
      || setenv("LC_ALL", "C.UTF-8", 1) != 0
      || setenv("HOME", root, 1) != 0
      || setenv("TMPDIR", "/tmp", 1) != 0
      || setenv("BOTTIE_STATIC_BOOTSTRAP", "1", 1) != 0
      || setenv("BOTTIE_BOOTSTRAP_ROOT", root, 1) != 0
      || setenv("BOTTIE_REPOSITORY_ROOT", repository, 1) != 0) {
    die_errno("kan vertrouwde omgeving niet opbouwen");
  }
  if (resource_gate && setenv("EVA_ISOLATED_RESOURCE_GATE", "1", 1) != 0) {
    die_errno("kan resourcegate-marker niet zetten");
  }
  if (coverage_fd >= 0) {
    char encoded[32];
    if (snprintf(encoded, sizeof(encoded), "%d", coverage_fd) <= 0
        || setenv("BOTTIE_COVERAGE_OUTPUT_FD", encoded, 1) != 0) {
      die_errno("kan coverage-fd niet doorgeven");
    }
  }
}

static void scan_tree_fd(int directory_fd, int depth, struct tree_usage *usage) {
  if (depth > MAX_TREE_DEPTH) die("authorityroot overschrijdt maximale diepte");
  int duplicate = openat(directory_fd, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (duplicate < 0) die_errno("kan directorydescriptor niet dupliceren");
  DIR *directory = fdopendir(duplicate);
  if (directory == NULL) {
    close(duplicate);
    die_errno("kan authorityroot niet itereren");
  }
  errno = 0;
  struct dirent *entry;
  while ((entry = readdir(directory)) != NULL) {
    if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
    usage->entries += 1;
    if (usage->entries > MAX_TREE_ENTRIES) {
      closedir(directory);
      die("authorityroot bevat te veel entries");
    }
    struct stat status;
    if (fstatat(directory_fd, entry->d_name, &status, AT_SYMLINK_NOFOLLOW) != 0) {
      if (errno == ENOENT) {
        errno = 0;
        continue;
      }
      closedir(directory);
      die_errno("authorityroot-entry veranderde tijdens scan");
    }
    if (status.st_blocks > 0) usage->bytes += (uint64_t)status.st_blocks * 512ULL;
    if (usage->bytes > MAX_TREE_BYTES) {
      closedir(directory);
      die("authorityroot overschrijdt diskgrens");
    }
    if (S_ISDIR(status.st_mode)) {
      int child = openat(directory_fd, entry->d_name,
        O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
      if (child < 0) {
        if (errno == ENOENT) {
          errno = 0;
          continue;
        }
        closedir(directory);
        die_errno("kan childdirectory niet veilig openen");
      }
      scan_tree_fd(child, depth + 1, usage);
      close(child);
    }
    errno = 0;
  }
  if (errno != 0) {
    closedir(directory);
    die_errno("authorityroot-iteratie faalde");
  }
  if (closedir(directory) != 0) die_errno("kan authorityroot-iterator niet sluiten");
}

static void check_tree_budget(int root_fd) {
  struct tree_usage usage = { 0, 0 };
  scan_tree_fd(root_fd, 0, &usage);
}

static void remove_tree_fd(int directory_fd, int depth) {
  if (depth > MAX_TREE_DEPTH) die("cleanup overschrijdt maximale diepte");
  int duplicate = openat(directory_fd, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (duplicate < 0) die_errno("kan cleanupdescriptor niet dupliceren");
  DIR *directory = fdopendir(duplicate);
  if (directory == NULL) {
    close(duplicate);
    die_errno("kan cleanupdirectory niet itereren");
  }
  uint64_t entries = 0;
  errno = 0;
  struct dirent *entry;
  while ((entry = readdir(directory)) != NULL) {
    if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
    entries += 1;
    if (entries > MAX_TREE_ENTRIES) {
      closedir(directory);
      die("cleanupdirectory bevat te veel entries");
    }
    struct stat status;
    if (fstatat(directory_fd, entry->d_name, &status, AT_SYMLINK_NOFOLLOW) != 0) {
      closedir(directory);
      die_errno("cleanup-entry veranderde");
    }
    if (S_ISDIR(status.st_mode)) {
      int child = openat(directory_fd, entry->d_name,
        O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
      if (child < 0) {
        closedir(directory);
        die_errno("kan cleanup-child niet openen");
      }
      remove_tree_fd(child, depth + 1);
      close(child);
      if (unlinkat(directory_fd, entry->d_name, AT_REMOVEDIR) != 0) {
        closedir(directory);
        die_errno("kan cleanupdirectory niet verwijderen");
      }
    } else if (unlinkat(directory_fd, entry->d_name, 0) != 0) {
      closedir(directory);
      die_errno("kan cleanupbestand niet verwijderen");
    }
    errno = 0;
  }
  if (errno != 0) {
    closedir(directory);
    die_errno("cleanupiteratie faalde");
  }
  if (closedir(directory) != 0) die_errno("kan cleanupiterator niet sluiten");
}

static double monotonic_seconds(void) {
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) die_errno("kan monotone tijd niet lezen");
  return (double)now.tv_sec + (double)now.tv_nsec / 1000000000.0;
}

static int supervise(char *const arguments[], const char *cwd, int root_fd, int stdin_fd,
    int liveness_fd) {
  const pid_t parent_pid = getpid();
  pid_t child = fork();
  if (child < 0) die_errno("kan begrensd childproces niet starten");
  if (child == 0) {
    if (setpgid(0, 0) != 0) die_errno("kan childprocesgroep niet isoleren");
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0) die_errno("kan parent-death-signaal niet zetten");
    if (getppid() != parent_pid) _exit(125);
    if (stdin_fd >= 0 && dup2(stdin_fd, STDIN_FILENO) != STDIN_FILENO) {
      die_errno("kan gepinde child-stdin niet installeren");
    }
    apply_child_limits();
    if (chdir(cwd) != 0) die_errno("kan child-cwd niet zetten");
    execve(arguments[0], arguments, environ);
    die_errno("kan vertrouwd childprogramma niet execen");
  }
  if (setpgid(child, child) != 0 && errno != EACCES && errno != ESRCH) {
    die_errno("kan childgroep niet bevestigen");
  }
  active_child = child;
  const double deadline = monotonic_seconds() + WALL_TIMEOUT_SECONDS;
  unsigned int ticks = 0;
  for (;;) {
    int status = 0;
    pid_t result = waitpid(child, &status, WNOHANG);
    if (result == child) {
      active_child = -1;
      if (WIFEXITED(status)) return WEXITSTATUS(status);
      return 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    }
    if (result < 0) die_errno("kan begrensd childproces niet volgen");
    if (pending_signal != 0) {
      int signal_number = pending_signal;
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      active_child = -1;
      return 128 + signal_number;
    }
    if (liveness_lost(liveness_fd)) {
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      active_child = -1;
      fprintf(stderr, "isolated-test-bootstrap: outer bootstrap verdween onverwacht\n");
      return 143;
    }
    if (monotonic_seconds() >= deadline) {
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      active_child = -1;
      fprintf(stderr, "isolated-test-bootstrap: harde timeout na %d seconden\n",
        WALL_TIMEOUT_SECONDS);
      return 124;
    }
    if ((ticks++ % 5U) == 0U) {
      struct tree_usage usage = { 0, 0 };
      /* Budgetfouten stoppen de volledige bootstrap fail-closed. */
      scan_tree_fd(root_fd, 0, &usage);
    }
    struct timespec pause = { .tv_sec = 0, .tv_nsec = 200000000L };
    while (nanosleep(&pause, &pause) != 0 && errno == EINTR) {}
  }
}

static char *joined_path(const char *left, const char *right) {
  size_t length = strlen(left) + strlen(right) + 2;
  char *result = calloc(length, 1);
  if (result == NULL) die_errno("kan padbuffer niet alloceren");
  if (snprintf(result, length, "%s/%s", left, right) < 0) die("kan pad niet bouwen");
  return result;
}

static bool inside_resource_unit(int argc, char **argv) {
  return argc > 1 && strcmp(argv[1], INTERNAL_RESOURCE_FLAG) == 0;
}

static void require_cgroup_value(const char *directory, const char *name,
    const char *expected) {
  char *filename = joined_path(directory, name);
  int descriptor = open(filename, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
  free(filename);
  if (descriptor < 0) die_errno("kan cgroupgrens niet openen");
  char buffer[128];
  ssize_t length = read(descriptor, buffer, sizeof(buffer) - 1);
  if (close(descriptor) != 0 || length <= 0 || (size_t)length >= sizeof(buffer)) {
    die_errno("kan cgroupgrens niet lezen");
  }
  buffer[length] = '\0';
  while (length > 0 && (buffer[length - 1] == '\n' || buffer[length - 1] == ' ')) {
    buffer[--length] = '\0';
  }
  if (strcmp(buffer, expected) != 0) die("cgroupgrens wijkt af van het exacte contract");
}

static void attest_resource_unit(void) {
  char cgroup[PATH_MAX];
  int descriptor = open("/proc/self/cgroup", O_RDONLY | O_CLOEXEC);
  if (descriptor < 0) die_errno("kan resource-cgroup niet lezen");
  ssize_t length = read(descriptor, cgroup, sizeof(cgroup) - 1);
  if (close(descriptor) != 0 || length <= 3 || (size_t)length >= sizeof(cgroup)) {
    die_errno("resource-cgroup is niet leesbaar");
  }
  cgroup[length] = '\0';
  char *line = strstr(cgroup, "0::/");
  if (line != cgroup || strchr(cgroup, '\n') == NULL) {
    die("resource-cgroup heeft geen enkelvoudig cgroup-v2 contract");
  }
  cgroup[strchr(cgroup, '\n') - cgroup] = '\0';
  if (strstr(cgroup, "/bottie-isolated-tests-") == NULL
      || strstr(cgroup, ".service") == NULL) {
    die("bootstrap draait niet in de verwachte transient resource-unit");
  }
  char *directory = joined_path("/sys/fs/cgroup", cgroup + 3);
  require_cgroup_value(directory, "memory.max", "4294967296");
  require_cgroup_value(directory, "memory.swap.max", "0");
  require_cgroup_value(directory, "pids.max", "256");
  require_cgroup_value(directory, "cpu.max", CPU_QUOTA);
  free(directory);
  struct statfs temporary;
  if (statfs("/tmp", &temporary) != 0 || temporary.f_type != TMPFS_MAGIC
      || (uint64_t)temporary.f_blocks * (uint64_t)temporary.f_bsize > TMPFS_MAX_BYTES) {
    die("resource-unit mist de begrensde private /tmp-tmpfs");
  }
}

static void exact_resource_launcher_environment(const char *runtime_directory) {
  if (clearenv() != 0
      || setenv("PATH", "/usr/bin:/bin", 1) != 0
      || setenv("LANG", "C.UTF-8", 1) != 0
      || setenv("LC_ALL", "C.UTF-8", 1) != 0
      || setenv("XDG_RUNTIME_DIR", runtime_directory, 1) != 0) {
    die_errno("kan pre-systemd omgeving niet exact saneren");
  }
}

static void prepare_coverage_report_directory(const char *repository, uid_t uid) {
  int repository_fd = open(repository, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  struct stat repository_status;
  if (repository_fd < 0 || fstat(repository_fd, &repository_status) != 0
      || !S_ISDIR(repository_status.st_mode) || repository_status.st_uid != uid
      || (repository_status.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
    if (repository_fd >= 0) close(repository_fd);
    die("coverage-repositoryparent is niet descriptor-safe en privé beschrijfbaar");
  }
  if (mkdirat(repository_fd, "coverage", 0700) != 0 && errno != EEXIST) {
    close(repository_fd);
    die_errno("kan vaste coverage-reportdirectory niet maken");
  }
  int coverage_fd = openat(repository_fd, "coverage",
    O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  struct stat coverage_status;
  if (coverage_fd < 0 || fstat(coverage_fd, &coverage_status) != 0
      || !S_ISDIR(coverage_status.st_mode) || coverage_status.st_uid != uid
      || (coverage_status.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
    if (coverage_fd >= 0) close(coverage_fd);
    close(repository_fd);
    die("coverage-reportdirectory is geen vaste eigen directory");
  }
  if (close(coverage_fd) != 0 || close(repository_fd) != 0) {
    die_errno("kan coverage-reportdescriptors niet sluiten");
  }
}

static void stop_resource_unit(const char *unit) {
  const pid_t parent_pid = getpid();
  pid_t child = fork();
  if (child < 0) die_errno("kan resource-unit stopproces niet starten");
  if (child == 0) {
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent_pid) _exit(125);
    char *const arguments[] = {
      (char *)SYSTEMCTL_BINARY, "--user", "--quiet", "stop", (char *)unit, NULL,
    };
    execve(SYSTEMCTL_BINARY, arguments, environ);
    _exit(125);
  }
  const double deadline = monotonic_seconds() + 10.0;
  for (;;) {
    int status;
    pid_t result = waitpid(child, &status, WNOHANG);
    if (result == child) return;
    if (result < 0 && errno != EINTR) die_errno("kan resource-unit stopproces niet volgen");
    if (monotonic_seconds() >= deadline) {
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      die("resource-unit stopte niet binnen de cleanupgrens");
    }
    struct timespec pause = { .tv_sec = 0, .tv_nsec = 100000000L };
    while (nanosleep(&pause, &pause) != 0 && errno == EINTR) {}
  }
}

static void close_liveness_writer(void) {
  int descriptor = liveness_write_fd;
  if (descriptor >= 0) {
    liveness_write_fd = -1;
    if (close(descriptor) != 0) die_errno("kan liveness-writer niet sluiten");
  }
}

static int wait_resource_launcher(pid_t child, const char *unit, int writer) {
  active_child = child;
  liveness_write_fd = writer;
  const double hard_deadline = monotonic_seconds() + WALL_TIMEOUT_SECONDS + 30.0;
  double signal_deadline = 0.0;
  int observed_signal = 0;
  for (;;) {
    int status = 0;
    pid_t result = waitpid(child, &status, WNOHANG);
    if (result == child) {
      active_child = -1;
      close_liveness_writer();
      if (observed_signal != 0) return 128 + observed_signal;
      if (WIFEXITED(status)) return WEXITSTATUS(status);
      stop_resource_unit(unit);
      return 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
    }
    if (result < 0 && errno != EINTR) die_errno("kan systemd resource-launcher niet volgen");
    if (pending_signal != 0 && observed_signal == 0) {
      observed_signal = pending_signal;
      close_liveness_writer();
      signal_deadline = monotonic_seconds() + 8.0;
    }
    if (observed_signal != 0 && monotonic_seconds() >= signal_deadline) {
      stop_resource_unit(unit);
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      active_child = -1;
      return 128 + observed_signal;
    }
    if (monotonic_seconds() >= hard_deadline) {
      close_liveness_writer();
      stop_resource_unit(unit);
      kill(-child, SIGKILL);
      kill(child, SIGKILL);
      waitpid(child, NULL, 0);
      active_child = -1;
      fprintf(stderr, "isolated-test-bootstrap: resourcegate overschreed harde timeout\n");
      return 124;
    }
    struct timespec pause = { .tv_sec = 0, .tv_nsec = 100000000L };
    while (nanosleep(&pause, &pause) != 0 && errno == EINTR) {}
  }
}

static int launch_resource_unit(int argc, char **argv, const char *self_path) {
  require_regular(SYSTEMD_RUN_BINARY, 0, true);
  require_regular(SYSTEMCTL_BINARY, 0, true);
  const char *runtime_directory = getenv("XDG_RUNTIME_DIR");
  char expected_runtime[64];
  if (snprintf(expected_runtime, sizeof(expected_runtime), "/run/user/%u", getuid()) <= 0
      || runtime_directory == NULL || strcmp(runtime_directory, expected_runtime) != 0) {
    die("unprivileged systemd resourcegate mist exacte XDG_RUNTIME_DIR");
  }
  char unit[128];
  char repository[PATH_MAX];
  char canonical_repository[PATH_MAX];
  if (getcwd(repository, sizeof(repository)) == NULL
      || realpath(repository, canonical_repository) == NULL
      || strcmp(repository, canonical_repository) != 0) {
    die("repository-cwd is niet canoniek vóór de resourcegate");
  }
  const bool coverage = argc > 1 && strcmp(argv[1], "--coverage") == 0;
  if (coverage) prepare_coverage_report_directory(repository, getuid());
  struct timespec now;
  if (clock_gettime(CLOCK_MONOTONIC, &now) != 0
      || snprintf(unit, sizeof(unit), "bottie-isolated-tests-%u-%ld-%ld",
        getuid(), (long)getpid(), (long)now.tv_nsec) <= 0) {
    die_errno("kan unieke transient unitnaam niet bouwen");
  }
  size_t count = (size_t)argc + 48U;
  char **arguments = calloc(count, sizeof(char *));
  if (arguments == NULL) die_errno("kan resourcegateargumenten niet alloceren");
  size_t offset = 0;
  arguments[offset++] = (char *)SYSTEMD_RUN_BINARY;
  arguments[offset++] = "--user";
  arguments[offset++] = "--wait";
  arguments[offset++] = "--collect";
  arguments[offset++] = "--pipe";
  arguments[offset++] = "--quiet";
  arguments[offset++] = "--same-dir";
  arguments[offset++] = "--unit";
  arguments[offset++] = unit;
  arguments[offset++] = "-p";
  arguments[offset++] = "Type=exec";
  arguments[offset++] = "-p";
  arguments[offset++] = "MemoryMax=4294967296";
  arguments[offset++] = "-p";
  arguments[offset++] = "MemorySwapMax=0";
  arguments[offset++] = "-p";
  arguments[offset++] = "TasksMax=256";
  arguments[offset++] = "-p";
  arguments[offset++] = "CPUQuota=400%";
  arguments[offset++] = "-p";
  arguments[offset++] = "RuntimeMaxSec=2700s";
  arguments[offset++] = "-p";
  arguments[offset++] = "LimitFSIZE=134217728";
  arguments[offset++] = "-p";
  arguments[offset++] = "KillMode=control-group";
  arguments[offset++] = "-p";
  arguments[offset++] = "TimeoutStopSec=5s";
  arguments[offset++] = "-p";
  arguments[offset++] = "UMask=0077";
  arguments[offset++] = "-p";
  arguments[offset++] = "ProtectSystem=strict";
  arguments[offset++] = "-p";
  arguments[offset++] = "ProtectHome=read-only";
  const size_t repository_length = strlen(repository);
  const bool self_inside_repository = strncmp(self_path, repository, repository_length) == 0
    && self_path[repository_length] == '/';
  if (!self_inside_repository) {
    arguments[offset++] = "-p";
    char *bind_self = calloc(strlen(self_path) + 32, 1);
    if (bind_self == NULL
        || snprintf(bind_self, strlen(self_path) + 32,
          "BindReadOnlyPaths=%s", self_path) <= 0) {
      die_errno("kan bootstrap-bindcontract niet bouwen");
    }
    arguments[offset++] = bind_self;
  }
  arguments[offset++] = "-p";
  char *bind_repository = calloc(strlen(repository) + 32, 1);
  if (bind_repository == NULL
      || snprintf(bind_repository, strlen(repository) + 32,
        "BindReadOnlyPaths=%s", repository) <= 0) {
    die_errno("kan repository-bindcontract niet bouwen");
  }
  arguments[offset++] = bind_repository;
  if (coverage) {
    arguments[offset++] = "-p";
    char *bind_coverage = calloc(strlen(repository) + 48, 1);
    if (bind_coverage == NULL
        || snprintf(bind_coverage, strlen(repository) + 48,
          "BindPaths=%s/coverage", repository) <= 0) {
      die_errno("kan coverage-report-bindcontract niet bouwen");
    }
    arguments[offset++] = bind_coverage;
  }
  arguments[offset++] = "-p";
  arguments[offset++] =
    "TemporaryFileSystem=/tmp:rw,nosuid,nodev,size=3221225472,mode=1777";
  arguments[offset++] = (char *)self_path;
  arguments[offset++] = (char *)INTERNAL_RESOURCE_FLAG;
  for (int index = 1; index < argc; index++) arguments[offset++] = argv[index];
  arguments[offset] = NULL;
  exact_resource_launcher_environment(expected_runtime);
  int liveness[2];
  if (pipe2(liveness, O_CLOEXEC) != 0) die_errno("kan resource-liveness-pipe niet maken");
  const pid_t parent_pid = getpid();
  pid_t child = fork();
  if (child < 0) die_errno("kan systemd resource-launcher niet starten");
  if (child == 0) {
    if (setpgid(0, 0) != 0
        || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0
        || getppid() != parent_pid) _exit(125);
    if (dup2(liveness[0], STDIN_FILENO) != STDIN_FILENO) _exit(125);
    close(liveness[0]);
    close(liveness[1]);
    execve(SYSTEMD_RUN_BINARY, arguments, environ);
    _exit(125);
  }
  if (setpgid(child, child) != 0 && errno != EACCES && errno != ESRCH) {
    die_errno("kan systemd resource-launchergroep niet bevestigen");
  }
  if (close(liveness[0]) != 0) die_errno("kan liveness-reader in outer parent niet sluiten");
  return wait_resource_launcher(child, unit, liveness[1]);
}

int main(int argc, char **argv) {
  uid_t uid = getuid();
  if (uid == 0 || geteuid() == 0) die("tests mogen nooit als root worden gestart");
  allow_idmapped_root = inside_resource_unit(argc, argv);
  char self_path[PATH_MAX];
  require_static_self(uid, self_path);
  close_inherited_descriptors();
  install_signal_handlers();
  if (!inside_resource_unit(argc, argv)) return launch_resource_unit(argc, argv, self_path);
  argv += 1;
  argc -= 1;
  attest_resource_unit();
  attest_liveness_pipe();
  require_regular(NODE_BINARY, 0, true);
  bool coverage = argc > 1 && strcmp(argv[1], "--coverage") == 0;
  int first_test = coverage ? 2 : 1;
  if (argc <= first_test) die("geef minstens één expliciet testbestand op");

  char repository[PATH_MAX];
  if (getcwd(repository, sizeof(repository)) == NULL) die_errno("kan repositorypad niet bepalen");
  char canonical_repository[PATH_MAX];
  if (realpath(repository, canonical_repository) == NULL
      || strcmp(repository, canonical_repository) != 0) {
    die("repository-cwd is niet canoniek");
  }
  char *runner_source = joined_path(repository, "scripts/run-isolated-tests.mjs");
  int runner_fd = create_sealed_runner(runner_source, uid);

  char root_template[] = BOOTSTRAP_PREFIX "XXXXXX";
  char *root = mkdtemp(root_template);
  if (root == NULL || chmod(root, 0700) != 0) die_errno("kan private bootstraproot niet maken");
  int root_fd = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
  if (root_fd < 0) die_errno("kan private bootstraproot niet pinnen");
  struct stat root_status;
  if (fstat(root_fd, &root_status) != 0 || root_status.st_uid != uid
      || (root_status.st_mode & 0777) != 0700) {
    die("bootstraproot is niet privé");
  }
  struct stat root_path_status;
  if (lstat(root, &root_path_status) != 0
      || root_path_status.st_dev != root_status.st_dev
      || root_path_status.st_ino != root_status.st_ino) {
    die("bootstraprootpad veranderde vóór pinning");
  }
  int coverage_fd = -1;
  char *coverage_path = NULL;
  if (coverage) {
    coverage_path = joined_path(root, "coverage-output");
    if (mkdirat(root_fd, "coverage-output", 0700) != 0) {
      die_errno("kan coverage-output niet maken");
    }
    coverage_fd = openat(root_fd, "coverage-output", O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    if (coverage_fd < 0) die_errno("kan coverage-output niet pinnen");
    struct stat coverage_status;
    if (fstat(coverage_fd, &coverage_status) != 0 || !S_ISDIR(coverage_status.st_mode)
        || coverage_status.st_uid != uid || (coverage_status.st_mode & 0777) != 0700) {
      die("coverage-outputdescriptor is niet privé en regulier");
    }
  }
  exact_environment(root, repository, coverage_fd, true);

  size_t runner_count = (size_t)(argc - first_test) + (coverage ? 7U : 6U);
  char **runner_arguments = calloc(runner_count, sizeof(char *));
  if (runner_arguments == NULL) die_errno("kan runnerargumenten niet alloceren");
  size_t offset = 0;
  runner_arguments[offset++] = (char *)NODE_BINARY;
  runner_arguments[offset++] = (char *)NODE_WASM_FLAG;
  runner_arguments[offset++] = "--experimental-default-type=module";
  runner_arguments[offset++] = "--preserve-symlinks-main";
  runner_arguments[offset++] = "/proc/self/fd/0";
  if (coverage) runner_arguments[offset++] = "--coverage";
  for (int index = first_test; index < argc; index++) runner_arguments[offset++] = argv[index];
  runner_arguments[offset] = NULL;

  if (lseek(runner_fd, 0, SEEK_SET) != 0) die_errno("kan sealed runner niet terugspoelen");
#ifdef BOOTSTRAP_TEST_PRE_NODE_DELAY_MS
  struct timespec test_delay = {
    .tv_sec = BOOTSTRAP_TEST_PRE_NODE_DELAY_MS / 1000,
    .tv_nsec = (BOOTSTRAP_TEST_PRE_NODE_DELAY_MS % 1000) * 1000000L,
  };
  while (nanosleep(&test_delay, &test_delay) != 0 && errno == EINTR) {}
#endif
  int status = supervise(runner_arguments, repository, root_fd, runner_fd, STDIN_FILENO);
  if (close(runner_fd) != 0) die_errno("kan sealed runnerdescriptor niet sluiten");
  if (status == 0 && coverage) {
    check_tree_budget(root_fd);
    char *c8 = joined_path(repository, "node_modules/c8/bin/c8.js");
    require_regular(c8, uid, false);
    char coverage_descriptor_path[64];
    if (snprintf(coverage_descriptor_path, sizeof(coverage_descriptor_path),
        "/proc/self/fd/%d", coverage_fd) <= 0) {
      die("kan coverage-descriptorpad niet bouwen");
    }
    char *report_arguments[] = {
      (char *)NODE_BINARY, (char *)NODE_WASM_FLAG, c8, "report", "--clean=false", "--temp-directory",
      coverage_descriptor_path, NULL,
    };
    unsetenv("BOTTIE_COVERAGE_OUTPUT_FD");
    status = supervise(report_arguments, repository, root_fd, -1, STDIN_FILENO);
    if (status == 0) {
      char *quality = joined_path(repository, "scripts/quality-gate.mjs");
      require_regular(quality, uid, false);
      char *quality_arguments[] = {
        (char *)NODE_BINARY, (char *)NODE_WASM_FLAG, quality, "maintainer-coverage", NULL,
      };
      status = supervise(quality_arguments, repository, root_fd, -1, STDIN_FILENO);
      free(quality);
    }
    free(c8);
  }

  if (coverage_fd >= 0) close(coverage_fd);
  check_tree_budget(root_fd);
  remove_tree_fd(root_fd, 0);
  struct stat final_root_status;
  if (lstat(root, &final_root_status) != 0
      || final_root_status.st_dev != root_status.st_dev
      || final_root_status.st_ino != root_status.st_ino) {
    die("bootstraprootpad veranderde vóór descriptor-cleanup");
  }
  close(root_fd);
  if (rmdir(root) != 0) die_errno("kan bootstraproot niet verwijderen");
  free(coverage_path);
  free(runner_arguments);
  free(runner_source);
  return status;
}
