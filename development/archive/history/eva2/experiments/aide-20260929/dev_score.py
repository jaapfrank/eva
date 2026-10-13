"""Frozen development scorer; candidates receive only authorized metadata."""
import json

def decode_context(context):
    text="\n".join(context)
    if not text:return text,{"active":[],"recent_outcomes":[],"limited":False}
    start=text.find("{")
    if start<0:raise ValueError("missing metadata envelope")
    data,_=json.JSONDecoder().raw_decode(text[start:])
    return text,data

def score(cases, outputs):
    results=[]
    for c,o in zip(cases,outputs,strict=True):
        failures=[];hits=0
        try:
            if o["id"]!=c["id"] or o.get("error"):raise ValueError("case failed")
            text,data=decode_context(o["context"])
            rows=c["listing"]["jobs"];allowed=c["allowed"]
            if o["reads"]!=int(allowed):failures.append("authority/read count")
            if not allowed and text:failures.append("denied scope output")
            for secret in ("NEVER_SEND_SECRET","NEVER_SEND_RESULT","NEVER_SEND_TASK","NEVER_SEND_QUESTION"):
                if secret in text:failures.append("private field leak")
            if set(data)!={"active","recent_outcomes","limited"}:failures.append("context schema")
            if len(data["active"])>8 or len(data["recent_outcomes"])>3:failures.append("context bound")
            by_id={j["work_id"]:j for j in rows};ids=[]
            for group,limit in [("active",160),("recent_outcomes",120)]:
                for j in data[group]:
                    original=by_id[j["work_id"]];ids.append(j["work_id"])
                    keys={"work_id","title","state"}|({"result_available"} if group=="recent_outcomes" else set())
                    if set(j)!=keys or j["title"]!=original["title"][:limit] or j["state"]!=original["state"]:failures.append("changed metadata")
                    if group=="active" and (original["state"] not in {"admitted","call_started"} or original.get("stop_requested",False)):failures.append("false active")
                    if group=="recent_outcomes" and (not original.get("recent_outcome",False) or j["result_available"]!=original.get("result_available",False)):failures.append("false recent/result")
            if len(ids)!=len(set(ids)):failures.append("duplicate jobs")
            active=[j for j in rows if j["state"] in {"admitted","call_started"} and not j.get("stop_requested",False)]
            recent=[j for j in rows if j.get("recent_outcome",False)]
            expected_limited=bool(c["listing"].get("truncated",False) or len(active)>8 or len(recent)>3) if allowed else False
            if data["limited"]!=expected_limited:failures.append("limited flag")
            hits=sum(x in ids for x in c["required_ids"])
        except (ValueError,KeyError,TypeError):
            failures.append("invalid context");text="";hits=0
        results.append({"id":c["id"],"requiredHits":hits,"requiredTotal":len(c["required_ids"]),"hardFailures":failures,"contextBytes":len(text.encode())})
    return {"hardFailures":sum(len(x["hardFailures"]) for x in results),"requiredHits":sum(x["requiredHits"] for x in results),"requiredTotal":sum(x["requiredTotal"] for x in results),"contextBytes":sum(x["contextBytes"] for x in results),"cases":results}
