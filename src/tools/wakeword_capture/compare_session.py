"""Evaluate a confirmed capture session locally; never train, play, or upload audio."""
import argparse, hashlib, json, os, wave
from pathlib import Path
os.environ['CUDA_VISIBLE_DEVICES']='-1'
os.environ['TF_CPP_MIN_LOG_LEVEL']='3'
os.environ['OMP_NUM_THREADS']='2'
os.environ['TF_NUM_INTRAOP_THREADS']='2'
os.environ['TF_NUM_INTEROP_THREADS']='2'
def gate(baseline,candidate,complete):
    reduction=None if baseline['negativeActivated']==0 else 1-candidate['negativeActivated']/baseline['negativeActivated']
    recall=candidate['positiveDetected']/candidate['positiveTotal'] if candidate['positiveTotal'] else 0
    loss=max((baseline['positions'][p]['recall']-candidate['positions'][p]['recall'] for p in ['near','normal']),default=1)
    return {'falseClipReduction':reduction,'candidateRecall':recall,'maxPositionRecallLoss':loss,'screenPass':complete and reduction is not None and reduction>=.5 and recall>=.9 and loss<=.05,'installationApproved':False,'boundary':'Small browser-microphone screen only. Device audio/VAD/echo and longer daily-use false-trigger rate remain unproven; no threshold chosen on these held-out clips.'}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,default=Path('/mnt/e/Projecten/wakeword/hey_eva/hey_eva.json'))
    parser.add_argument('--candidate',type=Path,required=True,help='Frozen local microWakeWord model manifest, including cutoff/window.')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import numpy as np
    from microwakeword.inference import Model
    directory=args.session.resolve();manifest=json.loads((directory/'manifest.json').read_text())
    if manifest.get('schema')!='WakewordLabeledSession/v1' or manifest.get('purpose')!='heldout_evaluation_only':raise ValueError('Not a collector held-out session')
    clips=manifest['clips'];protocol={r['id']:r for r in manifest['protocol']}
    if len({r['id'] for r in clips})!=len(clips):raise ValueError('Duplicate clip IDs')
    result={'session':manifest['id'],'sessionPurpose':manifest['purpose'],'manifestSha256':hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(),'complete':len(clips)==30 and manifest.get('complete') is True,'models':{}}
    models={}
    for name,file in [('baseline',args.baseline),('candidate',args.candidate)]:
        data=json.loads(file.read_text());model_path=(file.parent/data['model']).resolve()
        if not model_path.is_file():raise ValueError('Local TFLite file required')
        cfg=data['micro'];cutoff=int(float(cfg['probability_cutoff'])*255);window=int(cfg['sliding_window_size'])
        if not 0<=cutoff<=255 or not 1<=window<=100 or cfg['feature_step_size']!=10:raise ValueError('Only validated 10ms manifests supported')
        models[name]=(model_path,cutoff,window)
    for name,(model_path,cutoff,window) in models.items():
        rows=[]
        for clip in clips:
            expected=protocol.get(clip['id']);file=(directory/clip['file']).resolve()
            if file.parent!=directory or not expected or any(clip[k]!=expected[k] for k in ['label','text','position']) or clip.get('labelConfirmed')!='spoken-as-shown':raise ValueError('Clip labels/path not confirmed')
            digest=hashlib.sha256(file.read_bytes()).hexdigest()
            if digest!=clip['sha256']:raise ValueError('Audio hash changed: '+clip['id'])
            with wave.open(str(file),'rb') as f:
                if (f.getframerate(),f.getnchannels(),f.getsampwidth())!=(16000,1,2):raise ValueError('16kHz mono PCM16 required')
                pcm=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2')
            model=Model(str(model_path));pred=np.asarray(model.predict_clip(pcm,step_ms=10));scale,zero=model.output_details[0]['quantization'];raw=np.rint(pred/scale+zero)
            detected=bool(len(raw)>=window and np.any(np.convolve(raw,np.ones(window)/window,'valid')>cutoff))
            rows.append({'id':clip['id'],'label':clip['label'],'position':clip['position'],'sha256':digest,'detected':detected})
        positive=[r for r in rows if r['label']=='positive'];negative=[r for r in rows if r['label']=='negative']
        positions={}
        for p in ['near','normal']:
            subset=[r for r in positive if r['position']==p];positions[p]={'detected':sum(r['detected'] for r in subset),'total':len(subset),'recall':sum(r['detected'] for r in subset)/len(subset) if subset else 0}
        result['models'][name]={'modelSha256':hashlib.sha256(model_path.read_bytes()).hexdigest(),'cutoffUint8':cutoff,'window':window,'positiveDetected':sum(r['detected'] for r in positive),'positiveTotal':len(positive),'negativeActivated':sum(r['detected'] for r in negative),'negativeTotal':len(negative),'positions':positions,'clips':rows}
    result['gate']=gate(result['models']['baseline'],result['models']['candidate'],result['complete'])
    if args.output.exists():raise ValueError('Output already exists; choose a new receipt path')
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'complete':result['complete'],'gate':result['gate'],'result':str(args.output)},ensure_ascii=False))
if __name__=='__main__':main()
