import json
import sys
import time

sys.stdin.read()
time.sleep(600)
print(json.dumps({"done": "finished"}))
