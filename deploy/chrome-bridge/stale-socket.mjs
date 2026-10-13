import {lstat,unlink} from 'node:fs/promises';
import {createConnection} from 'node:net';

// A Unix socket pathname can survive a host reboot. Never remove a live
// listener, another owner's file, a symlink or a regular file.
export async function removeStaleSocket(path) {
  let before;
  try {before=await lstat(path);}catch(error){if(error.code==='ENOENT')return;throw error;}
  if(!before.isSocket() || before.uid!==process.getuid())throw Error('Unexpected socket file ownership or type');
  const active=await new Promise((resolve,reject)=>{
    const connection=createConnection(path);
    connection.setTimeout(2000,()=>{connection.destroy();reject(Error('Socket probe timed out'));});
    connection.once('connect',()=>{connection.destroy();resolve(true);});
    connection.once('error',error=>{connection.destroy();if(['ECONNREFUSED','ENOENT'].includes(error.code))resolve(false);else reject(error);});
  });
  if(active)throw Error('Chrome socket already has an active listener');
  let current;
  try {current=await lstat(path);}catch(error){if(error.code==='ENOENT')return;throw error;}
  if(current.ino!==before.ino || current.dev!==before.dev || !current.isSocket())throw Error('Socket changed during probe');
  await unlink(path);
}
