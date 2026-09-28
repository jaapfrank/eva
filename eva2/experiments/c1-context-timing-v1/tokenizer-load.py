import time,json
from eva.adapters.projection.hindsight import _recall_tokenizer
from eva.adapters.model.pydantic_conversation import _input_tokenizer
rows=[]
for label,fn in [('recall_cold',_recall_tokenizer),('input_cold',_input_tokenizer),('recall_warm',_recall_tokenizer),('input_warm',_input_tokenizer)]:
 start=time.monotonic();tokenizer=fn();rows.append({'stage':label,'seconds':round(time.monotonic()-start,4),'encoding':tokenizer.name})
print(json.dumps(rows))
