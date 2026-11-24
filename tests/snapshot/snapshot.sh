#!/bin/bash
# снимает поведение всех пяти обёрток в один json
export PYTHONIOENCODING=utf-8
SC="$1"; TAG="$2"
S="$SC/sample.mp4"; N="$SC/novideo_audioless.mp4"
PY314="${PY314:-py}"; ASTRA="${ASTRA_PYTHON:-python}"; BETA="${THUMBMAKER_PYTHON:-python}"
for sec in clipper audioclean clipmaker; do
  $PY314 -3 "$SC/harness.py" $sec "$S" "$N" > "$SC/$TAG.$sec.json" 2> "$SC/$TAG.$sec.err" || echo "  СБОЙ $sec (см. $TAG.$sec.err)"
done
"$ASTRA" "$SC/harness.py" astra "$S" "$N" > "$SC/$TAG.astra.json" 2> "$SC/$TAG.astra.err" || echo "  СБОЙ astra"
"$BETA" "$SC/harness.py" thumbmaker "$S" "$N" > "$SC/$TAG.thumbmaker.json" 2> "$SC/$TAG.thumbmaker.err" || echo "  СБОЙ thumbmaker"
$PY314 -3 - "$SC" "$TAG" <<'PY'
import json, sys, glob, os
sc, tag = sys.argv[1], sys.argv[2]
merged = {}
for f in sorted(glob.glob(os.path.join(sc, tag + ".*.json"))):
    if f.endswith(tag + ".all.json"): continue
    try: merged.update(json.load(open(f, encoding="utf-8")))
    except Exception as e: print("не прочитал", os.path.basename(f), e)
json.dump(merged, open(os.path.join(sc, tag + ".all.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=True)
print("проверок:", len(merged))
PY
