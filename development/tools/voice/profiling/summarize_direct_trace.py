"""Summarize measured notification boundaries; unknown provider internals stay unknown."""
import argparse,json,statistics
from pathlib import Path
from safe_codex_trace import error_summary
p=argparse.ArgumentParser();p.add_argument('--eva',action='store_true');p.add_argument('trace',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
events=[]
for line in a.trace.read_text().splitlines():
 try:event=json.loads(line)
 except ValueError:continue
 if isinstance(event,dict):events.append(event)
def selected(stage):return [x for x in events if x.get('stage')==stage]
def first(stage):
 rows=selected(stage);return rows[0]['elapsed_ms'] if rows else None
notes=selected('profile_codex_notification');items={};searches=[]
for e in notes:
 if e.get('method')=='item/started':items[e['item_index']]=e
 if e.get('method')=='item/completed' and e.get('item_type')=='webSearch':
  begin=items.get(e['item_index']);searches.append({'item_index':e['item_index'],'action_type':e.get('action_type'),'start_ms':begin['elapsed_ms'] if begin else None,'end_ms':e['elapsed_ms'],'observed_duration_ms':e['elapsed_ms']-begin['elapsed_ms'] if begin else None})
def notification(method):return next((x['elapsed_ms'] for x in notes if x.get('method')==method),None)
end=first('profile_microphone_clip_end');sink=selected('profile_first_sink_signal');usage=[x for x in notes if x.get('method')=='thread/tokenUsage/updated']
stats=selected('profile_rtc_stats');rtt=[x['roundTripTime']*1000 for x in stats if 'roundTripTime' in x]
report={'observed_error_signals':error_summary(events),'synthetic_only':True,'eva_core_connected':a.eva,'production_changed':False,'clock':'Local monotonic milliseconds from voice start; prewarm events occur before that reset and are not comparable.',
 'milestones_ms':{'microphone_clip_end':end,'media_ready':first('media_ready'),'delegation_received':next((x['elapsed_ms'] for x in events if x.get('stage')=='provider_event' and x.get('kind')=='delegation.created'),None),'backend_turn_start':notification('turn/started'),'first_backend_text_delta':notification('item/agentMessage/delta'),'backend_turn_end':notification('turn/completed'),'first_audio_per_turn':[x['elapsed_ms'] for x in sink]},
 'audio_after_clip_end_ms':[x['elapsed_ms']-end for x in sink] if end is not None else [],
 'observed_searches':searches,'last_token_usage':usage[-1].get('total') if usage else None,
 'last_nonquiet_microphone_frame_ms':next((x.get('signal_elapsed_ms') for x in selected('profile_last_microphone_signal')),None),
 'network':{'rtt_samples':len(rtt),'rtt_median_ms':round(statistics.median(rtt),2) if rtt else None,'rtt_max_ms':round(max(rtt),2) if rtt else None,'maximum_reported_packets_lost':max((x['packetsLost'] for x in stats if 'packetsLost' in x),default=None),'rtp_jitter_units':'RTP timestamp ticks, not milliseconds; audio clock is 48000 Hz.'},
 'max_observed_microphone_queue_frames':max((x['frames'] for x in selected('profile_microphone_queue')),default=None),
 'event_loop_lag':[{k:v for k,v in x.items() if k in {'samples','maximum_ms','p95_ms'}} for x in selected('profile_event_loop_lag')],
 'limitations':['Notification arrival times bound observed stages; they do not expose server queue time, pure inference time, DNS or search provider internal timing.','First software PCM is not measured acoustic playback.','Search completion proves a tool event, not forecast correctness. No query, URL, transcript or credentials are recorded.','A single sample is not a performance distribution.']}
a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
