import re, pathlib
base = pathlib.Path(r"C:\Users\subha\OneDrive\Desktop\AEGIS SITEWATCH\pioneer_safety_gear_v2\pioneer_safety_gear_v2\fire_dataset")
def src(split):
    return {re.sub(r"\.rf\.[0-9a-f]+", "", p.stem) for p in (base/split/"images").glob("*")}
tr, va = src("train"), src("valid")
print("train sources:", len(tr), "valid sources:", len(va), "overlap:", len(tr & va))
