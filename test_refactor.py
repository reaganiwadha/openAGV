import os
import shutil
from openagv.core import SqliteAssetBin, OTIOTimeline

# Setup test assets
os.makedirs("test_assets_refactor", exist_ok=True)
with open("test_assets_refactor/clip1.mp4", "w") as f:
    f.write("fakevideo1")

DB_PATH = "test_timeline_refactor.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

print("Testing OTIOTimeline Refactor...")
try:
    bin = SqliteAssetBin(DB_PATH)
    a1 = bin.add("test_assets_refactor/clip1.mp4")

    timeline = OTIOTimeline("MyTestTimelineRefactor")
    timeline.set_asset_bin(bin)

    print(timeline.add_clip_by_id(a1.id, 5.0))
    print(timeline.get_summary())
    
    print("SUCCESS: Refactor seems stable.")

except Exception as e:
    print(f"FAILURE: {e}")

# Cleanup
shutil.rmtree("test_assets_refactor")
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
