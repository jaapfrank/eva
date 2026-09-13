#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/openat2.h>
#ifdef EVWW_TEST_PHASE_BARRIERS
#include <signal.h>
#endif
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

/*
 * Non-production workspace-writer primitive.
 *
 * Authority is deliberately narrow: fd 3 is an already-open workspace root,
 * fd 4 is an already-open private journal directory, stdin carries one or
 * more canonical requests, and stdout carries canonical receipts. This file
 * does not authenticate the supplying process or operating-system identity;
 * rootGate, service/DAC ownership and the JavaScript integration are separate
 * release tranches.
 *
 * Request = "EVWWREQ1" || operation-id[32] || generation-u64be
 *   || target-length-u16be || content-length-u64be || content-sha256[32]
 *   || canonical ASCII relative target || raw content.
 * Receipt = "EVWWRCP1" || effect-u8 || generations || operation/request/
 *   content identities || target metadata || target || sha256(all prior bytes).
 * Create journal transitions are P(request+absent target) -> L(published inode)
 *   -> C(receipt); a noop is P -> C. Every record has a
 *   canonical length and SHA-256 and is fdatasync'd before the next effect.
 * Existing targets are never replaced by this component primitive.
 */

enum {
  ROOT_DESCRIPTOR = 3,
  JOURNAL_DIRECTORY_DESCRIPTOR = 4,
  REQUEST_HEADER_BYTES = 90,
  REQUEST_V2_HEADER_BYTES = 122,
  RECEIPT_PREFIX_BYTES = 131,
  RECEIPT_CHECKSUM_BYTES = 32,
  JOURNAL_PREFIX_BYTES = 17,
  JOURNAL_CHECKSUM_BYTES = 32,
  PREPARE_PREFIX_BYTES = 154,
  IDENTITY_PAYLOAD_BYTES = 173,
  SHA256_BYTES = 32,
  OPERATION_ID_BYTES = 32,
  MAX_TARGET_BYTES = 1024,
  MAX_SEGMENT_BYTES = 255,
  MAX_REQUESTS = 256,
  MAX_OPERATIONS = 4096
};

#define MAX_CONTENT_BYTES ((uint64_t)64 * 1024 * 1024)
#define MAX_INPUT_BYTES ((size_t)128 * 1024 * 1024)
#define MAX_JOURNAL_BYTES ((uint64_t)256 * 1024 * 1024)

static const uint8_t REQUEST_MAGIC[8] = {'E','V','W','W','R','E','Q','1'};
static const uint8_t RECEIPT_MAGIC[8] = {'E','V','W','W','R','C','P','1'};
static const uint8_t JOURNAL_MAGIC[8] = {'E','V','W','W','J','R','N','1'};
static const uint8_t PREPARE_MAGIC[8] = {'E','V','W','W','P','R','P','1'};
static const uint8_t PUBLISHED_MAGIC[8] = {'E','V','W','W','L','N','K','1'};
static const char JOURNAL_NAME[] = "workspace-writer.journal";

enum effect_kind {
  EFFECT_CREATED = 1,
  EFFECT_NOOP = 3
};

enum prior_kind {
  PRIOR_ABSENT = 0,
  PRIOR_REGULAR = 1
};

struct sha256_context {
  uint32_t state[8];
  uint64_t total_bytes;
  uint8_t block[64];
  size_t block_bytes;
};

struct bytes {
  uint8_t *data;
  size_t length;
};

struct request {
  const uint8_t *frame;
  size_t frame_length;
  const uint8_t *content;
  uint64_t content_length;
  uint8_t operation_id[OPERATION_ID_BYTES];
  uint8_t content_sha256[SHA256_BYTES];
  uint8_t request_sha256[SHA256_BYTES];
  uint64_t expected_generation;
  uint16_t target_length;
  char target[MAX_TARGET_BYTES + 1];
};

struct prior_state {
  enum prior_kind kind;
  uint64_t device;
  uint64_t inode;
  uint32_t mode;
  uint64_t links;
  uint32_t user;
  uint32_t group;
  uint64_t size;
  int64_t modified_seconds;
  uint32_t modified_nanoseconds;
  int64_t changed_seconds;
  uint32_t changed_nanoseconds;
  uint8_t content_sha256[SHA256_BYTES];
};

struct directory_identity {
  uint64_t device;
  uint64_t inode;
  uint32_t mode;
  uint64_t links;
  uint32_t user;
  uint32_t group;
};

struct observation {
  bool present;
  bool exact_content;
  struct prior_state state;
};

struct receipt {
  uint8_t *data;
  size_t length;
};

struct operation {
  struct request request;
  struct prior_state prior;
  struct directory_identity parent;
  struct prior_state published;
  enum effect_kind effect;
  bool published_recorded;
  bool committed;
  struct receipt receipt;
};

struct broker_state {
  int journal_descriptor;
  uint64_t journal_end;
  uint64_t generation;
  struct operation *operations;
  size_t operation_count;
  struct bytes journal_image;
};

struct parent_target {
  int parent_descriptor;
  char parent_relative[MAX_TARGET_BYTES + 1];
  char base[MAX_SEGMENT_BYTES + 1];
};

static const char *failure_code = NULL;

static int fail(const char *code) {
  if (failure_code == NULL) {
    failure_code = code;
  }
  return -1;
}

static int test_phase_barrier(const char *phase) {
#ifdef EVWW_TEST_PHASE_BARRIERS
  const char *selected = getenv("EVWW_TEST_STOP_PHASE");
  if (selected != NULL && strcmp(selected, phase) == 0 && raise(SIGSTOP) != 0) {
    return fail("test_phase_stop_failed");
  }
#else
  (void)phase;
#endif
  return 0;
}

static int synchronize_published_material(int descriptor) {
#ifdef EVWW_TEST_PHASE_BARRIERS
  const char *selected = getenv("EVWW_TEST_FAIL_PHASE");
  if (selected != NULL && strcmp(selected, "postlink-material-fsync") == 0) {
    errno = EIO;
    return -1;
  }
#endif
  return fsync(descriptor);
}

static uint32_t rotate_right(uint32_t value, unsigned int count) {
  return (value >> count) | (value << (32U - count));
}

static uint32_t sha256_choice(uint32_t x, uint32_t y, uint32_t z) {
  return (x & y) ^ ((~x) & z);
}

static uint32_t sha256_majority(uint32_t x, uint32_t y, uint32_t z) {
  return (x & y) ^ (x & z) ^ (y & z);
}

static void sha256_transform(struct sha256_context *context, const uint8_t block[64]) {
  static const uint32_t constants[64] = {
    0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,
    0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
    0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,
    0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
    0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,
    0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
    0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,
    0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
    0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,
    0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
    0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,
    0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
    0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,
    0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
    0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,
    0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
  };
  uint32_t words[64];
  for (size_t index = 0; index < 16; ++index) {
    size_t offset = index * 4U;
    words[index] = ((uint32_t)block[offset] << 24U)
      | ((uint32_t)block[offset + 1U] << 16U)
      | ((uint32_t)block[offset + 2U] << 8U)
      | (uint32_t)block[offset + 3U];
  }
  for (size_t index = 16; index < 64; ++index) {
    uint32_t left = words[index - 15U];
    uint32_t right = words[index - 2U];
    uint32_t small_zero = rotate_right(left, 7U) ^ rotate_right(left, 18U) ^ (left >> 3U);
    uint32_t small_one = rotate_right(right, 17U) ^ rotate_right(right, 19U) ^ (right >> 10U);
    words[index] = words[index - 16U] + small_zero + words[index - 7U] + small_one;
  }
  uint32_t a = context->state[0];
  uint32_t b = context->state[1];
  uint32_t c = context->state[2];
  uint32_t d = context->state[3];
  uint32_t e = context->state[4];
  uint32_t f = context->state[5];
  uint32_t g = context->state[6];
  uint32_t h = context->state[7];
  for (size_t index = 0; index < 64; ++index) {
    uint32_t big_one = rotate_right(e, 6U) ^ rotate_right(e, 11U) ^ rotate_right(e, 25U);
    uint32_t temporary_one = h + big_one + sha256_choice(e, f, g)
      + constants[index] + words[index];
    uint32_t big_zero = rotate_right(a, 2U) ^ rotate_right(a, 13U) ^ rotate_right(a, 22U);
    uint32_t temporary_two = big_zero + sha256_majority(a, b, c);
    h = g;
    g = f;
    f = e;
    e = d + temporary_one;
    d = c;
    c = b;
    b = a;
    a = temporary_one + temporary_two;
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

static void sha256_initialize(struct sha256_context *context) {
  static const uint32_t initial[8] = {
    0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,
    0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U
  };
  memcpy(context->state, initial, sizeof(initial));
  context->total_bytes = 0U;
  context->block_bytes = 0U;
}

static void sha256_update(struct sha256_context *context, const uint8_t *data, size_t length) {
  context->total_bytes += (uint64_t)length;
  while (length > 0U) {
    size_t available = sizeof(context->block) - context->block_bytes;
    size_t take = length < available ? length : available;
    memcpy(context->block + context->block_bytes, data, take);
    context->block_bytes += take;
    data += take;
    length -= take;
    if (context->block_bytes == sizeof(context->block)) {
      sha256_transform(context, context->block);
      context->block_bytes = 0U;
    }
  }
}

static void put_u64(uint8_t *output, uint64_t value) {
  for (size_t index = 0; index < 8U; ++index) {
    output[7U - index] = (uint8_t)(value & UINT64_C(0xff));
    value >>= 8U;
  }
}

static uint64_t get_u64(const uint8_t *input) {
  uint64_t value = 0U;
  for (size_t index = 0; index < 8U; ++index) {
    value = (value << 8U) | (uint64_t)input[index];
  }
  return value;
}

static void put_u32(uint8_t *output, uint32_t value) {
  for (size_t index = 0; index < 4U; ++index) {
    output[3U - index] = (uint8_t)(value & UINT32_C(0xff));
    value >>= 8U;
  }
}

static uint32_t get_u32(const uint8_t *input) {
  uint32_t value = 0U;
  for (size_t index = 0; index < 4U; ++index) {
    value = (value << 8U) | (uint32_t)input[index];
  }
  return value;
}

static void put_u16(uint8_t *output, uint16_t value) {
  output[0] = (uint8_t)(value >> 8U);
  output[1] = (uint8_t)(value & UINT16_C(0xff));
}

static uint16_t get_u16(const uint8_t *input) {
  return (uint16_t)(((uint16_t)input[0] << 8U) | (uint16_t)input[1]);
}

static void put_i64(uint8_t *output, int64_t value) {
  uint64_t bits = 0U;
  memcpy(&bits, &value, sizeof(bits));
  put_u64(output, bits);
}

static int64_t get_i64(const uint8_t *input) {
  uint64_t bits = get_u64(input);
  int64_t value = 0;
  memcpy(&value, &bits, sizeof(value));
  return value;
}

static void sha256_finish(struct sha256_context *context, uint8_t output[SHA256_BYTES]) {
  uint64_t bit_length = context->total_bytes * UINT64_C(8);
  uint8_t padding[128] = {0x80U};
  size_t padding_length = context->block_bytes < 56U
    ? 56U - context->block_bytes : 120U - context->block_bytes;
  sha256_update(context, padding, padding_length);
  uint8_t encoded_length[8];
  put_u64(encoded_length, bit_length);
  sha256_update(context, encoded_length, sizeof(encoded_length));
  for (size_t index = 0; index < 8U; ++index) {
    put_u32(output + (index * 4U), context->state[index]);
  }
}

static void sha256_bytes(const uint8_t *data, size_t length, uint8_t output[SHA256_BYTES]) {
  struct sha256_context context;
  sha256_initialize(&context);
  sha256_update(&context, data, length);
  sha256_finish(&context, output);
}

static bool all_zero(const uint8_t *data, size_t length) {
  uint8_t combined = 0U;
  for (size_t index = 0; index < length; ++index) {
    combined |= data[index];
  }
  return combined == 0U;
}

static bool exact_stat(const struct stat *left, const struct stat *right) {
  return left->st_dev == right->st_dev
    && left->st_ino == right->st_ino
    && left->st_mode == right->st_mode
    && left->st_nlink == right->st_nlink
    && left->st_uid == right->st_uid
    && left->st_gid == right->st_gid
    && left->st_size == right->st_size
    && left->st_mtim.tv_sec == right->st_mtim.tv_sec
    && left->st_mtim.tv_nsec == right->st_mtim.tv_nsec
    && left->st_ctim.tv_sec == right->st_ctim.tv_sec
    && left->st_ctim.tv_nsec == right->st_ctim.tv_nsec;
}

static int write_all(int descriptor, const uint8_t *data, size_t length) {
  size_t offset = 0U;
  while (offset < length) {
    ssize_t written = write(descriptor, data + offset, length - offset);
    if (written < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("write_failed");
    }
    if (written == 0) {
      return fail("write_failed");
    }
    offset += (size_t)written;
  }
  return 0;
}

static int pwrite_all(int descriptor, const uint8_t *data, size_t length, uint64_t position) {
  size_t offset = 0U;
  while (offset < length) {
    if (position > (uint64_t)INT64_MAX - (uint64_t)offset) {
      return fail("journal_size_refused");
    }
    off_t at = (off_t)(position + (uint64_t)offset);
    ssize_t written = pwrite(descriptor, data + offset, length - offset, at);
    if (written < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("journal_write_failed");
    }
    if (written == 0) {
      return fail("journal_write_failed");
    }
    offset += (size_t)written;
  }
  return 0;
}

static int read_all_stdin(struct bytes *input) {
  size_t capacity = 64U * 1024U;
  input->data = malloc(capacity);
  input->length = 0U;
  if (input->data == NULL) {
    return fail("memory_unavailable");
  }
  for (;;) {
    if (input->length == capacity) {
      if (capacity >= MAX_INPUT_BYTES) {
        return fail("request_stream_too_large");
      }
      size_t next = capacity * 2U;
      if (next > MAX_INPUT_BYTES) {
        next = MAX_INPUT_BYTES;
      }
      uint8_t *grown = realloc(input->data, next);
      if (grown == NULL) {
        return fail("memory_unavailable");
      }
      input->data = grown;
      capacity = next;
    }
    ssize_t received = read(STDIN_FILENO, input->data + input->length, capacity - input->length);
    if (received < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("request_read_failed");
    }
    if (received == 0) {
      return 0;
    }
    input->length += (size_t)received;
  }
}

static bool allowed_path_character(uint8_t value) {
  return (value >= (uint8_t)'a' && value <= (uint8_t)'z')
    || (value >= (uint8_t)'A' && value <= (uint8_t)'Z')
    || (value >= (uint8_t)'0' && value <= (uint8_t)'9')
    || value == (uint8_t)'.' || value == (uint8_t)'_'
    || value == (uint8_t)'-' || value == (uint8_t)'/';
}

static int validate_target(const uint8_t *target, size_t length) {
  if (length == 0U || length > MAX_TARGET_BYTES
      || target[0] == (uint8_t)'/' || target[length - 1U] == (uint8_t)'/') {
    return fail("invalid_target");
  }
  size_t segment_start = 0U;
  for (size_t index = 0; index <= length; ++index) {
    if (index < length && !allowed_path_character(target[index])) {
      return fail("invalid_target");
    }
    if (index < length && target[index] != (uint8_t)'/') {
      continue;
    }
    size_t segment_length = index - segment_start;
    if (segment_length == 0U || segment_length > MAX_SEGMENT_BYTES
        || (segment_length == 1U && target[segment_start] == (uint8_t)'.')
        || (segment_length == 2U && target[segment_start] == (uint8_t)'.'
          && target[segment_start + 1U] == (uint8_t)'.')) {
      return fail("invalid_target");
    }
    segment_start = index + 1U;
  }
  return 0;
}

static int parse_request_frame(const uint8_t *data, size_t available,
                               struct request *request, size_t *consumed) {
  if (available < REQUEST_HEADER_BYTES) {
    return fail("truncated_request");
  }
  bool v2 = memcmp(data, "EVWWREQ2", 8U) == 0;
  size_t header_bytes = v2 ? REQUEST_V2_HEADER_BYTES : REQUEST_HEADER_BYTES;
  if (available < header_bytes) return fail("truncated_request");
  if (v2 && all_zero(data + REQUEST_HEADER_BYTES, SHA256_BYTES))
    return fail("invalid_request_binding");
  if (!v2 && memcmp(data, REQUEST_MAGIC, sizeof(REQUEST_MAGIC)) != 0) {
    return fail("invalid_request_magic");
  }
  if (all_zero(data + 8U, OPERATION_ID_BYTES)) {
    return fail("invalid_operation_id");
  }
  uint16_t target_length = get_u16(data + 48U);
  uint64_t content_length = get_u64(data + 50U);
  if (target_length == 0U || target_length > MAX_TARGET_BYTES) {
    return fail("invalid_target");
  }
  if (content_length > MAX_CONTENT_BYTES) {
    return fail("content_too_large");
  }
  uint64_t frame_length_u64 = (uint64_t)header_bytes
    + (uint64_t)target_length + content_length;
  if (frame_length_u64 > (uint64_t)SIZE_MAX || frame_length_u64 > (uint64_t)available) {
    return fail("truncated_request");
  }
  size_t frame_length = (size_t)frame_length_u64;
  const uint8_t *target = data + header_bytes;
  if (validate_target(target, (size_t)target_length) < 0) {
    return -1;
  }
  const uint8_t *content = target + target_length;
  uint8_t actual_content_sha256[SHA256_BYTES];
  sha256_bytes(content, (size_t)content_length, actual_content_sha256);
  if (memcmp(actual_content_sha256, data + 58U, SHA256_BYTES) != 0) {
    return fail("content_hash_mismatch");
  }
  memset(request, 0, sizeof(*request));
  request->frame = data;
  request->frame_length = frame_length;
  request->content = content;
  request->content_length = content_length;
  request->expected_generation = get_u64(data + 40U);
  request->target_length = target_length;
  memcpy(request->operation_id, data + 8U, OPERATION_ID_BYTES);
  memcpy(request->content_sha256, actual_content_sha256, SHA256_BYTES);
  memcpy(request->target, target, target_length);
  request->target[target_length] = '\0';
  sha256_bytes(data, frame_length, request->request_sha256);
  *consumed = frame_length;
  return 0;
}

static int parse_request_stream(const struct bytes *input, struct request *requests,
                                size_t *request_count) {
  size_t offset = 0U;
  size_t count = 0U;
  while (offset < input->length) {
    if (count >= MAX_REQUESTS) {
      return fail("too_many_requests");
    }
    size_t consumed = 0U;
    if (parse_request_frame(input->data + offset, input->length - offset,
                            &requests[count], &consumed) < 0) {
      return -1;
    }
    offset += consumed;
    ++count;
  }
  *request_count = count;
  return 0;
}

static int secure_openat(int directory_descriptor, const char *relative,
                         int flags, mode_t mode) {
  struct open_how how;
  memset(&how, 0, sizeof(how));
  how.flags = (uint64_t)(unsigned int)flags;
  how.mode = (uint64_t)mode;
  how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS
    | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV;
  long result = syscall(SYS_openat2, directory_descriptor, relative, &how, sizeof(how));
  if (result < 0L) {
    return -1;
  }
  if (result > (long)INT32_MAX) {
    (void)close((int)result);
    errno = EMFILE;
    return -1;
  }
  return (int)result;
}

static int verify_directory_descriptor(int descriptor) {
  struct stat status;
  if (fcntl(descriptor, F_GETFD) < 0 || fstat(descriptor, &status) < 0
      || !S_ISDIR(status.st_mode)) {
    return fail("directory_descriptor_refused");
  }
  return 0;
}

static bool same_directory_identity(const struct stat *left, const struct stat *right) {
  return left->st_dev == right->st_dev && left->st_ino == right->st_ino;
}

static int directory_is_ancestor(int ancestor_descriptor, int candidate_descriptor) {
  struct stat ancestor;
  if (fstat(ancestor_descriptor, &ancestor) < 0) {
    return fail("directory_ancestry_refused");
  }
  int current = fcntl(candidate_descriptor, F_DUPFD_CLOEXEC, 5);
  if (current < 0) {
    return fail("directory_ancestry_refused");
  }
  for (size_t depth = 0; depth < 4096U; ++depth) {
    struct stat current_status;
    if (fstat(current, &current_status) < 0) {
      (void)close(current);
      return fail("directory_ancestry_refused");
    }
    if (same_directory_identity(&ancestor, &current_status)) {
      (void)close(current);
      return 1;
    }
    int parent = openat(current, "..",
                        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (parent < 0) {
      (void)close(current);
      return fail("directory_ancestry_refused");
    }
    struct stat parent_status;
    if (fstat(parent, &parent_status) < 0) {
      (void)close(parent);
      (void)close(current);
      return fail("directory_ancestry_refused");
    }
    bool at_namespace_root = same_directory_identity(&current_status, &parent_status);
    if (close(current) < 0) {
      (void)close(parent);
      return fail("directory_ancestry_refused");
    }
    current = parent;
    if (at_namespace_root) {
      (void)close(current);
      return 0;
    }
  }
  (void)close(current);
  return fail("directory_ancestry_refused");
}

static int verify_directory_separation(void) {
  int root_contains_journal = directory_is_ancestor(
    ROOT_DESCRIPTOR, JOURNAL_DIRECTORY_DESCRIPTOR
  );
  if (root_contains_journal < 0) {
    return -1;
  }
  int journal_contains_root = directory_is_ancestor(
    JOURNAL_DIRECTORY_DESCRIPTOR, ROOT_DESCRIPTOR
  );
  if (journal_contains_root < 0) {
    return -1;
  }
  if (root_contains_journal == 1 || journal_contains_root == 1) {
    return fail("directory_authority_overlap");
  }
  return 0;
}

static int resolve_parent(const struct request *request, struct parent_target *target) {
  const char *slash = strrchr(request->target, '/');
  memset(target, 0, sizeof(*target));
  if (slash == NULL) {
    size_t base_length = strlen(request->target);
    memcpy(target->base, request->target, base_length + 1U);
    target->parent_descriptor = fcntl(ROOT_DESCRIPTOR, F_DUPFD_CLOEXEC, 5);
    if (target->parent_descriptor < 0) {
      return fail("path_resolution_refused");
    }
    return 0;
  }
  size_t parent_length = (size_t)(slash - request->target);
  size_t base_length = strlen(slash + 1);
  char parent[MAX_TARGET_BYTES + 1];
  memcpy(parent, request->target, parent_length);
  parent[parent_length] = '\0';
  memcpy(target->parent_relative, parent, parent_length + 1U);
  memcpy(target->base, slash + 1, base_length + 1U);
  target->parent_descriptor = secure_openat(
    ROOT_DESCRIPTOR, parent,
    O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW, 0
  );
  if (target->parent_descriptor < 0) {
    if (errno == ENOSYS) {
      return fail("openat2_unavailable");
    }
    return fail("path_resolution_refused");
  }
  return 0;
}

static void close_parent(struct parent_target *target) {
  if (target->parent_descriptor >= 0) {
    (void)close(target->parent_descriptor);
    target->parent_descriptor = -1;
  }
}

static int read_directory_identity(int descriptor, struct directory_identity *identity) {
  struct stat status;
  if (fstat(descriptor, &status) < 0 || !S_ISDIR(status.st_mode)) {
    return fail("parent_binding_changed");
  }
  identity->device = (uint64_t)status.st_dev;
  identity->inode = (uint64_t)status.st_ino;
  identity->mode = (uint32_t)status.st_mode;
  identity->links = (uint64_t)status.st_nlink;
  identity->user = (uint32_t)status.st_uid;
  identity->group = (uint32_t)status.st_gid;
  return 0;
}

static bool same_parent_identity(const struct directory_identity *left,
                                 const struct directory_identity *right) {
  return left->device == right->device
    && left->inode == right->inode
    && left->mode == right->mode
    && left->links == right->links
    && left->user == right->user
    && left->group == right->group;
}

static int assert_parent_binding(const struct parent_target *target,
                                 const struct directory_identity *expected) {
  struct directory_identity held;
  if (read_directory_identity(target->parent_descriptor, &held) < 0
      || !same_parent_identity(&held, expected)) {
    return failure_code == NULL ? fail("parent_binding_changed") : -1;
  }
  int rebound = target->parent_relative[0] == '\0'
    ? fcntl(ROOT_DESCRIPTOR, F_DUPFD_CLOEXEC, 5)
    : secure_openat(ROOT_DESCRIPTOR, target->parent_relative,
                    O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW, 0);
  if (rebound < 0) {
    return fail("parent_binding_changed");
  }
  struct directory_identity rebound_identity;
  int result = read_directory_identity(rebound, &rebound_identity);
  int close_result = close(rebound);
  if (result < 0 || close_result < 0
      || !same_parent_identity(&rebound_identity, expected)) {
    return failure_code == NULL ? fail("parent_binding_changed") : -1;
  }
  return 0;
}

static int hash_regular_descriptor(int descriptor, const struct request *request,
                                   struct observation *observation) {
  struct stat before;
  struct stat after;
  if (fstat(descriptor, &before) < 0) {
    return fail("target_stat_failed");
  }
  if (!S_ISREG(before.st_mode)) {
    return fail("target_not_regular");
  }
  if (before.st_size < 0 || (uint64_t)before.st_size > MAX_CONTENT_BYTES) {
    return fail("target_too_large");
  }
  struct sha256_context hash;
  sha256_initialize(&hash);
  uint8_t buffer[64U * 1024U];
  uint64_t position = 0U;
  bool exact = (uint64_t)before.st_size == request->content_length;
  while (position < (uint64_t)before.st_size) {
    size_t wanted = sizeof(buffer);
    uint64_t remaining = (uint64_t)before.st_size - position;
    if (remaining < (uint64_t)wanted) {
      wanted = (size_t)remaining;
    }
    ssize_t received = pread(descriptor, buffer, wanted, (off_t)position);
    if (received < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("target_read_failed");
    }
    if (received == 0) {
      return fail("target_changed");
    }
    size_t bytes_read = (size_t)received;
    sha256_update(&hash, buffer, bytes_read);
    if (exact && memcmp(buffer, request->content + (size_t)position, bytes_read) != 0) {
      exact = false;
    }
    position += (uint64_t)bytes_read;
  }
  uint8_t extra;
  if (pread(descriptor, &extra, 1U, before.st_size) != 0) {
    return fail("target_changed");
  }
  if (fstat(descriptor, &after) < 0 || !exact_stat(&before, &after)) {
    return fail("target_changed");
  }
  memset(observation, 0, sizeof(*observation));
  observation->present = true;
  observation->exact_content = exact;
  observation->state.kind = PRIOR_REGULAR;
  observation->state.device = (uint64_t)after.st_dev;
  observation->state.inode = (uint64_t)after.st_ino;
  observation->state.mode = (uint32_t)after.st_mode;
  observation->state.links = (uint64_t)after.st_nlink;
  observation->state.user = (uint32_t)after.st_uid;
  observation->state.group = (uint32_t)after.st_gid;
  observation->state.size = (uint64_t)after.st_size;
  observation->state.modified_seconds = (int64_t)after.st_mtim.tv_sec;
  observation->state.modified_nanoseconds = (uint32_t)after.st_mtim.tv_nsec;
  observation->state.changed_seconds = (int64_t)after.st_ctim.tv_sec;
  observation->state.changed_nanoseconds = (uint32_t)after.st_ctim.tv_nsec;
  sha256_finish(&hash, observation->state.content_sha256);
  return 0;
}

static int observe_target(const struct parent_target *target, const struct request *request,
                          struct observation *observation) {
  int descriptor = secure_openat(
    target->parent_descriptor, target->base,
    O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0
  );
  if (descriptor < 0) {
    if (errno == ENOENT) {
      memset(observation, 0, sizeof(*observation));
      observation->state.kind = PRIOR_ABSENT;
      return 0;
    }
    if (errno == ENOSYS) {
      return fail("openat2_unavailable");
    }
    if (errno == ELOOP || errno == EXDEV) {
      return fail("path_resolution_refused");
    }
    return fail("target_open_failed");
  }
  int result = hash_regular_descriptor(descriptor, request, observation);
  int close_result = close(descriptor);
  if (result == 0 && close_result < 0) {
    return fail("target_close_failed");
  }
  return result;
}

static bool same_prior(const struct prior_state *left, const struct prior_state *right) {
  return left->kind == right->kind
    && left->device == right->device
    && left->inode == right->inode
    && left->mode == right->mode
    && left->links == right->links
    && left->user == right->user
    && left->group == right->group
    && left->size == right->size
    && left->modified_seconds == right->modified_seconds
    && left->modified_nanoseconds == right->modified_nanoseconds
    && left->changed_seconds == right->changed_seconds
    && left->changed_nanoseconds == right->changed_nanoseconds
    && memcmp(left->content_sha256, right->content_sha256, SHA256_BYTES) == 0;
}

static int assert_target_matches_prior(const struct parent_target *target,
                                       const struct operation *operation) {
  struct observation current;
  if (observe_target(target, &operation->request, &current) < 0) {
    return -1;
  }
  if (operation->prior.kind == PRIOR_ABSENT) {
    return current.present ? fail("destination_exists") : 0;
  }
  if (!current.present || !same_prior(&current.state, &operation->prior)) {
    return fail("target_changed_before_publish");
  }
  return 0;
}

static bool exact_target_with_links(const struct observation *observation,
                                    const struct request *request,
                                    uint64_t expected_links) {
  mode_t permissions = (mode_t)(observation->state.mode & (uint32_t)07777U);
  return observation->present && observation->exact_content
    && observation->state.size == request->content_length
    && memcmp(observation->state.content_sha256, request->content_sha256,
              SHA256_BYTES) == 0
    && permissions == (mode_t)0600
    && observation->state.links == expected_links
    && observation->state.user == (uint32_t)geteuid();
}

static bool exact_published_target(const struct observation *observation,
                                   const struct request *request) {
  return exact_target_with_links(observation, request, UINT64_C(1));
}

static void encode_prior(uint8_t *output, const struct prior_state *prior) {
  output[0] = (uint8_t)prior->kind;
  put_u64(output + 1U, prior->device);
  put_u64(output + 9U, prior->inode);
  put_u32(output + 17U, prior->mode);
  put_u64(output + 21U, prior->links);
  put_u32(output + 29U, prior->user);
  put_u32(output + 33U, prior->group);
  put_u64(output + 37U, prior->size);
  put_i64(output + 45U, prior->modified_seconds);
  put_u32(output + 53U, prior->modified_nanoseconds);
  put_i64(output + 57U, prior->changed_seconds);
  put_u32(output + 65U, prior->changed_nanoseconds);
  memcpy(output + 69U, prior->content_sha256, SHA256_BYTES);
}

static int decode_prior(const uint8_t *input, struct prior_state *prior) {
  memset(prior, 0, sizeof(*prior));
  prior->kind = (enum prior_kind)input[0];
  prior->device = get_u64(input + 1U);
  prior->inode = get_u64(input + 9U);
  prior->mode = get_u32(input + 17U);
  prior->links = get_u64(input + 21U);
  prior->user = get_u32(input + 29U);
  prior->group = get_u32(input + 33U);
  prior->size = get_u64(input + 37U);
  prior->modified_seconds = get_i64(input + 45U);
  prior->modified_nanoseconds = get_u32(input + 53U);
  prior->changed_seconds = get_i64(input + 57U);
  prior->changed_nanoseconds = get_u32(input + 65U);
  memcpy(prior->content_sha256, input + 69U, SHA256_BYTES);
  if (prior->kind == PRIOR_ABSENT) {
    uint8_t empty[101] = {0};
    empty[0] = (uint8_t)PRIOR_ABSENT;
    if (memcmp(input, empty, sizeof(empty)) != 0) {
      return fail("journal_noncanonical_prior");
    }
    return 0;
  }
  if (prior->kind != PRIOR_REGULAR
      || (prior->mode & (uint32_t)S_IFMT) != (uint32_t)S_IFREG
      || prior->links == 0U || prior->modified_nanoseconds >= UINT32_C(1000000000)
      || prior->changed_nanoseconds >= UINT32_C(1000000000)) {
    return fail("journal_noncanonical_prior");
  }
  return 0;
}

static void encode_parent_identity(uint8_t *output,
                                   const struct directory_identity *identity) {
  put_u64(output, identity->device);
  put_u64(output + 8U, identity->inode);
  put_u32(output + 16U, identity->mode);
  put_u64(output + 20U, identity->links);
  put_u32(output + 28U, identity->user);
  put_u32(output + 32U, identity->group);
}

static int decode_parent_identity(const uint8_t *input,
                                  struct directory_identity *identity) {
  identity->device = get_u64(input);
  identity->inode = get_u64(input + 8U);
  identity->mode = get_u32(input + 16U);
  identity->links = get_u64(input + 20U);
  identity->user = get_u32(input + 28U);
  identity->group = get_u32(input + 32U);
  if ((identity->mode & (uint32_t)S_IFMT) != (uint32_t)S_IFDIR
      || identity->links == 0U) {
    return fail("journal_invalid_parent_identity");
  }
  return 0;
}

static int build_prepare_payload(const struct operation *operation, struct bytes *payload) {
  uint64_t length_u64 = (uint64_t)PREPARE_PREFIX_BYTES
    + (uint64_t)operation->request.frame_length;
  if (length_u64 > (uint64_t)SIZE_MAX) {
    return fail("journal_size_refused");
  }
  payload->length = (size_t)length_u64;
  payload->data = calloc(1U, payload->length);
  if (payload->data == NULL) {
    return fail("memory_unavailable");
  }
  memcpy(payload->data, PREPARE_MAGIC, sizeof(PREPARE_MAGIC));
  payload->data[8] = (uint8_t)operation->effect;
  put_u64(payload->data + 9U, (uint64_t)operation->request.frame_length);
  encode_prior(payload->data + 17U, &operation->prior);
  encode_parent_identity(payload->data + 118U, &operation->parent);
  memcpy(payload->data + PREPARE_PREFIX_BYTES,
         operation->request.frame, operation->request.frame_length);
  return 0;
}

static int build_identity_payload(const uint8_t magic[8],
                                  const struct operation *operation,
                                  const struct prior_state *identity,
                                  struct bytes *payload) {
  payload->data = calloc(1U, IDENTITY_PAYLOAD_BYTES);
  payload->length = IDENTITY_PAYLOAD_BYTES;
  if (payload->data == NULL) {
    return fail("memory_unavailable");
  }
  memcpy(payload->data, magic, 8U);
  memcpy(payload->data + 8U, operation->request.operation_id, OPERATION_ID_BYTES);
  memcpy(payload->data + 40U, operation->request.request_sha256, SHA256_BYTES);
  encode_prior(payload->data + 72U, identity);
  return 0;
}

static int parse_identity_payload(const uint8_t *payload, size_t payload_length,
                                  const uint8_t magic[8],
                                  const struct operation *operation,
                                  struct prior_state *identity) {
  if (payload_length != IDENTITY_PAYLOAD_BYTES
      || memcmp(payload, magic, 8U) != 0
      || memcmp(payload + 8U, operation->request.operation_id,
                OPERATION_ID_BYTES) != 0
      || memcmp(payload + 40U, operation->request.request_sha256,
                SHA256_BYTES) != 0
      || decode_prior(payload + 72U, identity) < 0) {
    return failure_code == NULL ? fail("journal_invalid_identity") : -1;
  }
  mode_t permissions = (mode_t)(identity->mode & (uint32_t)07777U);
  if (identity->kind != PRIOR_REGULAR || identity->links != UINT64_C(1)
      || identity->user != (uint32_t)geteuid()
      || permissions != (mode_t)0600
      || identity->size != operation->request.content_length
      || memcmp(identity->content_sha256, operation->request.content_sha256,
                SHA256_BYTES) != 0) {
    return fail("journal_invalid_identity");
  }
  return 0;
}

static int build_receipt(const struct operation *operation, struct receipt *receipt) {
  uint64_t body_length_u64 = (uint64_t)RECEIPT_PREFIX_BYTES
    + (uint64_t)operation->request.target_length;
  uint64_t total_length_u64 = body_length_u64 + (uint64_t)RECEIPT_CHECKSUM_BYTES;
  if (total_length_u64 > (uint64_t)SIZE_MAX
      || operation->request.expected_generation == UINT64_MAX) {
    return fail("generation_overflow");
  }
  receipt->length = (size_t)total_length_u64;
  receipt->data = calloc(1U, receipt->length);
  if (receipt->data == NULL) {
    return fail("memory_unavailable");
  }
  memcpy(receipt->data, RECEIPT_MAGIC, sizeof(RECEIPT_MAGIC));
  receipt->data[8] = (uint8_t)operation->effect;
  put_u64(receipt->data + 9U, operation->request.expected_generation);
  put_u64(receipt->data + 17U, operation->request.expected_generation + UINT64_C(1));
  memcpy(receipt->data + 25U, operation->request.operation_id, OPERATION_ID_BYTES);
  memcpy(receipt->data + 57U, operation->request.request_sha256, SHA256_BYTES);
  memcpy(receipt->data + 89U, operation->request.content_sha256, SHA256_BYTES);
  put_u16(receipt->data + 121U, operation->request.target_length);
  put_u64(receipt->data + 123U, operation->request.content_length);
  memcpy(receipt->data + RECEIPT_PREFIX_BYTES,
         operation->request.target, operation->request.target_length);
  sha256_bytes(receipt->data, (size_t)body_length_u64,
               receipt->data + (size_t)body_length_u64);
  return 0;
}

static int parse_receipt(const uint8_t *data, size_t length, struct receipt *receipt) {
  if (length < RECEIPT_PREFIX_BYTES + RECEIPT_CHECKSUM_BYTES
      || memcmp(data, RECEIPT_MAGIC, sizeof(RECEIPT_MAGIC)) != 0) {
    return fail("journal_invalid_receipt");
  }
  uint16_t target_length = get_u16(data + 121U);
  uint64_t expected_length = (uint64_t)RECEIPT_PREFIX_BYTES
    + (uint64_t)target_length + (uint64_t)RECEIPT_CHECKSUM_BYTES;
  if (target_length == 0U || target_length > MAX_TARGET_BYTES
      || expected_length != (uint64_t)length) {
    return fail("journal_invalid_receipt");
  }
  uint8_t checksum[SHA256_BYTES];
  sha256_bytes(data, length - RECEIPT_CHECKSUM_BYTES, checksum);
  if (memcmp(checksum, data + length - RECEIPT_CHECKSUM_BYTES, SHA256_BYTES) != 0) {
    return fail("journal_invalid_receipt");
  }
  receipt->data = malloc(length);
  if (receipt->data == NULL) {
    return fail("memory_unavailable");
  }
  memcpy(receipt->data, data, length);
  receipt->length = length;
  return 0;
}

static int verify_journal_publication(int descriptor, const uint8_t *record,
                                      size_t record_length, uint64_t position) {
  size_t offset = 0U;
  uint8_t buffer[64U * 1024U];
  while (offset < record_length) {
    size_t wanted = record_length - offset;
    if (wanted > sizeof(buffer)) {
      wanted = sizeof(buffer);
    }
    if (position > (uint64_t)INT64_MAX - (uint64_t)offset) {
      return fail("journal_size_refused");
    }
    ssize_t received = pread(descriptor, buffer, wanted,
                             (off_t)(position + (uint64_t)offset));
    if (received < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("journal_readback_failed");
    }
    if (received == 0 || memcmp(buffer, record + offset, (size_t)received) != 0) {
      return fail("journal_readback_mismatch");
    }
    offset += (size_t)received;
  }
  struct stat held;
  if (fstat(descriptor, &held) < 0 || !S_ISREG(held.st_mode)
      || held.st_nlink != (nlink_t)1 || held.st_uid != geteuid()
      || (held.st_mode & (mode_t)07777) != (mode_t)0600
      || held.st_size < 0
      || (uint64_t)held.st_size != position + (uint64_t)record_length) {
    return fail("journal_authority_refused");
  }
  int named_descriptor = secure_openat(
    JOURNAL_DIRECTORY_DESCRIPTOR, JOURNAL_NAME,
    O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0
  );
  if (named_descriptor < 0) {
    return fail("journal_binding_changed");
  }
  struct stat named;
  int named_result = fstat(named_descriptor, &named);
  int close_result = close(named_descriptor);
  if (named_result < 0 || close_result < 0
      || held.st_dev != named.st_dev || held.st_ino != named.st_ino
      || !exact_stat(&held, &named)) {
    return fail("journal_binding_changed");
  }
  return 0;
}

static int append_journal_record(struct broker_state *state, uint8_t type,
                                 const uint8_t *payload, size_t payload_length) {
  uint64_t record_length_u64 = (uint64_t)JOURNAL_PREFIX_BYTES
    + (uint64_t)payload_length + (uint64_t)JOURNAL_CHECKSUM_BYTES;
  if (record_length_u64 > MAX_JOURNAL_BYTES
      || state->journal_end > MAX_JOURNAL_BYTES - record_length_u64
      || record_length_u64 > (uint64_t)SIZE_MAX) {
    return fail("journal_size_refused");
  }
  size_t record_length = (size_t)record_length_u64;
  uint8_t *record = malloc(record_length);
  if (record == NULL) {
    return fail("memory_unavailable");
  }
  memcpy(record, JOURNAL_MAGIC, sizeof(JOURNAL_MAGIC));
  record[8] = type;
  put_u64(record + 9U, (uint64_t)payload_length);
  memcpy(record + JOURNAL_PREFIX_BYTES, payload, payload_length);
  sha256_bytes(record, record_length - JOURNAL_CHECKSUM_BYTES,
               record + record_length - JOURNAL_CHECKSUM_BYTES);
  int result = pwrite_all(state->journal_descriptor, record, record_length, state->journal_end);
  if (result == 0 && fdatasync(state->journal_descriptor) < 0) {
    result = fail("journal_sync_failed");
  }
  if (result == 0
      && verify_journal_publication(state->journal_descriptor, record,
                                    record_length, state->journal_end) < 0) {
    result = -1;
  }
  if (result == 0) {
    state->journal_end += record_length_u64;
  }
  free(record);
  return result;
}

static struct operation *operation_by_id(struct broker_state *state,
                                         const uint8_t operation_id[OPERATION_ID_BYTES]) {
  for (size_t index = 0; index < state->operation_count; ++index) {
    if (memcmp(state->operations[index].request.operation_id,
               operation_id, OPERATION_ID_BYTES) == 0) {
      return &state->operations[index];
    }
  }
  return NULL;
}

static int parse_prepare_payload(const uint8_t *payload, size_t payload_length,
                                 struct operation *operation) {
  if (payload_length < PREPARE_PREFIX_BYTES + REQUEST_HEADER_BYTES
      || memcmp(payload, PREPARE_MAGIC, sizeof(PREPARE_MAGIC)) != 0) {
    return fail("journal_invalid_prepare");
  }
  uint8_t effect = payload[8];
  uint64_t request_length = get_u64(payload + 9U);
  if (request_length > (uint64_t)SIZE_MAX
      || request_length != (uint64_t)(payload_length - PREPARE_PREFIX_BYTES)) {
    return fail("journal_invalid_prepare");
  }
  memset(operation, 0, sizeof(*operation));
  if (decode_prior(payload + 17U, &operation->prior) < 0) {
    return -1;
  }
  if (decode_parent_identity(payload + 118U, &operation->parent) < 0) {
    return -1;
  }
  size_t consumed = 0U;
  if (parse_request_frame(payload + PREPARE_PREFIX_BYTES, (size_t)request_length,
                          &operation->request, &consumed) < 0
      || consumed != (size_t)request_length) {
    return fail("journal_invalid_prepare");
  }
  if ((effect == (uint8_t)EFFECT_CREATED && operation->prior.kind != PRIOR_ABSENT)
      || (effect == (uint8_t)EFFECT_NOOP && operation->prior.kind != PRIOR_REGULAR)
      || (effect != (uint8_t)EFFECT_CREATED && effect != (uint8_t)EFFECT_NOOP)) {
    return fail("journal_invalid_prepare");
  }
  operation->effect = (enum effect_kind)effect;
  return 0;
}

static int validate_commit(struct operation *operation,
                           const uint8_t *payload, size_t payload_length) {
  struct receipt parsed = {0};
  struct receipt expected = {0};
  if (parse_receipt(payload, payload_length, &parsed) < 0) {
    return -1;
  }
  if (build_receipt(operation, &expected) < 0) {
    free(parsed.data);
    return -1;
  }
  if (parsed.length != expected.length
      || memcmp(parsed.data, expected.data, expected.length) != 0) {
    free(expected.data);
    free(parsed.data);
    return fail("journal_receipt_mismatch");
  }
  free(expected.data);
  operation->receipt = parsed;
  operation->committed = true;
  return 0;
}

static int load_journal_image(int descriptor, struct bytes *image) {
  struct stat status;
  if (fstat(descriptor, &status) < 0 || !S_ISREG(status.st_mode)
      || status.st_nlink != (nlink_t)1 || status.st_uid != geteuid()
      || (status.st_mode & (mode_t)07777) != (mode_t)0600) {
    return fail("journal_authority_refused");
  }
  if (status.st_size < 0 || (uint64_t)status.st_size > MAX_JOURNAL_BYTES) {
    return fail("journal_size_refused");
  }
  image->length = (size_t)status.st_size;
  image->data = image->length == 0U ? NULL : malloc(image->length);
  if (image->length > 0U && image->data == NULL) {
    return fail("memory_unavailable");
  }
  size_t offset = 0U;
  while (offset < image->length) {
    ssize_t received = pread(descriptor, image->data + offset,
                             image->length - offset, (off_t)offset);
    if (received < 0) {
      if (errno == EINTR) {
        continue;
      }
      return fail("journal_read_failed");
    }
    if (received == 0) {
      return fail("journal_changed");
    }
    offset += (size_t)received;
  }
  struct stat after;
  if (fstat(descriptor, &after) < 0 || !exact_stat(&status, &after)) {
    return fail("journal_changed");
  }
  return 0;
}

static int scan_journal(struct broker_state *state) {
  const uint8_t *data = state->journal_image.data;
  size_t length = state->journal_image.length;
  size_t offset = 0U;
  struct operation *pending = NULL;
  while (offset < length) {
    size_t remaining = length - offset;
    if (remaining < JOURNAL_PREFIX_BYTES) {
      return fail("journal_incomplete_record");
    }
    if (memcmp(data + offset, JOURNAL_MAGIC, sizeof(JOURNAL_MAGIC)) != 0) {
      return fail("journal_magic_mismatch");
    }
    uint8_t type = data[offset + 8U];
    uint64_t payload_length_u64 = get_u64(data + offset + 9U);
    if (payload_length_u64 > MAX_JOURNAL_BYTES) {
      return fail("journal_size_refused");
    }
    uint64_t record_length_u64 = (uint64_t)JOURNAL_PREFIX_BYTES
      + payload_length_u64 + (uint64_t)JOURNAL_CHECKSUM_BYTES;
    if (record_length_u64 > (uint64_t)remaining) {
      return fail("journal_incomplete_record");
    }
    size_t record_length = (size_t)record_length_u64;
    uint8_t expected_checksum[SHA256_BYTES];
    sha256_bytes(data + offset, record_length - JOURNAL_CHECKSUM_BYTES,
                 expected_checksum);
    if (memcmp(expected_checksum,
               data + offset + record_length - JOURNAL_CHECKSUM_BYTES,
               SHA256_BYTES) != 0) {
      return fail("journal_checksum_mismatch");
    }
    const uint8_t *payload = data + offset + JOURNAL_PREFIX_BYTES;
    size_t payload_length = (size_t)payload_length_u64;
    if (type == (uint8_t)'P') {
      if (pending != NULL || state->operation_count >= MAX_OPERATIONS) {
        return fail("journal_state_mismatch");
      }
      struct operation parsed;
      if (parse_prepare_payload(payload, payload_length, &parsed) < 0) {
        return -1;
      }
      if (parsed.request.expected_generation != state->generation
          || operation_by_id(state, parsed.request.operation_id) != NULL) {
        return fail("journal_state_mismatch");
      }
      state->operations[state->operation_count] = parsed;
      pending = &state->operations[state->operation_count];
      ++state->operation_count;
    } else if (type == (uint8_t)'L') {
      if (pending == NULL || pending->effect == EFFECT_NOOP
          || pending->published_recorded
          || parse_identity_payload(payload, payload_length, PUBLISHED_MAGIC,
                                    pending, &pending->published) < 0) {
        return failure_code == NULL ? fail("journal_state_mismatch") : -1;
      }
      pending->published_recorded = true;
    } else if (type == (uint8_t)'C') {
      bool state_complete = pending != NULL
        && ((pending->effect == EFFECT_NOOP
            && !pending->published_recorded)
          || (pending->effect != EFFECT_NOOP
            && pending->published_recorded));
      if (!state_complete || validate_commit(pending, payload, payload_length) < 0) {
        return failure_code == NULL ? fail("journal_state_mismatch") : -1;
      }
      ++state->generation;
      pending = NULL;
    } else {
      return fail("journal_record_type_refused");
    }
    offset += record_length;
    state->journal_end = (uint64_t)offset;
  }
  return 0;
}

static int open_journal(struct broker_state *state, bool read_only) {
  int descriptor = secure_openat(
    JOURNAL_DIRECTORY_DESCRIPTOR, JOURNAL_NAME,
    read_only ? (O_RDONLY | O_CLOEXEC | O_NOFOLLOW)
              : (O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW),
    read_only ? 0 : (mode_t)0600
  );
  if (descriptor < 0) {
    if (errno == ENOSYS) {
      return fail("openat2_unavailable");
    }
    return fail("journal_open_failed");
  }
  state->journal_descriptor = descriptor;
  if (flock(descriptor, LOCK_EX | LOCK_NB) < 0) {
    return fail("broker_already_active");
  }
  if (!read_only && fsync(JOURNAL_DIRECTORY_DESCRIPTOR) < 0) {
    return fail("journal_directory_sync_failed");
  }
  if (load_journal_image(descriptor, &state->journal_image) < 0) {
    return -1;
  }
  if (verify_journal_publication(descriptor, NULL, 0U,
                                 (uint64_t)state->journal_image.length) < 0) {
    return -1;
  }
  state->journal_end = (uint64_t)state->journal_image.length;
  return scan_journal(state);
}

static int verify_material_descriptor(int descriptor, const struct request *request,
                                      uint64_t expected_links,
                                      struct prior_state *identity) {
  struct observation observed;
  if (hash_regular_descriptor(descriptor, request, &observed) < 0) {
    return -1;
  }
  mode_t permissions = (mode_t)(observed.state.mode & (uint32_t)07777U);
  if (!observed.present || !observed.exact_content
      || observed.state.size != request->content_length
      || memcmp(observed.state.content_sha256, request->content_sha256,
                SHA256_BYTES) != 0
      || permissions != (mode_t)0600
      || observed.state.links != expected_links
      || observed.state.user != (uint32_t)geteuid()) {
    return fail("anonymous_material_mismatch");
  }
  if (identity != NULL) {
    *identity = observed.state;
  }
  return 0;
}

static int create_anonymous_material(const struct parent_target *target,
                                     struct operation *operation) {
  int descriptor = secure_openat(
    target->parent_descriptor, ".",
    O_RDWR | O_TMPFILE | O_CLOEXEC, (mode_t)0600
  );
  if (descriptor < 0) {
    if (errno == ENOSYS || errno == EOPNOTSUPP || errno == EINVAL) {
      return fail("anonymous_material_unavailable");
    }
    return fail("anonymous_material_open_failed");
  }
  int result = 0;
  if (write_all(descriptor, operation->request.content,
                (size_t)operation->request.content_length) < 0
      || fchmod(descriptor, (mode_t)0600) < 0
      || fdatasync(descriptor) < 0
      || verify_material_descriptor(descriptor, &operation->request, UINT64_C(0),
                                    NULL) < 0) {
    result = failure_code == NULL ? fail("anonymous_material_failed") : -1;
  }
  if (result == 0
      && (assert_parent_binding(target, &operation->parent) < 0
        || assert_target_matches_prior(target, operation) < 0)) {
    result = -1;
  }
  if (result == 0 && test_phase_barrier("prelink") < 0) {
    result = -1;
  }
  if (result == 0
      && linkat(descriptor, "", target->parent_descriptor,
                target->base, AT_EMPTY_PATH) < 0) {
    result = fail(errno == EEXIST ? "destination_exists" : "anonymous_material_link_failed");
  }
  if (result == 0 && synchronize_published_material(descriptor) < 0) {
    result = fail("publish_material_sync_failed");
  }
  if (result == 0 && fsync(target->parent_descriptor) < 0) {
    result = fail("publish_directory_sync_failed");
  }
  struct prior_state held;
  struct observation published;
  if (result == 0
      && (verify_material_descriptor(descriptor, &operation->request,
                                     UINT64_C(1), &held) < 0
        || observe_target(target, &operation->request, &published) < 0
        || !exact_published_target(&published, &operation->request)
        || !same_prior(&held, &published.state))) {
    result = failure_code == NULL ? fail("published_target_mismatch") : -1;
  }
  if (result == 0) {
    operation->published = published.state;
  }
  if (result == 0 && test_phase_barrier("postlink-pre-L") < 0) {
    result = -1;
  }
  if (close(descriptor) < 0 && result == 0) {
    result = fail("anonymous_material_close_failed");
  }
  return result;
}

static int append_identity_record(struct broker_state *state, uint8_t type,
                                  const uint8_t magic[8],
                                  struct operation *operation,
                                  const struct prior_state *identity) {
  struct bytes payload = {0};
  if (build_identity_payload(magic, operation, identity, &payload) < 0) {
    return -1;
  }
  int result = append_journal_record(state, type, payload.data, payload.length);
  free(payload.data);
  return result;
}

static int record_published_target(struct broker_state *state,
                                   const struct parent_target *target,
                                   struct operation *operation) {
  struct observation published;
  bool has_held_identity = operation->published.kind == PRIOR_REGULAR;
  if (assert_parent_binding(target, &operation->parent) < 0
      || observe_target(target, &operation->request, &published) < 0
      || !exact_published_target(&published, &operation->request)
      || (has_held_identity && !same_prior(&operation->published, &published.state))) {
    return failure_code == NULL ? fail("published_target_mismatch") : -1;
  }
  operation->published = published.state;
  if (append_identity_record(state, (uint8_t)'L', PUBLISHED_MAGIC,
                             operation, &operation->published) < 0) {
    return -1;
  }
  operation->published_recorded = true;
  if (test_phase_barrier("post-L") < 0) {
    return -1;
  }
  return 0;
}

static int publish_operation(struct broker_state *state,
                             const struct parent_target *target,
                             struct operation *operation) {
  if (assert_parent_binding(target, &operation->parent) < 0
      || assert_target_matches_prior(target, operation) < 0) {
    return -1;
  }
  if (create_anonymous_material(target, operation) < 0) {
    return -1;
  }
  return record_published_target(state, target, operation);
}

static int verify_operation_effect(struct operation *operation) {
  struct parent_target target = {.parent_descriptor = -1};
  if (resolve_parent(&operation->request, &target) < 0) {
    return -1;
  }
  struct observation current;
  int result = assert_parent_binding(&target, &operation->parent);
  if (result == 0) {
    result = observe_target(&target, &operation->request, &current);
  }
  if (result == 0 && operation->effect == EFFECT_CREATED) {
    if (!operation->published_recorded
        || !exact_published_target(&current, &operation->request)
        || !same_prior(&current.state, &operation->published)) {
      result = failure_code == NULL ? fail("committed_target_mismatch") : -1;
    }
  } else if (result == 0 && operation->effect == EFFECT_NOOP) {
    if (!exact_published_target(&current, &operation->request)
        || !same_prior(&current.state, &operation->prior)) {
      result = fail("committed_target_mismatch");
    }
  }
  close_parent(&target);
  return result;
}

static int append_commit(struct broker_state *state, struct operation *operation) {
  if (verify_operation_effect(operation) < 0
      || build_receipt(operation, &operation->receipt) < 0) {
    return -1;
  }
  if (append_journal_record(state, (uint8_t)'C',
                            operation->receipt.data, operation->receipt.length) < 0) {
    return -1;
  }
  operation->committed = true;
  ++state->generation;
  if (test_phase_barrier("post-C") < 0) {
    return -1;
  }
  return 0;
}

static int recover_pending_operation(struct broker_state *state,
                                     struct operation *operation) {
  struct parent_target target = {.parent_descriptor = -1};
  if (resolve_parent(&operation->request, &target) < 0) {
    return -1;
  }
  struct observation current;
  int result = assert_parent_binding(&target, &operation->parent);
  if (result == 0) {
    result = observe_target(&target, &operation->request, &current);
  }
  if (result < 0) {
    close_parent(&target);
    return -1;
  }
  if (operation->effect == EFFECT_NOOP) {
    if (!current.present || !same_prior(&current.state, &operation->prior)
        || !current.exact_content
        || !exact_published_target(&current, &operation->request)) {
      result = fail("pending_effect_uncertain");
    }
  } else {
    if (operation->published_recorded) {
      if (!current.present || !current.exact_content
          || !same_prior(&current.state, &operation->published)
          || fsync(target.parent_descriptor) < 0) {
        result = failure_code == NULL ? fail("pending_effect_uncertain") : -1;
      }
    } else if (!current.present) {
      if (publish_operation(state, &target, operation) < 0) {
        result = -1;
      }
    } else {
      result = fail("pending_effect_uncertain");
    }
  }
  if (result == 0 && assert_parent_binding(&target, &operation->parent) < 0) {
    result = -1;
  }
  if (result == 0) {
    result = append_commit(state, operation);
  }
  close_parent(&target);
  return result;
}

static int recover_pending(struct broker_state *state) {
  if (state->operation_count == 0U) {
    return 0;
  }
  struct operation *last = &state->operations[state->operation_count - 1U];
  return last->committed ? 0 : recover_pending_operation(state, last);
}

static int new_operation(struct broker_state *state, const struct request *request,
                         struct operation **created) {
  if (state->operation_count >= MAX_OPERATIONS) {
    return fail("too_many_operations");
  }
  if (request->expected_generation != state->generation) {
    return fail("generation_mismatch");
  }
  struct parent_target target = {.parent_descriptor = -1};
  if (resolve_parent(request, &target) < 0) {
    return -1;
  }
  struct operation operation;
  memset(&operation, 0, sizeof(operation));
  operation.request = *request;
  if (read_directory_identity(target.parent_descriptor, &operation.parent) < 0
      || assert_parent_binding(&target, &operation.parent) < 0) {
    close_parent(&target);
    return -1;
  }
  struct observation observed;
  if (observe_target(&target, request, &observed) < 0
      || assert_parent_binding(&target, &operation.parent) < 0) {
    close_parent(&target);
    return -1;
  }
  if (observed.present && !observed.exact_content) {
    close_parent(&target);
    return fail("replace_unsupported");
  }
  if (observed.present && !exact_published_target(&observed, request)) {
    close_parent(&target);
    return fail("noop_metadata_refused");
  }
  operation.prior = observed.state;
  operation.effect = observed.present ? EFFECT_NOOP : EFFECT_CREATED;
  struct bytes prepare = {0};
  if (build_prepare_payload(&operation, &prepare) < 0
      || append_journal_record(state, (uint8_t)'P', prepare.data, prepare.length) < 0) {
    free(prepare.data);
    close_parent(&target);
    return -1;
  }
  free(prepare.data);
  state->operations[state->operation_count] = operation;
  struct operation *stored = &state->operations[state->operation_count];
  ++state->operation_count;
  int result = 0;
  if (test_phase_barrier("post-P") < 0) {
    result = -1;
  } else if (stored->effect == EFFECT_NOOP) {
    if (assert_parent_binding(&target, &stored->parent) < 0
        || assert_target_matches_prior(&target, stored) < 0) {
      result = -1;
    }
  } else if (publish_operation(state, &target, stored) < 0) {
    result = -1;
  }
  if (result == 0 && assert_parent_binding(&target, &stored->parent) < 0) {
    result = -1;
  }
  if (result == 0) {
    result = append_commit(state, stored);
  }
  close_parent(&target);
  if (result == 0) {
    *created = stored;
  }
  return result;
}

static int process_request(struct broker_state *state, const struct request *request) {
  struct operation *operation = operation_by_id(state, request->operation_id);
  if (operation != NULL) {
    if (operation->request.frame_length != request->frame_length
        || memcmp(operation->request.frame, request->frame, request->frame_length) != 0) {
      return fail("operation_conflict");
    }
    if (!operation->committed) {
      return fail("pending_operation_unrecovered");
    }
  } else if (new_operation(state, request, &operation) < 0) {
    return -1;
  }
  if (verify_operation_effect(operation) < 0
      || write_all(STDOUT_FILENO, operation->receipt.data, operation->receipt.length) < 0) {
    return fail("receipt_write_failed");
  }
  return 0;
}

/* Observation never publishes or repairs a journal. L is already durable
 * publication evidence; derive its identical receipt without appending C. */
static int observe_request(struct broker_state *state, const struct request *request) {
  struct operation *operation = operation_by_id(state, request->operation_id);
  if (operation == NULL) return fail("operation_not_found");
  if (operation->request.frame_length != request->frame_length
      || memcmp(operation->request.frame, request->frame, request->frame_length) != 0)
    return fail("operation_conflict");
  if (!operation->committed && !operation->published_recorded)
    return fail("pending_effect_uncertain");
  if (verify_operation_effect(operation) < 0) return -1;
  if (!operation->committed && build_receipt(operation, &operation->receipt) < 0)
    return -1;
  return write_all(STDOUT_FILENO, operation->receipt.data, operation->receipt.length);
}

static void destroy_state(struct broker_state *state) {
  for (size_t index = 0; index < state->operation_count; ++index) {
    free(state->operations[index].receipt.data);
    state->operations[index].receipt.data = NULL;
  }
  free(state->journal_image.data);
  state->journal_image.data = NULL;
  free(state->operations);
  state->operations = NULL;
  if (state->journal_descriptor >= 0) {
    (void)close(state->journal_descriptor);
    state->journal_descriptor = -1;
  }
}

int main(int argc, char **argv) {
  bool read_only = argc == 2 && strcmp(argv[1], "--observe") == 0;
  struct bytes input = {0};
  struct request requests[MAX_REQUESTS];
  size_t request_count = 0U;
  struct broker_state state;
  memset(&state, 0, sizeof(state));
  state.journal_descriptor = -1;
  state.operations = calloc(MAX_OPERATIONS, sizeof(*state.operations));
  int result = 0;
  if (state.operations == NULL) {
    result = fail("memory_unavailable");
  } else if (argc != 1 && !read_only) {
    result = fail("arguments_refused");
  } else if (verify_directory_descriptor(ROOT_DESCRIPTOR) < 0
             || verify_directory_descriptor(JOURNAL_DIRECTORY_DESCRIPTOR) < 0
             || verify_directory_separation() < 0
             || read_all_stdin(&input) < 0
             || parse_request_stream(&input, requests, &request_count) < 0
             || open_journal(&state, read_only) < 0) {
    result = -1;
  } else {
    bool v2 = false;
    for (size_t index = 0; index < request_count; ++index)
      if (memcmp(requests[index].frame, "EVWWREQ2", 8U) == 0) v2 = true;
    if ((read_only || v2) && (request_count != 1U || !v2)) {
      result = fail("single_v2_request_required");
    }
    /* New v2 callers cannot trigger recovery for another or changed request. */
    if (result == 0 && v2 && !read_only && state.operation_count > 0U) {
      struct operation *last = &state.operations[state.operation_count - 1U];
      if (!last->committed && (last->request.frame_length != requests[0].frame_length
          || memcmp(last->request.frame, requests[0].frame, requests[0].frame_length) != 0))
        result = fail("operation_conflict");
    }
    if (result == 0 && !read_only && recover_pending(&state) < 0) result = -1;
    for (size_t index = 0; result == 0 && index < request_count; ++index) {
      if ((read_only ? observe_request(&state, &requests[index])
                     : process_request(&state, &requests[index])) < 0) {
        result = -1;
        break;
      }
    }
  }
  destroy_state(&state);
  free(input.data);
  if (result < 0) {
    const char *code = failure_code == NULL ? "internal_failure" : failure_code;
    (void)fprintf(stderr, "workspace_writer_broker:%s\n", code);
    return 65;
  }
  return 0;
}
