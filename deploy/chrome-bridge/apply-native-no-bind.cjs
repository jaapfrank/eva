// Apply only the reviewed factory opt-out; the original signed tarball remains the source baseline.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const expected=require('./no-bind-manifest.json');
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
function apply(root=__dirname){
 const file=path.join(root,expected.path),patch=path.join(root,'core-no-bind.patch');
 if(digest(fs.readFileSync(patch))!==expected.patchSha256)throw Error('No-bind patch provenance differs');
 const info=fs.lstatSync(file);if(!info.isFile()||info.isSymbolicLink()||info.nlink!==1)throw Error('Unsafe native source file');
 const data=fs.readFileSync(file),hash=digest(data);
 if(hash===expected.afterSha256)return{alreadyApplied:true,sha256:hash};
 if(hash!==expected.beforeSha256)throw Error('Unreviewed native source baseline');
 const needle='const { endpoint } = await browser.bind(bindOptions.title, { workspaceDir: bindOptions.workspaceDir });';const text=data.toString('utf8');
 if(text.split(needle).length!==2)throw Error('Native factory changed');
 const result=Buffer.from(text.replace(needle,'const { endpoint } = bindOptions.evaNoBind === true ? {} : await browser.bind(bindOptions.title, { workspaceDir: bindOptions.workspaceDir });'));
 if(digest(result)!==expected.afterSha256)throw Error('No-bind derivative differs');
 const temporary=file+'.eva-'+crypto.randomBytes(12).toString('hex');let fd;
 try{fd=fs.openSync(temporary,'wx',info.mode&0o777);fs.writeFileSync(fd,result);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;if(process.platform!=='win32')fs.chownSync(temporary,info.uid,info.gid);fs.renameSync(temporary,file);if(process.platform!=='win32'){const parent=fs.openSync(path.dirname(file),'r');try{fs.fsyncSync(parent)}finally{fs.closeSync(parent)}}}
 finally{if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(temporary)}catch(error){if(error.code!=='ENOENT')throw error}}
 return{alreadyApplied:false,sha256:expected.afterSha256};
}
module.exports={apply};if(require.main===module)console.log(JSON.stringify(apply()));
