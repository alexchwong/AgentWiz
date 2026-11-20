import os
open(os.environ.get("AGENTWIZ_UPGRADE_CANARY", 'agentwiz-upgrade-canary'), "w").write("ran")
