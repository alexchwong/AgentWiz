import json
import sys

data = json.load(sys.stdin)
print(json.dumps({"words": len(data["text"].split())}))
