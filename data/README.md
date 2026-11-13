# Data

PLATO-40 synthetic dataset, proximata Hackathon '26 · Rhomberg track, CC BY 4.0.
Column reference: [quickstart/README.md](quickstart/README.md).

| Folder | Content | In git? |
|---|---|---|
| `quickstart/` | 21 days, January. Every scenario class at least once. | Yes (9 MB) — tests and CI use it |
| `dev/` | 365 days, 2026. | No (150 MB) |
| `holdout/` | Unseen dataset, released ~11:00 on Saturday. | No |

The three datasets are independent synthetic runs. Do not concatenate them.

## Getting the dev dataset

Download `PLATO40_v2_dev.zip` from the link on the proximata Discord and unpack it so that the
files sit directly in `data/dev/`:

```bash
unzip PLATO40_v2_dev.zip -d data/
mv data/PLATO40_v2_dev data/dev
uv run steward info data/dev        # should report 365 days and 40 rooms
```

Checksums of the original archives are in [SHA256SUMS.txt](SHA256SUMS.txt)
(`shasum -a 256 PLATO40_v2_dev.zip`).

## Holdout

The encrypted archive `PLATO40_v2_holdout_encrypted.7z` was distributed with the other files;
keep it in `data/holdout/`. You need a 7z tool before the release: `brew install sevenzip`.

```bash
cd data/holdout && 7zz x PLATO40_v2_holdout_encrypted.7z     # asks for the password from Discord
uv run steward run data/holdout/<extracted folder> --out decisions_holdout.jsonl
```

Tag `pre-holdout` **before** extracting. Run the pipeline unchanged.
