import re, pathlib
base = pathlib.Path(".") / "fire_dataset"
key = lambda p: re.sub(r"\.rf\.[0-9a-f]+", "", p.stem)
tr = {key(p) for p in (base/"train"/"images").glob("*")}
te = {key(p) for p in (base/"test"/"images").glob("*")}
print("test sources:", len(te), "overlap with train:", len(tr & te))
