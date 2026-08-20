#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const forbiddenLiveDatabase = path.join(root, 'data', 'eva.db');
const oldColumnNames = Object.freeze([
  'execution_key', 'turn_id', 'tool', 'args', 'status', 'started_ts',
  'finished_ts', 'output', 'semantic_hash', 'semantic_binding', 'output_sha256',
  'material_sha256', 'intent_record_sha256', 'completion_record_sha256',
]);
const v2OnlyColumnNames = Object.freeze([
  'execution_seq', 'execution_contract', 'execution_verification',
]);
const executionLedgerTriggerNames = Object.freeze([
  'turn_tool_executions_contract_immutable',
  'turn_tool_executions_intent_immutable',
  'turn_tool_executions_key_immutable',
  'turn_tool_executions_no_delete',
  'turn_tool_executions_no_replace',
  'turn_tool_executions_sequence_immutable',
  'turn_tool_executions_terminal_immutable',
  'turn_tool_executions_valid_contract',
  'turn_tool_executions_valid_sequence',
  'turn_tool_executions_valid_verification',
]);
const production17Columns = Object.freeze([
  ['execution_key', 'TEXT', 0, null, 1], ['turn_id', 'TEXT', 1, null, 0],
  ['tool', 'TEXT', 1, null, 0], ['args', 'TEXT', 0, null, 0],
  ['status', 'TEXT', 1, "'started'", 0], ['started_ts', 'TEXT', 1, null, 0],
  ['finished_ts', 'TEXT', 0, null, 0], ['output', 'TEXT', 0, null, 0],
  ['semantic_hash', 'TEXT', 0, null, 0], ['semantic_binding', 'TEXT', 0, null, 0],
  ['output_sha256', 'TEXT', 0, null, 0], ['material_sha256', 'TEXT', 0, null, 0],
  ['intent_record_sha256', 'TEXT', 0, null, 0],
  ['completion_record_sha256', 'TEXT', 0, null, 0],
  ['execution_seq', 'INTEGER', 1, '0', 0],
  ['execution_contract', 'TEXT', 1, "'ExecutionRecord/v1'", 0],
  ['execution_verification', 'TEXT', 1, "'legacy_unverifiable'", 0],
]);
const production17Objects = Object.freeze([
  ['index', 'idx_turn_tool_executions_sequence', 'turn_tool_executions',
    '91478a37a7006a43fa697df7007a9964ccf56e8eaea2c08de24293f8cb3f49e9'],
  ['index', 'idx_turn_tool_executions_turn', 'turn_tool_executions',
    '553f7fc2fb22438705f8c7170988bdca3c9d58ca51ef2d3621c3b568f06aea6a'],
  ['index', 'sqlite_autoindex_turn_tool_executions_1', 'turn_tool_executions', null],
  ['table', 'turn_tool_executions', 'turn_tool_executions',
    'a1ec1e4d545305b9f12682f8b2321ee4c1409a2b35d5abb22dbebf145290e9c5'],
  ['trigger', 'turn_tool_executions_contract_immutable', 'turn_tool_executions',
    'de2f69112d9d7912e7cbce591ed9a4b4b3de744f8f1ad474c6b34656ff0515f1'],
  ['trigger', 'turn_tool_executions_intent_immutable', 'turn_tool_executions',
    '0ce70de9e2e7be1f1a5b1f8cdb614c2cbd954c267a8eb7cf73a67461eb396e15'],
  ['trigger', 'turn_tool_executions_key_immutable', 'turn_tool_executions',
    'f72bf4b79f464e6b62052ee3458e67db628b2907b705bf90ff9506c7f2cd35d2'],
  ['trigger', 'turn_tool_executions_no_delete', 'turn_tool_executions',
    '01f6528ba583f92719e59c83c1d8ff1b8d2882f7d06b132873b3b156bf1dd833'],
  ['trigger', 'turn_tool_executions_no_replace', 'turn_tool_executions',
    '63f89d7a92a60cca83789c8e938d89aec2cc68dff316fe403ded851a8088ab37'],
  ['trigger', 'turn_tool_executions_sequence_immutable', 'turn_tool_executions',
    'a96643c7e6f3f46f42cc77286fa434258009c030e8f466330fa0a72e0231663b'],
  ['trigger', 'turn_tool_executions_terminal_immutable', 'turn_tool_executions',
    '1adb1c405afdc80e60c8299170355f9f8056f59ac68178d91f67b69c1516f869'],
  ['trigger', 'turn_tool_executions_valid_contract', 'turn_tool_executions',
    '25818af22ffde9abe6af4f5c23ce882eff966a2aa7659d32c144729976cb607c'],
  ['trigger', 'turn_tool_executions_valid_sequence', 'turn_tool_executions',
    'ee0e1ed2e269720231db1adc7fcd68ceb08a48eabc2842a738053ab82c336ff5'],
  ['trigger', 'turn_tool_executions_valid_verification', 'turn_tool_executions',
    'bfd213150ab2b331f26476ec55ce16aa17c5205a14aeed32c201e94f04451b0b'],
]);
const production17IndexSignatures = Object.freeze({
  idx_turn_tool_executions_sequence: Object.freeze([
    [0, 14, 'execution_seq', 0, 'BINARY', 1], [1, -1, null, 0, 'BINARY', 0],
  ]),
  idx_turn_tool_executions_turn: Object.freeze([
    [0, 1, 'turn_id', 0, 'BINARY', 1], [1, 2, 'tool', 0, 'BINARY', 1],
    [2, -1, null, 0, 'BINARY', 0],
  ]),
  sqlite_autoindex_turn_tool_executions_1: Object.freeze([
    [0, 0, 'execution_key', 0, 'BINARY', 1], [1, -1, null, 0, 'BINARY', 0],
  ]),
});
const production14IndexSignatures = Object.freeze({
  idx_turn_tool_executions_turn: production17IndexSignatures.idx_turn_tool_executions_turn,
  sqlite_autoindex_turn_tool_executions_1:
    production17IndexSignatures.sqlite_autoindex_turn_tool_executions_1,
});
const production14TableSql = `CREATE TABLE turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT
, semantic_hash TEXT, semantic_binding TEXT, output_sha256 TEXT, material_sha256 TEXT, intent_record_sha256 TEXT, completion_record_sha256 TEXT)`;
const production14IndexSql = `CREATE INDEX idx_turn_tool_executions_turn
  ON turn_tool_executions(turn_id, tool)`;
const recoverySourceTable = 'turn_tool_executions_v2_recovery_source';
const allowedExecutionStatuses = new Set([
  'started', 'done', 'transport_sending', 'transport_accepted',
  'transport_retryable', 'transport_rejected', 'transport_uncertain',
]);
const telegramLeafTools = new Set([
  '@transport/telegram.sendMessage',
  '@transport/telegram.sendPhoto',
  '@transport/telegram.sendAudio',
]);
const realTemporaryRoot = fs.realpathSync.native('/tmp');
const maximumSourceBytes = 512 * 1024 * 1024;
const maximumLedgerRows = 100_000;
const maximumNonLedgerRows = 1_000_000;
const maximumSchemaObjects = 4_096;
const maximumTables = 512;
const maximumSystemExecutableBytes = 64 * 1024 * 1024;
const maximumHelperArtifactBytes = 16 * 1024 * 1024;
const publicationReserveMarginBytes = 64 * 1024 * 1024;
const compilerTimeoutMs = 30_000;
const publisherProtocolTimeoutMs = 30_000;
const publisherMaximumProtocolBytes = 64 * 1024;
const openAnonymousTemporaryFile = 0o20000000 | fs.constants.O_DIRECTORY;
const absoluteCompilerAnchor = '/usr/bin/cc';
const absoluteAssemblerAnchor = '/usr/bin/as';
const absoluteLinkerAnchor = '/usr/bin/ld.bfd';
const publicationHelperSource = String.raw`#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

typedef struct {
  uint32_t state[8];
  uint64_t bits;
  unsigned char block[64];
  size_t used;
} sha256_ctx;

static const uint32_t K[64] = {
  0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
  0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
  0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
  0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
  0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
  0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
  0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
  0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
};

static uint32_t rotr(uint32_t x, unsigned n) { return (x >> n) | (x << (32U - n)); }
static void sha_block(sha256_ctx *c, const unsigned char *p) {
  uint32_t w[64], a,b,d,e,f,g,h,i,t1,t2,cc;
  for (i=0;i<16;i++) w[i]=((uint32_t)p[4*i]<<24)|((uint32_t)p[4*i+1]<<16)|((uint32_t)p[4*i+2]<<8)|p[4*i+3];
  for (;i<64;i++) { uint32_t x=w[i-15],y=w[i-2]; w[i]=(rotr(x,7)^rotr(x,18)^(x>>3))+w[i-16]+(rotr(y,17)^rotr(y,19)^(y>>10))+w[i-7]; }
  a=c->state[0]; b=c->state[1]; cc=c->state[2]; d=c->state[3]; e=c->state[4]; f=c->state[5]; g=c->state[6]; h=c->state[7];
  for (i=0;i<64;i++) { t1=h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^((~e)&g))+K[i]+w[i]; t2=(rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&cc)^(b&cc)); h=g; g=f; f=e; e=d+t1; d=cc; cc=b; b=a; a=t1+t2; }
  c->state[0]+=a; c->state[1]+=b; c->state[2]+=cc; c->state[3]+=d; c->state[4]+=e; c->state[5]+=f; c->state[6]+=g; c->state[7]+=h;
}
static void sha_init(sha256_ctx *c) { uint32_t s[8]={0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U}; memcpy(c->state,s,sizeof(s)); c->bits=0; c->used=0; }
static void sha_update(sha256_ctx *c,const unsigned char *p,size_t n) { c->bits+=(uint64_t)n*8U; while(n){ size_t take=64-c->used; if(take>n)take=n; memcpy(c->block+c->used,p,take); c->used+=take;p+=take;n-=take;if(c->used==64){sha_block(c,c->block);c->used=0;}} }
static void sha_final(sha256_ctx *c,unsigned char out[32]) { uint64_t bits=c->bits; c->block[c->used++]=0x80; if(c->used>56){while(c->used<64)c->block[c->used++]=0;sha_block(c,c->block);c->used=0;} while(c->used<56)c->block[c->used++]=0; for(int i=7;i>=0;i--)c->block[c->used++]=(unsigned char)(bits>>(8*i)); sha_block(c,c->block); for(int i=0;i<8;i++){out[4*i]=(unsigned char)(c->state[i]>>24);out[4*i+1]=(unsigned char)(c->state[i]>>16);out[4*i+2]=(unsigned char)(c->state[i]>>8);out[4*i+3]=(unsigned char)c->state[i];} }
static void hex_digest(const unsigned char in[32],char out[65]) { static const char h[]="0123456789abcdef"; for(int i=0;i<32;i++){out[2*i]=h[in[i]>>4];out[2*i+1]=h[in[i]&15];}out[64]=0; }
static int write_all(int fd,const unsigned char *p,size_t n){while(n){ssize_t w=write(fd,p,n);if(w<0&&errno==EINTR)continue;if(w<=0)return -1;p+=w;n-=(size_t)w;}return 0;}
static int read_byte(int fd,unsigned char *c){for(;;){ssize_t n=read(fd,c,1);if(n<0&&errno==EINTR)continue;return n==1?0:-1;}}
static int fail(const char *where){dprintf(2,"helper error: %s errno=%d %s\n",where,errno,strerror(errno));return 1;}
static int same_inode(const struct stat *a,const struct stat *b){return a->st_dev==b->st_dev&&a->st_ino==b->st_ino;}
static int same_stable_file(const struct stat *a,const struct stat *b){
  return same_inode(a,b)&&a->st_nlink==b->st_nlink&&a->st_uid==b->st_uid&&a->st_gid==b->st_gid&&a->st_mode==b->st_mode&&a->st_size==b->st_size&&a->st_mtim.tv_sec==b->st_mtim.tv_sec&&a->st_mtim.tv_nsec==b->st_mtim.tv_nsec&&a->st_ctim.tv_sec==b->st_ctim.tv_sec&&a->st_ctim.tv_nsec==b->st_ctim.tv_nsec;
}
static int hash_exact_fd(int fd,uint64_t expected,const struct stat *baseline,char hex[65]){
  struct stat before,after;if(fstat(fd,&before)<0||!same_stable_file(baseline,&before)){errno=ESTALE;return -1;}
  sha256_ctx c;sha_init(&c);unsigned char buf[1048576];uint64_t position=0;
  while(position<expected){size_t want=expected-position>sizeof(buf)?sizeof(buf):(size_t)(expected-position);ssize_t n=pread(fd,buf,want,(off_t)position);if(n<0&&errno==EINTR)continue;if(n<=0){errno=ENODATA;return -1;}sha_update(&c,buf,(size_t)n);position+=(uint64_t)n;}
  unsigned char trailing;ssize_t probe;do{probe=pread(fd,&trailing,1,(off_t)expected);}while(probe<0&&errno==EINTR);if(probe<0)return -1;if(probe!=0){errno=EFBIG;return -1;}
  if(fstat(fd,&after)<0||!same_stable_file(baseline,&after)){errno=ESTALE;return -1;}
  unsigned char digest[32];sha_final(&c,digest);hex_digest(digest,hex);return 0;
}
static int absent_at(int parentfd,const char *name){struct stat st;if(fstatat(parentfd,name,&st,AT_SYMLINK_NOFOLLOW)==0){errno=EEXIST;return -1;}return errno==ENOENT?0:-1;}
static int sidecars_absent(int parentfd,const char *name){
  static const char *suffixes[]={"-wal","-shm","-journal"};char candidate[4096];
  for(size_t i=0;i<sizeof(suffixes)/sizeof(suffixes[0]);i++){
    int written=snprintf(candidate,sizeof(candidate),"%s%s",name,suffixes[i]);
    if(written<0||(size_t)written>=sizeof(candidate)){errno=ENAMETOOLONG;return -1;}
    if(absent_at(parentfd,candidate)<0)return -1;
  }
  return 0;
}
static int cleanup_build_authority(int helperdirfd,int tmprootfd,const char *dirname){
  const int sourcefd=6,binaryfd=7;struct stat dir,root,source,binary,named,source_after,binary_after,dir_after;
  if(!dirname[0]||strchr(dirname,'/')||!strcmp(dirname,".")||!strcmp(dirname,"..")){errno=EINVAL;return -1;}
  if(fstat(helperdirfd,&dir)<0||fstat(tmprootfd,&root)<0||fstat(sourcefd,&source)<0||fstat(binaryfd,&binary)<0)return -1;
  if(!S_ISDIR(dir.st_mode)||!S_ISDIR(root.st_mode)||(dir.st_mode&0777)!=0700||dir.st_uid!=geteuid()||!S_ISREG(source.st_mode)||!S_ISREG(binary.st_mode)||(source.st_mode&0777)!=0400||(binary.st_mode&0777)!=0500||source.st_uid!=geteuid()||binary.st_uid!=geteuid()||source.st_nlink!=1||binary.st_nlink!=1){errno=EPERM;return -1;}
  if(fstatat(tmprootfd,dirname,&named,AT_SYMLINK_NOFOLLOW)<0||!same_inode(&dir,&named)){errno=ESTALE;return -1;}
  if(fstatat(helperdirfd,"publisher.c",&named,AT_SYMLINK_NOFOLLOW)<0||!same_inode(&source,&named)){errno=ESTALE;return -1;}
  if(fstatat(helperdirfd,"publisher",&named,AT_SYMLINK_NOFOLLOW)<0||!same_inode(&binary,&named)){errno=ESTALE;return -1;}
  if(unlinkat(helperdirfd,"publisher.c",0)<0||unlinkat(helperdirfd,"publisher",0)<0||fsync(helperdirfd)<0)return -1;
  if(absent_at(helperdirfd,"publisher.c")<0||absent_at(helperdirfd,"publisher")<0)return -1;
  if(fstat(sourcefd,&source_after)<0||fstat(binaryfd,&binary_after)<0)return -1;
  if(!same_inode(&source,&source_after)||!same_inode(&binary,&binary_after)||source_after.st_nlink!=0||binary_after.st_nlink!=0||source.st_uid!=source_after.st_uid||source.st_gid!=source_after.st_gid||source.st_mode!=source_after.st_mode||source.st_size!=source_after.st_size||binary.st_uid!=binary_after.st_uid||binary.st_gid!=binary_after.st_gid||binary.st_mode!=binary_after.st_mode||binary.st_size!=binary_after.st_size){errno=ESTALE;return -1;}
  if(unlinkat(tmprootfd,dirname,AT_REMOVEDIR)<0||fsync(tmprootfd)<0)return -1;
  if(absent_at(tmprootfd,dirname)<0)return -1;
  if(fstat(helperdirfd,&dir_after)<0||!same_inode(&dir,&dir_after)||dir_after.st_nlink!=0){errno=ESTALE;return -1;}
  dprintf(1,"CLEANED %ju %ju %ju %ju %ju %ju %ju %ju %ju\n",(uintmax_t)source.st_dev,(uintmax_t)source.st_ino,(uintmax_t)source_after.st_nlink,(uintmax_t)binary.st_dev,(uintmax_t)binary.st_ino,(uintmax_t)binary_after.st_nlink,(uintmax_t)dir.st_dev,(uintmax_t)dir.st_ino,(uintmax_t)dir_after.st_nlink);
  return 0;
}

int main(int argc,char **argv){
  if(argc!=5)return fail("arguments");
  const int parentfd=3,helperdirfd=4,tmprootfd=5; const char *name=argv[1]; char *end=NULL;
  if(!name[0]||strchr(name,'/')||!strcmp(name,".")||!strcmp(name,"..")){errno=EINVAL;return fail("target name");}
  errno=0; uint64_t expected=strtoull(argv[2],&end,10); if(errno||!end||*end||expected==0||expected>536870912U){errno=EINVAL;return fail("size");}
  const char *expected_hash=argv[3]; if(strlen(expected_hash)!=64){errno=EINVAL;return fail("hash");}
  if(cleanup_build_authority(helperdirfd,tmprootfd,argv[4])<0)return fail("cleanup build authority");
  if(absent_at(parentfd,name)<0)return fail("target exists before publication");
  if(sidecars_absent(parentfd,name)<0)return fail("target sidecar exists before publication");
  int tmpfd=openat(parentfd,".",O_TMPFILE|O_RDWR|O_CLOEXEC,0600);
  if(tmpfd<0)return fail("openat O_TMPFILE");
  if(fchmod(tmpfd,0600)<0){close(tmpfd);return fail("fchmod tmp");}
  sha256_ctx c; sha_init(&c); unsigned char buf[1048576]; uint64_t left=expected;
  while(left){size_t want=left>sizeof(buf)?sizeof(buf):(size_t)left;ssize_t n=read(0,buf,want);if(n<0&&errno==EINTR)continue;if(n<=0){close(tmpfd);return fail("input bytes");}if(write_all(tmpfd,buf,(size_t)n)<0){close(tmpfd);return fail("write tmp");}sha_update(&c,buf,(size_t)n);left-=(uint64_t)n;}
  unsigned char digest[32];char hex[65];sha_final(&c,digest);hex_digest(digest,hex);if(strcmp(hex,expected_hash)){errno=EBADMSG;close(tmpfd);return fail("input hash");}
  if(fsync(tmpfd)<0){close(tmpfd);return fail("fsync tmp");}
  struct stat st;if(fstat(tmpfd,&st)<0){close(tmpfd);return fail("fstat tmp");}if(!S_ISREG(st.st_mode)||(st.st_mode&0777)!=0600||st.st_uid!=geteuid()||(uint64_t)st.st_size!=expected||st.st_nlink!=0){errno=EBADMSG;close(tmpfd);return fail("tmp identity");}
  if(hash_exact_fd(tmpfd,expected,&st,hex)<0){close(tmpfd);return fail("exact tmp readback");}if(strcmp(hex,expected_hash)){errno=EBADMSG;close(tmpfd);return fail("readback hash");}
  if(absent_at(parentfd,name)<0){close(tmpfd);return fail("target exists before release");}
  if(sidecars_absent(parentfd,name)<0){close(tmpfd);return fail("target sidecar exists before release");}
  dprintf(1,"VERIFIED %ju %ju %ju %jd %s\n",(uintmax_t)st.st_dev,(uintmax_t)st.st_ino,(uintmax_t)st.st_nlink,(intmax_t)st.st_size,hex);
  unsigned char command;if(read_byte(0,&command)<0||(command!='L'&&command!='K'&&command!='D')){errno=ECANCELED;close(tmpfd);return fail("link release");}
  if(command=='D')_exit(94);
  if(absent_at(parentfd,name)<0){dprintf(1,"UNLINKED\n");close(tmpfd);return fail("target exists at link");}
  if(sidecars_absent(parentfd,name)<0){dprintf(1,"UNLINKED\n");close(tmpfd);return fail("target sidecar exists at link");}
  if(linkat(tmpfd,"",parentfd,name,AT_EMPTY_PATH)<0){dprintf(1,"UNLINKED\n");close(tmpfd);return fail("linkat AT_EMPTY_PATH");}
  if(fsync(parentfd)<0){close(tmpfd);return fail("fsync parent");}
  if(fstat(tmpfd,&st)<0){close(tmpfd);return fail("fstat linked");}if((st.st_mode&0777)!=0600||st.st_uid!=geteuid()||st.st_nlink!=1||(uint64_t)st.st_size!=expected){errno=EBADMSG;close(tmpfd);return fail("linked identity");}
  if(sidecars_absent(parentfd,name)<0){close(tmpfd);return fail("target sidecar exists after link");}
  if(command=='K')_exit(93);
  dprintf(1,"LINKED %ju %ju %ju %jd %s\n",(uintmax_t)st.st_dev,(uintmax_t)st.st_ino,(uintmax_t)st.st_nlink,(intmax_t)st.st_size,hex);
  if(read_byte(0,&command)<0||command!='A'){errno=ECANCELED;close(tmpfd);return fail("parent acknowledgement");}
  if(fstat(tmpfd,&st)<0){close(tmpfd);return fail("final fstat");}if((st.st_mode&0777)!=0600||st.st_uid!=geteuid()||st.st_nlink!=1||(uint64_t)st.st_size!=expected){errno=EBADMSG;close(tmpfd);return fail("final identity");}
  if(hash_exact_fd(tmpfd,expected,&st,hex)<0){close(tmpfd);return fail("exact final readback");}if(strcmp(hex,expected_hash)){errno=EBADMSG;close(tmpfd);return fail("final hash");}
  dprintf(1,"DONE %ju %ju %ju %jd %s\n",(uintmax_t)st.st_dev,(uintmax_t)st.st_ino,(uintmax_t)st.st_nlink,(intmax_t)st.st_size,hex);close(tmpfd);return 0;
}`;

function canonicalJson(value) {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item) ?? 'null').join(',')}]`;
  }
  return `{${Object.keys(value).sort().filter((key) => value[key] !== undefined)
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

let descriptorHashTestHookUsed = false;

function sha256ExactDescriptor(descriptor, expectedStat, { maximumBytes, label }) {
  if (expectedStat == null || typeof expectedStat.size !== 'bigint'
    || expectedStat.size <= 0n || expectedStat.size > BigInt(maximumBytes)) {
    throw new Error(`${label} descriptor exact-hash size is unsupported`);
  }
  const exactSize = Number(expectedStat.size);
  if (!Number.isSafeInteger(exactSize)) {
    throw new Error(`${label} descriptor exact-hash size is not safely addressable`);
  }
  const before = fs.fstatSync(descriptor, { bigint: true });
  if (!before.isFile() || !sameStableFileStat(expectedStat, before)) {
    throw new Error(`${label} descriptor authority changed before exact hash`);
  }
  const hash = createHash('sha256');
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  let position = 0;
  while (position < exactSize) {
    const bytes = fs.readSync(
      descriptor, buffer, 0, Math.min(buffer.length, exactSize - position), position,
    );
    if (bytes === 0) throw new Error(`${label} descriptor truncated during exact hash`);
    hash.update(buffer.subarray(0, bytes));
    position += bytes;
    if (!descriptorHashTestHookUsed) {
      descriptorHashTestHookUsed = true;
      waitForSynchronousTestHook(
        'EVA_LEDGER_RECOVERY_TEST_HOOK_HASH_FIRST_CHUNK',
        canonicalJson({ position, descriptorSize: exactSize }),
      );
    }
  }
  const trailing = Buffer.allocUnsafe(1);
  if (fs.readSync(descriptor, trailing, 0, 1, exactSize) !== 0) {
    throw new Error(`${label} descriptor grew during exact hash`);
  }
  const after = fs.fstatSync(descriptor, { bigint: true });
  if (!sameStableFileStat(expectedStat, after)) {
    throw new Error(`${label} descriptor authority changed during exact hash`);
  }
  return hash.digest('hex');
}

function readDescriptorBuffer(descriptor, { maximumBytes = maximumSourceBytes } = {}) {
  const stat = fs.fstatSync(descriptor);
  if (!Number.isSafeInteger(stat.size) || stat.size <= 0 || stat.size > maximumBytes) {
    throw new Error('database descriptor has an unsupported size');
  }
  const value = Buffer.allocUnsafe(stat.size);
  let position = 0;
  while (position < value.length) {
    const bytes = fs.readSync(
      descriptor, value, position, Math.min(1024 * 1024, value.length - position), position,
    );
    if (bytes === 0) throw new Error('database descriptor was truncated during read');
    position += bytes;
  }
  const trailing = Buffer.allocUnsafe(1);
  if (fs.readSync(descriptor, trailing, 0, 1, position) !== 0) {
    throw new Error('database descriptor grew during read');
  }
  return value;
}

function sqliteJournalHeader(bytes, label) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 100
    || bytes.subarray(0, 16).toString('binary') !== 'SQLite format 3\0') {
    throw new Error(`${label} has an invalid SQLite header`);
  }
  if (bytes[18] === 1 && bytes[19] === 1) return 'rollback';
  if (bytes[18] === 2 && bytes[19] === 2) return 'wal';
  throw new Error(`${label} has an invalid journal header`);
}

function normalizedSql(value) {
  const source = String(value || '');
  const literals = [];
  let syntax = '';
  for (let index = 0; index < source.length;) {
    const quote = source[index];
    const lineComment = source.startsWith('--', index);
    const blockComment = source.startsWith('/*', index);
    if (!lineComment && !blockComment && !["'", '"', '`', '['].includes(quote)) {
      syntax += quote; index += 1; continue;
    }
    const start = index;
    if (lineComment) {
      index += 2;
      while (index < source.length && !['\r', '\n'].includes(source[index])) index += 1;
    } else if (blockComment) {
      const end = source.indexOf('*/', index + 2);
      index = end < 0 ? source.length : end + 2;
    } else {
      const closing = quote === '[' ? ']' : quote;
      index += 1;
      while (index < source.length) {
        if (source[index] !== closing) { index += 1; continue; }
        if (source[index + 1] === closing) { index += 2; continue; }
        index += 1;
        break;
      }
    }
    const literal = literals.push(source.slice(start, index)) - 1;
    syntax += `\u{e000}${literal}\u{e001}`;
  }
  const normalized = syntax.replace(/\bmain\./gu, '').replace(/;\s*$/u, '').replace(/\s+/gu, ' ')
    .replace(/\s*([(),=])\s*/gu, '$1').trim().toLowerCase();
  return normalized.replace(/\u{e000}(\d+)\u{e001}/gu, (
    _match, literal,
  ) => literals[Number(literal)]);
}

function parseArguments(argv) {
  const options = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const option = argv[index];
    const value = argv[index + 1];
    if (!['--source', '--target'].includes(option) || value == null || options.has(option)) {
      throw new Error('usage: recovery --source <existing-snapshot.db> --target <new-file.db>');
    }
    options.set(option, value);
  }
  if (argv.length !== 4 || !options.get('--source') || !options.get('--target')) {
    throw new Error('usage: recovery --source <existing-snapshot.db> --target <new-file.db>');
  }
  return { source: path.resolve(options.get('--source')), target: path.resolve(options.get('--target')) };
}

function pathExists(filename) {
  try { fs.lstatSync(filename); return true; } catch (error) {
    if (error?.code === 'ENOENT') return false;
    throw error;
  }
}

function descriptorPath(descriptor, suffix = '') {
  return `/proc/${process.pid}/fd/${descriptor}${suffix}`;
}

function strictDescendant(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative);
}

function nearestExistingPath(candidate) {
  let current = path.resolve(candidate);
  while (!pathExists(current)) {
    const parent = path.dirname(current);
    if (parent === current) throw new Error('path has no existing ancestor');
    current = parent;
  }
  return current;
}

function projectedRealPath(candidate) {
  const absolute = path.resolve(candidate);
  const existing = nearestExistingPath(absolute);
  return path.resolve(fs.realpathSync.native(existing), path.relative(existing, absolute));
}

function waitForSynchronousTestHook(variableName, payload = 'ready') {
  const hook = process.env[variableName];
  if (!hook || process.env.NODE_TEST_CONTEXT == null) return;
  fs.writeFileSync(hook, String(payload), { flag: 'wx', mode: 0o600 });
  const signal = new Int32Array(new SharedArrayBuffer(4));
  while (pathExists(hook)) Atomics.wait(signal, 0, 0, 5);
}

function openAuthorities(sourceArgument, targetArgument) {
  if (path.resolve(sourceArgument) === path.resolve(forbiddenLiveDatabase)) {
    throw new Error('refusing canonical live database source');
  }
  const projectedSource = projectedRealPath(sourceArgument);
  const projectedTarget = projectedRealPath(targetArgument);
  if (!strictDescendant(realTemporaryRoot, projectedSource)
    || !strictDescendant(realTemporaryRoot, projectedTarget)) {
    throw new Error('source and target must be strict real /tmp descendants');
  }
  waitForSynchronousTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_PARENT_PREOPEN');
  let sourceDescriptor;
  let sourceParentDescriptor;
  let sourceParentStat;
  let parentDescriptor;
  const effectiveUser = typeof process.geteuid === 'function' ? process.geteuid() : null;
  const sourceParentArgument = path.dirname(sourceArgument);
  const sourceName = path.basename(sourceArgument);
  try {
    sourceParentDescriptor = fs.openSync(
      sourceParentArgument,
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
    );
    sourceParentStat = fs.fstatSync(sourceParentDescriptor, { bigint: true });
    if ((effectiveUser != null && sourceParentStat.uid !== BigInt(effectiveUser))
      || (sourceParentStat.mode & 0o077n) !== 0n) {
      throw new Error('source parent must be private and owned by the current user');
    }
    const actualSourceParent = fs.realpathSync.native(descriptorPath(sourceParentDescriptor));
    if (actualSourceParent !== path.dirname(projectedSource)) {
      throw new Error('source parent authority changed before open');
    }
    if (sourceName !== path.basename(projectedSource)) {
      throw new Error('source basename changed before open');
    }
    sourceDescriptor = fs.openSync(
      `${descriptorPath(sourceParentDescriptor)}/${sourceName}`,
      fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW,
    );
  } catch (error) {
    if (sourceDescriptor != null) fs.closeSync(sourceDescriptor);
    if (sourceParentDescriptor != null) fs.closeSync(sourceParentDescriptor);
    if (error?.code === 'ELOOP') {
      throw new Error('source must be a regular, non-symlink database snapshot');
    }
    throw error;
  }
  const sourceStat = fs.fstatSync(sourceDescriptor, { bigint: true });
  if (!sourceStat.isFile()) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    throw new Error('source must be a regular, non-symlink database snapshot');
  }
  if (sourceStat.nlink !== 1n) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    throw new Error('source must not be a hardlinked database snapshot');
  }
  if ((effectiveUser != null && sourceStat.uid !== BigInt(effectiveUser))
    || (sourceStat.mode & 0o077n) !== 0n) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    throw new Error('source must be private and owned by the current user');
  }
  const source = fs.realpathSync.native(descriptorPath(sourceDescriptor));
  if (source !== projectedSource) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    throw new Error('source file authority changed before read');
  }
  let liveStat = null;
  try { liveStat = fs.statSync(forbiddenLiveDatabase, { bigint: true }); } catch (error) {
    if (error?.code !== 'ENOENT') {
      fs.closeSync(sourceDescriptor);
      fs.closeSync(sourceParentDescriptor);
      throw new Error(`cannot safely identify the live database: ${error?.message || error}`);
    }
  }
  if (source === fs.realpathSync(root) + `${path.sep}data${path.sep}eva.db`
    || (liveStat && sourceStat.dev === liveStat.dev && sourceStat.ino === liveStat.ino)) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    throw new Error('refusing canonical live database source');
  }
  try {
    waitForSynchronousTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_TARGET_PARENT_PREOPEN');
    parentDescriptor = fs.openSync(
      path.dirname(targetArgument),
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
    );
    const targetParent = fs.realpathSync.native(descriptorPath(parentDescriptor));
    if (targetParent !== path.dirname(projectedTarget)) {
      throw new Error('target parent authority changed before open');
    }
    const parentStat = fs.fstatSync(parentDescriptor, { bigint: true });
    if (!parentStat.isDirectory()) throw new Error('target parent is not a directory');
    if ((effectiveUser != null && parentStat.uid !== BigInt(effectiveUser))
      || (parentStat.mode & 0o077n) !== 0n) {
      throw new Error('target parent must be private and owned by the current user');
    }
    const targetName = path.basename(targetArgument);
    if (!targetName || ['.', '..'].includes(targetName)) throw new Error('invalid target basename');
    if (targetName !== path.basename(projectedTarget)) {
      throw new Error('target basename changed before open');
    }
    const target = `${descriptorPath(parentDescriptor)}/${targetName}`;
    if (pathExists(target)) throw new Error('target already exists');
    if (sourceParentStat.dev === parentStat.dev && sourceParentStat.ino === parentStat.ino) {
      throw new Error('source and target parents must differ');
    }
    if ([sourceName, `${sourceName}-wal`, `${sourceName}-shm`, `${sourceName}-journal`]
      .includes(targetName)) {
      throw new Error('target basename conflicts with source authority');
    }
    for (const suffix of ['-wal', '-shm', '-journal']) {
      if (pathExists(`${descriptorPath(sourceParentDescriptor)}/${sourceName}${suffix}`)) {
        throw new Error(`source is not self-contained: ${suffix.slice(1)} sidecar exists`);
      }
    }
    return {
      sourceDescriptor, sourceStat,
      sourceName, sourceParentDescriptor, sourceParentStat,
      target, targetName, parentDescriptor, parentStat,
      parentAuthority: descriptorPath(parentDescriptor), effectiveUser,
    };
  } catch (error) {
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    if (parentDescriptor != null) fs.closeSync(parentDescriptor);
    throw error;
  }
}

function tableRowsFingerprint(database, tableName, columns, {
  rowidAlias = null, maximumRows = maximumNonLedgerRows,
} = {}) {
  const quotedTable = `"${tableName.replaceAll('"', '""')}"`;
  const rowCount = Number(database.prepare(
    `SELECT count(*) AS count FROM main.${quotedTable}`,
  ).get().count);
  if (!Number.isSafeInteger(rowCount) || rowCount < 0 || rowCount > maximumRows) {
    throw new Error(`table ${tableName} row cap exceeded: ${rowCount}`);
  }
  const valueProjection = (expression, index) => [
    `typeof(${expression}) AS "__recovery_type_${index}"`,
    `hex(CAST(${expression} AS BLOB)) AS "__recovery_bytes_${index}"`,
  ];
  const rowidProjection = rowidAlias == null ? [] : valueProjection(
    `"${rowidAlias.replaceAll('"', '""')}"`, 'rowid',
  );
  const columnProjection = columns.flatMap((name, index) => valueProjection(
    `"${name.replaceAll('"', '""')}"`, index,
  ));
  const projection = [...rowidProjection, ...columnProjection].join(',');
  const hashes = [];
  for (const row of database.prepare(`SELECT ${projection} FROM main.${quotedTable}`).iterate()) {
    const identity = rowidAlias == null ? [] : [[
      String(row.__recovery_type_rowid), String(row.__recovery_bytes_rowid),
    ]];
    hashes.push(sha256(canonicalJson([
      ...identity, ...columns.map((_name, index) => [
        String(row[`__recovery_type_${index}`]),
        String(row[`__recovery_bytes_${index}`]),
      ]),
    ])));
  }
  if (hashes.length !== rowCount) throw new Error(`table ${tableName} changed during fingerprint`);
  hashes.sort();
  return Object.freeze({ count: hashes.length, sha256: sha256(canonicalJson(hashes)) });
}

function ledgerRowsManifest(database) {
  return tableRowsFingerprint(database, 'turn_tool_executions', oldColumnNames, {
    maximumRows: maximumLedgerRows,
  });
}

function persistentPragmas(database, intendedJournalMode = null) {
  return Object.freeze({
    encoding: String(database.pragma('encoding', { simple: true })),
    pageSize: String(database.pragma('page_size', { simple: true })),
    autoVacuum: String(database.pragma('auto_vacuum', { simple: true })),
    userVersion: String(database.pragma('user_version', { simple: true })),
    applicationId: String(database.pragma('application_id', { simple: true })),
    journalMode: intendedJournalMode
      || String(database.pragma('journal_mode', { simple: true })).toLowerCase(),
    schemaVersion: String(database.pragma('schema_version', { simple: true })),
  });
}

function stablePersistentPragmas(pragmas) {
  return Object.freeze({
    encoding: pragmas.encoding,
    pageSize: pragmas.pageSize,
    autoVacuum: pragmas.autoVacuum,
    userVersion: pragmas.userVersion,
    applicationId: pragmas.applicationId,
  });
}

function nonLedgerFingerprints(database) {
  const mainTableRows = database.prepare(`SELECT name,type,sql FROM main.sqlite_master
    WHERE type IN ('table','view') ORDER BY name`).all().map((row) => Object.freeze({
    name: String(row.name), type: String(row.type),
    sql: row.sql == null ? null : String(row.sql),
  }));
  const tableListRows = [];
  for (const row of database.prepare('PRAGMA table_list').iterate()) {
    const entry = Object.freeze({
      schema: String(row.schema), name: String(row.name), type: String(row.type),
      wr: Number(row.wr), ncol: Number(row.ncol), strict: Number(row.strict),
    });
    if ((entry.schema === 'main' && entry.name === 'sqlite_schema')
      || (entry.schema === 'temp' && entry.name === 'sqlite_temp_schema')) continue;
    if (entry.schema !== 'main') {
      throw new Error(`unaccounted table_list entry: ${canonicalJson(entry)}`);
    }
    tableListRows.push(entry);
    if (tableListRows.length > maximumSchemaObjects) {
      throw new Error(`non-ledger table_list cap exceeded: ${tableListRows.length}`);
    }
  }
  if (mainTableRows.length !== tableListRows.length) {
    throw new Error(`sqlite_master/table_list table authority count differs: ${canonicalJson({
      sqliteMaster: mainTableRows.map((entry) => entry.name),
      tableList: tableListRows.map((entry) => [entry.schema, entry.name, entry.type, entry.wr]),
    })}`);
  }
  for (const master of mainTableRows) {
    const matches = tableListRows.filter((entry) => entry.name === master.name);
    if (matches.length !== 1
      || (master.type === 'view') !== (matches[0]?.type === 'view')
      || (master.type === 'table'
        && !['table', 'virtual', 'shadow'].includes(matches[0]?.type))) {
      throw new Error(`sqlite_master/table_list table authority differs: ${master.name}`);
    }
  }
  for (const listed of tableListRows) {
    if (!mainTableRows.some((master) => master.name === listed.name)) {
      throw new Error(`unaccounted main table_list entry: ${listed.name}`);
    }
  }
  const authoritativeTables = mainTableRows.filter((entry) => entry.type === 'table');
  const tableCount = Number(database.prepare(`SELECT count(*) AS count
    FROM main.sqlite_master WHERE type='table' AND name<>'turn_tool_executions'`).get().count);
  if (!Number.isSafeInteger(tableCount) || tableCount < 0 || tableCount > maximumTables) {
    throw new Error(`non-ledger table cap exceeded: ${tableCount}`);
  }
  const result = [];
  const tables = database.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='table' AND name<>'turn_tool_executions'
    ORDER BY name`).all();
  let totalRows = 0;
  for (const { name: rawName } of tables) {
    const name = String(rawName);
    const escaped = name.replaceAll("'", "''");
    const tableColumns = database.prepare(`PRAGMA main.table_xinfo('${escaped}')`).all();
    const columns = tableColumns.filter((column) => Number(column.hidden) === 0)
      .map((column) => String(column.name));
    if (!columns.length) throw new Error(`non-ledger table has no visible columns: ${name}`);
    const tableList = tableListRows.find((entry) => entry.name === name);
    if (tableList == null || !['table', 'virtual', 'shadow'].includes(tableList.type)
      || ![0, 1].includes(tableList.wr)) {
      throw new Error(`non-ledger table_list authority is invalid: ${name}`);
    }
    const master = authoritativeTables.find((entry) => entry.name === name);
    if (tableList.type === 'virtual'
      && !/^create virtual table .+ using fts5\(/u.test(normalizedSql(master?.sql || ''))) {
      throw new Error(`non-ledger virtual table lacks an explicit FTS5 row identity contract: ${name}`);
    }
    const withoutRowid = tableList.wr === 1;
    const shadowed = new Set(tableColumns.map((column) => String(column.name).toLowerCase()));
    const rowidAlias = withoutRowid
      ? null : ['rowid', '_rowid_', 'oid'].find((alias) => !shadowed.has(alias));
    if (!withoutRowid && rowidAlias == null) {
      throw new Error(`non-ledger table has no accessible intrinsic rowid alias: ${name}`);
    }
    const remainingRows = maximumNonLedgerRows - totalRows;
    const rows = tableRowsFingerprint(database, name, columns, {
      rowidAlias, maximumRows: remainingRows,
    });
    totalRows += rows.count;
    result.push(Object.freeze({
      name,
      rowIdentity: Object.freeze({
        kind: withoutRowid ? 'without-rowid'
          : tableList.type === 'virtual' ? 'fts5-intrinsic-rowid' : 'intrinsic-rowid',
        alias: rowidAlias,
      }),
      rows,
    }));
  }
  const schemaObjectCount = Number(database.prepare(`SELECT count(*) AS count
    FROM main.sqlite_master WHERE name<>'turn_tool_executions'
      AND tbl_name<>'turn_tool_executions'`).get().count);
  if (!Number.isSafeInteger(schemaObjectCount) || schemaObjectCount < 0
    || schemaObjectCount > maximumSchemaObjects) {
    throw new Error(`non-ledger schema object cap exceeded: ${schemaObjectCount}`);
  }
  const schemaObjects = database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE name<>'turn_tool_executions'
      AND tbl_name<>'turn_tool_executions' ORDER BY type,name`).all().map((row) => ({
    type: String(row.type), name: String(row.name), tblName: String(row.tbl_name),
    sql: row.sql == null ? null : String(row.sql),
  }));
  const pragmas = stablePersistentPragmas(persistentPragmas(database));
  const body = Object.freeze({
    tables: Object.freeze(result), schemaObjects: Object.freeze(schemaObjects), pragmas,
    tableCount, totalRows, schemaObjectCount,
  });
  return Object.freeze({ ...body, sha256: sha256(canonicalJson(body)) });
}

function databaseChecks(database) {
  const integrity = database.pragma('integrity_check')
    .map((row) => String(Object.values(row)[0]));
  const foreignKeys = database.pragma('foreign_key_check').map((row) => ({
    table: String(row.table), rowid: row.rowid == null ? null : String(row.rowid),
    parent: String(row.parent), fkid: Number(row.fkid),
  }));
  if (integrity.length !== 1 || integrity[0] !== 'ok') {
    throw new Error(`integrity_check failed: ${integrity.join(';')}`);
  }
  if (foreignKeys.length) throw new Error('foreign_key_check failed');
  return Object.freeze({ integrityCheck: 'ok', foreignKeyViolations: Object.freeze([]) });
}

function parseCanonicalJson(value, label) {
  try {
    const parsed = value == null ? null : JSON.parse(value);
    if (canonicalJson(parsed) !== value && !(parsed === null && value == null)) {
      throw new Error(`${label} is not canonical`);
    }
    return parsed;
  } catch (error) {
    throw new Error(`${label} is invalid: ${error?.message || error}`);
  }
}

function outputMetadata(row) {
  const output = String(row.output || '');
  let materialSha256 = null;
  try {
    const parsed = JSON.parse(output);
    const candidate = parsed?.data?.material_sha256 || parsed?.artifact_receipt?.sha256 || null;
    if (/^[a-f0-9]{64}$/u.test(String(candidate || ''))) materialSha256 = String(candidate);
  } catch { /* non-JSON output has only an output hash */ }
  return { outputSha256: sha256(output), materialSha256 };
}

function executionScalarShapeValid(row) {
  return typeof row.execution_key === 'string' && row.execution_key.trim() !== ''
    && typeof row.turn_id === 'string' && row.turn_id.trim() !== ''
    && typeof row.tool === 'string' && row.tool.trim() !== ''
    && typeof row.args === 'string'
    && (row.semantic_binding == null || typeof row.semantic_binding === 'string')
    && typeof row.started_ts === 'string' && row.started_ts.trim() !== ''
    && Number.isFinite(Date.parse(row.started_ts))
    && (row.finished_ts == null || (typeof row.finished_ts === 'string'
      && Number.isFinite(Date.parse(row.finished_ts))))
    && (row.output == null || typeof row.output === 'string');
}

function executionLifecycleShapeValid(row) {
  if (row.status === 'started' || row.status === 'transport_sending') {
    return row.finished_ts == null && row.output == null;
  }
  if (row.status === 'done') {
    return typeof row.finished_ts === 'string' && row.finished_ts !== ''
      && Date.parse(row.finished_ts) >= Date.parse(row.started_ts)
      && typeof row.output === 'string';
  }
  const completedTransport = typeof row.finished_ts === 'string' && row.finished_ts !== '';
  return completedTransport && Date.parse(row.finished_ts) >= Date.parse(row.started_ts)
    && typeof row.output === 'string';
}

function exactObjectKeys(value, expected) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    && canonicalJson(Object.keys(value).sort()) === canonicalJson([...expected].sort());
}

function positiveSafeInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function nullablePositiveSafeInteger(value) {
  return value == null ? null : positiveSafeInteger(value);
}

function optionalPositiveIntegerValid(value) {
  return value == null || (typeof value === 'number' && positiveSafeInteger(value) != null);
}

function onlyObjectKeys(value, allowed) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every((key) => allowed.includes(key));
}

function validateTelegramSendOptions(intent) {
  const options = intent.sendOptions;
  const threadValid = !Object.hasOwn(options, 'message_thread_id')
    || (typeof options.message_thread_id === 'number'
      && positiveSafeInteger(options.message_thread_id) != null);
  if (!threadValid) throw new Error('Telegram transport thread option is invalid');
  if (intent.kind === 'text') {
    if (!onlyObjectKeys(options, ['message_thread_id', 'reply_parameters'])) {
      throw new Error('Telegram text transport options are invalid');
    }
    if (Object.hasOwn(options, 'reply_parameters')) {
      if (Number(intent.segment?.index) !== 0
        || !exactObjectKeys(options.reply_parameters, [
          'message_id', 'allow_sending_without_reply',
        ])
        || typeof options.reply_parameters.message_id !== 'number'
        || positiveSafeInteger(options.reply_parameters.message_id) == null
        || options.reply_parameters.allow_sending_without_reply !== true) {
        throw new Error('Telegram text reply option is invalid');
      }
    }
    return;
  }
  const mediaKeys = intent.kind === 'audio'
    ? ['caption', 'message_thread_id', 'title', 'performer']
    : ['caption', 'message_thread_id'];
  if (!onlyObjectKeys(options, mediaKeys)
    || (Object.hasOwn(options, 'caption')
      && (typeof options.caption !== 'string' || options.caption.length === 0
        || options.caption.length > 1_024))
    || ['title', 'performer'].some((key) => Object.hasOwn(options, key)
      && (typeof options[key] !== 'string' || options[key].length === 0
        || options[key].length > 128))) {
    throw new Error('Telegram media transport options are invalid');
  }
}

function validateTelegramIntent(intent) {
  const common = ['contract', 'kind', 'chatId', 'deliveryKey', 'payloadSha256', 'sendOptions'];
  const expectedKeys = intent?.kind === 'text'
    ? [...common, 'chunkKey', 'directTurnSignalRef', 'originPrincipal',
      'segment', 'wholeReplySha256']
    : [...common, 'captionSha256'];
  if (!exactObjectKeys(intent, expectedKeys)
    || intent.contract !== 'TelegramTransportIntent/v2'
    || !['text', 'photo', 'audio'].includes(intent.kind)
    || typeof intent.chatId !== 'string' || !/^-?[1-9][0-9]*$/u.test(intent.chatId)
    || typeof intent.deliveryKey !== 'string' || intent.deliveryKey.trim() === ''
    || !proofHashValid(intent.payloadSha256)
    || intent.sendOptions == null || typeof intent.sendOptions !== 'object'
    || Array.isArray(intent.sendOptions)) {
    throw new Error('Telegram transport intent shape is invalid');
  }
  if (intent.kind === 'text') {
    if (typeof intent.chunkKey !== 'string'
      || intent.chunkKey !== `${intent.deliveryKey}:chunk:${Number(intent.segment?.index) + 1}`
      || !(intent.directTurnSignalRef == null
        || (typeof intent.directTurnSignalRef === 'string'
          && intent.directTurnSignalRef.trim() !== ''))
      || !(intent.originPrincipal == null
        || (typeof intent.originPrincipal === 'string' && intent.originPrincipal.trim() !== ''))
      || !proofHashValid(intent.wholeReplySha256)
      || !exactObjectKeys(intent.segment, ['splitVersion', 'index', 'count'])
      || intent.segment.splitVersion !== 'split-3800/v1'
      || typeof intent.segment.index !== 'number'
      || !Number.isSafeInteger(intent.segment.index) || intent.segment.index < 0
      || typeof intent.segment.count !== 'number'
      || !Number.isSafeInteger(intent.segment.count)
      || intent.segment.count <= intent.segment.index
      || (intent.segment.count === 1
        && intent.wholeReplySha256 !== intent.payloadSha256)) {
      throw new Error('Telegram text transport intent shape is invalid');
    }
  } else if (!proofHashValid(intent.captionSha256)
    || intent.captionSha256 !== sha256(String(intent.sendOptions.caption || ''))) {
    throw new Error('Telegram media transport intent shape is invalid');
  }
  validateTelegramSendOptions(intent);
  return intent;
}

function validateAcceptedTransportOutput(row, intent, output) {
  const response = output?.response != null && typeof output.response === 'object'
    && !Array.isArray(output.response) ? output.response : {};
  const responseKeys = [
    'chatId', 'messageId', 'telegramDate', 'messageThreadId', 'replyToMessageId',
    'textSha256', 'captionSha256', 'mediaFileId',
  ];
  const { receiptSha256, ...core } = output && typeof output === 'object' ? output : {};
  const expectedThread = nullablePositiveSafeInteger(intent.sendOptions.message_thread_id);
  const expectedReply = nullablePositiveSafeInteger(
    intent.sendOptions.reply_parameters?.message_id,
  );
  const baseValid = exactObjectKeys(output, [
    'contract', 'accepted', 'operationKey', 'intentSha256', 'attempt',
    'response', 'receiptSha256',
  ])
    && exactObjectKeys(response, responseKeys)
    && output.contract === 'TelegramAcceptedReceipt/v3'
    && output.accepted === true
    && output.operationKey === row.execution_key
    && output.intentSha256 === sha256(canonicalJson(intent))
    && proofHashValid(receiptSha256)
    && receiptSha256 === sha256(canonicalJson(core))
    && String(response.chatId || '') === String(intent.chatId)
    && typeof response.messageId === 'string'
    && positiveSafeInteger(response.messageId) != null
    && typeof response.telegramDate === 'number'
    && positiveSafeInteger(response.telegramDate) != null
    && typeof output.attempt === 'number'
    && positiveSafeInteger(output.attempt) != null
    && output.attempt <= 5
    && optionalPositiveIntegerValid(response.messageThreadId)
    && optionalPositiveIntegerValid(response.replyToMessageId)
    && nullablePositiveSafeInteger(response.messageThreadId) === expectedThread
    && nullablePositiveSafeInteger(response.replyToMessageId) === expectedReply;
  const payloadValid = intent.kind === 'text'
    ? response.textSha256 === intent.payloadSha256
      && response.captionSha256 == null && response.mediaFileId == null
    : response.textSha256 == null
      && String(response.mediaFileId || '').trim() !== ''
      && (!intent.sendOptions.caption
        ? response.captionSha256 == null || response.captionSha256 === intent.captionSha256
        : response.captionSha256 === intent.captionSha256)
      && intent.captionSha256 === sha256(String(intent.sendOptions.caption || ''));
  if (!baseValid || !payloadValid) {
    throw new Error('transport_accepted output is invalid');
  }
}

function validateFailedTransportOutput(row, output) {
  const expectedClassification = row.status.slice('transport_'.length);
  const baseKeys = ['classification', 'attempts', 'message'];
  if (output.classification !== expectedClassification
    || typeof output.attempts !== 'number'
    || !Number.isSafeInteger(output.attempts) || output.attempts < 0 || output.attempts > 5
    || typeof output.message !== 'string' || output.message.trim() === ''
    || output.message.length > 300) {
    throw new Error(`${row.status} output is invalid`);
  }
  if (row.status === 'transport_retryable') {
    if (!exactObjectKeys(output, [...baseKeys, 'retryAt', 'telegramCode'])
      || output.attempts < 1 || typeof output.retryAt !== 'string'
      || !Number.isFinite(Date.parse(output.retryAt))
      || typeof output.telegramCode !== 'number'
      || positiveSafeInteger(output.telegramCode) == null) {
      throw new Error('transport_retryable output is invalid');
    }
  } else if (row.status === 'transport_uncertain') {
    const direct = exactObjectKeys(output, baseKeys);
    const api = exactObjectKeys(output, [...baseKeys, 'retryAt', 'telegramCode'])
      && output.retryAt === null && output.telegramCode === null;
    if (output.attempts < 1 || (!direct && !api)) {
      throw new Error('transport_uncertain output is invalid');
    }
  } else {
    const direct = exactObjectKeys(output, [...baseKeys, 'retryable'])
      && output.retryable === false;
    const api = exactObjectKeys(output, [...baseKeys, 'retryAt', 'telegramCode'])
      && output.retryAt === null && typeof output.telegramCode === 'number'
      && positiveSafeInteger(output.telegramCode) != null;
    const exhausted = exactObjectKeys(
      output, [...baseKeys, 'retryAt', 'telegramCode', 'retryable'],
    ) && typeof output.retryAt === 'string' && Number.isFinite(Date.parse(output.retryAt))
      && typeof output.telegramCode === 'number'
      && positiveSafeInteger(output.telegramCode) != null && output.retryable === false;
    if (!direct && !api && !exhausted) throw new Error('transport_rejected output is invalid');
  }
}

function validateExecutionStatusStructure(row, { requireCanonicalOutput = false } = {}) {
  const transport = row.status.startsWith('transport_');
  const telegramNamespace = row.tool.startsWith('@transport/telegram.');
  if (telegramLeafTools.has(row.tool) && !transport) {
    throw new Error('Telegram leaf tool requires transport lifecycle');
  }
  if (telegramNamespace && !telegramLeafTools.has(row.tool)) {
    throw new Error('Telegram namespace requires an exact transport leaf lifecycle');
  }
  if (transport && !telegramNamespace) {
    throw new Error('transport execution tool is outside @transport/telegram.*');
  }
  if (!transport) return;
  const expectedTools = Object.freeze([
    Object.freeze(['text', '@transport/telegram.sendMessage']),
    Object.freeze(['photo', '@transport/telegram.sendPhoto']),
    Object.freeze(['audio', '@transport/telegram.sendAudio']),
  ]);
  let parsedIntent;
  try { parsedIntent = JSON.parse(row.args); } catch (error) {
    throw new Error(`Telegram transport intent is invalid: ${error.message}`);
  }
  const intent = validateTelegramIntent(parsedIntent);
  const expectedTool = expectedTools.find(([kind]) => kind === intent.kind)?.[1];
  if (row.tool !== expectedTool) {
    throw new Error('transport execution tool does not match Telegram intent kind');
  }
  if (row.output_sha256 != null || row.material_sha256 != null
    || row.completion_record_sha256 != null) {
    throw new Error('transport execution carries completion proof fields');
  }
  if (row.status === 'transport_sending') {
    if (row.finished_ts != null || row.output != null) {
      throw new Error('transport_sending execution shape is invalid');
    }
    return;
  }
  if (typeof row.output !== 'string') {
    throw new Error(`${row.status} execution output is missing`);
  }
  let output;
  try { output = JSON.parse(row.output); } catch (error) {
    throw new Error(`${row.status} output is invalid: ${error.message}`);
  }
  if (requireCanonicalOutput && canonicalJson(output) !== row.output) {
    throw new Error(`${row.status} output is not canonical`);
  }
  if (output == null || typeof output !== 'object' || Array.isArray(output)) {
    throw new Error(`${row.status} execution output is not an object`);
  }
  if (row.status === 'transport_accepted') {
    validateAcceptedTransportOutput(row, intent, output);
    return;
  }
  validateFailedTransportOutput(row, output);
}

function validateVerifiedRecord(row) {
  if (!['ExecutionRecord/v1', 'ExecutionRecord/v2'].includes(row.execution_contract)) {
    throw new Error('unsupported execution contract');
  }
  const sequence = Number(row.execution_seq);
  if (!Number.isSafeInteger(sequence) || sequence <= 0) throw new Error('invalid execution sequence');
  if (row.execution_verification !== 'verified') throw new Error('record is not verified');
  if (!allowedExecutionStatuses.has(row.status)) throw new Error('unsupported execution status');
  if (!executionScalarShapeValid(row) || !executionLifecycleShapeValid(row, { verified: true })) {
    throw new Error('verified execution field shape is invalid');
  }
  validateExecutionStatusStructure(row, { requireCanonicalOutput: true });
  const args = parseCanonicalJson(row.args, 'execution args');
  if (args == null || typeof args !== 'object' || Array.isArray(args)) {
    throw new Error('execution args is not an object');
  }
  const binding = parseCanonicalJson(row.semantic_binding, 'execution binding');
  if (!legacyBindingValid(row.semantic_binding)) {
    throw new Error('verified execution binding is invalid');
  }
  if (!proofHashValid(row.semantic_hash)) {
    throw new Error('verified execution semantic hash is invalid');
  }
  if (!proofHashValid(row.intent_record_sha256)) {
    throw new Error('verified execution intent hash shape is invalid');
  }
  const legacyIntent = {
    turnId: row.turn_id, tool: row.tool, args, binding, semanticHash: row.semantic_hash,
  };
  const intent = row.execution_contract === 'ExecutionRecord/v1' ? legacyIntent : {
    executionContract: row.execution_contract, executionSeq: sequence, ...legacyIntent,
  };
  if (!row.semantic_hash || row.intent_record_sha256 !== sha256(canonicalJson(intent))) {
    throw new Error('invalid execution intent proof');
  }
  if (row.status !== 'done') {
    if (row.output_sha256 != null || row.material_sha256 != null
      || row.completion_record_sha256 != null) {
      throw new Error('non-done execution carries unsupported completion proof');
    }
    return;
  }
  if (!proofHashValid(row.output_sha256)
    || !nullableProofHashValid(row.material_sha256)
    || !proofHashValid(row.completion_record_sha256)) {
    throw new Error('verified execution completion hash shape is invalid');
  }
  const metadata = outputMetadata(row);
  if (row.output_sha256 !== metadata.outputSha256
    || (row.material_sha256 || null) !== metadata.materialSha256) {
    throw new Error('invalid execution output proof');
  }
  const legacyCompletion = {
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    args: row.args, semanticHash: row.semantic_hash, semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256, status: row.status,
    startedTs: row.started_ts, finishedTs: row.finished_ts,
    outputSha256: row.output_sha256, materialSha256: row.material_sha256,
  };
  const completion = row.execution_contract === 'ExecutionRecord/v1' ? legacyCompletion : {
    executionContract: row.execution_contract, executionSeq: sequence, ...legacyCompletion,
  };
  if (row.completion_record_sha256 !== sha256(canonicalJson(completion))) {
    throw new Error('invalid execution completion proof');
  }
}

function proofHashValid(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
}

function nullableProofHashValid(value) {
  return value == null || proofHashValid(value);
}

function legacyArgsClassification(value) {
  if (typeof value !== 'string') throw new Error('legacy execution args is not text');
  let parsed;
  try { parsed = JSON.parse(value); } catch (error) {
    throw new Error(`legacy execution args JSON is invalid: ${error.message}`);
  }
  if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('legacy execution args JSON is not an object');
  }
  const canonical = canonicalJson(parsed);
  if (typeof canonical !== 'string') {
    throw new Error('legacy execution args cannot be deterministically canonicalized');
  }
  return Object.freeze({ noncanonical: canonical !== value });
}

function legacyBindingValid(value) {
  if (value == null) return true;
  if (typeof value !== 'string') return false;
  let binding;
  try { binding = JSON.parse(value); } catch { return false; }
  if (binding == null || typeof binding !== 'object' || Array.isArray(binding)
    || canonicalJson(binding) !== value) return false;
  const fields = new Set([
    'understandingSha256', 'requirementId', 'addressesRequirementIds', 'basisReceiptIds',
    'capabilityId', 'operationId', 'role', 'executionType',
  ]);
  if (Object.keys(binding).some((field) => !fields.has(field))) return false;
  const addresses = binding.addressesRequirementIds;
  const basis = binding.basisReceiptIds;
  return typeof binding.understandingSha256 === 'string'
    && /^[a-f0-9]{64}$/u.test(binding.understandingSha256)
    && typeof binding.requirementId === 'string'
    && /^r(?:[1-9]|1[0-2])$/u.test(binding.requirementId)
    && Array.isArray(addresses) && addresses.length > 0 && addresses.length <= 12
    && new Set(addresses).size === addresses.length
    && addresses.every((id) => typeof id === 'string' && /^r(?:[1-9]|1[0-2])$/u.test(id))
    && addresses.includes(binding.requirementId)
    && Array.isArray(basis) && basis.length <= 64 && new Set(basis).size === basis.length
    && basis.every((id) => typeof id === 'string' && /^s[1-9][0-9]*$/u.test(id))
    && typeof binding.capabilityId === 'string'
    && /^[a-z][a-z0-9_.:-]{0,119}$/u.test(binding.capabilityId)
    && typeof binding.operationId === 'string'
    && /^[a-z][a-z0-9_.:-]{0,119}$/u.test(binding.operationId)
    && typeof binding.role === 'string' && /^[a-z][a-z0-9_:-]{0,79}$/u.test(binding.role)
    && (binding.executionType == null || (typeof binding.executionType === 'string'
      && /^[a-z][a-z0-9_:-]{0,79}$/u.test(binding.executionType)));
}

function validateLegacyUnverifiableRecord(row) {
  const sequence = Number(row.execution_seq);
  if (!Number.isSafeInteger(sequence) || sequence <= 0) {
    throw new Error('legacy execution sequence is invalid');
  }
  if (!allowedExecutionStatuses.has(row.status)) {
    throw new Error('legacy execution status is invalid');
  }
  const scalarShape = executionScalarShapeValid(row);
  const lifecycleShape = executionLifecycleShapeValid(row);
  if (!scalarShape || !lifecycleShape) {
    throw new Error('legacy execution field shape is invalid');
  }
  validateExecutionStatusStructure(row);
  let argsClassification;
  try { argsClassification = legacyArgsClassification(row.args); } catch (error) {
    throw new Error(`legacy execution args is invalid: ${error.message}`);
  }
  if (!legacyBindingValid(row.semantic_binding)) {
    throw new Error('legacy execution binding is invalid');
  }
  const intentPair = row.semantic_hash == null && row.intent_record_sha256 == null
    || proofHashValid(row.semantic_hash) && proofHashValid(row.intent_record_sha256);
  const completionProofShape = row.status === 'done'
    ? row.output_sha256 == null && row.material_sha256 == null
        && row.completion_record_sha256 == null
      || proofHashValid(row.output_sha256)
        && nullableProofHashValid(row.material_sha256)
        && proofHashValid(row.completion_record_sha256)
    : row.output_sha256 == null && row.material_sha256 == null
      && row.completion_record_sha256 == null;
  if (!intentPair || !completionProofShape) {
    throw new Error('legacy execution proof shape is invalid');
  }
  return argsClassification;
}

function executionClassificationManifest(database) {
  const counts = {
    verified: 0, legacy_unverifiable: 0, redacted: 0, noncanonicalLegacyArgs: 0,
  };
  const rowCount = Number(database.prepare(`SELECT count(*) AS count
    FROM main.turn_tool_executions`).get().count);
  if (!Number.isSafeInteger(rowCount) || rowCount < 0 || rowCount > maximumLedgerRows) {
    throw new Error(`execution ledger row cap exceeded: ${rowCount}`);
  }
  const rows = database.prepare('SELECT * FROM main.turn_tool_executions ORDER BY execution_seq')
    .all();
  for (const row of rows) {
    if (row.execution_verification === 'verified') {
      if (String(row.args || '').includes('[AFGESCHERMD:')) {
        throw new Error(`verified redacted execution: ${row.execution_key}`);
      }
      try { validateVerifiedRecord(row); } catch (error) {
        throw new Error(`invalid verified execution ${row.execution_key}: ${error.message}`);
      }
      counts.verified += 1;
      continue;
    }
    if (row.execution_verification !== 'legacy_unverifiable'
      || row.execution_contract !== 'ExecutionRecord/v1') {
      throw new Error(`invalid legacy classification: ${row.execution_key}`);
    }
    const legacy = validateLegacyUnverifiableRecord(row);
    if (legacy.noncanonical) counts.noncanonicalLegacyArgs += 1;
    if (String(row.args || '').includes('[AFGESCHERMD:')) {
      counts.redacted += 1;
    } else {
      let unexpectedlyValid = true;
      try { validateVerifiedRecord({ ...row, execution_verification: 'verified' }); }
      catch { unexpectedlyValid = false; }
      if (unexpectedlyValid) throw new Error(`verifiable row classified legacy: ${row.execution_key}`);
    }
    counts.legacy_unverifiable += 1;
  }
  return Object.freeze({
    contract: 'ExecutionLedgerClassificationManifest/v1', rows: rows.length, ...counts,
  });
}

function assertNoExternalLedgerReferences(database) {
  const allowed = new Set([
    'turn_tool_executions', 'idx_turn_tool_executions_turn',
    'idx_turn_tool_executions_sequence', ...executionLedgerTriggerNames,
  ]);
  const unexpected = [];
  let referenceCount = 0;
  for (const row of database.prepare(`SELECT type,name FROM main.sqlite_master
    WHERE sql IS NOT NULL AND lower(sql) LIKE '%turn_tool_executions%'
    ORDER BY type,name`).iterate()) {
    referenceCount += 1;
    if (referenceCount > maximumSchemaObjects) {
      throw new Error('execution-ledger reference scan cap exceeded');
    }
    if (!allowed.has(String(row.name))) {
      unexpected.push(Object.freeze({ type: String(row.type), name: String(row.name) }));
    }
  }
  if (unexpected.length) {
    throw new Error(`external execution-ledger references exist: ${canonicalJson(unexpected)}`);
  }
  const reserved = database.prepare(`SELECT type,name FROM main.sqlite_master
    WHERE name=? LIMIT 1`).get(recoverySourceTable);
  if (reserved != null) throw new Error('reserved recovery object already exists');
  let scannedTables = 0;
  let scannedForeignKeys = 0;
  for (const { name } of database.prepare(`SELECT name FROM main.sqlite_master
    WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name`).iterate()) {
    scannedTables += 1;
    if (scannedTables > maximumTables) throw new Error('foreign-key table scan cap exceeded');
    const escaped = String(name).replaceAll("'", "''");
    for (const foreignKey of database.prepare(
      `PRAGMA main.foreign_key_list('${escaped}')`,
    ).iterate()) {
      scannedForeignKeys += 1;
      if (scannedForeignKeys > maximumSchemaObjects) {
        throw new Error('foreign-key definition scan cap exceeded');
      }
      if (String(foreignKey.table) === 'turn_tool_executions') {
        throw new Error(`foreign key references execution ledger: ${name}`);
      }
    }
  }
}

function enforceSourceManifestCaps(database) {
  const tableCount = Number(database.prepare(`SELECT count(*) AS count
    FROM main.sqlite_master WHERE type='table'`).get().count);
  if (!Number.isSafeInteger(tableCount) || tableCount < 0 || tableCount > maximumTables) {
    throw new Error(`source table cap exceeded: ${tableCount}`);
  }
  const schemaObjectCount = Number(database.prepare(`SELECT count(*) AS count
    FROM main.sqlite_master`).get().count);
  if (!Number.isSafeInteger(schemaObjectCount) || schemaObjectCount < 0
    || schemaObjectCount > maximumSchemaObjects) {
    throw new Error(`source schema object cap exceeded: ${schemaObjectCount}`);
  }
  return Object.freeze({ tableCount, schemaObjectCount });
}

function production17SchemaManifest(database) {
  const protectedNames = new Set([
    'turn_tool_executions', 'idx_turn_tool_executions_turn',
    'idx_turn_tool_executions_sequence', ...executionLedgerTriggerNames,
  ]);
  const shadow = database.prepare(`SELECT type,name,tbl_name FROM temp.sqlite_master
    ORDER BY type,name`).all().find((row) => (
    protectedNames.has(String(row.name)) || String(row.tbl_name) === 'turn_tool_executions'
  ));
  if (shadow) throw new Error(`temporary ledger shadow exists: ${canonicalJson(shadow)}`);
  const columns = database.prepare("PRAGMA main.table_info('turn_tool_executions')").all()
    .map(({ name, type, notnull, dflt_value, pk }) => [
      String(name), String(type), Number(notnull),
      dflt_value == null ? null : String(dflt_value), Number(pk),
    ]);
  const objects = database.prepare(`SELECT type,name,tbl_name,sql FROM main.sqlite_master
    WHERE name='turn_tool_executions' OR tbl_name='turn_tool_executions'
    ORDER BY type,name`).all().map((row) => [
    String(row.type), String(row.name), String(row.tbl_name),
    row.sql == null ? null : sha256(normalizedSql(row.sql)),
  ]);
  const indexSignatures = Object.create(null);
  for (const name of Object.keys(production17IndexSignatures)) {
    const escaped = name.replaceAll("'", "''");
    indexSignatures[name] = database.prepare(`PRAGMA main.index_xinfo('${escaped}')`).all()
      .map(({ seqno, cid, name: column, desc, coll, key }) => [
        Number(seqno), Number(cid), column == null ? null : String(column),
        Number(desc), coll == null ? null : String(coll), Number(key),
      ]);
  }
  if (canonicalJson(columns) !== canonicalJson(production17Columns)
    || canonicalJson(objects) !== canonicalJson(production17Objects)
    || canonicalJson(indexSignatures) !== canonicalJson(production17IndexSignatures)) {
    throw new Error('source ledger layout/object SQL is not exact production14-migrated-v2');
  }
  const body = Object.freeze({
    contract: 'ExecutionLedgerIncidentSchemaManifest/v1',
    layout: 'production14-migrated-v2', columns, objects,
    indexSignatures: Object.freeze(indexSignatures),
  });
  return Object.freeze({ ...body, sha256: sha256(canonicalJson(body)) });
}

function sourceManifest(database, intendedJournalMode = null) {
  enforceSourceManifestCaps(database);
  let schema;
  try { schema = production17SchemaManifest(database); } catch (error) {
    throw new Error(`source ledger layout is not exact production14-migrated-v2: ${error.message}`);
  }
  assertNoExternalLedgerReferences(database);
  return Object.freeze({
    schema, classification: executionClassificationManifest(database),
    oldRows: ledgerRowsManifest(database), nonLedger: nonLedgerFingerprints(database),
    persistentPragmas: persistentPragmas(database, intendedJournalMode),
    checks: databaseChecks(database),
  });
}

function production14SchemaManifest(database) {
  const columns = database.prepare("PRAGMA main.table_info('turn_tool_executions')").all()
    .map(({ name, type, notnull, dflt_value, pk }) => [
      String(name), String(type), Number(notnull), dflt_value == null ? null : String(dflt_value),
      Number(pk),
    ]);
  const expectedColumns = [
    ['execution_key', 'TEXT', 0, null, 1], ['turn_id', 'TEXT', 1, null, 0],
    ['tool', 'TEXT', 1, null, 0], ['args', 'TEXT', 0, null, 0],
    ['status', 'TEXT', 1, "'started'", 0], ['started_ts', 'TEXT', 1, null, 0],
    ['finished_ts', 'TEXT', 0, null, 0], ['output', 'TEXT', 0, null, 0],
    ...oldColumnNames.slice(8).map((name) => [name, 'TEXT', 0, null, 0]),
  ];
  const tableSql = database.prepare(`SELECT sql FROM main.sqlite_master
    WHERE type='table' AND name='turn_tool_executions'`).get()?.sql;
  const objects = database.prepare(`SELECT type,name,sql FROM main.sqlite_master
    WHERE name='turn_tool_executions' OR tbl_name='turn_tool_executions'
    ORDER BY type,name`).all();
  const objectNames = objects.map(({ type, name }) => [String(type), String(name)]);
  const expectedObjects = [
    ['index', 'idx_turn_tool_executions_turn'],
    ['index', 'sqlite_autoindex_turn_tool_executions_1'],
    ['table', 'turn_tool_executions'],
  ];
  const turnIndexSql = objects.find((row) => row.name === 'idx_turn_tool_executions_turn')?.sql;
  const indexSignatures = Object.create(null);
  for (const name of Object.keys(production14IndexSignatures)) {
    const escaped = name.replaceAll("'", "''");
    indexSignatures[name] = database.prepare(`PRAGMA main.index_xinfo('${escaped}')`).all()
      .map(({ seqno, cid, name: column, desc, coll, key }) => [
        Number(seqno), Number(cid), column == null ? null : String(column),
        Number(desc), coll == null ? null : String(coll), Number(key),
      ]);
  }
  if (canonicalJson(columns) !== canonicalJson(expectedColumns)
    || canonicalJson(objectNames) !== canonicalJson(expectedObjects)
    || canonicalJson(indexSignatures) !== canonicalJson(production14IndexSignatures)
    || normalizedSql(tableSql) !== normalizedSql(production14TableSql)
    || normalizedSql(turnIndexSql) !== normalizedSql(production14IndexSql)) {
    throw new Error('target production14 schema manifest is inexact');
  }
  const body = Object.freeze({
    contract: 'ExecutionLedgerProduction14SchemaManifest/v1', columns, objects: objectNames,
    indexSignatures: Object.freeze(indexSignatures),
  });
  return Object.freeze({ ...body, sha256: sha256(canonicalJson(body)) });
}

function rebuildLedger(database) {
  database.transaction(() => {
    for (const name of executionLedgerTriggerNames) {
      database.exec(`DROP TRIGGER main.${name}`);
    }
    database.exec('DROP INDEX main.idx_turn_tool_executions_sequence');
    database.exec('DROP INDEX main.idx_turn_tool_executions_turn');
    database.exec(`ALTER TABLE main.turn_tool_executions RENAME TO ${recoverySourceTable}`);
    database.exec(production14TableSql);
    database.exec(`INSERT INTO main.turn_tool_executions (${oldColumnNames.join(',')})
      SELECT ${oldColumnNames.join(',')} FROM main.${recoverySourceTable}`);
    database.exec(`DROP TABLE main.${recoverySourceTable}`);
    database.exec(production14IndexSql);
  }).immediate();
}

function production14Manifest(database, intendedJournalMode = null) {
  return Object.freeze({
    schema: production14SchemaManifest(database),
    oldRows: ledgerRowsManifest(database),
    nonLedger: nonLedgerFingerprints(database),
    persistentPragmas: persistentPragmas(database, intendedJournalMode),
    checks: databaseChecks(database),
  });
}

function sameManifest(left, right, label) {
  if (canonicalJson(left) !== canonicalJson(right)) throw new Error(`${label} changed`);
}

function modeString(stat) {
  const mode = typeof stat.mode === 'bigint' ? stat.mode : BigInt(stat.mode);
  return (mode & 0o777n).toString(8).padStart(4, '0');
}

function descriptorCurrentPath(descriptor) {
  try {
    return fs.realpathSync.native(descriptorPath(descriptor));
  } catch (error) {
    return `<unavailable:${error?.code || error?.message || error}>`;
  }
}

function pinnedStat(stat) {
  return Object.freeze({
    dev: String(stat.dev), ino: String(stat.ino), uid: String(stat.uid), gid: String(stat.gid),
    mode: modeString(stat), nlink: String(stat.nlink), size: String(stat.size),
  });
}

function sameIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function sameStableFileStat(left, right) {
  const fields = [
    'dev', 'ino', 'uid', 'gid', 'mode', 'nlink', 'size', 'mtimeNs', 'ctimeNs',
  ];
  return fields.every((field) => typeof left[field] === 'bigint'
    && typeof right[field] === 'bigint' && left[field] === right[field]);
}

function sameUnlinkedBuildFileStat(left, right) {
  return ['dev', 'ino', 'uid', 'gid', 'mode', 'size']
    .every((field) => left[field] === right[field]) && left.nlink === 1n && right.nlink === 0n;
}

function sameUnlinkedDirectoryStat(left, right) {
  return ['dev', 'ino', 'uid', 'gid', 'mode']
    .every((field) => left[field] === right[field]) && left.nlink > 0n && right.nlink === 0n;
}

function assertPrivateFileAuthority(authority, stage, {
  expectedSize = null, expectedNlink = 1,
} = {}) {
  const {
    descriptor, parentDescriptor, name, expectedPath, initialStat, effectiveUser, label,
  } = authority;
  const current = fs.fstatSync(descriptor, { bigint: true });
  if (!current.isFile() || !sameIdentity(current, initialStat)
    || current.uid !== initialStat.uid || current.gid !== initialStat.gid
    || current.mode !== initialStat.mode || current.nlink !== BigInt(expectedNlink)
    || (effectiveUser != null && current.uid !== BigInt(effectiveUser))
    || (current.mode & 0o077n) !== 0n
    || (expectedSize != null && current.size !== expectedSize)) {
    throw new Error(`${label} authority changed ${stage}`);
  }
  const currentPath = fs.realpathSync.native(descriptorPath(descriptor));
  let named;
  try {
    named = fs.lstatSync(`${descriptorPath(parentDescriptor)}/${name}`, { bigint: true });
  }
  catch { throw new Error(`${label} authority changed ${stage}`); }
  if (currentPath !== expectedPath || !named.isFile() || named.isSymbolicLink()
    || !sameIdentity(named, current)) {
    throw new Error(`${label} authority changed ${stage}`);
  }
  return current;
}

function assertNoSidecars(parentDescriptor, basename, label) {
  for (const suffix of ['-wal', '-shm', '-journal']) {
    if (pathExists(`${descriptorPath(parentDescriptor)}/${basename}${suffix}`)) {
      throw new Error(`${label} has a ${suffix.slice(1)} sidecar`);
    }
  }
}

function pinnedNameState(parentDescriptor, basename, pinned) {
  try {
    const named = fs.lstatSync(
      `${descriptorPath(parentDescriptor)}/${basename}`, { bigint: true },
    );
    if (!named.isFile() || named.isSymbolicLink()) return 'non-regular-or-symlink';
    return sameIdentity(named, pinned) ? 'same-pinned-inode' : 'different-inode';
  } catch (error) {
    if (error?.code === 'ENOENT') return 'absent';
    throw error;
  }
}

function configureAnonymousMemoryDatabase(database, label) {
  database.pragma('temp_store = MEMORY');
  if (Number(database.pragma('temp_store', { simple: true })) !== 2) {
    throw new Error(`${label} temp_store is not MEMORY`);
  }
  assertAnonymousMemoryDatabase(database, label);
}

function assertAnonymousMemoryDatabase(database, label) {
  const databaseList = database.pragma('database_list');
  const main = databaseList.filter((entry) => String(entry.name) === 'main');
  if (main.length !== 1 || databaseList.some((entry) => (
    !['main', 'temp'].includes(String(entry.name)) || String(entry.file || '') !== ''
  ))) {
    throw new Error(`${label} SQLite storage is not anonymous main/temp memory`);
  }
}

function boundedDatabaseByteEstimate(database, label) {
  const pageCount = BigInt(database.pragma('page_count', { simple: true }));
  const pageSize = BigInt(database.pragma('page_size', { simple: true }));
  const estimate = pageCount * pageSize;
  if (estimate <= 0n || estimate > BigInt(maximumSourceBytes)) {
    throw new Error(`${label} estimated serialized size exceeds ${maximumSourceBytes} byte cap`);
  }
  return estimate;
}

function verifyImmutableBuffer(bytes, manifestFactory, label) {
  if (!Buffer.isBuffer(bytes) || bytes.length <= 0 || bytes.length > maximumSourceBytes) {
    throw new Error(`${label} buffer has an unsupported size`);
  }
  const hash = sha256(bytes);
  const journalHeader = sqliteJournalHeader(bytes, label);
  const database = new Database(bytes, { readonly: true });
  database.defaultSafeIntegers(true);
  let manifest;
  try {
    if (!database.readonly) throw new Error(`${label} verifier is not read-only`);
    configureAnonymousMemoryDatabase(database, `${label} verifier`);
    boundedDatabaseByteEstimate(database, `${label} verifier`);
    manifest = manifestFactory(database, 'delete');
    assertAnonymousMemoryDatabase(database, `${label} verifier after manifest`);
  } finally {
    database.close();
  }
  return Object.freeze({
    contract: 'PinnedImmutableDatabaseVerification/v1',
    storage: 'anonymous-main-and-temp-memory', tempStore: 'memory',
    fileSha256: hash, byteLength: String(bytes.length), journalHeader, manifest,
  });
}

function compareProduction14Manifests(actual, expected, label) {
  for (const field of ['schema', 'oldRows', 'nonLedger', 'checks']) {
    sameManifest(actual[field], expected[field], `${label} ${field}`);
  }
  sameManifest(
    stablePersistentPragmas(actual.persistentPragmas),
    stablePersistentPragmas(expected.persistentPragmas),
    `${label} persistent pragmas`,
  );
  if (actual.persistentPragmas.schemaVersion !== expected.persistentPragmas.schemaVersion) {
    throw new Error(`${label} schema_version changed`);
  }
}

function failureEvidence(label, descriptor) {
  if (descriptor == null) return null;
  try {
    const stat = fs.fstatSync(descriptor, { bigint: true });
    return `${label} descriptor dev=${stat.dev} ino=${stat.ino} nlink=${stat.nlink} size=${stat.size} mode=${modeString(stat)} currentDescriptorPath=${descriptorCurrentPath(descriptor)}`;
  } catch (error) {
    return `${label} descriptor unavailable: ${error?.message || error}`;
  }
}

function requiredPublicationReserve(sourceSize) {
  return BigInt(sourceSize) * 3n + BigInt(publicationReserveMarginBytes);
}

function publicationFilesystemCapacity(parentDescriptor, sourceSize) {
  const statfs = fs.statfsSync(descriptorPath(parentDescriptor), { bigint: true });
  const availableBytes = statfs.bavail * statfs.bsize;
  const requiredBytes = requiredPublicationReserve(sourceSize);
  if (availableBytes < requiredBytes) {
    throw new Error(`insufficient target filesystem reserve: ${availableBytes}<${requiredBytes}`);
  }
  return Object.freeze({
    availableBytes: String(availableBytes), requiredBytes: String(requiredBytes),
    policy: 'three-times-source-plus-64MiB',
  });
}

function privateToolchainEnvironment(temporaryDirectory) {
  return Object.freeze({
    LANG: 'C', LC_ALL: 'C', TMPDIR: temporaryDirectory,
  });
}

function openAttestedSystemExecutable(anchor, label) {
  const canonicalPath = fs.realpathSync.native(anchor);
  if (!path.isAbsolute(canonicalPath)
    || !(canonicalPath === '/usr' || strictDescendant('/usr', canonicalPath))) {
    throw new Error(`${label} is outside the trusted absolute /usr toolchain`);
  }
  let ancestor = path.dirname(canonicalPath);
  while (true) {
    const stat = fs.statSync(ancestor);
    if (!stat.isDirectory() || stat.uid !== 0 || stat.gid !== 0 || (stat.mode & 0o022) !== 0) {
      throw new Error(`${label} has a writable or non-root system ancestor: ${ancestor}`);
    }
    if (ancestor === '/') break;
    ancestor = path.dirname(ancestor);
  }
  const descriptor = fs.openSync(
    canonicalPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW,
  );
  try {
    const stat = fs.fstatSync(descriptor, { bigint: true });
    if (!stat.isFile() || stat.uid !== 0n || stat.gid !== 0n
      || (stat.mode & 0o022n) !== 0n || (stat.mode & 0o111n) === 0n
      || fs.realpathSync.native(descriptorPath(descriptor)) !== canonicalPath) {
      throw new Error(`${label} does not have immutable system authority`);
    }
    return {
      label, canonicalPath, descriptor, stat,
      fileSha256: sha256ExactDescriptor(descriptor, stat, {
        maximumBytes: maximumSystemExecutableBytes, label,
      }),
    };
  } catch (error) {
    fs.closeSync(descriptor);
    throw error;
  }
}

function runPinnedCompiler(compiler, args, environment, label, extraDescriptors = []) {
  const result = spawnSync(descriptorPath(compiler.descriptor), args, {
    encoding: 'utf8', maxBuffer: 4 * 1024 * 1024, env: environment,
    timeout: compilerTimeoutMs, killSignal: 'SIGKILL',
    stdio: ['ignore', 'pipe', 'pipe', ...extraDescriptors],
  });
  if (result.error?.code === 'ETIMEDOUT' || result.signal === 'SIGKILL') {
    throw new Error(`${label} exceeded ${compilerTimeoutMs}ms hard timeout`);
  }
  if (result.error || result.status !== 0) {
    throw new Error(`${label} failed: ${result.error?.message || result.stderr || result.stdout}`);
  }
  return result;
}

function attestPublicationToolchain(temporaryDirectory) {
  const environment = privateToolchainEnvironment(temporaryDirectory);
  const opened = [];
  try {
    const compiler = openAttestedSystemExecutable(absoluteCompilerAnchor, 'compiler');
    opened.push(compiler);
    const triple = String(runPinnedCompiler(
      compiler, ['-dumpmachine'], environment, 'discover compiler target triple',
    ).stdout || '').trim();
    const version = String(runPinnedCompiler(
      compiler, ['-dumpfullversion', '-dumpversion'], environment, 'discover compiler version',
    ).stdout || '').trim();
    if (!/^[a-z0-9_+.]+(?:-[a-z0-9_+.]+)+$/u.test(triple)
      || !/^[0-9]+(?:\.[0-9]+){0,3}$/u.test(version)) {
      throw new Error(`compiler returned unsafe target identity: ${triple}/${version}`);
    }
    const compilerAbiVersion = version.split('.')[0];
    if (path.basename(compiler.canonicalPath) !== `${triple}-gcc-${compilerAbiVersion}`) {
      throw new Error('compiler ABI directory does not match the canonical compiler binary');
    }
    const compilerRoot = `/usr/libexec/gcc/${triple}/${compilerAbiVersion}`;
    const compilerFrontend = openAttestedSystemExecutable(
      path.join(compilerRoot, 'cc1'), 'cc1',
    );
    opened.push(compilerFrontend);
    const assembler = openAttestedSystemExecutable(absoluteAssemblerAnchor, 'assembler');
    opened.push(assembler);
    const linker = openAttestedSystemExecutable(absoluteLinkerAnchor, 'linker');
    opened.push(linker);
    if (assembler.canonicalPath !== `/usr/bin/${triple}-as`
      || linker.canonicalPath !== `/usr/bin/${triple}-ld.bfd`) {
      throw new Error('assembler/linker authorities do not match the compiler target triple');
    }
    return {
      compiler, compilerFrontend, assembler, linker, environment,
      targetTriple: triple, fullVersion: version, abiMajor: compilerAbiVersion,
      compilerPrefix: `${path.dirname(compilerFrontend.canonicalPath)}${path.sep}`,
      binaryPrefix: `${path.dirname(assembler.canonicalPath)}${path.sep}`,
    };
  } catch (error) {
    for (const authority of opened) fs.closeSync(authority.descriptor);
    throw error;
  }
}

function reverifyPublicationToolchain(toolchain) {
  for (const authority of [
    toolchain.compiler, toolchain.compilerFrontend, toolchain.assembler, toolchain.linker,
  ]) {
    const stat = fs.fstatSync(authority.descriptor, { bigint: true });
    if (!sameStableFileStat(authority.stat, stat)
      || fs.realpathSync.native(descriptorPath(authority.descriptor)) !== authority.canonicalPath
      || sha256ExactDescriptor(authority.descriptor, authority.stat, {
        maximumBytes: maximumSystemExecutableBytes, label: authority.label,
      }) !== authority.fileSha256) {
      throw new Error(`${authority.label} authority changed during helper compilation`);
    }
  }
}

function toolchainManifest(toolchain) {
  for (const authority of [
    toolchain.compiler, toolchain.compilerFrontend, toolchain.assembler, toolchain.linker,
  ]) {
    if (authority.executedEvidence == null) {
      throw new Error(`missing executed-tool evidence for ${authority.label}`);
    }
  }
  return Object.freeze(Object.fromEntries([
    toolchain.compiler, toolchain.compilerFrontend, toolchain.assembler, toolchain.linker,
  ].map((authority) => [authority.label, Object.freeze({
    canonicalPath: authority.canonicalPath,
    fileSha256: authority.fileSha256,
    pinned: pinnedStat(authority.stat),
    ...(authority === toolchain.compiler ? {
      targetTriple: toolchain.targetTriple,
      fullVersion: toolchain.fullVersion,
      abiMajor: toolchain.abiMajor,
    } : {}),
    executedEvidence: authority.executedEvidence,
  })])));
}

function closePublicationToolchain(toolchain) {
  if (!toolchain) return;
  for (const authority of [
    toolchain.compiler, toolchain.compilerFrontend, toolchain.assembler, toolchain.linker,
  ]) {
    if (authority?.descriptor != null) fs.closeSync(authority.descriptor);
  }
}

function assertBuildFileAuthority({
  descriptor, initialStat, directoryDescriptor, name, expectedPath, expectedHash, label,
}) {
  const stat = fs.fstatSync(descriptor, { bigint: true });
  const named = fs.lstatSync(`${descriptorPath(directoryDescriptor)}/${name}`, { bigint: true });
  if (!sameStableFileStat(initialStat, stat) || !named.isFile() || named.isSymbolicLink()
    || !sameIdentity(named, stat)
    || fs.realpathSync.native(descriptorPath(descriptor)) !== expectedPath
    || sha256ExactDescriptor(descriptor, initialStat, {
      maximumBytes: maximumHelperArtifactBytes, label,
    }) !== expectedHash) {
    throw new Error(`${label} named authority changed`);
  }
  return stat;
}

function assertStaticLinuxX8664Elf(descriptor) {
  const stat = fs.fstatSync(descriptor);
  if (stat.size < 64 || stat.size > 8 * 1024 * 1024) {
    throw new Error('publication helper ELF size is outside the attested bound');
  }
  const header = Buffer.alloc(64);
  if (fs.readSync(descriptor, header, 0, header.length, 0) !== header.length
    || header.subarray(0, 4).toString('hex') !== '7f454c46'
    || header[4] !== 2 || header[5] !== 1
    || header.readUInt16LE(16) !== 2 || header.readUInt16LE(18) !== 62) {
    throw new Error('publication helper is not a static Linux x86-64 ELF executable');
  }
  const programOffset = Number(header.readBigUInt64LE(32));
  const programEntrySize = header.readUInt16LE(54);
  const programCount = header.readUInt16LE(56);
  const programBytes = programEntrySize * programCount;
  if (!Number.isSafeInteger(programOffset) || programEntrySize < 56 || programCount <= 0
    || programOffset < 64 || programBytes > 64 * 1024
    || programOffset + programBytes > stat.size) {
    throw new Error('publication helper ELF program table is invalid');
  }
  const programs = Buffer.alloc(programBytes);
  if (fs.readSync(descriptor, programs, 0, programs.length, programOffset) !== programs.length) {
    throw new Error('publication helper ELF program table is truncated');
  }
  for (let index = 0; index < programCount; index += 1) {
    if (programs.readUInt32LE(index * programEntrySize) === 3) {
      throw new Error('publication helper ELF unexpectedly has a dynamic interpreter');
    }
  }
  return Object.freeze({
    format: 'ELF64', byteOrder: 'little-endian', machine: 'x86-64',
    linkage: 'static', programHeaders: String(programCount),
  });
}

function cleanupUnlaunchedPublicationHelper(helper) {
  const failures = [];
  if (helper.directoryDescriptor == null || helper.directoryStat == null) {
    failures.push('helper directory was created but no pinned directory identity is available');
    closePublicationToolchain(helper.toolchain);
    if (helper.temporaryRootDescriptor != null) fs.closeSync(helper.temporaryRootDescriptor);
    helper.closed = true;
    return failures;
  }
  for (const item of [
    ['publisher.c', helper.sourceDescriptor, helper.sourceStat],
    ['publisher.o', helper.objectDescriptor, helper.objectStat],
    ['publisher', helper.descriptor, helper.stat],
  ]) {
    const [name, descriptor, expected] = item;
    const entry = `${descriptorPath(helper.directoryDescriptor)}/${name}`;
    try {
      if (!pathExists(entry)) continue;
      if (descriptor == null || expected == null) {
        throw new Error('entry exists without a pinned descriptor identity');
      }
      const named = fs.lstatSync(entry, { bigint: true });
      const current = fs.fstatSync(descriptor, { bigint: true });
      if (!sameIdentity(named, expected) || !sameIdentity(current, expected)) {
        throw new Error('entry differs from the pinned build inode');
      }
      fs.unlinkSync(entry);
      if (pathExists(entry)) throw new Error('entry remains after unlink');
      const unlinked = fs.fstatSync(descriptor, { bigint: true });
      if (!sameUnlinkedBuildFileStat(expected, unlinked)) {
        throw new Error('pinned build inode was not proven unlinked');
      }
    } catch (error) {
      failures.push(`${name}: ${error?.message || error}`);
    }
  }
  try { fs.fsyncSync(helper.directoryDescriptor); }
  catch (error) { failures.push(`helper directory fsync: ${error?.message || error}`); }
  for (const descriptor of [
    helper.descriptor, helper.objectDescriptor, helper.sourceDescriptor,
  ]) {
    if (descriptor == null) continue;
    try { fs.closeSync(descriptor); } catch (error) {
      failures.push(`helper file descriptor close: ${error?.message || error}`);
    }
  }
  closePublicationToolchain(helper.toolchain);
  try {
    const entries = fs.readdirSync(descriptorPath(helper.directoryDescriptor));
    if (entries.length) throw new Error(`private build directory retains ${entries.join(',')}`);
    const namedDirectory = fs.lstatSync(
      `${descriptorPath(helper.temporaryRootDescriptor)}/${helper.directoryName}`,
      { bigint: true },
    );
    const directoryStat = fs.fstatSync(helper.directoryDescriptor, { bigint: true });
    if (!sameIdentity(namedDirectory, helper.directoryStat)
      || !sameIdentity(directoryStat, helper.directoryStat)) {
      throw new Error('private build directory identity changed before removal');
    }
    fs.rmdirSync(`${descriptorPath(helper.temporaryRootDescriptor)}/${helper.directoryName}`);
    fs.fsyncSync(helper.temporaryRootDescriptor);
    if (pathExists(`${descriptorPath(helper.temporaryRootDescriptor)}/${helper.directoryName}`)) {
      throw new Error('private build directory remains after removal');
    }
    if (!sameUnlinkedDirectoryStat(
      directoryStat, fs.fstatSync(helper.directoryDescriptor, { bigint: true }),
    )) {
      throw new Error('pinned build directory was not proven unlinked');
    }
  } catch (error) {
    failures.push(`helper directory removal: ${error?.message || error}`);
  }
  for (const descriptor of [helper.directoryDescriptor, helper.temporaryRootDescriptor]) {
    if (descriptor == null) continue;
    try { fs.closeSync(descriptor); } catch (error) {
      failures.push(`helper directory descriptor close: ${error?.message || error}`);
    }
  }
  helper.closed = true;
  return failures;
}

function compilePublicationHelper(effectiveUser) {
  const temporaryRootDescriptor = fs.openSync(
    realTemporaryRoot,
    fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
  );
  const temporaryRootStat = fs.fstatSync(temporaryRootDescriptor, { bigint: true });
  if (!temporaryRootStat.isDirectory()
    || fs.realpathSync.native(descriptorPath(temporaryRootDescriptor)) !== realTemporaryRoot) {
    fs.closeSync(temporaryRootDescriptor);
    throw new Error('temporary build root is not exact and pinned');
  }
  const createdDirectory = fs.mkdtempSync(
    `${descriptorPath(temporaryRootDescriptor)}/eva-ledger-publisher-`,
  );
  const directoryName = path.basename(createdDirectory);
  const directory = path.join(realTemporaryRoot, directoryName);
  fs.chmodSync(createdDirectory, 0o700);
  let directoryDescriptor;
  let sourceDescriptor;
  let objectDescriptor;
  let descriptor;
  let toolchain;
  const helper = {
    temporaryRootDescriptor, temporaryRootStat, directoryName, directory,
    directoryDescriptor: null, directoryStat: null,
    sourceDescriptor: null, sourceStat: null, objectDescriptor: null, objectStat: null,
    descriptor: null, stat: null, toolchain: null,
    buildArtifactsRemoved: false, closed: false,
  };
  try {
    directoryDescriptor = fs.openSync(
      createdDirectory,
      fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW,
    );
    helper.directoryDescriptor = directoryDescriptor;
    const directoryStat = fs.fstatSync(directoryDescriptor, { bigint: true });
    helper.directoryStat = directoryStat;
    if (!directoryStat.isDirectory()
      || (effectiveUser != null && directoryStat.uid !== BigInt(effectiveUser))
      || (directoryStat.mode & 0o077n) !== 0n
      || fs.realpathSync.native(descriptorPath(directoryDescriptor)) !== directory
      || !sameIdentity(
        fs.lstatSync(`${descriptorPath(temporaryRootDescriptor)}/${directoryName}`, {
          bigint: true,
        }), directoryStat,
      )) {
      throw new Error('publication helper directory is not private and pinned');
    }
    toolchain = attestPublicationToolchain(directory);
    helper.toolchain = toolchain;
    const sourceName = 'publisher.c';
    const binaryName = 'publisher';
    const sourcePath = `${descriptorPath(directoryDescriptor)}/${sourceName}`;
    const binaryPath = `${descriptorPath(directoryDescriptor)}/${binaryName}`;
    fs.writeFileSync(sourcePath, publicationHelperSource, {
      encoding: 'utf8', flag: 'wx', mode: 0o600,
    });
    sourceDescriptor = fs.openSync(
      sourcePath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW,
    );
    helper.sourceDescriptor = sourceDescriptor;
    fs.fchmodSync(sourceDescriptor, 0o400);
    fs.fsyncSync(sourceDescriptor);
    const sourceStat = fs.fstatSync(sourceDescriptor, { bigint: true });
    helper.sourceStat = sourceStat;
    const sourceSha256 = sha256(publicationHelperSource);
    if (!sourceStat.isFile() || sourceStat.nlink !== 1n
      || (effectiveUser != null && sourceStat.uid !== BigInt(effectiveUser))
      || (sourceStat.mode & 0o077n) !== 0n
      || fs.realpathSync.native(descriptorPath(sourceDescriptor))
        !== path.join(directory, sourceName)
      || sha256ExactDescriptor(sourceDescriptor, sourceStat, {
        maximumBytes: maximumHelperArtifactBytes, label: 'publication helper source',
      }) !== sourceSha256) {
      throw new Error('publication helper source is not private, exact, and pinned');
    }
    waitForSynchronousTestHook(
      'EVA_LEDGER_RECOVERY_TEST_HOOK_HELPER_SOURCE_PINNED',
      canonicalJson({ directory, sourcePath: path.join(directory, sourceName) }),
    );
    assertBuildFileAuthority({
      descriptor: sourceDescriptor, initialStat: sourceStat, directoryDescriptor,
      name: sourceName, expectedPath: path.join(directory, sourceName),
      expectedHash: sourceSha256, label: 'publication helper source',
    });
    objectDescriptor = fs.openSync(
      descriptorPath(directoryDescriptor),
      openAnonymousTemporaryFile | fs.constants.O_RDWR, 0o600,
    );
    helper.objectDescriptor = objectDescriptor;
    const compilation = runPinnedCompiler(toolchain.compiler, [
      '-v',
      '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
      '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2',
      `-B${toolchain.compilerPrefix}`, `-B${toolchain.binaryPrefix}`,
      '-c', '-o', '/proc/self/fd/3', '-x', 'c', descriptorPath(sourceDescriptor),
    ], toolchain.environment, 'publication helper compilation', [objectDescriptor]);
    toolchain.compiler.executedEvidence = Object.freeze({
      method: 'pinned-descriptor-exec', phase: 'compile-anonymous-object',
      fileSha256: toolchain.compiler.fileSha256,
    });
    for (const authority of [
      toolchain.compilerFrontend, toolchain.assembler,
    ]) {
      if (!String(compilation.stderr || '').includes(` ${authority.canonicalPath} `)) {
        throw new Error(`compiler trace did not prove exact ${authority.label} execution`);
      }
      authority.executedEvidence = Object.freeze({
        method: 'pinned-driver-verbose-trace', phase: 'compile-anonymous-object',
        fileSha256: authority.fileSha256,
      });
    }
    fs.fchmodSync(objectDescriptor, 0o400);
    fs.fsyncSync(objectDescriptor);
    const objectStat = fs.fstatSync(objectDescriptor, { bigint: true });
    helper.objectStat = objectStat;
    const objectSha256 = sha256ExactDescriptor(objectDescriptor, objectStat, {
      maximumBytes: maximumHelperArtifactBytes, label: 'publication helper object',
    });
    if (!objectStat.isFile() || objectStat.nlink !== 0n
      || (effectiveUser != null && objectStat.uid !== BigInt(effectiveUser))
      || (objectStat.mode & 0o077n) !== 0n) {
      throw new Error('publication helper object is not private and pinned');
    }
    const compilerLibraryRoot = `/usr/lib/gcc/${toolchain.targetTriple}/${toolchain.abiMajor}`;
    const multiarchLibraryRoot = `/usr/lib/${toolchain.targetTriple}`;
    const systemLinkInputs = [
      `${multiarchLibraryRoot}/crt1.o`, `${multiarchLibraryRoot}/crti.o`,
      `${compilerLibraryRoot}/crtbeginT.o`,
    ];
    const systemLinkTail = [
      '--start-group', `${compilerLibraryRoot}/libgcc.a`,
      `${compilerLibraryRoot}/libgcc_eh.a`, `${multiarchLibraryRoot}/libc.a`, '--end-group',
      `${compilerLibraryRoot}/crtend.o`, `${multiarchLibraryRoot}/crtn.o`,
    ];
    for (const input of [...systemLinkInputs, ...systemLinkTail.filter((item) => item[0] === '/')]) {
      const canonical = fs.realpathSync.native(input);
      if (!(canonical === '/usr' || strictDescendant('/usr', canonical))) {
        throw new Error(`static linker input is outside /usr: ${input}`);
      }
    }
    runPinnedCompiler(toolchain.linker, [
      '--fatal-warnings', '-static', '-z', 'relro', '-z', 'now', '-z', 'noexecstack',
      '-o', binaryPath, ...systemLinkInputs, '/proc/self/fd/3', ...systemLinkTail,
    ], toolchain.environment, 'publication helper direct pinned link', [objectDescriptor]);
    toolchain.linker.executedEvidence = Object.freeze({
      method: 'pinned-descriptor-exec', phase: 'link-static-helper',
      fileSha256: toolchain.linker.fileSha256,
    });
    descriptor = fs.openSync(binaryPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    helper.descriptor = descriptor;
    fs.fchmodSync(descriptor, 0o500);
    fs.fsyncSync(descriptor);
    fs.fsyncSync(directoryDescriptor);
    const stat = fs.fstatSync(descriptor, { bigint: true });
    helper.stat = stat;
    const sourceStatAfter = fs.fstatSync(sourceDescriptor, { bigint: true });
    if (!stat.isFile() || stat.nlink !== 1n
      || (effectiveUser != null && stat.uid !== BigInt(effectiveUser))
      || (stat.mode & 0o077n) !== 0n
      || !sameStableFileStat(sourceStat, sourceStatAfter)
      || sha256ExactDescriptor(sourceDescriptor, sourceStat, {
        maximumBytes: maximumHelperArtifactBytes, label: 'publication helper source',
      }) !== sourceSha256) {
      throw new Error('compiled publication helper is not private, exact, and pinned');
    }
    const binarySha256 = sha256ExactDescriptor(descriptor, stat, {
      maximumBytes: maximumHelperArtifactBytes, label: 'publication helper binary',
    });
    const binaryElf = assertStaticLinuxX8664Elf(descriptor);
    const objectStatFinal = fs.fstatSync(objectDescriptor, { bigint: true });
    if (!sameStableFileStat(objectStat, objectStatFinal) || objectStatFinal.nlink !== 0n
      || sha256ExactDescriptor(objectDescriptor, objectStatFinal, {
        maximumBytes: maximumHelperArtifactBytes, label: 'publication helper object',
      }) !== objectSha256) {
      throw new Error('anonymous publication helper object changed during direct link');
    }
    fs.closeSync(objectDescriptor);
    objectDescriptor = null;
    helper.objectDescriptor = null;
    helper.objectEvidence = Object.freeze({
      storage: 'O_TMPFILE-anonymous', fileSha256: objectSha256,
      pinned: pinnedStat(objectStat), afterLink: pinnedStat(objectStatFinal),
    });
    waitForSynchronousTestHook(
      'EVA_LEDGER_RECOVERY_TEST_HOOK_HELPER_BINARY_PINNED',
      canonicalJson({ directory, binaryPath: path.join(directory, binaryName) }),
    );
    assertBuildFileAuthority({
      descriptor: sourceDescriptor, initialStat: sourceStat, directoryDescriptor,
      name: sourceName, expectedPath: path.join(directory, sourceName),
      expectedHash: sourceSha256, label: 'publication helper source',
    });
    assertBuildFileAuthority({
      descriptor, initialStat: stat, directoryDescriptor,
      name: binaryName, expectedPath: path.join(directory, binaryName),
      expectedHash: binarySha256, label: 'publication helper binary',
    });
    reverifyPublicationToolchain(toolchain);
    return Object.assign(helper, {
      sourceDescriptor, sourceStat, sourceSha256,
      descriptor, stat, binarySha256, binaryElf,
      toolchainManifest: toolchainManifest(toolchain),
    });
  } catch (error) {
    const failures = cleanupUnlaunchedPublicationHelper(helper);
    throw new Error(
      `${error?.message || error}${failures.length
        ? `; private helper cleanup incomplete: ${failures.join('; ')}`
        : '; private helper build artifacts removed'}`,
    );
  }
}

function parseHelperIdentity(line, phase) {
  const match = new RegExp(
    `^${phase} ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([a-f0-9]{64})$`, 'u',
  ).exec(line);
  if (!match) throw new Error(`publication helper protocol error at ${phase}: ${line}`);
  return Object.freeze({
    dev: match[1], ino: match[2], nlink: match[3], size: match[4], fileSha256: match[5],
  });
}

function parseHelperCleanup(line) {
  const match = /^CLEANED ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+) ([0-9]+)$/u.exec(line);
  if (!match) throw new Error(`publication helper protocol error at CLEANED: ${line}`);
  return Object.freeze({
    sourceDev: match[1], sourceIno: match[2], sourceNlink: match[3],
    binaryDev: match[4], binaryIno: match[5], binaryNlink: match[6],
    directoryDev: match[7], directoryIno: match[8], directoryNlink: match[9],
  });
}

async function publisherDeadline(publisher, operation, label) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_resolve, reject) => {
        timer = setTimeout(() => {
          publisher.child.kill('SIGKILL');
          reject(new Error(
            `publication helper ${label} exceeded ${publisherProtocolTimeoutMs}ms hard timeout`,
          ));
        }, publisherProtocolTimeoutMs);
      }),
    ]);
  } finally {
    if (timer != null) clearTimeout(timer);
  }
}

async function writePublisherInput(publisher, value, label) {
  await publisherDeadline(publisher, new Promise((resolve, reject) => {
    publisher.child.stdin.write(value, (error) => error ? reject(error) : resolve());
  }), label);
}

async function nextPublisherLine(publisher, phase) {
  const result = await publisherDeadline(publisher, publisher.lines.next(), `${phase} response`);
  if (!result.done) return String(result.value);
  const exit = await publisherDeadline(publisher, publisher.exit, `${phase} exit observation`);
  throw new Error(
    `publication helper exited before ${phase}: status=${exit.status} signal=${exit.signal || 'none'} stderr=${publisher.stderr}`,
  );
}

async function startPublisher(helper, parentDescriptor, targetName, bytes, expectedHash) {
  const child = spawn(
    descriptorPath(helper.descriptor),
    [targetName, String(bytes.length), expectedHash, helper.directoryName],
    {
      stdio: [
        'pipe', 'pipe', 'pipe', parentDescriptor, helper.directoryDescriptor,
        helper.temporaryRootDescriptor, helper.sourceDescriptor, helper.descriptor,
      ],
      env: { LANG: 'C', LC_ALL: 'C' },
    },
  );
  const lineInterface = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  const publisher = {
    child, lineInterface, lines: lineInterface[Symbol.asyncIterator](), stderr: '',
    stdoutBytes: 0, releaseRequested: false, linkRefused: false, cleanup: null,
    exit: new Promise((resolve) => child.once('close', (status, signal) => resolve({ status, signal }))),
    spawned: new Promise((resolve, reject) => {
      child.once('spawn', resolve);
      child.once('error', reject);
    }),
    verified: null, linked: null, done: null,
  };
  child.stdout.on('data', (chunk) => {
    publisher.stdoutBytes += chunk.length;
    if (publisher.stdoutBytes > publisherMaximumProtocolBytes) child.kill('SIGKILL');
  });
  child.stderr.on('data', (chunk) => {
    publisher.stderr = `${publisher.stderr}${chunk}`.slice(-64 * 1024);
  });
  try {
    await publisherDeadline(publisher, publisher.spawned, 'spawn');
    publisher.cleanup = parseHelperCleanup(await nextPublisherLine(publisher, 'CLEANED'));
    if (publisher.cleanup.sourceDev !== String(helper.sourceStat.dev)
      || publisher.cleanup.sourceIno !== String(helper.sourceStat.ino)
      || publisher.cleanup.sourceNlink !== '0'
      || publisher.cleanup.binaryDev !== String(helper.stat.dev)
      || publisher.cleanup.binaryIno !== String(helper.stat.ino)
      || publisher.cleanup.binaryNlink !== '0'
      || publisher.cleanup.directoryDev !== String(helper.directoryStat.dev)
      || publisher.cleanup.directoryIno !== String(helper.directoryStat.ino)
      || publisher.cleanup.directoryNlink !== '0'
      || pathExists(`${descriptorPath(helper.temporaryRootDescriptor)}/${helper.directoryName}`)) {
      throw new Error('publication helper cleanup proof differs from pinned build authority');
    }
    helper.buildArtifactsRemoved = true;
    await writePublisherInput(publisher, bytes, 'candidate byte transfer');
    publisher.verified = parseHelperIdentity(
      await nextPublisherLine(publisher, 'VERIFIED'), 'VERIFIED',
    );
    if (publisher.verified.nlink !== '0'
      || publisher.verified.size !== String(bytes.length)
      || publisher.verified.fileSha256 !== expectedHash) {
      throw new Error('publication helper VERIFIED identity differs from serialized bytes');
    }
    return publisher;
  } catch (error) {
    await stopPublisher(publisher);
    throw error;
  }
}

async function releasePublisherLink(publisher, expectedSize, expectedHash) {
  publisher.releaseRequested = true;
  const command = process.env.NODE_TEST_CONTEXT != null
    && process.env.EVA_LEDGER_RECOVERY_TEST_HELPER_DIE_BEFORE_LINK === 'true' ? 'D'
    : process.env.NODE_TEST_CONTEXT != null
      && process.env.EVA_LEDGER_RECOVERY_TEST_HELPER_DIE_AFTER_LINK === 'true' ? 'K' : 'L';
  await writePublisherInput(publisher, Buffer.from(command), 'link release');
  const response = await nextPublisherLine(publisher, 'LINKED');
  if (response === 'UNLINKED') {
    publisher.linkRefused = true;
    const exit = await publisherDeadline(publisher, publisher.exit, 'UNLINKED exit');
    throw new Error(
      `publication helper refused final link: status=${exit.status} signal=${exit.signal || 'none'} stderr=${publisher.stderr}`,
    );
  }
  publisher.linked = parseHelperIdentity(response, 'LINKED');
  if (publisher.linked.nlink !== '1' || publisher.linked.size !== String(expectedSize)
    || publisher.linked.fileSha256 !== expectedHash
    || publisher.linked.dev !== publisher.verified.dev
    || publisher.linked.ino !== publisher.verified.ino) {
    throw new Error('publication helper LINKED identity differs from verified O_TMPFILE');
  }
  return publisher.linked;
}

async function acknowledgePublisher(publisher, expectedSize, expectedHash) {
  await writePublisherInput(publisher, Buffer.from('A'), 'parent acknowledgement');
  publisher.done = parseHelperIdentity(
    await nextPublisherLine(publisher, 'DONE'), 'DONE',
  );
  if (publisher.done.nlink !== '1' || publisher.done.size !== String(expectedSize)
    || publisher.done.fileSha256 !== expectedHash
    || publisher.done.dev !== publisher.linked.dev
    || publisher.done.ino !== publisher.linked.ino) {
    throw new Error('publication helper DONE identity differs from linked O_TMPFILE');
  }
  publisher.child.stdin.end();
  const exit = await publisherDeadline(publisher, publisher.exit, 'clean exit');
  if (exit.status !== 0 || exit.signal != null) {
    throw new Error(`publication helper failed after acknowledgement: ${canonicalJson(exit)}`);
  }
  return publisher.done;
}

async function stopPublisher(publisher) {
  if (!publisher?.child || publisher.child.exitCode != null || publisher.child.signalCode != null) return;
  publisher.child.stdin.destroy();
  publisher.child.kill('SIGKILL');
  await publisherDeadline(publisher, publisher.exit, 'forced stop');
}

function namedEntryEvidence(label, parentDescriptor, name, descriptor = null) {
  if (parentDescriptor == null || !name) return null;
  try {
    const stat = fs.lstatSync(
      `${descriptorPath(parentDescriptor)}/${name}`, { bigint: true },
    );
    let relation = 'uncompared';
    if (descriptor != null) {
      try {
        relation = sameIdentity(stat, fs.fstatSync(descriptor, { bigint: true }))
          ? 'same-inode' : 'different-inode';
      }
      catch { relation = 'descriptor-unavailable'; }
    }
    return `${label} name=${name} state=present relation=${relation} dev=${stat.dev} ino=${stat.ino} nlink=${stat.nlink} size=${stat.size} mode=${modeString(stat)}`;
  } catch (error) {
    if (error?.code === 'ENOENT') return `${label} name=${name} state=absent`;
    return `${label} name=${name} state=unavailable error=${error?.message || error}`;
  }
}

function observeFinalLinkState({
  published, publisher, parentDescriptor, targetName, targetDescriptor,
}) {
  if (published || publisher?.linked?.nlink === '1') return 'true';
  let named;
  try {
    named = fs.lstatSync(
      `${descriptorPath(parentDescriptor)}/${targetName}`, { bigint: true },
    );
  } catch (error) {
    if (error?.code !== 'ENOENT') return 'uncertain';
    if (publisher?.linkRefused === true || publisher?.releaseRequested !== true) return 'false';
    return 'uncertain';
  }
  try {
    if (targetDescriptor != null
      && sameIdentity(named, fs.fstatSync(targetDescriptor, { bigint: true }))) {
      return 'true';
    }
  } catch { return 'uncertain'; }
  if (publisher?.verified
    && String(named.dev) === publisher.verified.dev
    && String(named.ino) === publisher.verified.ino) return 'true';
  if (publisher?.linkRefused === true) return 'false';
  return publisher?.releaseRequested === true ? 'uncertain' : 'false';
}

function waitForTestHook(variableName) {
  const hook = process.env[variableName];
  if (!hook || process.env.NODE_TEST_CONTEXT == null) return Promise.resolve();
  fs.writeFileSync(hook, 'ready');
  return new Promise((resolve) => {
    const poll = () => {
      if (!pathExists(hook)) resolve();
      else setTimeout(poll, 5);
    };
    poll();
  });
}

async function recover(sourceArgument, targetArgument) {
  const authority = openAuthorities(sourceArgument, targetArgument);
  const {
    sourceDescriptor, sourceStat, sourceName, sourceParentDescriptor, sourceParentStat,
    target, targetName, parentDescriptor, parentStat, effectiveUser,
  } = authority;
  let capacity;
  let sourceSizedCapacity;
  let sourceHashBefore;
  let before;
  let after;
  let journalTransition;
  let helper;
  let publisher;
  let targetAuthority;
  let targetDescriptor;
  let published = false;
  let sourceBytes;
  let serializedBytes;
  try {
    if (sourceStat.size > BigInt(maximumSourceBytes)) {
      throw new Error(`source exceeds ${maximumSourceBytes} byte recovery cap`);
    }
    sourceSizedCapacity = publicationFilesystemCapacity(parentDescriptor, sourceStat.size);
    sourceHashBefore = sha256ExactDescriptor(sourceDescriptor, sourceStat, {
      maximumBytes: maximumSourceBytes, label: 'source',
    });
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_PINNED');
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_PARENT_PINNED');
    sourceBytes = readDescriptorBuffer(sourceDescriptor);
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_SOURCE_BUFFERED');
    const sourceHashAfter = sha256ExactDescriptor(sourceDescriptor, sourceStat, {
      maximumBytes: maximumSourceBytes, label: 'source',
    });
    if (sourceHashBefore !== sourceHashAfter) throw new Error('source changed during recovery copy');
    const sourceStatAfter = fs.fstatSync(sourceDescriptor, { bigint: true });
    if (!sameStableFileStat(sourceStat, sourceStatAfter)
      || BigInt(sourceBytes.length) !== sourceStat.size
      || sha256(sourceBytes) !== sourceHashBefore) {
      throw new Error('source descriptor metadata changed during recovery copy');
    }
    assertNoSidecars(sourceParentDescriptor, sourceName, 'source');
    const sourceHeader = sqliteJournalHeader(sourceBytes, 'source');
    if (sourceHeader !== 'rollback') {
      throw new Error('source main file uses WAL header and is not a standalone recovery image');
    }
    const candidateDatabase = new Database(sourceBytes);
    sourceBytes = null;
    candidateDatabase.defaultSafeIntegers(true);
    try {
      configureAnonymousMemoryDatabase(candidateDatabase, 'candidate');
      boundedDatabaseByteEstimate(candidateDatabase, 'source candidate');
      before = sourceManifest(
        candidateDatabase, 'delete',
      );
      journalTransition = Object.freeze({
        before: before.persistentPragmas.journalMode,
        checkpoint: null,
        after: 'delete',
        mechanism: 'anonymous-in-memory-rebuild-and-serialize',
      });
      rebuildLedger(candidateDatabase);
      after = production14Manifest(candidateDatabase, 'delete');
      sameManifest(after.oldRows, before.oldRows, 'old ledger fields');
      sameManifest(after.nonLedger, before.nonLedger, 'non-ledger data');
      sameManifest(
        stablePersistentPragmas(after.persistentPragmas),
        stablePersistentPragmas(before.persistentPragmas),
        'persistent database pragmas',
      );
      if (after.persistentPragmas.journalMode !== 'delete') {
        throw new Error('target journal mode is not DELETE');
      }
      if (after.persistentPragmas.schemaVersion === before.persistentPragmas.schemaVersion) {
        throw new Error('schema_version did not record the intentional ledger rebuild');
      }
      boundedDatabaseByteEstimate(candidateDatabase, 'rebuilt candidate');
      assertAnonymousMemoryDatabase(candidateDatabase, 'rebuilt candidate');
      serializedBytes = candidateDatabase.serialize();
      if (serializedBytes.length <= 0 || serializedBytes.length > maximumSourceBytes) {
        throw new Error(`serialized candidate exceeds ${maximumSourceBytes} byte cap`);
      }
    } finally {
      candidateDatabase.close();
    }
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_CANDIDATE_REBUILT');
    const candidateVerification = verifyImmutableBuffer(
      serializedBytes, production14Manifest, 'serialized candidate',
    );
    compareProduction14Manifests(
      candidateVerification.manifest, after, 'immutable candidate verification',
    );
    if (candidateVerification.journalHeader !== 'rollback'
      || candidateVerification.manifest.persistentPragmas.journalMode !== 'delete') {
      throw new Error('serialized candidate journal state is not rollback/DELETE');
    }
    capacity = publicationFilesystemCapacity(parentDescriptor, serializedBytes.length);
    assertNoSidecars(parentDescriptor, targetName, 'target recovery');
    helper = compilePublicationHelper(effectiveUser);
    if (sha256ExactDescriptor(helper.descriptor, helper.stat, {
      maximumBytes: maximumHelperArtifactBytes, label: 'publication helper binary',
    }) !== helper.binarySha256) {
      throw new Error('publication helper changed after compilation');
    }
    publisher = await startPublisher(
      helper, parentDescriptor, targetName, serializedBytes,
      candidateVerification.fileSha256,
    );
    serializedBytes = null;
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_PUBLICATION_READY');
    assertNoSidecars(parentDescriptor, targetName, 'target recovery');
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_BEFORE_LINK_RELEASE');
    const linked = await releasePublisherLink(
      publisher, Number(candidateVerification.byteLength), candidateVerification.fileSha256,
    );
    published = true;
    targetDescriptor = fs.openSync(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const openedTargetStat = fs.fstatSync(targetDescriptor, { bigint: true });
    if (String(openedTargetStat.dev) !== linked.dev || String(openedTargetStat.ino) !== linked.ino
      || openedTargetStat.nlink !== 1n
      || modeString(openedTargetStat) !== '0600'
      || (effectiveUser != null && openedTargetStat.uid !== BigInt(effectiveUser))
      || String(openedTargetStat.size) !== candidateVerification.byteLength) {
      throw new Error('final link does not reference the verified anonymous publication inode');
    }
    targetAuthority = {
      descriptor: targetDescriptor, parentDescriptor, name: targetName,
      expectedPath: path.join(fs.realpathSync.native(descriptorPath(parentDescriptor)), targetName),
      initialStat: openedTargetStat, effectiveUser, label: 'target',
    };
    assertPrivateFileAuthority(targetAuthority, 'after final link', {
      expectedSize: openedTargetStat.size, expectedNlink: 1,
    });
    assertNoSidecars(parentDescriptor, targetName, 'target recovery');
    await waitForTestHook('EVA_LEDGER_RECOVERY_TEST_HOOK_AFTER_PUBLISH');
    if (process.env.NODE_TEST_CONTEXT != null
      && process.env.EVA_LEDGER_RECOVERY_TEST_FAIL_AFTER_PUBLISH === 'true') {
      throw new Error('forced recovery failure after publication');
    }
    assertPrivateFileAuthority(targetAuthority, 'after publication', {
      expectedSize: openedTargetStat.size, expectedNlink: 1,
    });
    assertNoSidecars(parentDescriptor, targetName, 'target recovery');
    const targetHash = sha256ExactDescriptor(targetDescriptor, openedTargetStat, {
      maximumBytes: maximumSourceBytes, label: 'target',
    });
    if (targetHash !== candidateVerification.fileSha256) {
      throw new Error('published target is not the verified recovery candidate');
    }
    let finalBytes = readDescriptorBuffer(targetDescriptor);
    const targetVerification = verifyImmutableBuffer(
      finalBytes, production14Manifest, 'published target',
    );
    finalBytes = null;
    compareProduction14Manifests(
      targetVerification.manifest, after, 'immutable published-target verification',
    );
    if (targetVerification.fileSha256 !== candidateVerification.fileSha256
      || targetVerification.journalHeader !== 'rollback'
      || targetVerification.manifest.persistentPragmas.journalMode !== 'delete') {
      throw new Error('published target immutable verification differs from candidate');
    }
    await acknowledgePublisher(
      publisher, Number(candidateVerification.byteLength), candidateVerification.fileSha256,
    );
    publisher = null;
    const finalTargetStat = assertPrivateFileAuthority(targetAuthority, 'after helper close', {
      expectedSize: openedTargetStat.size, expectedNlink: 1,
    });
    assertNoSidecars(parentDescriptor, targetName, 'target recovery');
    const targetHashFinal = sha256ExactDescriptor(targetDescriptor, finalTargetStat, {
      maximumBytes: maximumSourceBytes, label: 'target',
    });
    if (targetHashFinal !== targetHash
      || targetHashFinal !== candidateVerification.fileSha256) {
      throw new Error('published target changed after immutable verification');
    }
    const helperSourceStatFinal = fs.fstatSync(helper.sourceDescriptor, { bigint: true });
    const helperStatFinal = fs.fstatSync(helper.descriptor, { bigint: true });
    const helperDirectoryStatFinal = fs.fstatSync(helper.directoryDescriptor, { bigint: true });
    if (!sameUnlinkedBuildFileStat(helper.sourceStat, helperSourceStatFinal)
      || !sameUnlinkedBuildFileStat(helper.stat, helperStatFinal)
      || !sameUnlinkedDirectoryStat(helper.directoryStat, helperDirectoryStatFinal)
      || sha256ExactDescriptor(helper.sourceDescriptor, helperSourceStatFinal, {
        maximumBytes: maximumHelperArtifactBytes, label: 'publication helper source',
      }) !== helper.sourceSha256
      || sha256ExactDescriptor(helper.descriptor, helperStatFinal, {
        maximumBytes: maximumHelperArtifactBytes, label: 'publication helper binary',
      }) !== helper.binarySha256) {
      throw new Error('pinned publication helper changed during execution');
    }
    reverifyPublicationToolchain(helper.toolchain);
    const sourceStatFinal = fs.fstatSync(sourceDescriptor, { bigint: true });
    const sourceHashFinal = sha256ExactDescriptor(sourceDescriptor, sourceStatFinal, {
      maximumBytes: maximumSourceBytes, label: 'source',
    });
    if (!sameStableFileStat(sourceStat, sourceStatFinal)
      || sourceHashFinal !== sourceHashBefore) {
      throw new Error('source changed before final recovery report');
    }
    assertNoSidecars(sourceParentDescriptor, sourceName, 'source');
    const sourceNamedState = pinnedNameState(
      sourceParentDescriptor, sourceName, sourceStatFinal,
    );
    const currentSourceDescriptorPath = fs.realpathSync.native(
      descriptorPath(sourceDescriptor),
    );
    const currentSourceParentDescriptorPath = fs.realpathSync.native(
      descriptorPath(sourceParentDescriptor),
    );
    const currentTargetDescriptorPath = fs.realpathSync.native(
      descriptorPath(targetDescriptor),
    );
    const currentTargetParentDescriptorPath = fs.realpathSync.native(
      descriptorPath(parentDescriptor),
    );
    const currentTargetPath = currentTargetDescriptorPath;
    const body = Object.freeze({
      contract: 'ExecutionLedgerProduction14RecoveryReport/v1',
      limits: Object.freeze({
        maximumSourceBytes: String(maximumSourceBytes),
        maximumLedgerRows: String(maximumLedgerRows),
        maximumNonLedgerRows: String(maximumNonLedgerRows),
        maximumTables: String(maximumTables),
        maximumSchemaObjects: String(maximumSchemaObjects),
        compilerTimeoutMs: String(compilerTimeoutMs),
        publisherProtocolTimeoutMs: String(publisherProtocolTimeoutMs),
      }),
      source: Object.freeze({
        requestedPath: path.resolve(sourceArgument), currentDescriptorPath: currentSourceDescriptorPath,
        currentParentDescriptorPath: currentSourceParentDescriptorPath,
        pinned: pinnedStat(sourceStat),
        pinnedParent: Object.freeze({
          dev: String(sourceParentStat.dev), ino: String(sourceParentStat.ino),
        }),
        fileSha256Before: sourceHashBefore, fileSha256After: sourceHashFinal,
        metadataBefore: pinnedStat(sourceStat), metadataAfter: pinnedStat(sourceStatFinal),
        modeBefore: modeString(sourceStat), modeAfter: modeString(sourceStatFinal),
        sidecarAuthority: Object.freeze({
          scope: 'pinned-original-parent-and-basename',
          parentDev: String(sourceParentStat.dev), parentIno: String(sourceParentStat.ino),
          basename: sourceName,
          checkedSuffixes: Object.freeze(['-wal', '-shm', '-journal']),
          checkedAt: Object.freeze([
            'authority-open', 'after-source-buffer-read', 'before-final-report',
          ]),
          absentAtFinalCheck: true,
          namedSourceState: sourceNamedState,
        }),
        layout: before.schema.layout, schema: before.schema,
        classification: before.classification, oldRows: before.oldRows,
        nonLedger: before.nonLedger, persistentPragmas: before.persistentPragmas,
        checks: before.checks,
      }),
      anonymousCandidate: Object.freeze({
        storage: 'anonymous-in-memory-buffer',
        byteLength: candidateVerification.byteLength,
        fileSha256: candidateVerification.fileSha256,
        immutableVerification: candidateVerification,
      }),
      target: Object.freeze({
        requestedPath: path.resolve(targetArgument),
        currentDescriptorPath: currentTargetDescriptorPath,
        currentParentDescriptorPath: currentTargetParentDescriptorPath,
        currentPath: currentTargetPath,
        pinnedParent: Object.freeze({ dev: String(parentStat.dev), ino: String(parentStat.ino) }),
        pinnedFile: pinnedStat(finalTargetStat),
        fileSha256: targetHashFinal, layout: 'production14',
        schema: after.schema, oldRows: after.oldRows,
        nonLedger: after.nonLedger, persistentPragmas: after.persistentPragmas,
        checks: after.checks, immutableVerification: targetVerification,
        publication: Object.freeze({
          mechanism: 'O_TMPFILE+linkat(AT_EMPTY_PATH)', finalNlink: '1',
          finalLinkObserved: 'true',
          helperSourceSha256: helper.sourceSha256,
          helperPinnedSource: pinnedStat(helper.sourceStat),
          helperUnlinkedSource: pinnedStat(helperSourceStatFinal),
          helperObjectEvidence: helper.objectEvidence,
          helperBinarySha256: helper.binarySha256,
          helperBinaryFormat: helper.binaryElf,
          helperPinnedFile: pinnedStat(helper.stat),
          helperUnlinkedFile: pinnedStat(helperStatFinal),
          helperPinnedDirectory: pinnedStat(helper.directoryStat),
          helperUnlinkedDirectory: pinnedStat(helperDirectoryStatFinal),
          buildArtifactsRemoved: helper.buildArtifactsRemoved,
          toolchain: helper.toolchainManifest,
        }),
        capacity, sourceSizedCapacity, mode: '0600', sidecars: false,
      }),
      changes: Object.freeze({
        removedColumns: v2OnlyColumnNames,
        removedIndexes: Object.freeze(['idx_turn_tool_executions_sequence']),
        removedTriggers: executionLedgerTriggerNames,
        otherTablesChanged: false,
        journalMode: Object.freeze({
          before: before.persistentPragmas.journalMode,
          checkpoint: journalTransition.checkpoint,
          after: after.persistentPragmas.journalMode,
          intentional: true,
        }),
        schemaVersion: Object.freeze({
          before: before.persistentPragmas.schemaVersion,
          after: after.persistentPragmas.schemaVersion,
          intentional: true,
        }),
      }),
    });
    return Object.freeze({ ...body, reportSha256: sha256(canonicalJson(body)) });
  } catch (error) {
    let stopFailure = null;
    try { await stopPublisher(publisher); } catch (failure) { stopFailure = failure; }
    const finalLinkObserved = observeFinalLinkState({
      published, publisher, parentDescriptor, targetName, targetDescriptor,
    });
    let buildCleanupFailures = [];
    if (helper && !helper.buildArtifactsRemoved && !helper.closed) {
      buildCleanupFailures = cleanupUnlaunchedPublicationHelper(helper);
    }
    const evidenceFsyncFailures = [];
    for (const [label, descriptor] of [
      ['target', targetDescriptor], ['target parent', parentDescriptor],
    ]) {
      if (descriptor == null) continue;
      try { fs.fsyncSync(descriptor); }
      catch (failure) {
        evidenceFsyncFailures.push(`${label}: ${failure?.message || failure}`);
      }
    }
    const evidence = [
      failureEvidence('target', targetDescriptor),
      namedEntryEvidence('final target', parentDescriptor, targetName, targetDescriptor),
    ].filter(Boolean).join('; ');
    const durability = evidenceFsyncFailures.length
      ? `; evidence fsync failed: ${evidenceFsyncFailures.join('; ')}`
      : '; open evidence descriptors fsynced';
    throw new Error(
      `${error?.message || error}; finalLinkObserved=${finalLinkObserved}; final-link-observed=${finalLinkObserved}; automatic-unlink-attempted=false${stopFailure ? `; helper stop failed: ${stopFailure?.message || stopFailure}` : ''}${buildCleanupFailures.length ? `; private helper cleanup incomplete: ${buildCleanupFailures.join('; ')}` : ''}${evidence ? `; observed evidence: ${evidence}` : ''}${durability}`,
    );
  } finally {
    if (targetDescriptor != null) fs.closeSync(targetDescriptor);
    if (helper && !helper.closed) {
      if (helper.descriptor != null) fs.closeSync(helper.descriptor);
      if (helper.sourceDescriptor != null) fs.closeSync(helper.sourceDescriptor);
      if (helper.directoryDescriptor != null) fs.closeSync(helper.directoryDescriptor);
      if (helper.temporaryRootDescriptor != null) fs.closeSync(helper.temporaryRootDescriptor);
      closePublicationToolchain(helper.toolchain);
      helper.closed = true;
    }
    fs.closeSync(sourceDescriptor);
    fs.closeSync(sourceParentDescriptor);
    fs.closeSync(parentDescriptor);
  }
}

try {
  const { source, target } = parseArguments(process.argv.slice(2));
  const report = await recover(source, target);
  process.stdout.write(`${JSON.stringify(report)}\n`);
} catch (error) {
  process.stderr.write(`execution-ledger recovery refused: ${error?.message || error}\n`);
  process.exitCode = 1;
}
